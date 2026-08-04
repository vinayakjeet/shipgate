from __future__ import annotations

import math
from enum import StrEnum

from pydantic import BaseModel, Field

from shipgate.types import RunRecord


def regressed_past(delta: float, threshold: float) -> bool:
    """Whether `delta` breaches `threshold`, treating the boundary as passing.

    The tolerance is not pedantry. `0.84 - 0.86` evaluates to -0.020000000000000018
    in binary floating point, so a naive comparison fails a run that landed exactly
    on the threshold. A gate whose verdict depends on float representation is a
    gate nobody can reason about, and "exactly at the noise floor" has to pass or
    the check fires constantly.
    """
    if math.isclose(delta, -threshold, abs_tol=1e-9):
        return False
    return delta < -threshold


class Verdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    BASELINE_INVALID = "baseline-invalid"
    RUN_INVALID = "run-invalid"


class SliceDelta(BaseModel):
    tag: str
    baseline: float
    current: float

    @property
    def delta(self) -> float:
        return self.current - self.baseline


class GateResult(BaseModel):
    verdict: Verdict
    reason: str
    score: float
    baseline_score: float | None = None
    baseline_run_id: str | None = None
    error_count: int = 0
    n: int = 0
    slice_deltas: list[SliceDelta] = Field(default_factory=list)
    failing_slices: list[str] = Field(default_factory=list)
    threshold_overall: float = 0.02
    threshold_slice: float = 0.05

    @property
    def delta(self) -> float | None:
        if self.baseline_score is None:
            return None
        return self.score - self.baseline_score

    @property
    def blocks(self) -> bool:
        """Whether this verdict should stop the change.

        Only a genuine quality regression blocks. A missing baseline or a run
        full of provider errors is an infrastructure problem, and blocking on
        those trains people to ignore the gate, which costs more than the one
        regression it might have caught. Those exit zero and complain loudly
        instead.
        """
        return self.verdict is Verdict.FAIL


def evaluate(
    current: RunRecord,
    baseline: dict | None,
    *,
    threshold_overall: float = 0.02,
    threshold_slice: float = 0.05,
    max_error_rate: float = 0.10,
) -> GateResult:
    """Compare a run against its baseline and decide whether it ships.

    Order matters. Run validity is checked before the comparison, because a score
    computed from a run where a tenth of the items never got judged is not a
    measurement of anything, and comparing it to a baseline would produce a
    confident number built on nothing.
    """
    base = GateResult(
        verdict=Verdict.PASS,
        reason="",
        score=current.score,
        error_count=current.error_count,
        n=current.n,
        threshold_overall=threshold_overall,
        threshold_slice=threshold_slice,
    )

    error_rate = current.error_count / current.n if current.n else 0.0
    if error_rate > max_error_rate:
        return base.model_copy(
            update={
                "verdict": Verdict.RUN_INVALID,
                "reason": (
                    f"{current.error_count} of {current.n} items failed to score "
                    f"({error_rate:.0%} > {max_error_rate:.0%} allowed). "
                    "The score is not a measurement, so nothing is compared."
                ),
            }
        )

    if baseline is None:
        return base.model_copy(
            update={
                "verdict": Verdict.BASELINE_INVALID,
                "reason": (
                    f"no clean baseline run found for dataset {current.dataset_id!r} "
                    f"at hash {current.dataset_hash}. Run the eval on the default "
                    "branch to establish one."
                ),
            }
        )

    baseline_score = baseline["score"]
    baseline_slices = baseline.get("slices") or {}
    delta = current.score - baseline_score

    # Only slices present in both runs are comparable. A slice that appears or
    # disappears means the dataset changed, which the hash check already caught.
    deltas = [
        SliceDelta(tag=tag, baseline=baseline_slices[tag], current=value)
        for tag, value in sorted(current.slices.items())
        if tag in baseline_slices
    ]
    failing = [d.tag for d in deltas if regressed_past(d.delta, threshold_slice)]

    result = base.model_copy(
        update={
            "baseline_score": baseline_score,
            "baseline_run_id": baseline.get("run_id"),
            "slice_deltas": deltas,
            "failing_slices": failing,
        }
    )

    if regressed_past(delta, threshold_overall):
        return result.model_copy(
            update={
                "verdict": Verdict.FAIL,
                "reason": (
                    f"overall score dropped {abs(delta):.3f} "
                    f"(threshold {threshold_overall:.3f})"
                ),
            }
        )

    if failing:
        # The case the overall average hides: a model can hold its headline score
        # while one slice collapses, which is usually the one that matters.
        worst = min(deltas, key=lambda d: d.delta)
        return result.model_copy(
            update={
                "verdict": Verdict.FAIL,
                "reason": (
                    f"{len(failing)} slice(s) regressed past {threshold_slice:.3f}, "
                    f"worst is {worst.tag} at {worst.delta:+.3f}"
                ),
            }
        )

    if delta < 0:
        reason = f"within-noise ({delta:+.3f}, threshold {threshold_overall:.3f})"
    else:
        reason = f"score held or improved ({delta:+.3f})"
    return result.model_copy(update={"reason": reason})
