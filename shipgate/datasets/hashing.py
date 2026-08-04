from __future__ import annotations

import hashlib
import json

from shipgate.types import DatasetItem


def content_hash(items: list[DatasetItem]) -> str:
    """Order-invariant content hash of a dataset.

    Sorting by id first means reordering rows does not invalidate a baseline,
    while editing any field does. Milestone 1 builds the manifest on top of this.
    """
    canonical = [
        json.dumps(item.model_dump(), sort_keys=True, separators=(",", ":"))
        for item in sorted(items, key=lambda i: i.id)
    ]
    digest = hashlib.sha256("\n".join(canonical).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"
