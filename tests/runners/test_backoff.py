from __future__ import annotations

import pytest

from llm import ChatClient, ChatResponse, RateLimitError
from shipgate.runners.base import execute_run
from shipgate.runners.judge import JudgeRunner
from shipgate.scoring import error_count, overall_score
from shipgate.targets import StubTarget
from shipgate.types import DatasetItem

ITEMS = [
    DatasetItem(
        id=f"sup-{i:03d}",
        input={"prompt": f"ticket {i}"},
        expected="billing",
        slices=["intent:billing"],
    )
    for i in range(100)
]


class RateLimitedProvider:
    """Rejects a fixed fraction of calls with 429 before answering.

    Deterministic rather than random: a flaky test that reproduces one run in ten
    is worse than no test, and the point here is the guarantee, not the odds.
    """

    name = "ratelimited"

    def __init__(self, reject_every: int = 3) -> None:
        self._reject_every = reject_every
        self.calls = 0
        self.rejections = 0

    async def chat_completion(self, messages, **kwargs):
        self.calls += 1
        if self._reject_every and self.calls % self._reject_every == 0:
            self.rejections += 1
            raise RateLimitError("free tier exhausted", retry_after=0.0)
        return ChatResponse(
            text='{"verdict": "pass", "reason": "ok"}',
            provider=self.name,
            model="rl-1",
            tokens_in=10,
            tokens_out=5,
        )


@pytest.fixture
def judge_with(monkeypatch):
    import llm.providers.registry as registry

    def _build(reject_every: int, max_attempts: int = 5):
        provider = RateLimitedProvider(reject_every=reject_every)
        monkeypatch.setitem(registry._PROVIDERS, "ratelimited", provider)
        runner = JudgeRunner(
            client=ChatClient(max_retry_attempts=max_attempts), provider="ratelimited"
        )
        return runner, provider

    return _build


async def test_no_items_dropped_under_429s(judge_with):
    """The acceptance criterion for M2.4: roughly a third of calls get 429ed and
    the run still returns a verdict for all 100 items.

    Retry and the throttle both come from the chassis llm client, so the runner
    inherits free-tier survival rather than reimplementing it.
    """
    runner, provider = judge_with(reject_every=3)

    results = await execute_run(runner, ITEMS, StubTarget())

    assert len(results) == 100, "every item must produce a result"
    assert error_count(results) == 0, "429s must be retried, not recorded as failures"
    assert overall_score(results) == pytest.approx(1.0)
    assert provider.rejections > 25, "the test did not actually exercise rate limiting"


async def test_results_stay_aligned_with_items_under_concurrency(judge_with):
    """Concurrency must not reorder results. A score attributed to the wrong item
    would corrupt every per-slice number silently."""
    runner, _ = judge_with(reject_every=4)

    results = await execute_run(runner, ITEMS, StubTarget(), concurrency=8)

    assert [r.item_id for r in results] == [item.id for item in ITEMS]


async def test_a_provider_that_never_recovers_is_recorded_not_hung(judge_with):
    """Exhausted retries must surface as errors. A gate that hangs forever on a
    dead provider is worse than one that fails."""
    runner, _ = judge_with(reject_every=1, max_attempts=2)

    results = await execute_run(runner, ITEMS[:5], StubTarget())

    assert len(results) == 5
    assert error_count(results) == 5
    assert all("RateLimitError" in r.error for r in results)


async def test_retry_count_is_bounded(judge_with):
    """Backoff must give up eventually. Unbounded retry on a free tier burns the
    next window's quota too."""
    runner, provider = judge_with(reject_every=1, max_attempts=3)

    await execute_run(runner, ITEMS[:1], StubTarget())

    assert provider.calls == 3, "one item, three attempts, then stop"
