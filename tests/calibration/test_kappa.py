from __future__ import annotations

import pytest

from shipgate.calibration.kappa import cohens_kappa, disagreements


def labels(*pairs) -> tuple[dict[str, str], dict[str, str]]:
    human = {f"i{n}": h for n, (h, _) in enumerate(pairs)}
    judge = {f"i{n}": j for n, (_, j) in enumerate(pairs)}
    return human, judge


def test_known_fixture():
    """Hand-computed. 8 of 10 agree, so observed is 0.80.

    Human: 6 pass, 4 fail. Judge: 6 pass, 4 fail.
    Expected by chance = 0.6*0.6 + 0.4*0.4 = 0.52.
    kappa = (0.80 - 0.52) / (1 - 0.52) = 0.28 / 0.48 = 0.5833...
    """
    human, judge = labels(
        # 5 agreed passes
        ("pass", "pass"), ("pass", "pass"), ("pass", "pass"), ("pass", "pass"),
        ("pass", "pass"),
        # 3 agreed fails
        ("fail", "fail"), ("fail", "fail"), ("fail", "fail"),
        # one disagreement each way, which keeps both labelers at 6 pass / 4 fail
        ("pass", "fail"), ("fail", "pass"),
    )

    result = cohens_kappa(human, judge)
    assert result.n == 10
    assert result.observed == pytest.approx(0.8)
    assert result.expected == pytest.approx(0.52)
    assert result.kappa == pytest.approx(0.5833, abs=1e-3)
    assert result.interpretation == "moderate"


def test_perfect_and_chance():
    human, judge = labels(("pass", "pass"), ("fail", "fail"), ("pass", "pass"), ("fail", "fail"))
    perfect = cohens_kappa(human, judge)
    assert perfect.kappa == pytest.approx(1.0)
    assert perfect.interpretation == "almost perfect"

    # Judge answers the opposite of the human every time.
    human, judge = labels(("pass", "fail"), ("fail", "pass"), ("pass", "fail"), ("fail", "pass"))
    inverted = cohens_kappa(human, judge)
    assert inverted.kappa == pytest.approx(-1.0)
    assert inverted.interpretation == "worse than chance"


def test_a_constant_judge_scores_zero_despite_high_raw_agreement():
    """The reason kappa is the metric and raw agreement is not.

    Nine of ten items are 'pass'. A judge that answers 'pass' unconditionally gets
    90% raw agreement while contributing no information whatsoever.
    """
    human = {f"i{n}": "pass" for n in range(9)} | {"i9": "fail"}
    judge = {f"i{n}": "pass" for n in range(10)}

    result = cohens_kappa(human, judge)
    assert result.observed == pytest.approx(0.9)
    assert result.kappa == pytest.approx(0.0)


def test_total_agreement_on_a_single_label_is_not_reported_as_perfect():
    """Both labelers said 'pass' to everything. Chance agreement is already total,
    so kappa is undefined. Reporting 1.0 would be the most flattering possible lie."""
    human = {f"i{n}": "pass" for n in range(10)}
    judge = {f"i{n}": "pass" for n in range(10)}

    assert cohens_kappa(human, judge).kappa == 0.0


def test_only_items_labeled_by_both_are_compared():
    human = {"a": "pass", "b": "fail", "c": "pass"}
    judge = {"a": "pass", "b": "fail"}

    result = cohens_kappa(human, judge)
    assert result.n == 2


def test_no_overlap_raises_rather_than_returning_a_number():
    with pytest.raises(ValueError, match="no items"):
        cohens_kappa({"a": "pass"}, {"b": "fail"})


def test_confusion_matrix_shows_the_direction_of_error():
    """Which way the judge is wrong decides how you fix the rubric. A judge that
    is too lenient needs different wording than one that is too strict."""
    human, judge = labels(("pass", "fail"), ("pass", "fail"), ("fail", "fail"))
    result = cohens_kappa(human, judge)
    assert result.confusion["pass->fail"] == 2
    assert result.confusion["fail->fail"] == 1


def test_disagreements_list_is_what_you_read_to_write_v2():
    human, judge = labels(("pass", "pass"), ("pass", "fail"), ("fail", "pass"))
    assert disagreements(human, judge) == [("i1", "pass", "fail"), ("i2", "fail", "pass")]


@pytest.mark.parametrize(
    "kappa,band",
    [(-0.1, "worse than chance"), (0.1, "slight"), (0.3, "fair"),
     (0.5, "moderate"), (0.7, "substantial"), (0.9, "almost perfect")],
)
def test_interpretation_bands(kappa, band):
    from shipgate.calibration.kappa import Agreement

    assert Agreement(n=1, observed=0, expected=0, kappa=kappa).interpretation == band
