from __future__ import annotations

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
