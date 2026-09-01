"""What a judge configuration costs to operate, per 1,000 judgments.

The brief asks for cost per 1,000 judgments per configuration, and the run record
carried a `cost_usd` field that was hardcoded to 0.0 at both call sites. The
numbers were already being computed: the provider prices every response and the
judge recorded them onto a span before discarding them.

The tests worth having here are about the two ways a cost column lies. Reporting
an unpriced free tier as $0.00 makes it indistinguishable from a paid judge that
happened to cost nothing, and averaging failed judgments in as free makes a broken
configuration look efficient.
"""

from __future__ import annotations

from shipgate.scoring import judgment_cost
from shipgate.types import ItemResult


def judged(
    item_id: str = "i1",
    *,
    tokens_in: int = 100,
    tokens_out: int = 20,
    cost: float | None = 0.001,
    model: str = "gemini-flash",
) -> ItemResult:
    return ItemResult(
        item_id=item_id,
        output="out",
        score=1.0,
        meta={
            "judge_tokens_in": tokens_in,
            "judge_tokens_out": tokens_out,
            "judge_cost_usd": cost,
            "judge_model": model,
        },
    )


def failed(item_id: str = "bad") -> ItemResult:
    """A judgment that never happened, as `score_item` records it."""
    return ItemResult(
        item_id=item_id,
        output="",
        score=0.0,
        error="ProviderError: boom",
        meta={
            "judge_tokens_in": None,
            "judge_tokens_out": None,
            "judge_cost_usd": None,
            "judge_model": "",
        },
    )


def test_it_reports_a_rate_not_just_a_total() -> None:
    """A total is a fact about one run. A rate is a fact about the configuration,
    which is what the brief asks to compare."""
    cost = judgment_cost([judged(f"i{i}", cost=0.002) for i in range(50)])

    assert cost.judgments == 50
    assert cost.total_usd == pytest_approx(0.1)
    assert cost.per_1k_usd == pytest_approx(2.0)


def test_an_unpriced_provider_is_unpriced_rather_than_free() -> None:
    """The distinction the field existed to lose.

    A free tier costs nothing to run and that is worth reporting. It is not the
    same claim as a paid judge that happened to cost nothing, and a column that
    cannot tell them apart makes the cheapest configuration look identical to the
    one nobody priced.
    """
    cost = judgment_cost([judged(f"i{i}", cost=None) for i in range(10)])

    assert cost.per_1k_usd is None
    assert cost.total_usd is None
    assert cost.tokens_in == 1000, "tokens are still counted without a price"
    assert "unpriced rather than zero" in cost.describe()


def test_failed_judgments_are_not_averaged_in_as_free() -> None:
    """Otherwise a configuration that fails half its items looks half as expensive."""
    results = [judged(f"ok{i}", cost=0.002) for i in range(10)]
    results += [failed(f"bad{i}") for i in range(10)]

    cost = judgment_cost(results)

    assert cost.judgments == 10, "only judgments that happened count"
    assert cost.priced == 10
    assert cost.per_1k_usd == pytest_approx(2.0), "the rate is per judgment made"


def test_a_run_with_no_judge_reports_nothing_rather_than_zero() -> None:
    """The exact-match runner records no judge usage, and its cost is not $0.00,
    it is not applicable."""
    plain = [ItemResult(item_id="i1", output="x", score=1.0)]

    cost = judgment_cost(plain)

    assert cost.judgments == 0
    assert cost.per_1k_usd is None
    assert cost.total_usd is None


def test_it_names_the_model_that_produced_the_verdicts() -> None:
    """Rule 2 of the brief: every comparison records the model. A cost per 1,000
    without a model name is not comparable to anything."""
    cost = judgment_cost([judged(model="gemini-2.5-flash")])
    assert cost.model == "gemini-2.5-flash"
    assert "gemini-2.5-flash" in cost.describe()


def test_tokens_are_summed_across_both_directions() -> None:
    cost = judgment_cost(
        [judged("a", tokens_in=100, tokens_out=20), judged("b", tokens_in=300, tokens_out=80)]
    )
    assert cost.tokens_in == 400
    assert cost.tokens_out == 100


def pytest_approx(value: float, tol: float = 1e-9):
    """Local, so this file needs no import just to compare two floats."""

    class _Approx:
        def __eq__(self, other: object) -> bool:
            return isinstance(other, float) and abs(other - value) < tol

        def __repr__(self) -> str:  # pragma: no cover - only on failure
            return f"approx({value})"

    return _Approx()
