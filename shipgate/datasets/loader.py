from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from shipgate.types import DatasetItem


class DatasetError(ValueError):
    """A dataset file is unreadable or a row is invalid. Always names file and line."""


def load_jsonl(path: str | Path) -> list[DatasetItem]:
    """Load a JSONL dataset. A bad row reports its file and line number, because
    hunting a malformed line in a 100-row file from a bare stack trace is miserable."""
    path = Path(path)
    if not path.exists():
        raise DatasetError(f"dataset not found: {path}")

    items: list[DatasetItem] = []
    seen: set[str] = set()

    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DatasetError(f"{path}:{lineno}: invalid JSON: {exc.msg}") from exc
        try:
            item = DatasetItem.model_validate(payload)
        except ValidationError as exc:
            raise DatasetError(
                f"{path}:{lineno}: {exc.error_count()} invalid field(s): {exc}"
            ) from exc

        if item.id in seen:
            raise DatasetError(f"{path}:{lineno}: duplicate item id {item.id!r}")
        seen.add(item.id)
        items.append(item)

    if not items:
        raise DatasetError(f"{path}: dataset is empty")
    return items
