from __future__ import annotations

import time

from llm import ChatClient, ChatMessage
from shipgate.runners.judge import JUDGE_FAILURES, JudgeParseError, parse_json_object
from shipgate.runners.rubrics import PAIRWISE_RUBRIC_V1, PairwiseRubric
from shipgate.targets import Target
from shipgate.types import DatasetItem, ItemResult


def parse_winner(text: str) -> tuple[str, str]:
    """Return (winner, reason) where winner is 'A' or 'B'."""
    payload = parse_json_object(text)
    winner = str(payload.get("winner", "")).strip().upper()
    if winner not in {"A", "B"}:
        raise JudgeParseError(f"winner must be 'A' or 'B', got {winner!r}")

    return winner, str(payload.get("reason", ""))


class PairwiseRunner:
    """Prefers one candidate over another instead of scoring in isolation.

    Absolute grading drifts: a judge's idea of "good" moves between runs and
    between rubric edits. Asking which of two answers is better is a steadier
    question, which is why preference comparison is how most model evaluation is
    actually done.

    The catch is position bias. Judges favour whichever answer appears first,
    often strongly. So every pair is judged twice with the positions swapped, and
    the two verdicts are averaged. A judge with no position bias gives a
    consistent winner both ways and scores 1.0 or 0.0. A judge that just picks
    the first slot contradicts itself and scores 0.5, which is exactly the signal
    that the comparison is worthless.
    """

    name = "pairwise"

    def __init__(
        self,
        baseline: Target,
        client: ChatClient | None = None,
        provider: str = "gemini",
        model: str | None = None,
        rubric: PairwiseRubric = PAIRWISE_RUBRIC_V1,
    ) -> None:
        self._baseline = baseline
        self._client = client or ChatClient()
        self._provider = provider
        self._model = model
        self.rubric = rubric

    @property
    def fingerprint(self) -> str:
        baseline_id = getattr(self._baseline, "name", "unknown")
        return (
            f"pairwise:{self._provider}:{self._model or 'default'}:"
            f"{self.rubric.version}:vs-{baseline_id}"
        )

    async def _compare(self, item: DatasetItem, a: str, b: str) -> str:
        prompt = self.rubric.render_pair(
            ticket=str(item.input.get("prompt", "")),
            expected=item.expected or "",
            a=a,
            b=b,
        )
        messages = [
            ChatMessage(role="system", content=self.rubric.system),
            ChatMessage(role="user", content=prompt),
        ]
        kwargs = {"model": self._model} if self._model else {}
        response = await self._client.complete(self._provider, messages, **kwargs)
        return parse_winner(response.text)[0]

    async def score_item(self, item: DatasetItem, target: Target) -> ItemResult:
        started = time.monotonic()
        candidate = ""
        try:
            candidate = (await target(item)).output
            reference = (await self._baseline(item)).output

            # Same pair, both orderings. Candidate is A first, then B.
            first = await self._compare(item, candidate, reference)
            second = await self._compare(item, reference, candidate)

            wins = (1 if first == "A" else 0) + (1 if second == "B" else 0)
            score = wins / 2
            consistent = score in (0.0, 1.0)
            meta = {
                "first_pass_winner": first,
                "swapped_pass_winner": second,
                # False means the judge changed its mind when the order changed,
                # so this comparison says more about the prompt than the answers.
                "position_consistent": consistent,
            }
            error = None
        except JUDGE_FAILURES as exc:
            score, meta, error = 0.0, {}, f"{type(exc).__name__}: {exc}"

        return ItemResult(
            item_id=item.id,
            output=candidate,
            score=score,
            slices=item.slices,
            latency_ms=(time.monotonic() - started) * 1000,
            error=error,
            meta=meta,
        )


def position_bias_rate(results: list[ItemResult]) -> float:
    """Fraction of comparisons where swapping the order flipped the winner.

    This is a property of the judge, not of the models being compared. A high
    rate invalidates the run rather than favouring either side.
    """
    judged = [r for r in results if "position_consistent" in r.meta]
    if not judged:
        return 0.0
    return sum(1 for r in judged if not r.meta["position_consistent"]) / len(judged)
