from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from shipgate.types import DatasetItem, TargetResponse


class Target(Protocol):
    """The thing being evaluated. Milestone 4 adds an HTTP target that calls a
    gated project's endpoint. The stub below keeps Milestone 0 offline."""

    name: str

    async def __call__(self, item: DatasetItem) -> TargetResponse: ...


class ReplayTarget:
    """Serves predictions recorded earlier instead of calling a model.

    Calibration has to be reproducible and has to compare like with like. Re-calling
    the model would spend quota, and worse, would let the thing being judged drift
    between the rubric v1 and rubric v2 runs, so a change in kappa could no longer
    be attributed to the rubric.
    """

    def __init__(self, path: str | Path, name: str | None = None) -> None:
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(f"no predictions at {self.path}")

        self._predictions: dict[str, str] = {}
        models: set[str] = set()
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("prediction"):
                self._predictions[row["item_id"]] = row["prediction"]
                models.add(row.get("model", "unknown"))

        self.name = name or f"replay:{'+'.join(sorted(models)) or 'empty'}"

    def __len__(self) -> int:
        return len(self._predictions)

    async def __call__(self, item: DatasetItem) -> TargetResponse:
        if item.id not in self._predictions:
            raise KeyError(f"no recorded prediction for {item.id!r} in {self.path}")
        return TargetResponse(output=self._predictions[item.id], meta={"replayed": True})


class StubTarget:
    """Always predicts the majority class. This is a real baseline, not a mock:
    it is the number any actual model has to beat, and it needs no network."""

    name = "stub-majority"

    def __init__(self, label: str = "billing") -> None:
        self._label = label

    async def __call__(self, item: DatasetItem) -> TargetResponse:
        return TargetResponse(output=self._label, meta={"stub": True})
