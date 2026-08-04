from __future__ import annotations

import pytest

from shipgate.gate.verdict import Verdict, evaluate
from shipgate.types import RunRecord


def run(score: float, slices: dict[str, float] | None = None, **kw) -> RunRecord:
    return RunRecord(
        run_id="r_current",
        dataset_id="support-intent",
        dataset_hash="sha256:abc",
        runner="judge",
        n=kw.pop("n", 100),
        score=score,
        slices=slices or {},
        **kw,
    )


def baseline(score: float, slices: dict[str, float] | None = None) -> dict:
    return {"run_id": "r_baseline", "score": score, "slices": slices or {}}


def test_fails_over():
    """S1: a 6 point drop against a 2 point threshold blocks."""
    result = evaluate(run(0.80), baseline(0.86))
    assert result.verdict is Verdict.FAIL
    assert result.blocks
    assert result.delta == pytest.approx(-0.06)
    assert "dropped" in result.reason


def test_passes_within_noise():
    """S3: a drop smaller than the threshold passes, and says so explicitly
    rather than silently ignoring it."""
    result = evaluate(run(0.849), baseline(0.86))
    assert result.verdict is Verdict.PASS
    assert not result.blocks
    assert "within-noise" in result.reason


def test_improvement_passes():
    result = evaluate(run(0.90), baseline(0.86))
    assert result.verdict is Verdict.PASS
    assert "improved" in result.reason


def test_exactly_at_threshold_passes():
    """The boundary is inclusive. A gate that fires exactly at the noise floor
    fires constantly."""
    result = evaluate(run(0.84), baseline(0.86), threshold_overall=0.02)
    assert result.verdict is Verdict.PASS


def test_slice_guard():
    """S7: the average holds while one slice collapses. This is the failure the
    overall number is structurally incapable of showing."""
    result = evaluate(
        run(0.86, {"lang:en": 0.93, "lang:hi": 0.67}),
        baseline(0.86, {"lang:en": 0.85, "lang:hi": 0.87}),
    )
    assert result.verdict is Verdict.FAIL
    assert result.failing_slices == ["lang:hi"]
    assert "lang:hi" in result.reason
    assert result.delta == pytest.approx(0.0)


def test_slices_missing_from_baseline_are_not_compared():
    """A slice that only exists on one side means the dataset changed, which the
    hash check already handles. Comparing it would invent a delta."""
    result = evaluate(
        run(0.86, {"lang:en": 0.9, "lang:new": 0.0}),
        baseline(0.86, {"lang:en": 0.9}),
    )
    assert result.verdict is Verdict.PASS
    assert [d.tag for d in result.slice_deltas] == ["lang:en"]


def test_missing_baseline_reports_invalid_and_does_not_block():
    """S4. Blocking on a missing baseline would make a fresh dataset unmergeable
    and teach people to bypass the gate."""
    result = evaluate(run(0.86), None)
    assert result.verdict is Verdict.BASELINE_INVALID
    assert not result.blocks
    assert result.delta is None
    assert "default branch" in result.reason


def test_too_many_errors_invalidates_the_run_without_blocking():
    """S5. A tenth of items unscored means the number is not a measurement.
    Blocking here would fail a good change for an infrastructure reason."""
    result = evaluate(run(0.40, error_count=30), baseline(0.86))
    assert result.verdict is Verdict.RUN_INVALID
    assert not result.blocks
    assert "not a measurement" in result.reason


def test_a_few_errors_still_gates_normally():
    """Below the tolerance the run is still usable, and the error count travels
    with the result so the summary can caveat it."""
    result = evaluate(run(0.80, error_count=5), baseline(0.86))
    assert result.verdict is Verdict.FAIL
    assert result.error_count == 5


def test_run_validity_is_checked_before_the_comparison():
    """Order matters: a broken run with no baseline is a broken run, not a
    missing baseline. Reporting the wrong one sends you debugging the wrong thing."""
    result = evaluate(run(0.10, error_count=50), None)
    assert result.verdict is Verdict.RUN_INVALID


def test_thresholds_are_reported_for_the_summary():
    result = evaluate(run(0.86), baseline(0.86), threshold_overall=0.03, threshold_slice=0.07)
    assert result.threshold_overall == 0.03
    assert result.threshold_slice == 0.07
