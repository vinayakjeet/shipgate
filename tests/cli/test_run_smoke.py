from __future__ import annotations

import pytest
from typer.testing import CliRunner

from shipgate.cli import app
from shipgate.config import get_settings
from shipgate.store import db

runner = CliRunner()
needs_db = pytest.mark.skipif(
    not get_settings().shipgate_db_url, reason="SHIPGATE_DB_URL not set"
)


def test_run_scores_the_smoke_dataset():
    """The stub target always predicts the majority class, so 3 of 5 items match."""
    result = runner.invoke(app, ["run", "--dataset", "fixtures/smoke.jsonl", "--no-store"])
    assert result.exit_code == 0, result.output
    assert "score=0.60 n=5" in result.output


def test_run_reports_per_slice_scores():
    result = runner.invoke(app, ["run", "--dataset", "fixtures/smoke.jsonl", "--no-store"])
    assert "intent:billing: 1.00" in result.output
    assert "intent:technical: 0.00" in result.output


def test_run_rejects_unknown_runner():
    result = runner.invoke(
        app, ["run", "--dataset", "fixtures/smoke.jsonl", "--runner", "nope", "--no-store"]
    )
    assert result.exit_code != 0


def test_run_reports_missing_dataset_without_stack_trace(tmp_path):
    missing = tmp_path / "nope.jsonl"
    result = runner.invoke(app, ["run", "--dataset", str(missing), "--no-store"])
    assert result.exit_code == 2
    assert "dataset not found" in result.output
    assert "Traceback" not in result.output


def test_run_reports_malformed_line_with_line_number(tmp_path):
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"id": "a", "input": {}, "expected": "x"}\nnot json\n', encoding="utf-8")
    result = runner.invoke(app, ["run", "--dataset", str(bad), "--no-store"])
    assert result.exit_code == 2
    assert "bad.jsonl:2" in result.output


@needs_db
def test_run_writes_one_row():
    result = runner.invoke(app, ["run", "--dataset", "fixtures/smoke.jsonl"])
    assert result.exit_code == 0, result.output

    run_id = next(
        line.split("=", 1)[1] for line in result.output.splitlines() if line.startswith("run_id=")
    )
    try:
        with db.connect() as conn:
            stored = db.fetch_run(conn, run_id)
        assert stored is not None
        assert stored["n"] == 5
        assert stored["score"] == pytest.approx(0.60)
        assert stored["dataset_id"] == "smoke"
        assert stored["runner"] == "exact"
        assert stored["dataset_hash"].startswith("sha256:")
        assert stored["slices"]["intent:billing"] == pytest.approx(1.0)
    finally:
        with db.connect() as conn:
            conn.execute("delete from runs where run_id = %s", (run_id,))
            conn.commit()
