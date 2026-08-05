"""Measure judge-to-human agreement for a rubric version.

    uv run python scripts/calibrate.py --rubric v1

Runs the judge over recorded predictions, compares its verdicts against the hand
labels, and prints kappa plus every disagreement. The disagreements are the point:
kappa says the judge is wrong, only the disagreements say how, and that is what a
rubric rewrite has to be based on.

Judge verdicts are cached per rubric version, so re-running to inspect the
disagreements costs nothing and the numbers cannot drift between inspections.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The dataset is deliberately code-mixed and contains Devanagari, and the Windows
# console defaults to cp1252, which cannot encode it. Printing a Hindi ticket
# would crash the run after the judging is already paid for.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import shipgate  # noqa: E402,F401  loads .env
from shipgate.calibration.kappa import cohens_kappa, disagreements  # noqa: E402
from shipgate.calibration.labeling import LabelStore  # noqa: E402
from shipgate.datasets.hashing import content_hash  # noqa: E402
from shipgate.datasets.loader import load_jsonl  # noqa: E402
from shipgate.runners.base import execute_run  # noqa: E402
from shipgate.runners.judge import JudgeRunner  # noqa: E402
from shipgate.runners.rubrics import get_rubric  # noqa: E402
from shipgate.targets import ReplayTarget  # noqa: E402


async def judge_verdicts(
    items, target, rubric_version: str, provider: str, cache_path: Path, rpm: float
) -> dict[str, str]:
    """Judge every item, reusing anything already recorded for this rubric."""
    cached: dict[str, str] = {}
    if cache_path.exists():
        for line in cache_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                if row.get("rubric") == rubric_version and row.get("verdict"):
                    cached[row["item_id"]] = row["verdict"]

    todo = [i for i in items if i.id not in cached]
    print(f"rubric {rubric_version}: {len(cached)} cached, {len(todo)} to judge", flush=True)

    if todo:
        runner = JudgeRunner(provider=provider, rubric=get_rubric(rubric_version))
        # Paced under the provider limit, so the run never trips a 429 and never
        # pays the backoff that follows one.
        gap = 60.0 / rpm if rpm > 0 else 0.0
        for n, item in enumerate(todo, start=1):
            results = await execute_run(runner, [item], target)
            r = results[0]
            verdict = "" if r.error else ("pass" if r.score == 1.0 else "fail")
            with cache_path.open("a", encoding="utf-8") as fh:
                fh.write(
                    json.dumps(
                        {
                            "item_id": item.id,
                            "rubric": rubric_version,
                            "verdict": verdict,
                            "error": r.error or "",
                            "reason": r.meta.get("reason", ""),
                        }
                    )
                    + "\n"
                )
            if verdict:
                cached[item.id] = verdict
            print(f"  [{n}/{len(todo)}] {item.id} -> {verdict or r.error[:50]}", flush=True)
            if gap and n < len(todo):
                await asyncio.sleep(gap)

    return cached


async def main(args) -> None:
    items = load_jsonl(args.dataset)
    dataset_hash = content_hash(items)

    human = LabelStore(args.labels).load()
    if not human:
        raise SystemExit(f"no labels in {args.labels}. Run `shipgate label` first.")

    target = ReplayTarget(args.predictions)
    judge = await judge_verdicts(
        items, target, args.rubric, args.provider, Path(args.cache), args.rpm
    )

    result = cohens_kappa(human, judge)
    diffs = disagreements(human, judge)

    print()
    print("=" * 78)
    print(f"rubric        : {args.rubric}")
    print(f"dataset       : {dataset_hash[:24]}...  n={len(items)}")
    print(f"target        : {target.name}")
    print(f"compared      : {result.n} items labeled by both")
    print(f"raw agreement : {result.observed:.1%}")
    print(f"chance        : {result.expected:.1%}")
    print(f"kappa         : {result.kappa:.3f}  ({result.interpretation})")
    print("=" * 78)
    print()
    print("confusion (human -> judge):")
    for k, v in sorted(result.confusion.items()):
        marker = "" if k.split("->")[0] == k.split("->")[1] else "   <- disagreement"
        print(f"  {k:<18} {v:>3}{marker}")

    if diffs:
        print()
        print(f"{len(diffs)} disagreements. These are what rubric v2 has to address:")
        by_id = {i.id: i for i in items}
        preds = {}
        for line in Path(args.predictions).read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                preds[row["item_id"]] = row["prediction"]
        for item_id, h, j in diffs:
            item = by_id[item_id]
            print(f"\n  {item_id}  human={h}  judge={j}")
            print(f"    ticket   : {item.input['prompt'][:74]}")
            print(f"    expected : {item.expected}   predicted: {preds.get(item_id)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="datasets/support-intent.jsonl")
    parser.add_argument("--labels", default="datasets/labels.jsonl")
    parser.add_argument("--predictions", default="datasets/predictions-groq.jsonl")
    parser.add_argument("--rubric", default="v1")
    parser.add_argument("--provider", default="gemini")
    parser.add_argument("--cache", default="datasets/judge-verdicts.jsonl")
    parser.add_argument("--rpm", type=float, default=12.0)
    asyncio.run(main(parser.parse_args()))
