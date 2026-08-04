from __future__ import annotations

import time

from shipgate.targets import Target
from shipgate.types import DatasetItem, ItemResult


def normalize(text: str) -> str:
    return " ".join(text.strip().casefold().split())


class ExactMatchRunner:
    """Normalized string equality.

    No model in the loop, so it is free, fast, and has zero run-to-run variance.
    Anything this can score is not worth paying a judge for, and its scores need
    no calibration to be trusted.
    """

    name = "exact"

    @property
    def fingerprint(self) -> str:
        return "exact:v1"

    async def score_item(self, item: DatasetItem, target: Target) -> ItemResult:
        if item.expected is None:
            raise ValueError(
                f"item {item.id!r} has no `expected` value, which the exact runner requires. "
                "Use the judge runner for open-ended items."
            )

        started = time.monotonic()
        response = await target(item)
        return ItemResult(
            item_id=item.id,
            output=response.output,
            score=1.0 if normalize(response.output) == normalize(item.expected) else 0.0,
            slices=item.slices,
            latency_ms=(time.monotonic() - started) * 1000,
        )
