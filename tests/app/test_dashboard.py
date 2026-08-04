from __future__ import annotations

import uuid

import pytest

from app.dashboard import render, slice_table, sparkline
from shipgate.config import get_settings
from shipgate.store import db
from shipgate.types import RunRecord

needs_db = pytest.mark.skipif(
    not get_settings().shipgate_db_url, reason="SHIPGATE_DB_URL not set"
)


# Rendering is pure, so most of it is tested without touching the database.


def test_sparkline_needs_at_least_two_points():
    assert "not enough runs" in sparkline([])
    assert "not enough runs" in sparkline([0.6])


def test_sparkline_uses_a_fixed_zero_to_one_axis():
    """Auto-scaling would turn 0.86 to 0.87 into a cliff, which is the exact
    misreading a drift chart must not encourage."""
    flat_high = sparkline([0.86, 0.87], width=100, height=10)
    # y for 0.86 is 10 - 8.6 = 1.4, not 10 (which auto-scaling would produce).
    assert "0.0,1.4" in flat_high


def test_sparkline_clamps_out_of_range_scores():
    svg = sparkline([-0.5, 1.5], width=100, height=10)
    assert "0.0,10.0" in svg
    assert "100.0,0.0" in svg


def test_sparkline_marks_direction():
    assert 'class="good"' in sparkline([0.5, 0.9])
    assert 'class="bad"' in sparkline([0.9, 0.5])


def test_slice_table_flags_failing_slices():
    html = slice_table({"intent:billing": 1.0, "intent:account": 0.0})
    assert "intent:account" in html
    assert 'class="n bad"' in html


def test_slice_table_handles_no_slices():
    assert "no slices recorded" in slice_table({})


def test_render_escapes_dataset_ids():
    """Dataset ids come from user-supplied filenames, so they are untrusted."""
    summary = {
        "dataset_id": "<script>alert(1)</script>",
        "score": 0.5,
        "n": 10,
        "runner": "exact",
        "model": "stub",
        "slices": {},
        "trend": [],
    }
    html = render([summary])
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_render_handles_missing_optional_metrics():
    """Cost and latency are nullable, so a run without them must not crash the
    page that everyone opens first."""
    summary = {
        "dataset_id": "probe",
        "score": 0.5,
        "n": 10,
        "runner": "exact",
        "model": None,
        "slices": {},
        "trend": [],
        "cost_usd": None,
        "p50_latency_ms": None,
        "cache_hit_rate": None,
    }
    html = render([summary])
    assert "n/a" in html
    assert "probe" in html


def test_render_empty_state():
    assert "No runs recorded yet" in render([])


def test_render_surfaces_error_count():
    summary = {
        "dataset_id": "probe",
        "score": 0.5,
        "n": 10,
        "runner": "judge",
        "model": "gemini",
        "slices": {},
        "trend": [],
        "error_count": 7,
    }
    assert "errors=7" in render([summary])


@needs_db
def test_renders_with_runs(client):
    dataset_id = f"dash-probe-{uuid.uuid4().hex[:6]}"
    records = [
        RunRecord(
            run_id=f"r_dash_{uuid.uuid4().hex[:12]}",
            dataset_id=dataset_id,
            dataset_hash="sha256:test",
            runner="exact",
            model="stub-majority",
            n=100,
            score=score,
            slices={"intent:billing": 1.0, "intent:account": 0.0},
            p50_latency_ms=1.0,
            cost_usd=0.0,
            cache_hit_rate=0.0,
        )
        for score in (0.20, 0.25)
    ]
    with db.connect() as conn:
        db.migrate(conn)
        for record in records:
            db.insert_run(conn, record)
        conn.commit()

    try:
        resp = client.get("/")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/html")

        body = resp.text
        assert dataset_id in body
        assert "intent:account" in body
        assert "<svg" in body, "two runs should produce a trend line"
    finally:
        with db.connect() as conn:
            conn.execute("delete from runs where dataset_id = %s", (dataset_id,))
            conn.commit()
