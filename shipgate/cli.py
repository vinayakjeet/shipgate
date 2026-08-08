from __future__ import annotations

import asyncio
import os
import subprocess
import uuid
from pathlib import Path
from typing import Annotated

import spanlight
import typer

from shipgate.cache import PostgresResultCache
from shipgate.calibration.labeling import LabelStore, load_predictions, remaining
from shipgate.config import get_settings
from shipgate.datasets.diff import diff_files
from shipgate.datasets.hashing import content_hash
from shipgate.datasets.loader import DatasetError, load_jsonl
from shipgate.datasets.manifest import manifest_for_file, manifest_path_for, write_manifest
from shipgate.gate.baseline import resolve_baseline
from shipgate.gate.report import render_summary
from shipgate.gate.verdict import evaluate
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
    """Enables tracing when an OTLP endpoint is configured, and keeps subcommand
    mode on so command names stay stable as more of them land."""
    spanlight.init(service="shipgate")


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


def _git_ref() -> str | None:
    """Branch name. Baseline resolution filters on this, so an unknown ref means
    the run simply never becomes a baseline rather than corrupting one."""
    if value := os.environ.get("GITHUB_REF_NAME"):
        return value
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


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
        git_ref=_git_ref(),
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


@app.command()
def label(
    dataset: Annotated[Path, typer.Option("--dataset", help="Path to a JSONL dataset.")],
    labels_path: Annotated[
        Path, typer.Option("--labels", help="Where labels are written.")
    ] = Path("datasets/labels.jsonl"),
    predictions: Annotated[
        Path | None,
        typer.Option("--predictions", help="JSONL of real model predictions to judge."),
    ] = Path("datasets/predictions-groq.jsonl"),
    target_label: Annotated[
        str | None,
        typer.Option("--target-label", help="Fixed prediction, instead of a predictions file."),
    ] = None,
    limit: Annotated[
        int, typer.Option("--limit", help="Stop after this many, for short sittings.")
    ] = 0,
    store: Annotated[bool, typer.Option("--store/--no-store")] = True,
) -> None:
    """Hand-label items as pass or fail, to calibrate the judge against.

    Shows the ticket, the expected intent, and what the model answered. It never
    shows the judge's verdict, because a labeler who sees it will anchor on it and
    the resulting agreement number would measure nothing.
    """
    try:
        items = load_jsonl(dataset)
    except DatasetError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    dataset_hash = content_hash(items)

    # Prefer real predictions. Labeling against a fixed label is mechanical, since
    # the answer is then just whether expected equals that label, and a kappa
    # computed from it measures the judge's string comparison rather than its
    # judgement.
    predicted: dict[str, str] = {}
    if target_label is None:
        try:
            predicted = load_predictions(predictions)
        except FileNotFoundError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(code=2) from exc

    store_file = LabelStore(labels_path)
    labeled = store_file.load()
    todo = [i for i in remaining(items, labeled) if target_label or i.id in predicted]

    if not todo:
        typer.secho(f"all {len(labeled)} available items already labeled", fg=typer.colors.GREEN)
        raise typer.Exit(code=0)

    typer.echo(f"{len(labeled)} labeled, {len(todo)} remaining. Ctrl-C is safe, nothing is lost.")
    typer.echo("Answer: [p]ass  [f]ail  [s]kip  [q]uit\n")

    done = 0
    for item in todo:
        if limit and done >= limit:
            break

        typer.echo("-" * 72)
        typer.echo(f"{item.id}  ({', '.join(item.slices)})")
        typer.echo(f"\n  ticket:   {item.input.get('prompt', '')}")
        typer.echo(f"  expected: {item.expected}")
        typer.echo(f"  model:    {target_label or predicted[item.id]}\n")

        answer = typer.prompt("  correct?", default="p").strip().lower()
        if answer in {"q", "quit"}:
            break
        if answer in {"s", "skip"}:
            continue

        verdict = "pass" if answer.startswith("p") else "fail"
        store_file.append(item.id, verdict, dataset_hash)
        labeled[item.id] = verdict
        done += 1

        if store:
            try:
                with db.connect() as conn:
                    db.migrate(conn)
                    db.upsert_label(conn, dataset.stem, dataset_hash, item.id, verdict)
                    conn.commit()
            except db.DatabaseNotConfigured:
                # The JSONL already has it, so a missing database costs nothing.
                pass

    typer.echo("-" * 72)
    typer.secho(
        f"{len(labeled)} of {len(items)} labeled, {len(items) - len(labeled)} to go",
        fg=typer.colors.GREEN,
    )


