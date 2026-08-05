from __future__ import annotations

import asyncio

import pytest

from llm import ChatClient, ChatMessage, ChatResponse, RateLimitError
from llm.throttle import InMemoryThrottle

MESSAGES = [ChatMessage(role="user", content="hi")]


class RateLimitedOnce:
    """429s with a long retry_after, then succeeds."""

    name = "scripted"

    def __init__(self, retry_after: float, failures: int = 1) -> None:
        self._retry_after = retry_after
        self._remaining = failures
        self.calls = 0

    async def chat_completion(self, messages, **kwargs):
        self.calls += 1
        if self._remaining > 0:
            self._remaining -= 1
            raise RateLimitError("rate limited", retry_after=self._retry_after)
        return ChatResponse(text="ok", provider=self.name, model="m")


@pytest.fixture
def register(monkeypatch):
    import llm.providers.registry as registry

    def _register(provider):
        monkeypatch.setitem(registry._PROVIDERS, provider.name, provider)
        return provider

    return _register


async def test_retry_waits_the_delay_the_provider_asked_for(register, monkeypatch):
    """Regression test for a real failure against Gemini.

    The provider asked for 40 seconds. The throttle recorded that, but the gate
    was only checked once before the retry loop, so retries fell back to
    exponential backoff which totals about 31 seconds across five attempts. Every
    retry was spent while still rate limited and the call failed with quota to
    spare.
    """
    slept: list[float] = []

    async def record_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("llm.client.asyncio.sleep", record_sleep)

    provider = register(RateLimitedOnce(retry_after=40.0))
    client = ChatClient(throttle=InMemoryThrottle(), max_retry_attempts=3)

    response = await client.complete("scripted", MESSAGES)

    assert response.text == "ok"
    assert provider.calls == 2
    # The second attempt waited roughly the 40 seconds requested, not a capped
    # backoff value.
    assert any(s > 35 for s in slept), f"never waited the requested delay: {slept}"


async def test_no_wait_when_the_provider_is_not_limited(register, monkeypatch):
    """The gate inside the loop must not add latency to the happy path."""
    slept: list[float] = []

    async def record_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("llm.client.asyncio.sleep", record_sleep)

    register(RateLimitedOnce(retry_after=40.0, failures=0))
    client = ChatClient(throttle=InMemoryThrottle(), max_retry_attempts=3)

    await client.complete("scripted", MESSAGES)
    assert slept == []


async def test_a_cooldown_from_an_earlier_call_still_gates_a_new_one(register, monkeypatch):
    """The throttle is shared across calls, so one call's 429 slows the next."""
    slept: list[float] = []

    async def record_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("llm.client.asyncio.sleep", record_sleep)

    throttle = InMemoryThrottle()
    await throttle.trip("scripted", retry_after=25.0)

    register(RateLimitedOnce(retry_after=1.0, failures=0))
    client = ChatClient(throttle=throttle, max_retry_attempts=2)

    await client.complete("scripted", MESSAGES)
    assert slept and slept[0] > 20


async def test_exhausted_retries_still_raise(register, monkeypatch):
    async def no_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr("llm.client.asyncio.sleep", no_sleep)
    monkeypatch.setattr(asyncio, "sleep", no_sleep)

    provider = register(RateLimitedOnce(retry_after=5.0, failures=10))
    client = ChatClient(throttle=InMemoryThrottle(), max_retry_attempts=3)

    with pytest.raises(RateLimitError):
        await client.complete("scripted", MESSAGES)
    assert provider.calls == 3
