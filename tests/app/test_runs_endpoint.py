from __future__ import annotations

import uuid

import pytest

from shipgate.config import get_settings
from shipgate.store import db
from shipgate.types import RunRecord

needs_db = pytest.mark.skipif(
    not get_settings().shipgate_db_url, reason="SHIPGATE_DB_URL not set"
)


@pytest.fixture
def stored_run():
    record = RunRecord(
        run_id=f"r_api_{uuid.uuid4().hex[:12]}",
        dataset_id=f"api-probe-{uuid.uuid4().hex[:6]}",
        dataset_hash="sha256:test",
        runner="exact",
        model="stub-majority",
        n=5,
        score=0.6,
        slices={"lang:en": 0.6},
    )
    with db.connect() as conn:
        db.migrate(conn)
        db.insert_run(conn, record)
        conn.commit()
    yield record
    with db.connect() as conn:
        conn.execute("delete from runs where run_id = %s", (record.run_id,))
        conn.commit()


@needs_db
def test_runs_returns_rows(client, stored_run):
    resp = client.get("/api/runs", params={"dataset": stored_run.dataset_id})
    assert resp.status_code == 200

    runs = resp.json()["runs"]
    assert len(runs) == 1
    assert runs[0]["run_id"] == stored_run.run_id
    assert runs[0]["score"] == pytest.approx(0.6)
    assert runs[0]["slices"] == {"lang:en": 0.6}


@needs_db
def test_get_single_run(client, stored_run):
    resp = client.get(f"/api/runs/{stored_run.run_id}")
    assert resp.status_code == 200
    assert resp.json()["dataset_id"] == stored_run.dataset_id


@needs_db
def test_unknown_run_is_404(client):
    assert client.get("/api/runs/r_does_not_exist").status_code == 404


@needs_db
def test_limit_is_validated(client):
    assert client.get("/api/runs", params={"limit": 0}).status_code == 422
