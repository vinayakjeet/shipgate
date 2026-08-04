from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from shipgate.config import get_settings
from shipgate.datasets.manifest import DatasetManifest
from shipgate.types import RunRecord

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


class DatabaseNotConfigured(RuntimeError):
    """SHIPGATE_DB_URL is missing. Raised early with a fix, not a driver stack trace."""


def database_url(url: str | None = None) -> str:
    resolved = url or get_settings().shipgate_db_url
    if not resolved:
        raise DatabaseNotConfigured(
            "SHIPGATE_DB_URL is not set. Put the Neon connection string in .env "
            "locally, or in GitHub Actions secrets for CI."
        )
    return resolved


@contextmanager
def connect(url: str | None = None) -> Iterator[psycopg.Connection]:
    """Open a connection with dict rows. Commits are explicit, never implicit."""
    with psycopg.connect(database_url(url), row_factory=dict_row) as conn:
        yield conn


def migrate(conn: psycopg.Connection) -> None:
    """Apply schema.sql. Idempotent, so calling it on every start is fine."""
    conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))


def register_dataset(conn: psycopg.Connection, manifest: DatasetManifest) -> bool:
    """Record a dataset version. Returns True when this hash is new.

    Registering an unchanged dataset is a no-op rather than an error, so calling
    it on every run is safe. A changed dataset produces a different hash and
    therefore a new row, which is what keeps old baselines interpretable.
    """
    cur = conn.execute(
        """
        insert into datasets (dataset_id, dataset_hash, path, n, slice_counts)
        values (%(id)s, %(hash)s, %(path)s, %(n)s, %(slice_counts)s)
        on conflict (dataset_id, dataset_hash) do nothing
        """,
        {
            "id": manifest.id,
            "hash": manifest.hash,
            "path": manifest.path,
            "n": manifest.n,
            "slice_counts": json.dumps(manifest.slice_counts),
        },
    )
    return cur.rowcount == 1


def fetch_dataset_versions(conn: psycopg.Connection, dataset_id: str) -> list[dict]:
    """Every recorded version of a dataset, newest first."""
    return conn.execute(
        "select * from datasets where dataset_id = %s order by registered_at desc",
        (dataset_id,),
    ).fetchall()


def insert_run(conn: psycopg.Connection, run: RunRecord) -> str:
    conn.execute(
        """
        insert into runs (
            run_id, dataset_id, dataset_hash, git_sha, runner, model,
            n, score, slices, cost_usd, p50_latency_ms, cache_hit_rate, trigger
        ) values (
            %(run_id)s, %(dataset_id)s, %(dataset_hash)s, %(git_sha)s, %(runner)s, %(model)s,
            %(n)s, %(score)s, %(slices)s, %(cost_usd)s, %(p50_latency_ms)s,
            %(cache_hit_rate)s, %(trigger)s
        )
        """,
        {**run.model_dump(exclude={"slices", "started_at", "finished_at"}),
         "slices": json.dumps(run.slices)},
    )
    return run.run_id


def fetch_runs(
    conn: psycopg.Connection, dataset_id: str | None = None, limit: int = 50
) -> list[dict]:
    if dataset_id:
        cur = conn.execute(
            "select * from runs where dataset_id = %s order by started_at desc limit %s",
            (dataset_id, limit),
        )
    else:
        cur = conn.execute("select * from runs order by started_at desc limit %s", (limit,))
    return cur.fetchall()


def fetch_run(conn: psycopg.Connection, run_id: str) -> dict | None:
    return conn.execute("select * from runs where run_id = %s", (run_id,)).fetchone()
