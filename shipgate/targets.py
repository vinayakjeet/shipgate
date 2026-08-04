from __future__ import annotations

from typing import Protocol

from shipgate.types import DatasetItem, TargetResponse


class Target(Protocol):
    """The thing being evaluated. Milestone 4 adds an HTTP target that calls a
    gated project's endpoint. The stub below keeps Milestone 0 offline."""

    name: str

    async def __call__(self, item: DatasetItem) -> TargetResponse: ...


class StubTarget:
    """Always predicts the majority class. This is a real baseline, not a mock:
    it is the number any actual model has to beat, and it needs no network."""

    name = "stub-majority"

    def __init__(self, label: str = "billing") -> None:
        self._label = label

    async def __call__(self, item: DatasetItem) -> TargetResponse:
        return TargetResponse(output=self._label, meta={"stub": True})
