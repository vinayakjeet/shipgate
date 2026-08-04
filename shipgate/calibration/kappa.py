from __future__ import annotations

from collections import Counter

from pydantic import BaseModel, Field


class Agreement(BaseModel):
    """Judge to human agreement on a shared set of items."""

    n: int
    observed: float
    expected: float
    kappa: float
    confusion: dict[str, int] = Field(default_factory=dict)
    labels: list[str] = Field(default_factory=list)

    @property
    def interpretation(self) -> str:
        """Landis and Koch bands, which is the convention kappa is read against."""
        k = self.kappa
        if k < 0.0:
            return "worse than chance"
        if k < 0.20:
            return "slight"
        if k < 0.40:
            return "fair"
        if k < 0.60:
            return "moderate"
        if k < 0.80:
            return "substantial"
        return "almost perfect"


def cohens_kappa(human: dict[str, str], judge: dict[str, str]) -> Agreement:
    """Cohen's kappa between two labelers over the items they both labeled.

    Raw agreement is the number people quote and the number that misleads. On a
    task where 90% of items are "pass", a judge that blindly answers "pass" scores
    90% agreement while contributing nothing. Kappa subtracts the agreement you
    would expect from chance given each labeler's own distribution, so that judge
    scores 0.

    Returns kappa in [-1, 1]: 1 is perfect, 0 is chance, negative is systematic
    disagreement.
    """
    shared = sorted(set(human) & set(judge))
    n = len(shared)
    if n == 0:
        raise ValueError("no items were labeled by both the human and the judge")

    labels = sorted({human[i] for i in shared} | {judge[i] for i in shared})

    agreed = sum(1 for i in shared if human[i] == judge[i])
    observed = agreed / n

    human_counts = Counter(human[i] for i in shared)
    judge_counts = Counter(judge[i] for i in shared)
    expected = sum((human_counts[c] / n) * (judge_counts[c] / n) for c in labels)

    # When expected is 1.0 both labelers used a single label for everything.
    # Chance agreement is already total, so kappa is undefined rather than
    # perfect, and reporting 1.0 would be the most flattering possible lie.
    kappa = 0.0 if expected == 1.0 else (observed - expected) / (1 - expected)

    confusion = Counter(f"{human[i]}->{judge[i]}" for i in shared)

    return Agreement(
        n=n,
        observed=observed,
        expected=expected,
        kappa=kappa,
        confusion=dict(sorted(confusion.items())),
        labels=labels,
    )


def disagreements(human: dict[str, str], judge: dict[str, str]) -> list[tuple[str, str, str]]:
    """Items the two labelers scored differently, as (item_id, human, judge).

    This is the list you read to write rubric v2. Kappa tells you the judge is
    wrong; only the disagreements tell you how.
    """
    return [
        (item_id, human[item_id], judge[item_id])
        for item_id in sorted(set(human) & set(judge))
        if human[item_id] != judge[item_id]
    ]
