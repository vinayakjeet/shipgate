from __future__ import annotations

import asyncio
import time
from typing import Protocol, runtime_checkable

from shipgate.targets import Target
from shipgate.types import DatasetItem, ItemResult


@runtime_checkable
class Runner(Protocol):
    """Scores one item against a target.

    Runners implement `score_item` only. Looping, caching, and concurrency live
    in `execute_run` below, so every runner inherits them identically and a new
    runner cannot accidentally ship without backoff or cache support.
    """

    name: str

    @property
    def fingerprint(self) -> str:
        """Identifies what this runner would produce, for the cache key.

        Two runners with the same fingerprint must be interchangeable. The judge
        folds its rubric version in here, because a rubric edit changes the
        verdict for identical input and stale entries would otherwise be served
        forever.
        """
        ...

    async def score_item(self, item: DatasetItem, target: Target) -> ItemResult: ...


class ResultCache(Protocol):
    """Per-item result storage.

    Has to be shared storage rather than local disk: CI runs in a fresh
    container every time, so a local cache would never hit where it matters most.
    """

    async def get(self, key: str) -> ItemResult | None: ...

    async def put(self, key: str, result: ItemResult) -> None: ...


def cache_key(
    *, dataset_hash: str, item_id: str, runner_fingerprint: str, target_fingerprint: str
) -> str:
    """Every input that can change a result, and nothing that cannot.

    Omitting any part silently serves stale results. Including anything volatile
    (a timestamp, a run id) makes the cache never hit.
    """
    import hashlib

    parts = "|".join([dataset_hash, item_id, runner_fingerprint, target_fingerprint])
    return hashlib.sha256(parts.encode("utf-8")).hexdigest()


async def execute_run(
    runner: Runner,
    items: list[DatasetItem],
    target: Target,
    *,
    dataset_hash: str = "",
    cache: ResultCache | None = None,
    concurrency: int = 1,
) -> list[ItemResult]:
    """Score every item, reusing cached results where the inputs are unchanged.

    A failing item is recorded and the run continues. One malformed row must not
    cost the other ninety-nine, especially on a free tier where re-running is
    measured in minutes of rate-limited waiting.

    `concurrency` bounds in-flight scoring. Size it to the provider's rate limit,
    not to the machine: on a 15 rpm free tier, firing 100 requests at once just
    converts the whole run into 429s and backoff. The semaphore is deliberately
    held only around the scoring call, so cache hits never consume a slot.
    """
    target_fingerprint = getattr(target, "fingerprint", getattr(target, "name", "unknown"))
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def score_one(item: DatasetItem) -> ItemResult:
        key = cache_key(
            dataset_hash=dataset_hash,
            item_id=item.id,
            runner_fingerprint=runner.fingerprint,
            target_fingerprint=target_fingerprint,
        )

        if cache is not None:
            cached = await cache.get(key)
            if cached is not None:
                return cached.model_copy(update={"cache_hit": True})

        started = time.monotonic()
        try:
            async with semaphore:
                result = await runner.score_item(item, target)
        except Exception as exc:  # noqa: BLE001 - one bad item must not kill the run
            return ItemResult(
                item_id=item.id,
                output="",
                score=0.0,
                slices=item.slices,
                latency_ms=(time.monotonic() - started) * 1000,
                error=f"{type(exc).__name__}: {exc}",
            )

        # Errors are never cached. A rate limit or a parse failure is a property
        # of that moment, not of the input, and caching it would make one bad
        # minute permanent.
        if cache is not None and result.error is None:
            await cache.put(key, result)
        return result

    # gather preserves input order, so results still line up with items.
    return list(await asyncio.gather(*(score_one(item) for item in items)))
