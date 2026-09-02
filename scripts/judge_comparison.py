"""Which judge to use for which task type, rather than which judge is best.

The brief's ADDED MUST, and the reason it is worded that way: the largest
systematic study of LLM judges to date found rankings shift by up to 14 positions
across benchmarks. "GPT-5 is the best judge" is not a portable claim, it is a
per-task one, and a single aggregate kappa hides the slice where a judge is
useless.

    uv run python scripts/judge_comparison.py --rubric v3
    uv run python scripts/judge_comparison.py --rubric v3 --dry-run

Reuses whatever each configuration has already cached, so re-reading the table
costs nothing and the numbers cannot drift between readings. The first run of a
configuration pays for 100 judgments; every run after it is free.

Two things this deliberately does not do. It does not average the slices into a
winner, because the whole finding is that there is no single winner. And it does
not report a slice difference smaller than the judge noise floor as a difference,
because this repo measured that floor at 0.200 and reporting inside it would be
reporting instrument noise as a result.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The dataset is code-mixed and holds Devanagari; the Windows console defaults to
# cp1252 and would crash after the judging is already paid for.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import shipgate  # noqa: E402,F401  loads .env
from shipgate.calibration.kappa import cohens_kappa  # noqa: E402
from shipgate.calibration.labeling import LabelStore  # noqa: E402
from shipgate.datasets.loader import load_jsonl  # noqa: E402
from shipgate.runners.base import execute_run  # noqa: E402
from shipgate.runners.judge import JudgeRunner  # noqa: E402
from shipgate.runners.rubrics import get_rubric  # noqa: E402
from shipgate.targets import ReplayTarget  # noqa: E402

# The noise floor this repo measured over five identical runs of the same suite.
# A slice difference smaller than this is not a difference.
NOISE_FLOOR = 0.200

# The two configurations compared, and a note on why the second is not the model
# this repo's own numbers were measured on.
#
# Probed 2026-09-02: both Llama models QUOTAS.md documents return 404 from Groq.
# `llama-3.3-70b-versatile`, the configured default, and `llama-3.1-8b-instant`,
# the model the 0.200 noise floor was measured on, are both gone. That makes the
# published noise floor unreproducible on its own provider, which is the concrete
# form of the brief's rule that every comparison records model, version and date:
# a number whose model has vanished cannot be re-derived, only re-measured.
#
# `openai/gpt-oss-20b` answers and is already the model Copilot's eval uses, so
# the portfolio has one current-generation Groq model rather than two.
CONFIGS = {
    "gemini": {
        "provider": "gemini",
        "model": None,
        "cache": "datasets/judge-verdicts.jsonl",
        "rpm": 12.0,
    },
    "groq-oss-20b": {
        "provider": "groq",
        "model": "openai/gpt-oss-20b",
        "cache": "datasets/judge-verdicts-groq-oss20b.jsonl",
        "rpm": 25.0,
    },
}


@dataclass
class SliceResult:
    slice: str
    n: int
    kappa: float
    agreement: float
    # True when the human labels on this slice carry only one category. Kappa is
    # not merely deflated there, it is undefined: chance agreement is total, so
    # the statistic collapses to zero however well the judge did.
    degenerate: bool = False

    def cell(self) -> str:
        if self.degenerate:
            return f"n/a ({self.agreement:.0%} raw)"
        return f"{self.kappa:.3f} ({self.agreement:.0%} raw)"


def load_cached(path: Path, rubric: str) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("rubric") == rubric and row.get("verdict"):
            out[row["item_id"]] = row["verdict"]
    return out


async def judge_missing(
    items, target, rubric: str, config: dict, cached: dict[str, str]
) -> dict[str, str]:
    """Judge only what is not already cached, paced under the provider limit."""
    todo = [i for i in items if i.id not in cached]
    if not todo:
        return cached

    runner = JudgeRunner(
        provider=config["provider"], model=config["model"], rubric=get_rubric(rubric)
    )
    gap = 60.0 / config["rpm"] if config["rpm"] > 0 else 0.0
    cache_path = Path(config["cache"])

    for n, item in enumerate(todo, start=1):
        results = await execute_run(runner, [item], target)
        result = results[0]
        verdict = "" if result.error else ("pass" if result.score == 1.0 else "fail")
        with cache_path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "item_id": item.id,
                        "rubric": rubric,
                        "verdict": verdict,
                        "error": result.error or "",
                        "reason": result.meta.get("reason", ""),
                        "model": result.meta.get("judge_model", ""),
                    }
                )
                + "\n"
            )
        if verdict:
            cached[item.id] = verdict
        print(f"  [{n}/{len(todo)}] {item.id} -> {verdict or result.error[:48]}", flush=True)
        if gap and n < len(todo):
            await asyncio.sleep(gap)
    return cached


def per_slice(items, human: dict[str, str], judge: dict[str, str]) -> list[SliceResult]:
    """Kappa within each slice, not overall.

    A slice with one label present in the human column has an undefined kappa,
    because chance agreement is total. Those are reported as `n/a` rather than as
    a suspiciously perfect 1.0.
    """
    buckets: dict[str, list[str]] = defaultdict(list)
    for item in items:
        for tag in item.slices:
            buckets[tag].append(item.id)

    out: list[SliceResult] = []
    for tag, ids in sorted(buckets.items()):
        h = {i: human[i] for i in ids if i in human and i in judge}
        j = {i: judge[i] for i in ids if i in human and i in judge}
        if not h:
            continue
        result = cohens_kappa(h, j)
        out.append(
            SliceResult(
                tag,
                result.n,
                result.kappa,
                result.observed,
                degenerate=len(set(h.values())) < 2,
            )
        )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Per-task-type judge comparison.")
    parser.add_argument("--dataset", default="datasets/support-intent.jsonl")
    parser.add_argument("--labels", default="datasets/labels.jsonl")
    parser.add_argument("--predictions", default="datasets/predictions-groq.jsonl")
    parser.add_argument("--rubric", default="v3")
    parser.add_argument("--prefix", default="intent:", help="which slice family is the task type")
    parser.add_argument("--dry-run", action="store_true", help="use only cached verdicts")
    args = parser.parse_args()

    items = load_jsonl(args.dataset)
    human = LabelStore(args.labels).load()
    if not human:
        raise SystemExit(f"no labels in {args.labels}")
    target = ReplayTarget(args.predictions)

    verdicts: dict[str, dict[str, str]] = {}
    for name, config in CONFIGS.items():
        cached = load_cached(Path(config["cache"]), args.rubric)
        missing = len([i for i in items if i.id not in cached])
        print(f"{name}: {len(cached)} cached, {missing} to judge")
        if missing and not args.dry_run:
            cached = asyncio.run(judge_missing(items, target, args.rubric, config, cached))
        verdicts[name] = cached

    usable = {n: v for n, v in verdicts.items() if v}
    if len(usable) < 2:
        print(
            f"\nonly {len(usable)} configuration has verdicts, so there is nothing to compare. "
            "Run without --dry-run to judge the missing one."
        )
        return 1

    print()
    print(f"Rubric {args.rubric}, {len(items)} items, human labels as ground truth.")
    print("Kappa within each slice. Overall is shown last and is the number the")
    print("per-slice rows exist to contradict.\n")

    tables = {name: per_slice(items, human, v) for name, v in usable.items()}
    names = list(tables)
    header = " | ".join(f"{n} kappa" for n in names)
    print(f"| slice | n | {header} | winner |")
    print("|---" * (3 + len(names)) + "|")

    task_rows: list[tuple[str, str, float]] = []
    for slice_row in tables[names[0]]:
        tag = slice_row.slice
        cells = []
        scores: dict[str, float] = {}
        for name in names:
            match = next((r for r in tables[name] if r.slice == tag), None)
            if match is None:
                cells.append("n/a")
                continue
            cells.append(match.cell())
            # A degenerate slice is excluded from the comparison rather than
            # scored at zero, because zero there is an artifact of the label
            # distribution and not a statement about the judge.
            if not match.degenerate:
                scores[name] = match.kappa
        if len(scores) == len(names):
            best = max(scores, key=scores.get)
            spread = max(scores.values()) - min(scores.values())
            winner = best if spread >= NOISE_FLOOR else "tie inside noise"
        else:
            winner = "kappa undefined"
        if tag.startswith(args.prefix) and len(scores) == len(names):
            task_rows.append((tag, winner, max(scores.values()) - min(scores.values())))
        print(f"| {tag} | {slice_row.n} | {' | '.join(cells)} | {winner} |")

    overall = {}
    for name, v in usable.items():
        shared_h = {k: human[k] for k in human if k in v}
        overall[name] = cohens_kappa(shared_h, v).kappa
    best_overall = max(overall, key=overall.get)
    print(
        f"| **overall** | {len(human)} | "
        + " | ".join(f"**{overall[n]:.3f}**" for n in names)
        + f" | **{best_overall}** |"
    )

    print()
    contradictions = [t for t, w, _ in task_rows if w not in (best_overall, "tie inside noise")]
    if contradictions:
        print(
            f"{best_overall} wins overall and loses on: {', '.join(contradictions)}. "
            "That is the finding, and it is the reason this table is indexed by task type "
            "rather than collapsed into a recommendation."
        )
    else:
        print(
            f"{best_overall} wins overall and no task type contradicts it on this dataset. "
            f"A null result: on these four intents the ranking happens to be portable. It is "
            "one dataset and one rubric, so it is not evidence that rankings are portable in "
            "general, which is the claim the brief's reference measured shifting by up to 14 "
            "positions."
        )
    print(
        f"\nDifferences below the measured judge noise floor of {NOISE_FLOOR:.3f} are reported "
        "as ties rather than as wins."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
