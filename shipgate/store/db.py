from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from shipgate.config import get_settings
from shipgate.datasets.manifest import DatasetManifest
from shipgate.types import ItemResult, RunRecord

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
            run_id, dataset_id, dataset_hash, git_sha, git_ref, runner, model,
            n, score, slices, cost_usd, p50_latency_ms, cache_hit_rate,
            error_count, verdict, trigger
        ) values (
            %(run_id)s, %(dataset_id)s, %(dataset_hash)s, %(git_sha)s, %(git_ref)s,
            %(runner)s, %(model)s,
            %(n)s, %(score)s, %(slices)s, %(cost_usd)s, %(p50_latency_ms)s,
            %(cache_hit_rate)s, %(error_count)s, %(verdict)s, %(trigger)s
        )
        """,
        {**run.model_dump(exclude={"slices", "started_at", "finished_at"}),
         "slices": json.dumps(run.slices)},
    )
    return run.run_id


def insert_run_items(conn: psycopg.Connection, run_id: str, results: list[ItemResult]) -> int:
    """Store per-item outcomes. Written in one executemany rather than a loop,
    because 100 separate round trips to a free-tier database is most of a run."""
    if not results:
        return 0
    conn.cursor().executemany(
        """
        insert into run_items (
            run_id, item_id, output, score, slices, latency_ms, cache_hit, error, meta
        ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        on conflict (run_id, item_id) do nothing
        """,
        [
            (
                run_id,
                r.item_id,
                r.output,
                r.score,
                json.dumps(r.slices),
                r.latency_ms,
                r.cache_hit,
                r.error,
                json.dumps(r.meta),
            )
            for r in results
        ],
    )
    return len(results)


def fetch_run_items(
    conn: psycopg.Connection, run_id: str, failing_only: bool = False
) -> list[dict]:
    sql = "select * from run_items where run_id = %s"
    if failing_only:
        sql += " and score < 1.0"
    return conn.execute(sql + " order by score asc, item_id asc", (run_id,)).fetchall()


def upsert_label(
    conn: psycopg.Connection,
    dataset_id: str,
    dataset_hash: str,
    item_id: str,
    label: str,
    notes: str | None = None,
) -> None:
    """Record one hand label. Re-labeling the same item overwrites, so a
    correction during a labeling session is not a duplicate row."""
    conn.execute(
        """
        insert into labels (dataset_id, dataset_hash, item_id, label, notes)
        values (%s, %s, %s, %s, %s)
        on conflict (dataset_id, dataset_hash, item_id)
        do update set label = excluded.label,
                      notes = excluded.notes,
                      labeled_at = now()
        """,
        (dataset_id, dataset_hash, item_id, label, notes),
    )


def fetch_labels(conn: psycopg.Connection, dataset_id: str, dataset_hash: str) -> dict[str, str]:
    """Labels for one dataset version, as {item_id: label}.

    Scoped to the hash deliberately. A label describes an item as a human read
    it, so labels must not carry over to text nobody reviewed.
    """
    rows = conn.execute(
        "select item_id, label from labels where dataset_id = %s and dataset_hash = %s",
        (dataset_id, dataset_hash),
    ).fetchall()
    return {row["item_id"]: row["label"] for row in rows}


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


def fetch_dataset_summaries(conn: psycopg.Connection, history: int = 30) -> list[dict]:
    """One entry per dataset: its latest run plus recent scores for a trend line.

    Two queries rather than one per dataset, because the dashboard runs on a free
    tier where a per-row query would dominate the page load.
    """
    latest = conn.execute(
        """
        select distinct on (dataset_id) *
        from runs
        order by dataset_id, started_at desc
        """
    ).fetchall()

    history_rows = conn.execute(
        """
        select dataset_id, score, started_at
        from (
            select dataset_id, score, started_at,
                   row_number() over (partition by dataset_id order by started_at desc) as rn
            from runs
        ) ranked
        where rn <= %s
        order by dataset_id, started_at asc
        """,
        (history,),
    ).fetchall()

    trends: dict[str, list[float]] = {}
    for row in history_rows:
        trends.setdefault(row["dataset_id"], []).append(row["score"])

    return [{**row, "trend": trends.get(row["dataset_id"], [])} for row in latest]


def fetch_run(conn: psycopg.Connection, run_id: str) -> dict | None:
    return conn.execute("select * from runs where run_id = %s", (run_id,)).fetchone()
