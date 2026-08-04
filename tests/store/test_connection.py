from __future__ import annotations

import uuid

import pytest

from shipgate.config import get_settings
from shipgate.store import db
from shipgate.types import RunRecord

pytestmark = pytest.mark.skipif(
    not get_settings().shipgate_db_url,
    reason="SHIPGATE_DB_URL not set, skipping database round-trip",
)


def _record(**overrides) -> RunRecord:
    defaults = {
        "run_id": f"r_test_{uuid.uuid4().hex[:12]}",
        "dataset_id": "smoke",
        "dataset_hash": "sha256:test",
        "runner": "exact",
        "model": "stub",
        "n": 5,
        "score": 0.6,
        "slices": {"lang:en": 0.6},
        "cost_usd": 0.0,
        "p50_latency_ms": 1.0,
        "cache_hit_rate": 0.0,
    }
    return RunRecord(**{**defaults, **overrides})


def test_insert_and_read_run():
    """Round-trip one run. Rolls back so the shared database keeps no test rows."""
    with db.connect() as conn:
        db.migrate(conn)
        conn.commit()
        try:
            record = _record()
            db.insert_run(conn, record)

            stored = db.fetch_run(conn, record.run_id)
            assert stored is not None
            assert stored["dataset_id"] == "smoke"
            assert stored["n"] == 5
            assert stored["score"] == pytest.approx(0.6)
            assert stored["slices"] == {"lang:en": 0.6}
            assert stored["trigger"] == "manual"
            assert stored["started_at"] is not None
        finally:
            conn.rollback()


def test_migrate_is_idempotent():
    with db.connect() as conn:
        db.migrate(conn)
        db.migrate(conn)
        conn.commit()


def test_fetch_runs_filters_by_dataset():
    with db.connect() as conn:
        db.migrate(conn)
        conn.commit()
        try:
            mine = _record(dataset_id="filter-probe")
            db.insert_run(conn, mine)

            rows = db.fetch_runs(conn, dataset_id="filter-probe", limit=10)
            assert [r["run_id"] for r in rows] == [mine.run_id]
        finally:
            conn.rollback()


def test_missing_url_raises_actionable_error(monkeypatch):
    monkeypatch.setenv("SHIPGATE_DB_URL", "")
    with pytest.raises(db.DatabaseNotConfigured, match="SHIPGATE_DB_URL"):
        db.database_url("")