def _emit_action_outputs(outcome, run_id: str) -> None:
    """Write step outputs when running inside GitHub Actions.

    A no-op locally, so the same command works in both places rather than needing
    a CI-only code path that only gets exercised in CI.
    """
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    delta = "" if outcome.delta is None else f"{outcome.delta:.4f}"
    baseline = "" if outcome.baseline_score is None else f"{outcome.baseline_score:.4f}"
    lines = [
        f"verdict={outcome.verdict.value}",
        f"score={outcome.score:.4f}",
        f"baseline_score={baseline}",
        f"delta={delta}",
        f"run_id={run_id}",
        f"failing_slices={','.join(outcome.failing_slices)}",
    ]
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


@app.command()
def gate(
    dataset: Annotated[Path, typer.Option("--dataset", help="Path to a JSONL dataset.")],
    dataset_id: Annotated[
        str | None, typer.Option("--dataset-id", help="Defaults to the filename.")
    ] = None,
    runner: Annotated[str, typer.Option("--runner")] = "exact",
    baseline_ref: Annotated[
        str, typer.Option("--baseline-ref", help="Branch the baseline comes from.")
    ] = "main",
    threshold_overall: Annotated[float, typer.Option("--threshold-overall")] = 0.02,
    threshold_slice: Annotated[float, typer.Option("--threshold-slice")] = 0.05,
    summary_path: Annotated[
        Path | None, typer.Option("--summary", help="Write the markdown verdict here.")
    ] = None,
    target_label: Annotated[
        str,
        typer.Option(
            "--target-label",
            help="Label the stub target predicts. Changing it simulates a model change.",
        ),
    ] = "billing",
) -> None:
    """Score a dataset, compare against the baseline, and fail on regression."""
    if runner not in RUNNERS:
        raise typer.BadParameter(f"unknown runner {runner!r}. Valid: {', '.join(RUNNERS)}")

    try:
        items = load_jsonl(dataset)
    except DatasetError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    dataset_hash = content_hash(items)

    # The gate run is one session, so a CI failure is one trace to open rather
    # than a scatter of unrelated spans. Each scored item opens its own session
    # inside it, which is what keeps one item's failure from being read against
    # the next item's model call.
    with spanlight.session() as session_id:
        results = asyncio.run(
            execute_run(
                RUNNERS[runner](), items, StubTarget(target_label), dataset_hash=dataset_hash
            )
        )
    typer.echo(f"session {session_id}")

    record = RunRecord(
        run_id=f"r_{uuid.uuid4().hex[:16]}",
        dataset_id=dataset_id or dataset.stem,
        dataset_hash=dataset_hash,
        git_sha=_git_sha(),
        git_ref=_git_ref(),
        runner=runner,
        model=f"{StubTarget.name}:{target_label}",
        n=len(results),
        score=overall_score(results),
        slices=slice_scores(results),
        cost_usd=0.0,
        p50_latency_ms=p50_latency_ms(results),
        cache_hit_rate=cache_hit_rate(results),
        error_count=error_count(results),
        trigger="gate",
    )

    with db.connect() as conn:
        db.migrate(conn)

        # Resolve the baseline BEFORE storing this run. Otherwise the run becomes
        # its own baseline, every delta is exactly zero, and the gate can never
        # fail. exclude_run_id guards the same mistake from the other direction.
        baseline = resolve_baseline(
            conn,
            record.dataset_id,
            record.dataset_hash,
            baseline_ref,
            exclude_run_id=record.run_id,
        )

        # Evaluate before storing, so the verdict is recorded with the run. A run
        # saved without one is eligible to become tomorrow's baseline, which is
        # how a regression becomes the new normal after failing exactly once.
        outcome = evaluate(
            record,
            baseline,
            threshold_overall=threshold_overall,
            threshold_slice=threshold_slice,
        )
        record = record.model_copy(update={"verdict": outcome.verdict.value})

        db.insert_run(conn, record)
        db.insert_run_items(conn, record.run_id, results)
        conn.commit()

        failing = db.fetch_run_items(conn, record.run_id, failing_only=True)

    summary = render_summary(outcome, failing)
    typer.echo(summary)
    if summary_path:
        summary_path.write_text(summary + "\n", encoding="utf-8")

    _emit_action_outputs(outcome, record.run_id)

    # Only a real regression blocks. A missing baseline or a run full of provider
    # errors exits zero and complains, because blocking on infrastructure trains
    # people to bypass the gate.
    raise typer.Exit(code=1 if outcome.blocks else 0)


if __name__ == "__main__":
    app()
