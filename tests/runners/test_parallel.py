from __future__ import annotations

import asyncio

import pytest

from shipgate.runners.base import execute_run
from shipgate.targets import StubTarget
from shipgate.types import DatasetItem, ItemResult

ITEMS = [
    DatasetItem(id=f"i-{i:03d}", input={"prompt": f"t{i}"}, expected="billing", slices=["s:a"])
    for i in range(30)
]


class ConcurrencyTrackingRunner:
    """Records how many scorings overlap, so the bound is measured rather than
    assumed from wall-clock timing, which is flaky on shared CI runners."""

    name = "tracking"
    fingerprint = "tracking:v1"

    def __init__(self, delay: float = 0.01) -> None:
        self._delay = delay
        self.in_flight = 0
        self.peak_in_flight = 0
        self.completed = 0

    async def score_item(self, item: DatasetItem, target) -> ItemResult:
        self.in_flight += 1
        self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self._delay)
            self.completed += 1
            return ItemResult(item_id=item.id, output="billing", score=1.0, slices=item.slices)
        finally:
            self.in_flight -= 1


# Captured at import, before the conftest fixture stubs it out.
_REAL_SLEEP = asyncio.sleep


@pytest.fixture(autouse=True)
def real_sleep(fast_sleep, monkeypatch):
    """This module needs asyncio.sleep to actually yield, unlike the rest of the
    runner suite where it is stubbed out to keep backoff fast.

    Depends on `fast_sleep` explicitly so it is guaranteed to run afterwards and
    win, rather than relying on fixture ordering that could silently flip.
    """
    monkeypatch.setattr(asyncio, "sleep", _REAL_SLEEP)


async def test_concurrency_bound_respected():
    """The acceptance criterion for M2.5. Sized to the provider's rate limit, not
    the machine: firing 100 requests at a 15 rpm free tier just produces 429s."""
    runner = ConcurrencyTrackingRunner()

    results = await execute_run(runner, ITEMS, StubTarget(), concurrency=5)

    assert len(results) == 30
    assert runner.peak_in_flight <= 5, f"bound exceeded: {runner.peak_in_flight}"
    assert runner.peak_in_flight > 1, "nothing ran in parallel, the bound is not being used"


async def test_default_is_sequential():
    """Concurrency is opt-in. A caller that has not thought about the provider's
    rate limit gets the safe behaviour."""
    runner = ConcurrencyTrackingRunner()

    await execute_run(runner, ITEMS[:10], StubTarget())

    assert runner.peak_in_flight == 1


async def test_concurrency_is_faster_than_sequential():
    sequential = ConcurrencyTrackingRunner(delay=0.02)
    parallel = ConcurrencyTrackingRunner(delay=0.02)

    loop = asyncio.get_running_loop()

    start = loop.time()
    await execute_run(sequential, ITEMS[:20], StubTarget(), concurrency=1)
    sequential_elapsed = loop.time() - start

    start = loop.time()
    await execute_run(parallel, ITEMS[:20], StubTarget(), concurrency=10)
    parallel_elapsed = loop.time() - start

    assert parallel_elapsed < sequential_elapsed / 2


async def test_order_is_preserved_regardless_of_completion_order():
    """gather keeps input order. Without this a fast item could land in a slow
    item's slot and every per-slice score would be quietly wrong."""

    class VariableDelayRunner:
        name = "variable"
        fingerprint = "variable:v1"

        async def score_item(self, item: DatasetItem, target) -> ItemResult:
            # Later items finish first.
            index = int(item.id.split("-")[1])
            await asyncio.sleep((30 - index) * 0.001)
            return ItemResult(item_id=item.id, output="x", score=1.0, slices=item.slices)

    results = await execute_run(VariableDelayRunner(), ITEMS, StubTarget(), concurrency=10)

    assert [r.item_id for r in results] == [item.id for item in ITEMS]


async def test_concurrency_of_zero_is_treated_as_one():
    """A misconfigured value must not deadlock the run."""
    runner = ConcurrencyTrackingRunner()

    results = await execute_run(runner, ITEMS[:5], StubTarget(), concurrency=0)

    assert len(results) == 5
    assert runner.peak_in_flight == 1
