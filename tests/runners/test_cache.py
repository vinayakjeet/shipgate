from __future__ import annotations

import uuid

import pytest

from shipgate.cache import InMemoryResultCache, PostgresResultCache
from shipgate.config import get_settings
from shipgate.runners.base import cache_key, execute_run
from shipgate.runners.exact import ExactMatchRunner
from shipgate.scoring import cache_hit_rate
from shipgate.store import db
from shipgate.targets import StubTarget
from shipgate.types import DatasetItem, ItemResult, TargetResponse

needs_db = pytest.mark.skipif(
    not get_settings().shipgate_db_url, reason="SHIPGATE_DB_URL not set"
)


@pytest.fixture(scope="module", autouse=True)
def schema():
    """The cache table has to exist before the cache can use it."""
    if not get_settings().shipgate_db_url:
        return
    with db.connect() as conn:
        db.migrate(conn)
        conn.commit()

ITEMS = [
    DatasetItem(id="a", input={"prompt": "charged twice"}, expected="billing", slices=["i:b"]),
    DatasetItem(id="b", input={"prompt": "app crashes"}, expected="technical", slices=["i:t"]),
]


class CountingTarget:
    """Counts calls so a cache hit is provable rather than inferred from timing."""

    name = "counting"

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, item: DatasetItem) -> TargetResponse:
        self.calls += 1
        return TargetResponse(output="billing")


async def test_second_run_makes_no_calls():
    """The acceptance criterion for M2.3."""
    cache = InMemoryResultCache()
    runner, target = ExactMatchRunner(), CountingTarget()

    first = await execute_run(runner, ITEMS, target, dataset_hash="sha256:x", cache=cache)
    assert target.calls == 2
    assert cache_hit_rate(first) == 0.0

    second = await execute_run(runner, ITEMS, target, dataset_hash="sha256:x", cache=cache)
    assert target.calls == 2, "cached run must not touch the target"
    assert cache_hit_rate(second) == 1.0
    assert [r.score for r in second] == [r.score for r in first]


async def test_changing_the_dataset_hash_misses_the_cache():
    cache = InMemoryResultCache()
    target = CountingTarget()

    await execute_run(ExactMatchRunner(), ITEMS, target, dataset_hash="sha256:x", cache=cache)
    await execute_run(ExactMatchRunner(), ITEMS, target, dataset_hash="sha256:y", cache=cache)

    assert target.calls == 4


async def test_a_different_runner_fingerprint_misses_the_cache():
    """A rubric or model change must not serve stale verdicts."""
    base = cache_key(
        dataset_hash="h", item_id="a", runner_fingerprint="judge:gemini:x:v1",
        target_fingerprint="t",
    )
    tuned = cache_key(
        dataset_hash="h", item_id="a", runner_fingerprint="judge:gemini:x:v2",
        target_fingerprint="t",
    )
    assert base != tuned


async def test_a_different_target_misses_the_cache():
    """The whole point of the gate is that a code change produces a new score."""
    before = cache_key(
        dataset_hash="h", item_id="a", runner_fingerprint="exact:v1", target_fingerprint="sha-1"
    )
    after = cache_key(
        dataset_hash="h", item_id="a", runner_fingerprint="exact:v1", target_fingerprint="sha-2"
    )
    assert before != after


async def test_cache_key_is_stable_for_identical_inputs():
    args = dict(
        dataset_hash="h", item_id="a", runner_fingerprint="exact:v1", target_fingerprint="t"
    )
    assert cache_key(**args) == cache_key(**args)


class FlakyRunner:
    """Fails once, then succeeds. Proves errors are not cached."""

    name = "flaky"
    fingerprint = "flaky:v1"

    def __init__(self) -> None:
        self.calls = 0

    async def score_item(self, item: DatasetItem, target) -> ItemResult:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("transient")
        return ItemResult(item_id=item.id, output="ok", score=1.0, slices=item.slices)


async def test_errors_are_not_cached():
    """A rate limit is a property of the moment, not the input. Caching it would
    make one bad minute permanent."""
    cache = InMemoryResultCache()
    runner = FlakyRunner()
    one = ITEMS[:1]

    first = await execute_run(runner, one, StubTarget(), dataset_hash="h", cache=cache)
    assert first[0].error is not None
    assert first[0].score == 0.0

    second = await execute_run(runner, one, StubTarget(), dataset_hash="h", cache=cache)
    assert second[0].error is None
    assert second[0].score == 1.0
    assert runner.calls == 2


async def test_a_failing_item_does_not_kill_the_run():
    class AlwaysFails:
        name = "boom"
        fingerprint = "boom:v1"

        async def score_item(self, item, target):
            raise RuntimeError("nope")

    results = await execute_run(AlwaysFails(), ITEMS, StubTarget(), dataset_hash="h")
    assert len(results) == 2
    assert all(r.error is not None for r in results)


@needs_db
async def test_postgres_cache_round_trip():
    key = f"test-{uuid.uuid4().hex}"
    cache = PostgresResultCache()
    result = ItemResult(
        item_id="a", output="billing", score=1.0, slices=["i:b"], meta={"reason": "correct"}
    )

    try:
        assert await cache.get(key) is None
        await cache.put(key, result)

        stored = await cache.get(key)
        assert stored is not None
        assert stored.score == 1.0
        assert stored.output == "billing"
        assert stored.meta == {"reason": "correct"}
    finally:
        with db.connect() as conn:
            conn.execute("delete from result_cache where cache_key = %s", (key,))
            conn.commit()


@needs_db
async def test_postgres_cache_survives_a_fresh_process():
    """CI starts a new container per job, so persistence across processes is the
    only property that matters here."""
    key = f"test-{uuid.uuid4().hex}"
    result = ItemResult(item_id="a", output="billing", score=1.0)

    try:
        await PostgresResultCache().put(key, result)
        assert (await PostgresResultCache().get(key)).score == 1.0
    finally:
        with db.connect() as conn:
            conn.execute("delete from result_cache where cache_key = %s", (key,))
            conn.commit()
