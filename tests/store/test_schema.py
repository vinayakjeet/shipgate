from __future__ import annotations

import uuid

import pytest

from shipgate.config import get_settings
from shipgate.store import db
from shipgate.types import ItemResult, RunRecord

pytestmark = pytest.mark.skipif(
    not get_settings().shipgate_db_url, reason="SHIPGATE_DB_URL not set"
)


@pytest.fixture
def run():
    record = RunRecord(
        run_id=f"r_sch_{uuid.uuid4().hex[:12]}",
        dataset_id=f"schema-probe-{uuid.uuid4().hex[:6]}",
        dataset_hash="sha256:test",
        runner="judge",
        model="gemini-2.0-flash",
        n=3,
        score=2 / 3,
        slices={"intent:billing": 1.0, "intent:technical": 0.0},
        error_count=1,
    )
    with db.connect() as conn:
        db.migrate(conn)
        db.insert_run(conn, record)
        conn.commit()
    yield record
    with db.connect() as conn:
        conn.execute("delete from runs where run_id = %s", (record.run_id,))
        conn.commit()


def test_migration_idempotent():
    """Applied on every run and every request, so it has to be safe to repeat."""
    with db.connect() as conn:
        db.migrate(conn)
        db.migrate(conn)
        db.migrate(conn)
        conn.commit()


def test_error_count_round_trips(run):
    """The gate reads this next to the score to tell a broken judge from a bad
    model, so it has to survive storage."""
    with db.connect() as conn:
        stored = db.fetch_run(conn, run.run_id)
    assert stored["error_count"] == 1


def test_run_items_round_trip(run):
    results = [
        ItemResult(item_id="a", output="billing", score=1.0, slices=["intent:billing"]),
        ItemResult(item_id="b", output="wrong", score=0.0, slices=["intent:technical"]),
        ItemResult(item_id="c", output="", score=0.0, error="JudgeParseError: junk"),
    ]
    with db.connect() as conn:
        db.insert_run_items(conn, run.run_id, results)
        conn.commit()
        stored = db.fetch_run_items(conn, run.run_id)

    assert len(stored) == 3
    by_id = {r["item_id"]: r for r in stored}
    assert by_id["a"]["score"] == 1.0
    assert by_id["a"]["slices"] == ["intent:billing"]
    assert by_id["c"]["error"].startswith("JudgeParseError")


def test_failing_only_filters_and_orders_worst_first(run):
    """The gate reports the worst newly-failing examples, so the query does the
    ordering rather than pulling 100 rows to sort in Python."""
    results = [
        ItemResult(item_id="pass", output="billing", score=1.0),
        ItemResult(item_id="half", output="maybe", score=0.5),
        ItemResult(item_id="fail", output="nope", score=0.0),
    ]
    with db.connect() as conn:
        db.insert_run_items(conn, run.run_id, results)
        conn.commit()
        failing = db.fetch_run_items(conn, run.run_id, failing_only=True)

    assert [r["item_id"] for r in failing] == ["fail", "half"]


def test_run_items_are_deleted_with_their_run(run):
    """Cascade, so a deleted run cannot leave orphan items inflating the table on
    a free-tier database."""
    with db.connect() as conn:
        db.insert_run_items(conn, run.run_id, [ItemResult(item_id="a", output="x", score=1.0)])
        conn.commit()
        conn.execute("delete from runs where run_id = %s", (run.run_id,))
        conn.commit()
        assert db.fetch_run_items(conn, run.run_id) == []


def test_labels_round_trip_and_correct_in_place():
    dataset_id = f"label-probe-{uuid.uuid4().hex[:6]}"
    with db.connect() as conn:
        db.migrate(conn)
        try:
            db.upsert_label(conn, dataset_id, "sha256:v1", "sup-001", "pass", notes="clear")
            db.upsert_label(conn, dataset_id, "sha256:v1", "sup-002", "fail")
            conn.commit()

            assert db.fetch_labels(conn, dataset_id, "sha256:v1") == {
                "sup-001": "pass",
                "sup-002": "fail",
            }

            # Changing your mind mid-session corrects the row rather than duplicating it.
            db.upsert_label(conn, dataset_id, "sha256:v1", "sup-002", "pass")
            conn.commit()
            assert db.fetch_labels(conn, dataset_id, "sha256:v1")["sup-002"] == "pass"
        finally:
            conn.execute("delete from labels where dataset_id = %s", (dataset_id,))
            conn.commit()


def test_labels_do_not_leak_across_dataset_versions():
    """A label describes an item as a human read it. If the dataset changes, the
    old labels must not silently apply to text nobody reviewed."""
    dataset_id = f"label-probe-{uuid.uuid4().hex[:6]}"
    with db.connect() as conn:
        db.migrate(conn)
        try:
            db.upsert_label(conn, dataset_id, "sha256:v1", "sup-001", "pass")
            conn.commit()

            assert db.fetch_labels(conn, dataset_id, "sha256:v1") == {"sup-001": "pass"}
            assert db.fetch_labels(conn, dataset_id, "sha256:v2") == {}
        finally:
            conn.execute("delete from labels where dataset_id = %s", (dataset_id,))
            conn.commit()
