from __future__ import annotations

import pytest

from llm import ChatClient, ChatResponse, ProviderError, RateLimitError
from shipgate.runners.base import execute_run
from shipgate.runners.judge import JudgeParseError, JudgeRunner, parse_verdict
from shipgate.runners.rubrics import RUBRIC_V1, get_rubric
from shipgate.scoring import error_count, overall_score
from shipgate.targets import StubTarget
from shipgate.types import DatasetItem

ITEM = DatasetItem(
    id="sup-001",
    input={"prompt": "I was charged twice"},
    expected="billing",
    slices=["intent:billing", "lang:en"],
)


class ScriptedProvider:
    """Stands in for Gemini. Each call pops the next scripted reply, so a test can
    say exactly what the judge sees, including malformed responses."""

    name = "scripted"

    def __init__(self, replies: list) -> None:
        self._replies = list(replies)
        self.calls = 0
        self.prompts: list[str] = []

    async def chat_completion(self, messages, **kwargs):
        self.calls += 1
        self.prompts.append(messages[-1].content)
        reply = self._replies.pop(0) if self._replies else self._replies_exhausted()
        if isinstance(reply, Exception):
            raise reply
        return ChatResponse(
            text=reply, provider=self.name, model="scripted-1", tokens_in=10, tokens_out=5
        )

    def _replies_exhausted(self):
        raise AssertionError("provider called more times than the test scripted")


@pytest.fixture
def judge(monkeypatch):
    import llm.providers.registry as registry

    def _build(*replies) -> tuple[JudgeRunner, ScriptedProvider]:
        provider = ScriptedProvider(list(replies))
        monkeypatch.setitem(registry._PROVIDERS, "scripted", provider)
        runner = JudgeRunner(client=ChatClient(max_retry_attempts=3), provider="scripted")
        return runner, provider

    return _build


# parse_verdict is pure, so test it directly rather than through the network path.


def test_verdict_parsing():
    assert parse_verdict('{"verdict": "pass", "reason": "correct intent"}') == (
        1.0,
        "correct intent",
    )
    assert parse_verdict('{"verdict": "fail", "reason": "wrong"}')[0] == 0.0


def test_verdict_parsing_strips_code_fences():
    """Judges fence their JSON no matter what the prompt says."""
    fenced = '```json\n{"verdict": "pass", "reason": "ok"}\n```'
    assert parse_verdict(fenced) == (1.0, "ok")


def test_verdict_parsing_is_case_insensitive():
    assert parse_verdict('{"verdict": "PASS", "reason": ""}')[0] == 1.0


def test_verdict_parsing_tolerates_missing_reason():
    assert parse_verdict('{"verdict": "fail"}') == (0.0, "")


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "   ",
        "the answer is probably fine",
        '{"verdict": "maybe"}',
        '{"reason": "no verdict field"}',
        '["pass"]',
        '{"verdict": null}',
    ],
)
def test_malformed_response_is_error(bad):
    with pytest.raises(JudgeParseError):
        parse_verdict(bad)


async def test_run_scores_a_pass(judge):
    runner, provider = judge('{"verdict": "pass", "reason": "billing identified"}')
    results = await execute_run(runner, [ITEM], StubTarget())

    assert len(results) == 1
    assert results[0].score == 1.0
    assert results[0].error is None
    assert results[0].meta["reason"] == "billing identified"
    assert results[0].slices == ["intent:billing", "lang:en"]
    assert provider.calls == 1


async def test_run_scores_a_fail(judge):
    runner, _ = judge('{"verdict": "fail", "reason": "predicted the wrong intent"}')
    results = await execute_run(runner, [ITEM], StubTarget())
    assert results[0].score == 0.0
    assert results[0].error is None


async def test_malformed_judge_reply_is_recorded_not_silently_zero(judge):
    """The acceptance criterion. A judge that returns junk scores 0 but carries an
    error, so a broken judge cannot be mistaken for a model regression."""
    runner, _ = judge("I think it looks good honestly")
    results = await execute_run(runner, [ITEM], StubTarget())

    assert results[0].score == 0.0
    assert results[0].error is not None
    assert "JudgeParseError" in results[0].error
    assert error_count(results) == 1


async def test_transient_provider_error_is_retried_then_succeeds(judge):
    """Retry comes from the chassis llm client, so the judge inherits backoff for
    free rather than reimplementing it."""
    runner, provider = judge(
        ProviderError("upstream hiccup"), '{"verdict": "pass", "reason": "ok"}'
    )
    results = await execute_run(runner, [ITEM], StubTarget())

    assert results[0].score == 1.0
    assert results[0].error is None
    assert provider.calls == 2


async def test_rate_limit_is_retried(judge):
    runner, provider = judge(
        RateLimitError("slow down", retry_after=0.0), '{"verdict": "pass", "reason": "ok"}'
    )
    results = await execute_run(runner, [ITEM], StubTarget())
    assert results[0].score == 1.0
    assert provider.calls == 2


async def test_exhausted_retries_are_recorded_as_an_error(judge):
    runner, _ = judge(*[ProviderError("still down")] * 3)
    results = await execute_run(runner, [ITEM], StubTarget())

    assert results[0].score == 0.0
    assert "ProviderError" in results[0].error
    assert error_count(results) == 1


async def test_one_bad_item_does_not_kill_the_run(judge):
    second = ITEM.model_copy(update={"id": "sup-002"})
    runner, _ = judge("garbage", '{"verdict": "pass", "reason": "ok"}')
    results = await execute_run(runner, [ITEM, second], StubTarget())

    assert len(results) == 2
    assert error_count(results) == 1
    assert overall_score(results) == pytest.approx(0.5)


async def test_prompt_carries_ticket_expected_and_output(judge):
    runner, provider = judge('{"verdict": "pass", "reason": "ok"}')
    await execute_run(runner, [ITEM], StubTarget())

    prompt = provider.prompts[0]
    assert "I was charged twice" in prompt
    assert "billing" in prompt


def test_rubric_version_is_exposed_for_the_cache_key():
    """The cache key and every run row record this. A rubric edit changes what a
    score means, so runs judged under different rubrics must not compare."""
    assert JudgeRunner(client=ChatClient(), provider="mock").rubric_version == "v1"
    assert RUBRIC_V1.version == "v1"


def test_unknown_rubric_version_names_the_valid_ones():
    with pytest.raises(KeyError, match="v1"):
        get_rubric("v99")
