from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from shipgate.datasets.hashing import content_hash
from shipgate.datasets.loader import load_jsonl
from shipgate.types import DatasetItem

MANIFEST_NAME = "manifest.yaml"


class DatasetManifest(BaseModel):
    """The committed record of what a dataset version contained.

    This is what makes a score from six weeks ago interpretable. Without it you
    have a number and no way to know what was measured.
    """

    id: str
    path: str
    hash: str
    n: int
    slice_counts: dict[str, int] = Field(default_factory=dict)
    created_at: str


def build_manifest(items: list[DatasetItem], dataset_id: str, path: str | Path) -> DatasetManifest:
    counts = Counter(tag for item in items for tag in item.slices)
    return DatasetManifest(
        id=dataset_id,
        path=Path(path).as_posix(),
        hash=content_hash(items),
        n=len(items),
        slice_counts=dict(sorted(counts.items())),
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )


def manifest_path_for(dataset_path: str | Path) -> Path:
    return Path(dataset_path).parent / MANIFEST_NAME


def load_manifests(path: str | Path) -> dict[str, DatasetManifest]:
    """Read a manifest file keyed by dataset id. Missing file means no versions yet."""
    path = Path(path)
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {
        key: DatasetManifest.model_validate(value)
        for key, value in (raw.get("datasets") or {}).items()
    }


def write_manifest(path: str | Path, manifest: DatasetManifest) -> dict[str, DatasetManifest]:
    """Upsert one dataset into the manifest file, preserving the others."""
    path = Path(path)
    manifests = load_manifests(path)
    manifests[manifest.id] = manifest
    payload = {
        "datasets": {
            key: manifests[key].model_dump() for key in sorted(manifests)
        }
    }
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return manifests


def manifest_for_file(dataset_path: str | Path, dataset_id: str | None = None) -> DatasetManifest:
    dataset_path = Path(dataset_path)
    items = load_jsonl(dataset_path)
    return build_manifest(items, dataset_id or dataset_path.stem, dataset_path)
