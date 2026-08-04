from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from shipgate.datasets.hashing import content_hash
from shipgate.datasets.loader import load_jsonl
from shipgate.types import DatasetItem


class DatasetDiff(BaseModel):
    """What changed between two versions of a dataset.

    `changed` is the interesting one. Added and removed items shift the score for
    obvious reasons, but an edited item silently changes what a stable-looking
    score means.
    """

    before_hash: str
    after_hash: str
    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    changed: list[str] = Field(default_factory=list)
    unchanged: int = 0

    @property
    def is_identical(self) -> bool:
        return self.before_hash == self.after_hash


def diff_items(before: list[DatasetItem], after: list[DatasetItem]) -> DatasetDiff:
    before_by_id = {item.id: item for item in before}
    after_by_id = {item.id: item for item in after}

    added = sorted(after_by_id.keys() - before_by_id.keys())
    removed = sorted(before_by_id.keys() - after_by_id.keys())

    changed: list[str] = []
    unchanged = 0
    for item_id in sorted(before_by_id.keys() & after_by_id.keys()):
        if before_by_id[item_id].model_dump() == after_by_id[item_id].model_dump():
            unchanged += 1
        else:
            changed.append(item_id)

    return DatasetDiff(
        before_hash=content_hash(before),
        after_hash=content_hash(after),
        added=added,
        removed=removed,
        changed=changed,
        unchanged=unchanged,
    )


def diff_files(before: str | Path, after: str | Path) -> DatasetDiff:
    return diff_items(load_jsonl(before), load_jsonl(after))
