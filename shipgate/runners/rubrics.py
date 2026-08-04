from __future__ import annotations

from pydantic import BaseModel


class Rubric(BaseModel):
    """A versioned judging prompt.

    The version is not decoration. It is part of the result cache key and is
    stored on every run, because a rubric edit changes what the score means. Two
    runs judged under different rubrics are not comparable, and without the
    version recorded there is no way to know that happened.
    """

    version: str
    system: str
    template: str

    def render(self, ticket: str, expected: str, output: str) -> str:
        return self.template.format(ticket=ticket, expected=expected, output=output)


JUDGE_SYSTEM = (
    "You grade whether a support-ticket classifier produced the right intent. "
    "You reply with JSON only, no prose, no code fences."
)

# v1 is deliberately naive. It is the "before" row of the calibration table, and
# the point of M5 is to show what tuning buys, so this must not be pre-tuned.
RUBRIC_V1 = Rubric(
    version="v1",
    system=JUDGE_SYSTEM,
    template=(
        "Ticket: {ticket}\n"
        "Expected intent: {expected}\n"
        "Model output: {output}\n\n"
        'Is this output good? Reply {{"verdict": "pass" or "fail", "reason": "..."}}'
    ),
)

PAIRWISE_SYSTEM = (
    "You compare two candidate answers to a support ticket and pick the better "
    "one. You reply with JSON only, no prose, no code fences."
)

# Labels are deliberately positional (A and B) rather than named, so the runner
# can swap them and measure whether the judge is picking a side or a position.
PAIRWISE_V1 = Rubric(
    version="pw-v1",
    system=PAIRWISE_SYSTEM,
    template=(
        "Ticket: {ticket}\n"
        "Correct intent: {expected}\n\n"
        "Answer A: {a}\n"
        "Answer B: {b}\n\n"
        'Which answer is better? Reply {{"winner": "A" or "B", "reason": "..."}}'
    ),
)


class PairwiseRubric(Rubric):
    def render_pair(self, ticket: str, expected: str, a: str, b: str) -> str:
        return self.template.format(ticket=ticket, expected=expected, a=a, b=b)


PAIRWISE_RUBRIC_V1 = PairwiseRubric(**PAIRWISE_V1.model_dump())

RUBRICS = {RUBRIC_V1.version: RUBRIC_V1, PAIRWISE_RUBRIC_V1.version: PAIRWISE_RUBRIC_V1}


def get_rubric(version: str) -> Rubric:
    try:
        return RUBRICS[version]
    except KeyError:
        valid = ", ".join(sorted(RUBRICS))
        raise KeyError(f"unknown rubric version {version!r}. Valid: {valid}") from None
