"""Measure run-to-run judge variance, so the gate threshold is derived rather than
guessed.

    uv run python scripts/variance.py --runs 5 --sample 25

Judges are not deterministic. Scoring the same unchanged inputs several times and
watching the score move is the only honest way to know how large a drop has to be
before it means anything. A threshold set below that noise floor fires constantly
and gets ignored, which is worse than having no gate.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import shipgate  # noqa: E402,F401  loads .env
from shipgate.datasets.hashing import content_hash  # noqa: E402
from shipgate.datasets.loader import load_jsonl  # noqa: E402
from shipgate.runners.base import execute_run  # noqa: E402
from shipgate.runners.judge import JudgeRunner  # noqa: E402
from shipgate.runners.rubrics import get_rubric  # noqa: E402
from shipgate.scoring import error_count, overall_score  # noqa: E402
from shipgate.targets import ReplayTarget  # noqa: E402


async def main(args) -> None:
    items = load_jsonl(args.dataset)[: args.sample]
    target = ReplayTarget(args.predictions)
    rubric = get_rubric(args.rubric)

    print(f"rubric {args.rubric}, model {args.model or 'default'}, "
          f"{len(items)} items, {args.runs} repeats")
    print("Identical inputs every time. Any movement is the judge, not the data.\n")

    scores: list[float] = []
    gap = 60.0 / args.rpm if args.rpm > 0 else 0.0

    for run in range(1, args.runs + 1):
        # No cache: the whole point is to re-ask the judge the same question.
        runner = JudgeRunner(provider=args.provider, model=args.model, rubric=rubric)
        results = []
        for item in items:
            results += await execute_run(runner, [item], target)
            if gap:
                await asyncio.sleep(gap)

        score = overall_score(results)
        scores.append(score)
        errors = error_count(results)
        print(f"  run {run}: score={score:.4f}{'  errors=' + str(errors) if errors else ''}")

    spread = max(scores) - min(scores)
    stdev = statistics.stdev(scores) if len(scores) > 1 else 0.0

    print()
    print("=" * 70)
    print(f"dataset hash : {content_hash(load_jsonl(args.dataset))[:24]}...")
    print(f"mean         : {statistics.mean(scores):.4f}")
    print(f"stdev        : {stdev:.4f}")
    print(f"spread       : {spread:.4f}  (max minus min)")
    print("=" * 70)
    print()

    # Two standard deviations covers roughly 95 percent of runs, so a threshold
    # there fires on real movement rather than on the judge changing its mind.
    # Never below the observed spread: a threshold inside the range the score
    # already wanders through would fire on an unchanged model.
    suggested = max(2 * stdev, spread)
    print(f"suggested threshold_overall: {suggested:.3f}")
    print(f"  2 x stdev = {2 * stdev:.4f}, observed spread = {spread:.4f}, take the larger")
    if suggested == 0.0:
        print("  The judge was perfectly stable across these runs. That is a floor of")
        print("  zero measured noise, not proof of none: a larger sample or a harder")
        print("  slice would likely find some. Keep a small nonzero threshold.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="datasets/support-intent.jsonl")
    parser.add_argument("--predictions", default="datasets/predictions-groq.jsonl")
    parser.add_argument("--rubric", default="v3")
    parser.add_argument("--provider", default="groq")
    parser.add_argument(
        "--model",
        default=None,
        help="Override the judge model. The 70B free tier cannot sustain repeats.",
    )
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--sample", type=int, default=25)
    parser.add_argument("--rpm", type=float, default=25.0)
    asyncio.run(main(parser.parse_args()))
