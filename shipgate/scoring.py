from __future__ import annotations

from dataclasses import dataclass
from statistics import median

from shipgate.types import ItemResult


def overall_score(results: list[ItemResult]) -> float:
    """Mean item score. Errored items count as 0, because a run that half crashed
    is not a run that half passed."""
    if not results:
        return 0.0
    return sum(r.score for r in results) / len(results)


def slice_scores(results: list[ItemResult]) -> dict[str, float]:
    """Mean score per slice tag. An item in three slices counts in all three.

    This is the number that catches a regression the overall average hides: a
    model can hold 0.86 overall while one language slice collapses.
    """
    buckets: dict[str, list[float]] = {}
    for result in results:
        for tag in result.slices:
            buckets.setdefault(tag, []).append(result.score)
    return {tag: sum(scores) / len(scores) for tag, scores in sorted(buckets.items())}


def p50_latency_ms(results: list[ItemResult]) -> float | None:
    latencies = [r.latency_ms for r in results if r.latency_ms is not None]
    return median(latencies) if latencies else None


def cache_hit_rate(results: list[ItemResult]) -> float:
    if not results:
        return 0.0
    return sum(1 for r in results if r.cache_hit) / len(results)


def error_count(results: list[ItemResult]) -> int:
    """Items that failed rather than scored.

    These count as 0 in the overall score, so without surfacing the count a
    broken judge and a genuine model regression produce the same number. Any
    gate reading the score has to see this alongside it.
    """
    return sum(1 for r in results if r.error)


@dataclass(frozen=True)
class JudgmentCost:
    """What a run of judgments cost, and what a thousand of them would.

    Per-1000 rather than per-run because run sizes differ and the brief asks the
    comparable question: what does this judge configuration cost to operate. A
    total is a fact about one run; a rate is a fact about the configuration.
    """

    judgments: int
    priced: int
    tokens_in: int
    tokens_out: int
    total_usd: float | None
    per_1k_usd: float | None
    model: str

    def describe(self) -> str:
        if self.per_1k_usd is None:
            return (
                f"{self.judgments} judgments on {self.model or 'unknown model'}, "
                f"{self.tokens_in + self.tokens_out} tokens, no published price for this "
                "provider so cost is unpriced rather than zero"
            )
        return (
            f"{self.judgments} judgments on {self.model or 'unknown model'}, "
            f"${self.total_usd:.4f} total, ${self.per_1k_usd:.4f} per 1,000"
        )


def judgment_cost(results: list[ItemResult]) -> JudgmentCost:
    """Aggregate what the judge consumed, from what each item recorded.

    Items with no usage are excluded from `priced` rather than counted as free.
    A judge that errored produced no judgment, and averaging its absent cost in
    as zero makes a broken configuration look like an efficient one.
    """
    tokens_in = tokens_out = priced = 0
    total: float | None = None
    model = ""
    for result in results:
        meta = result.meta
        tokens_in += meta.get("judge_tokens_in") or 0
        tokens_out += meta.get("judge_tokens_out") or 0
        model = model or (meta.get("judge_model") or "")
        cost = meta.get("judge_cost_usd")
        if cost is not None:
            total = (total or 0.0) + cost
            priced += 1

    judgments = sum(1 for r in results if r.meta.get("judge_model"))
    per_1k = (total / priced * 1000) if total is not None and priced else None
    return JudgmentCost(
        judgments=judgments,
        priced=priced,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        total_usd=total,
        per_1k_usd=per_1k,
        model=model,
    )
