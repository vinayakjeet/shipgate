from __future__ import annotations

import asyncio
import os
import subprocess
import uuid
from pathlib import Path
from typing import Annotated

import typer

from shipgate.cache import PostgresResultCache
from shipgate.config import get_settings
from shipgate.datasets.diff import diff_files
from shipgate.datasets.hashing import content_hash
from shipgate.datasets.loader import DatasetError, load_jsonl
from shipgate.datasets.manifest import manifest_for_file, manifest_path_for, write_manifest
from shipgate.runners.base import execute_run
from shipgate.runners.exact import ExactMatchRunner
from shipgate.scoring import (
    cache_hit_rate,
    error_count,
    overall_score,
    p50_latency_ms,
    slice_scores,
)
from shipgate.store import db
from shipgate.targets import StubTarget
from shipgate.types import RunRecord

app = typer.Typer(add_completion=False, help="Eval datasets, runners, and the CI gate.")

RUNNERS = {"exact": ExactMatchRunner}


@app.callback()
def main() -> None:
    """Keeps subcommand mode on even while only `run` exists, so the command
    name stays stable as register, label, and calibrate land."""


def _git_sha() -> str:
    """Prefer the CI-provided sha, fall back to local git, then to 'dev'."""
    for env_var in ("GITHUB_SHA", "GIT_SHA"):
        if value := os.environ.get(env_var):
            return value[:40]
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return get_settings().git_sha


@app.command()
def diff(
    before: Annotated[Path, typer.Option("--before", help="The earlier dataset file.")],
    after: Annotated[Path, typer.Option("--after", help="The later dataset file.")],
) -> None:
    """Compare two dataset files: items added, removed, and edited."""
    try:
        result = diff_files(before, after)
    except DatasetError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    if result.is_identical:
        typer.echo("identical, same hash")
        return

    typer.echo(f"before={result.before_hash}")
    typer.echo(f"after={result.after_hash}")
    typer.echo(
        f"added={len(result.added)} removed={len(result.removed)} "
        f"changed={len(result.changed)} unchanged={result.unchanged}"
    )
    for label, ids in (("+", result.added), ("-", result.removed), ("~", result.changed)):
        for item_id in ids:
            typer.echo(f"  {label} {item_id}")


@app.command()
def register(
    dataset: Annotated[Path, typer.Option("--dataset", help="Path to a JSONL dataset.")],
    dataset_id: Annotated[
        str | None, typer.Option("--dataset-id", help="Defaults to the filename.")
    ] = None,
    store: Annotated[
        bool, typer.Option("--store/--no-store", help="Write the version to the database.")
    ] = True,
) -> None:
    """Hash a dataset, write its manifest, and record the version."""
    try:
        manifest = manifest_for_file(dataset, dataset_id)
    except DatasetError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    write_manifest(manifest_path_for(dataset), manifest)

    typer.echo(f"dataset_id={manifest.id}")
    typer.echo(f"hash={manifest.hash}")
    typer.echo(f"n={manifest.n}")
    for tag, count in manifest.slice_counts.items():
        typer.echo(f"  {tag}: {count}")

    if not store:
        return

    with db.connect() as conn:
        db.migrate(conn)
        created = db.register_dataset(conn, manifest)
        conn.commit()
    typer.echo("registered new version" if created else "version already registered, no change")


@app.command()
def run(
    dataset: Annotated[Path, typer.Option("--dataset", help="Path to a JSONL dataset.")],
    dataset_id: Annotated[
        str | None, typer.Option("--dataset-id", help="Defaults to the filename.")
    ] = None,
    runner: Annotated[
        str, typer.Option("--runner", help=f"One of: {', '.join(RUNNERS)}")
    ] = "exact",
    trigger: Annotated[str, typer.Option("--trigger", help="manual, pr, or cron.")] = "manual",
    store: Annotated[
        bool, typer.Option("--store/--no-store", help="Write the run to the database.")
    ] = True,
    cache: Annotated[
        bool, typer.Option("--cache/--no-cache", help="Reuse cached per-item results.")
    ] = False,
) -> None:
    """Score a dataset and record the run."""
    if runner not in RUNNERS:
        raise typer.BadParameter(f"unknown runner {runner!r}. Valid: {', '.join(RUNNERS)}")

    try:
        items = load_jsonl(dataset)
    except DatasetError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    dataset_hash = content_hash(items)
    # Off by default: the exact runner costs nothing to recompute, so a database
    # round trip per item would be slower than the work it saves. Judge runs,
    # which pay rate-limited seconds per item, are where this earns its keep.
    result_cache = PostgresResultCache() if cache else None

    results = asyncio.run(
        execute_run(
            RUNNERS[runner](),
            items,
            StubTarget(),
            dataset_hash=dataset_hash,
            cache=result_cache,
        )
    )

    record = RunRecord(
        run_id=f"r_{uuid.uuid4().hex[:16]}",
        dataset_id=dataset_id or dataset.stem,
        dataset_hash=dataset_hash,
        git_sha=_git_sha(),
        runner=runner,
        model=StubTarget.name,
        n=len(results),
        score=overall_score(results),
        slices=slice_scores(results),
        cost_usd=0.0,
        p50_latency_ms=p50_latency_ms(results),
        cache_hit_rate=cache_hit_rate(results),
        error_count=error_count(results),
        trigger=trigger,
    )

    typer.echo(f"score={record.score:.2f} n={record.n}")
    for tag, value in record.slices.items():
        typer.echo(f"  {tag}: {value:.2f}")

    # Errors score 0, so without this line a broken judge and a real regression
    # produce the same number.
    if errors := error_count(results):
        typer.secho(
            f"errors={errors} (scored 0, not judged) - treat this score as suspect",
            fg=typer.colors.YELLOW,
        )

    if not store:
        return

    with db.connect() as conn:
        db.migrate(conn)
        db.insert_run(conn, record)
        db.insert_run_items(conn, record.run_id, results)
        conn.commit()
    typer.echo(f"run_id={record.run_id}")


if __name__ == "__main__":
    app()
