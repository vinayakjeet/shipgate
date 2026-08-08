from __future__ import annotations

import asyncio
import time
from typing import Protocol, runtime_checkable

import spanlight
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from spanlight.attributes import ERROR_TYPE

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

        # One session per item, not per gate run. An item is an independent
        # scoring attempt, so a silent failure in item 42 should be detectable
        # without the other ninety-nine masking it, and the field study counts
        # sessions.
        with spanlight.session(name="shipgate.item"):
            span = trace.get_current_span()
            span.set_attribute("shipgate.item_id", item.id)
            span.set_attribute("shipgate.slices", ",".join(item.slices))

            if cache is not None:
                cached = await cache.get(key)
                if cached is not None:
                    span.set_attribute("shipgate.cache_hit", True)
                    span.set_attribute("shipgate.score", cached.score)
                    return cached.model_copy(update={"cache_hit": True})
            span.set_attribute("shipgate.cache_hit", False)

            started = time.monotonic()
            try:
                async with semaphore:
                    result = await runner.score_item(item, target)
            except Exception as exc:  # noqa: BLE001 - one bad item must not kill the run
                latency_ms = (time.monotonic() - started) * 1000
                span.set_attribute("shipgate.latency_ms", latency_ms)
                # The class, never the message. This used to interpolate the
                # exception, which put provider text and whatever it quoted back
                # into a span bound for a shared Grafana org.
                span.set_attribute(ERROR_TYPE, type(exc).__name__)
                span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                return ItemResult(
                    item_id=item.id,
                    output="",
                    score=0.0,
                    slices=item.slices,
                    latency_ms=latency_ms,
                    error=f"{type(exc).__name__}: {exc}",
                )

            span.set_attribute("shipgate.score", result.score)
            if result.latency_ms is not None:
                span.set_attribute("shipgate.latency_ms", result.latency_ms)
            if result.error:
                # A recorded error is still a failed item, so the span says so
                # even though the run continued.
                span.set_attribute(ERROR_TYPE, result.error.split(":", 1)[0])
                span.set_status(Status(StatusCode.ERROR, result.error.split(":", 1)[0]))

            # Errors are never cached. A rate limit or a parse failure is a
            # property of that moment, not of the input, and caching it would
            # make one bad minute permanent.
            if cache is not None and result.error is None:
                await cache.put(key, result)
            return result

    # A domain span, not a session. The session is the gate run, opened by the
    # CLI, and each item opens one of its own inside this.
    with spanlight.get_tracer().start_as_current_span("shipgate.run") as run_span:
        run_span.set_attribute("runner", runner.name)
        run_span.set_attribute("runner_fingerprint", runner.fingerprint)
        run_span.set_attribute("target", str(target_fingerprint))
        run_span.set_attribute("dataset_hash", dataset_hash)
        run_span.set_attribute("n", len(items))
        run_span.set_attribute("concurrency", max(1, concurrency))

        # gather preserves input order, so results still line up with items.
        results = list(await asyncio.gather(*(score_one(item) for item in items)))

        errors = sum(1 for r in results if r.error)
        run_span.set_attribute("error_count", errors)
        run_span.set_attribute("cache_hits", sum(1 for r in results if r.cache_hit))
        if results:
            run_span.set_attribute("score", sum(r.score for r in results) / len(results))
        return results
