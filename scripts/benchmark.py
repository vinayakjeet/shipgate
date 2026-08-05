"""Measure each runner on the real dataset and print the README benchmark table.

Reproducible on purpose: the numbers in the README are worthless if nobody can
regenerate them. Run with:

    uv run python scripts/benchmark.py --judge-sample 20

The judge sample is small by default because the Gemini free tier allows roughly
20 requests per window, and a full 100-item judge pass costs about five minutes of
mostly waiting. The sample size is printed in the table rather than hidden.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

# The project is not installed as a package (`[tool.uv] package = false`), and
# pytest's pythonpath setting does not apply outside pytest, so a script run from
# the repo root needs the root on sys.path explicitly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import shipgate  # noqa: E402,F401  loads .env so provider keys are visible
from shipgate.cache import InMemoryResultCache
from shipgate.datasets.hashing import content_hash
from shipgate.datasets.loader import load_jsonl
from shipgate.runners.base import execute_run
from shipgate.runners.exact import ExactMatchRunner
from shipgate.runners.judge import JudgeRunner
from shipgate.runners.pairwise import PairwiseRunner, position_bias_rate
from shipgate.scoring import cache_hit_rate, error_count, overall_score, p50_latency_ms
from shipgate.targets import StubTarget

DATASET = "datasets/support-intent.jsonl"


async def measure(runner, items, target, dataset_hash, cache=None) -> dict:
    started = time.monotonic()
    results = await execute_run(runner, items, target, dataset_hash=dataset_hash, cache=cache)
    wall = time.monotonic() - started
    return {
        "n": len(results),
        "score": overall_score(results),
        "p50_ms": p50_latency_ms(results),
        "cache_hit": cache_hit_rate(results),
        "errors": error_count(results),
        "wall_s": wall,
        "results": results,
    }


def row(name: str, m: dict, extra: str = "") -> str:
    p50 = "n/a" if m["p50_ms"] is None else f"{m['p50_ms']:.0f}"
    return (
        f"| {name} | {m['n']} | {m['score']:.2f} | {p50} | "
        f"{m['wall_s']:.1f} | {m['cache_hit']:.0%} | {m['errors']} | {extra} |"
    )


async def main(judge_sample: int) -> None:
    items = load_jsonl(DATASET)
    dataset_hash = content_hash(items)
    target = StubTarget("billing")
    sample = items[:judge_sample]
    rows = []

    print(f"dataset: {DATASET}  n={len(items)}  hash={dataset_hash[:23]}...\n")

    print("exact, full dataset ...")
    exact = await measure(ExactMatchRunner(), items, target, dataset_hash)
    rows.append(row("exact", exact, "no model in the loop"))

    print("exact, second pass with cache ...")
    cache = InMemoryResultCache()
    await measure(ExactMatchRunner(), items, target, dataset_hash, cache=cache)
    exact_cached = await measure(ExactMatchRunner(), items, target, dataset_hash, cache=cache)
    rows.append(row("exact (cached)", exact_cached, "every item served from cache"))

    print(f"judge via gemini, {judge_sample} items, rate limited ...")
    judge = await measure(JudgeRunner(provider="gemini"), sample, target, dataset_hash)
    rows.append(row("judge", judge, f"sample of {judge_sample}"))

    print(f"pairwise via gemini, {judge_sample} items, two calls each ...")
    pairwise_runner = PairwiseRunner(baseline=StubTarget("technical"), provider="gemini")
    pairwise = await measure(pairwise_runner, sample, target, dataset_hash)
    bias = position_bias_rate(pairwise["results"])
    rows.append(row("pairwise", pairwise, f"position bias {bias:.0%}"))

    print("\n" + "=" * 100)
    print("| runner | n | score | p50 ms | wall s | cache | errors | notes |")
    print("|---|---|---|---|---|---|---|---|")
    for r in rows:
        print(r)
    print("=" * 100)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--judge-sample", type=int, default=20)
    asyncio.run(main(parser.parse_args().judge_sample))
