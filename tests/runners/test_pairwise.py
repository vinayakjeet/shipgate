from __future__ import annotations

import pytest

from llm import ChatClient, ChatResponse
from shipgate.runners.base import execute_run
from shipgate.runners.pairwise import PairwiseRunner, parse_winner, position_bias_rate
from shipgate.scoring import overall_score
from shipgate.types import DatasetItem, TargetResponse

ITEM = DatasetItem(
    id="sup-001",
    input={"prompt": "I was charged twice"},
    expected="billing",
    slices=["intent:billing"],
)


class FixedTarget:
    def __init__(self, name: str, output: str) -> None:
        self.name = name
        self._output = output

    async def __call__(self, item: DatasetItem) -> TargetResponse:
        return TargetResponse(output=self._output)


class PositionBiasedJudge:
    """Always picks whichever answer is in slot A.

    This is the failure mode the swap exists to catch, and it is a real one:
    LLM judges favour the first option often enough that a single-order pairwise
    eval mostly measures ordering.
    """

    name = "biased"

    def __init__(self) -> None:
        self.calls = 0

    async def chat_completion(self, messages, **kwargs):
        self.calls += 1
        return ChatResponse(
            text='{"winner": "A", "reason": "first one looks fine"}',
            provider=self.name,
            model="biased-1",
        )


class ContentJudge:
    """Picks whichever answer contains the expected intent, regardless of slot."""

    name = "content"

    def __init__(self, winning_text: str) -> None:
        self._winning = winning_text
        self.calls = 0

    async def chat_completion(self, messages, **kwargs):
        self.calls += 1
        prompt = messages[-1].content
        a_block = prompt.split("Answer A:", 1)[1].split("Answer B:", 1)[0]
        winner = "A" if self._winning in a_block else "B"
        return ChatResponse(
            text=f'{{"winner": "{winner}", "reason": "content"}}',
            provider=self.name,
            model="content-1",
        )


@pytest.fixture
def pairwise(monkeypatch):
    import llm.providers.registry as registry

    def _build(provider_impl, candidate: str, baseline: str):
        monkeypatch.setitem(registry._PROVIDERS, provider_impl.name, provider_impl)
        runner = PairwiseRunner(
            baseline=FixedTarget("baseline", baseline),
            client=ChatClient(max_retry_attempts=2),
            provider=provider_impl.name,
        )
        return runner, FixedTarget("candidate", candidate)

    return _build


def test_parse_winner():
    assert parse_winner('{"winner": "A", "reason": "clearer"}') == ("A", "clearer")
    assert parse_winner('```json\n{"winner": "b"}\n```')[0] == "B"


@pytest.mark.parametrize("bad", ["", "neither", '{"winner": "C"}', '{"reason": "x"}', "[]"])
def test_parse_winner_rejects_junk(bad):
    from shipgate.runners.judge import JudgeParseError

    with pytest.raises(JudgeParseError):
        parse_winner(bad)


async def test_position_bias_swap(pairwise):
    """The acceptance criterion for M2.6.

    A judge that always picks slot A wins the first pass as A and loses the
    swapped pass, netting 0.5. That tie is the tell: the judge never expressed a
    preference about the answers at all.
    """
    judge = PositionBiasedJudge()
    runner, candidate = pairwise(judge, candidate="billing", baseline="technical")

    results = await execute_run(runner, [ITEM], candidate)

    assert results[0].score == 0.5
    assert results[0].meta["first_pass_winner"] == "A"
    assert results[0].meta["swapped_pass_winner"] == "A"
    assert results[0].meta["position_consistent"] is False
    assert judge.calls == 2, "each pair must be judged in both orders"


async def test_a_consistent_judge_gives_a_decisive_score(pairwise):
    judge = ContentJudge(winning_text="billing")
    runner, candidate = pairwise(judge, candidate="billing", baseline="technical")

    results = await execute_run(runner, [ITEM], candidate)

    assert results[0].score == 1.0
    assert results[0].meta["position_consistent"] is True
    assert results[0].meta["first_pass_winner"] == "A"
    assert results[0].meta["swapped_pass_winner"] == "B"


async def test_a_losing_candidate_scores_zero(pairwise):
    judge = ContentJudge(winning_text="technical")
    runner, candidate = pairwise(judge, candidate="billing", baseline="technical")

    results = await execute_run(runner, [ITEM], candidate)

    assert results[0].score == 0.0
    assert results[0].meta["position_consistent"] is True


async def test_position_bias_rate_flags_an_untrustworthy_judge(pairwise):
    """The rate is a property of the judge, not of either model. A high value
    invalidates the run rather than favouring a side."""
    items = [ITEM.model_copy(update={"id": f"sup-{i:03d}"}) for i in range(10)]

    biased, _ = pairwise(PositionBiasedJudge(), candidate="billing", baseline="technical")
    biased_results = await execute_run(biased, items, FixedTarget("candidate", "billing"))
    assert position_bias_rate(biased_results) == 1.0
    assert overall_score(biased_results) == 0.5

    fair, _ = pairwise(ContentJudge("billing"), candidate="billing", baseline="technical")
    fair_results = await execute_run(fair, items, FixedTarget("candidate", "billing"))
    assert position_bias_rate(fair_results) == 0.0


async def test_fingerprint_includes_rubric_and_baseline(pairwise):
    runner, _ = pairwise(ContentJudge("billing"), candidate="billing", baseline="technical")
    fingerprint = runner.fingerprint

    assert "pairwise" in fingerprint
    assert "pw-v1" in fingerprint
    assert "vs-baseline" in fingerprint, "changing the baseline must miss the cache"
