from __future__ import annotations

import json
from pathlib import Path

from shipgate.types import DatasetItem

VALID_LABELS = {"pass", "fail"}


class LabelStore:
    """Append-only JSONL of hand labels, with the database as the durable copy.

    JSONL first, flushed per label, because a labeling session is 90 minutes of
    irreplaceable human attention. A crash at minute 80 must cost nothing, and a
    file that is written and fsynced after every single answer survives anything
    short of the disk going away.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict[str, str]:
        """Existing labels as {item_id: label}. Later entries win, so re-labeling
        an item during a session is a correction rather than a duplicate."""
        if not self.path.exists():
            return {}
        labels: dict[str, str] = {}
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            labels[row["item_id"]] = row["label"]
        return labels

    def append(self, item_id: str, label: str, dataset_hash: str, notes: str = "") -> None:
        if label not in VALID_LABELS:
            raise ValueError(f"label must be one of {sorted(VALID_LABELS)}, got {label!r}")
        row = {
            "item_id": item_id,
            "label": label,
            "dataset_hash": dataset_hash,
            "notes": notes,
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()


def remaining(items: list[DatasetItem], labeled: dict[str, str]) -> list[DatasetItem]:
    """Items still needing a label, in dataset order.

    Deliberately not shuffled. Reading 100 tickets grouped by intent means the
    labeler builds a consistent mental rule for each category, which is what makes
    the labels worth comparing a judge against.
    """
    return [item for item in items if item.id not in labeled]
