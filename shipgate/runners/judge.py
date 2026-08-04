from __future__ import annotations

import json
import re
import time

from llm import ChatClient, ChatMessage, ProviderClientError, ProviderConfigError, ProviderError
from shipgate.runners.rubrics import RUBRIC_V1, Rubric
from shipgate.targets import Target
from shipgate.types import DatasetItem, ItemResult

# Judges wrap JSON in code fences no matter how firmly the prompt says not to.
_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.MULTILINE)


class JudgeParseError(ValueError):
    """The judge replied with something that is not a usable verdict."""


# Everything that means "this item did not get judged" rather than "it failed".
JUDGE_FAILURES = (JudgeParseError, ProviderError, ProviderClientError, ProviderConfigError)


def parse_verdict(text: str) -> tuple[float, str]:
    """Turn a judge reply into (score, reason).

    Strict on purpose. A judge that returns junk must surface as an error rather
    than quietly scoring zero, because a scoring bug and a genuine model failure
    look identical in the aggregate and only one of them is a regression.
    """
    cleaned = _FENCE.sub("", text).strip()
    if not cleaned:
        raise JudgeParseError("judge returned an empty response")

    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise JudgeParseError(f"judge response was not JSON: {text[:200]!r}") from exc

    if not isinstance(payload, dict):
        raise JudgeParseError(f"judge response was not a JSON object: {text[:200]!r}")

    verdict = str(payload.get("verdict", "")).strip().lower()
    if verdict not in {"pass", "fail"}:
        raise JudgeParseError(f"verdict must be 'pass' or 'fail', got {verdict!r}")

    return (1.0 if verdict == "pass" else 0.0), str(payload.get("reason", ""))


class JudgeRunner:
    """Scores open-ended output with an LLM judge.

    Exact match cannot tell "billing" from "this is a billing issue". The judge
    can, at the cost of being a noisy instrument that needs calibrating against
    human labels before anyone trusts it to block a merge. That calibration is
    Milestone 5 and it is the reason this project exists.
    """

    name = "judge"

    def __init__(
        self,
        client: ChatClient | None = None,
        provider: str = "gemini",
        model: str | None = None,
        rubric: Rubric = RUBRIC_V1,
    ) -> None:
        self._client = client or ChatClient()
        self._provider = provider
        self._model = model
        self.rubric = rubric

    @property
    def rubric_version(self) -> str:
        return self.rubric.version

    @property
    def fingerprint(self) -> str:
        """Model and rubric both belong here. Same input judged under a different
        rubric is a different verdict, so leaving the version out would serve
        stale results forever after a rubric edit."""
        return f"judge:{self._provider}:{self._model or 'default'}:{self.rubric.version}"

    async def _judge_one(self, item: DatasetItem, output: str) -> tuple[float, str]:
        prompt = self.rubric.render(
            ticket=str(item.input.get("prompt", "")),
            expected=item.expected or "",
            output=output,
        )
        messages = [
            ChatMessage(role="system", content=self.rubric.system),
            ChatMessage(role="user", content=prompt),
        ]
        kwargs = {"model": self._model} if self._model else {}
        response = await self._client.complete(self._provider, messages, **kwargs)
        return parse_verdict(response.text)

    async def score_item(self, item: DatasetItem, target: Target) -> ItemResult:
        started = time.monotonic()
        output = ""
        try:
            output = (await target(item)).output
            score, reason = await self._judge_one(item, output)
            error = None
        except JUDGE_FAILURES as exc:
            # Scored 0 but never silently: the error is recorded on the item and
            # surfaced in the run summary, so a broken judge cannot pass itself
            # off as a model regression.
            score, reason, error = 0.0, "", f"{type(exc).__name__}: {exc}"

        return ItemResult(
            item_id=item.id,
            output=output,
            score=score,
            slices=item.slices,
            latency_ms=(time.monotonic() - started) * 1000,
            error=error,
            meta={"reason": reason} if reason else {},
        )
