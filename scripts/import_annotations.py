"""Compare a second annotator's intent labels against the dataset, and print the
items that need a human decision.

Feed it the pipe-separated output of docs/annotation-prompt.txt:

    uv run python scripts/import_annotations.py annotations.txt

It never changes the dataset. It produces a shortlist, because the point is to
spend attention on the items where two annotators disagree rather than re-reading
the eighty they agree on.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shipgate.datasets.loader import load_jsonl  # noqa: E402

VALID = {"billing", "technical", "account", "other"}


def parse(path: Path) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#") or "|" not in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 3:
            print(f"  skipped {path}:{lineno}: expected at least 3 fields", file=sys.stderr)
            continue

        item_id, intent = parts[0], parts[1].lower()
        if intent not in VALID:
            print(f"  skipped {path}:{lineno}: unknown intent {intent!r}", file=sys.stderr)
            continue

        rows[item_id] = {
            "intent": intent,
            "confidence": parts[2].lower() if len(parts) > 2 else "clear",
            "competing": parts[3] if len(parts) > 3 and parts[3] != "-" else "",
            "reason": parts[4] if len(parts) > 4 else "",
        }
    return rows


def main(annotations: Path, dataset: str) -> None:
    items = {i.id: i for i in load_jsonl(dataset)}
    second = parse(annotations)

    missing = sorted(set(items) - set(second))
    extra = sorted(set(second) - set(items))
    shared = sorted(set(items) & set(second))

    disagreements = [i for i in shared if second[i]["intent"] != items[i].expected]
    borderline = [
        i for i in shared if second[i]["confidence"].startswith("border") and i not in disagreements
    ]

    print(f"dataset items      : {len(items)}")
    print(f"second annotations : {len(second)}")
    if missing:
        print(f"MISSING annotations: {len(missing)} -> {', '.join(missing[:10])}")
    if extra:
        print(f"unknown ids        : {len(extra)} -> {', '.join(extra[:10])}")
    print()
    agreed = len(shared) - len(disagreements)
    agreement = agreed / len(shared) if shared else 0.0
    print(f"raw agreement      : {agreement:.0%} ({agreed}/{len(shared)})")
    print(f"needs your decision: {len(disagreements)} disagreements + {len(borderline)} borderline")
    print()

    if disagreements:
        print("=" * 78)
        print("DISAGREEMENTS: the two annotators chose different intents.")
        print("Decide each one. If the second annotator is right, fix the dataset.")
        print("=" * 78)
        for item_id in disagreements:
            row = second[item_id]
            print(f"\n{item_id}")
            print(f"  ticket    : {items[item_id].input['prompt']}")
            print(f"  dataset   : {items[item_id].expected}")
            print(f"  annotator : {row['intent']}   {row['reason']}")

    if borderline:
        print("\n" + "=" * 78)
        print("BORDERLINE but agreed. Worth a look: these are the items where the")
        print("judge is most likely to disagree with you during calibration.")
        print("=" * 78)
        for item_id in borderline:
            row = second[item_id]
            competing = f" (vs {row['competing']})" if row["competing"] else ""
            print(f"  {item_id}  {items[item_id].expected}{competing}  {row['reason'][:60]}")

    if not disagreements and not borderline:
        print("No disagreements and nothing borderline.")
        print("Treat that as suspicious rather than reassuring: either the task is")
        print("genuinely trivial, in which case the judge is not worth calibrating,")
        print("or the annotator was not being critical enough.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("annotations", type=Path)
    parser.add_argument("--dataset", default="datasets/support-intent.jsonl")
    args = parser.parse_args()
    main(args.annotations, args.dataset)
