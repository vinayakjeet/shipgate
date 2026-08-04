from __future__ import annotations

from shipgate.gate.report import render_summary
from shipgate.gate.verdict import evaluate
from shipgate.types import RunRecord


def run(score: float, slices=None, **kw) -> RunRecord:
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


def baseline(score: float, slices=None) -> dict:
    return {"run_id": "r_baseline", "score": score, "slices": slices or {}}


def test_renders_markdown():
    result = evaluate(
        run(0.80, {"lang:en": 0.90, "lang:hi": 0.60}),
        baseline(0.86, {"lang:en": 0.91, "lang:hi": 0.80}),
    )
    summary = render_summary(result)

    assert summary.startswith("## ShipGate: FAILED")
    assert "| overall | 0.860 | 0.800 | -0.060 |" in summary
    assert "| lang:hi | 0.800 | 0.600 | -0.200 **regressed** |" in summary
    assert "Thresholds: overall 0.020" in summary
    assert "Baseline run: `r_baseline`" in summary


def test_names_failing_slice():
    """The difference between a gate people act on and one they mute is whether
    the summary says which slice broke."""
    result = evaluate(
        run(0.86, {"lang:en": 0.93, "lang:hi": 0.67}),
        baseline(0.86, {"lang:en": 0.85, "lang:hi": 0.87}),
    )
    summary = render_summary(result)
    assert "lang:hi" in summary
    assert "**regressed**" in summary


def test_lists_worst_failing_examples():
    result = evaluate(run(0.80), baseline(0.86))
    items = [
        {"item_id": "sup-004", "score": 0.0, "output": "billing", "error": None},
        {"item_id": "sup-009", "score": 0.0, "output": "", "error": "JudgeParseError: junk"},
        {"item_id": "sup-011", "score": 0.5, "output": "maybe technical", "error": None},
        {"item_id": "sup-020", "score": 0.5, "output": "unused", "error": None},
    ]
    summary = render_summary(result, items)

    assert "### Worst failing examples" in summary
    assert "`sup-004`" in summary
    assert "JudgeParseError" in summary
    # Only the three worst, so the summary stays readable on a 100-item dataset.
    assert "`sup-020`" not in summary


def test_passing_run_reads_as_a_pass():
    summary = render_summary(evaluate(run(0.90), baseline(0.86)))
    assert summary.startswith("## ShipGate: pass")
    assert "+0.040" in summary


def test_within_noise_is_stated_explicitly():
    summary = render_summary(evaluate(run(0.849), baseline(0.86)))
    assert "within-noise" in summary


def test_missing_baseline_summary_explains_the_fix():
    summary = render_summary(evaluate(run(0.86), None))
    assert "no baseline" in summary
    assert "default branch" in summary
    assert "| overall | n/a | 0.860 | n/a |" in summary


def test_error_count_is_caveated_not_buried():
    """Errors score 0, so a summary that reports only the score is misleading."""
    summary = render_summary(evaluate(run(0.80, error_count=5), baseline(0.86)))
    assert "5 of 100 items failed to score" in summary
    assert "floor rather than a measurement" in summary


def test_invalid_run_summary_says_nothing_was_compared():
    summary = render_summary(evaluate(run(0.40, error_count=30), baseline(0.86)))
    assert "run invalid" in summary
    assert "not a measurement" in summary


def test_output_is_truncated_so_one_item_cannot_flood_the_summary():
    result = evaluate(run(0.80), baseline(0.86))
    items = [{"item_id": "sup-001", "score": 0.0, "output": "x" * 500, "error": None}]
    summary = render_summary(result, items)
    assert len(summary) < 1500
