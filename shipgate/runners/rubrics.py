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
        """Fills whichever placeholders the template actually uses.

        v2 deliberately omits `expected`, because handing the judge the answer
        turns calibration into a string comparison. Formatting has to tolerate
        that rather than requiring every rubric to accept every field.
        """
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

# v2 does not receive the expected answer, and that is the whole change.
#
# v1 was handed both the expected intent and the prediction, so agreeing with a
# human who applies strict equality is trivial: a `==` operator scores kappa 1.0
# on that task. It measured string comparison, not judgement.
#
# In production there is no expected label. That absence is the entire reason to
# want a judge. So v2 sees only the ticket and the prediction and has to decide
# for itself, which is the question actually worth calibrating.
#
# The classification rule is stated explicitly because the human labels follow it
# consistently, and a judge cannot match a standard nobody told it about.
RUBRIC_V2 = Rubric(
    version="v2",
    system=JUDGE_SYSTEM,
    template=(
        "A support ticket was classified into exactly one intent.\n\n"
        "Intents:\n"
        "- billing: money. Charges, refunds, invoices, payment failures, coupons, pricing.\n"
        "- technical: the product malfunctioning. Crashes, errors, sync, uploads, performance.\n"
        "- account: identity and access. Passwords, login, 2FA, profile, verification, deletion.\n"
        "- other: not a problem with the service. Sales, careers, press, docs,\n"
        "  feature requests.\n\n"
        "Rules:\n"
        "1. Judge the user's primary need, not every topic mentioned.\n"
        "2. When a ticket names both a desired outcome and a malfunction blocking it, "
        "classify by the outcome. A refund request justified by downtime is billing, "
        "not technical.\n"
        "3. A question about the service is other, even when its subject is billing "
        "or account.\n\n"
        "Ticket: {ticket}\n"
        "Classified as: {output}\n\n"
        'Is that the correct intent? Reply {{"verdict": "pass" or "fail", "reason": "..."}}'
    ),
)


# v3 is v2 plus two rules, each written against a specific observed disagreement
# rather than guessed at.
#
# Four of v2's five disagreements were the same boundary: the user's account state
# is broken (null username, profile photo, unverified badge, SSO blocking login).
# The human called those account, the judge saw "something is broken" and accepted
# technical. Rule 2 already said to classify by outcome rather than malfunction,
# but stating a principle generally was not enough to make it transfer, so rule 4
# names the boundary directly.
#
# The fifth was the opposite failure: v2's rule about questions was too broad and
# swept "how do I download my invoices" into other. Rule 5 narrows it to questions
# about the company rather than about the user's own account.
#
# Tuned on those five items, which means the improvement it shows is optimistic.
# Confirming it needs items this rubric has not seen.
RUBRIC_V3 = Rubric(
    version="v3",
    system=JUDGE_SYSTEM,
    template=(
        "A support ticket was classified into exactly one intent.\n\n"
        "Intents:\n"
        "- billing: money. Charges, refunds, invoices, payment failures, coupons, pricing.\n"
        "- technical: the product malfunctioning. Crashes, errors, sync, uploads, performance.\n"
        "- account: identity and access. Passwords, login, 2FA, profile, verification, deletion.\n"
        "- other: not a problem with the service. Sales, careers, press, docs,\n"
        "  feature requests.\n\n"
        "Rules:\n"
        "1. Judge the user's primary need, not every topic mentioned.\n"
        "2. When a ticket names both a desired outcome and a malfunction blocking it, "
        "classify by the outcome. A refund request justified by downtime is billing, "
        "not technical.\n"
        "3. Applying rule 2 to the account boundary: if what is broken is the user's "
        "own account state, profile, credentials, or sign-in, it is account, not "
        "technical, even though something is malfunctioning. A profile photo that "
        "will not change, a username showing as null, a verification badge that is "
        "wrong, and sign-in failing are all account.\n"
        "4. technical is for the product misbehaving in ways unrelated to who the "
        "user is: crashes, rendering, uploads, sync, latency, exports.\n"
        "5. A question about the company or its policies is other. A question about "
        "the user's own invoices, charges, or account is billing or account, even "
        "when phrased as how do I.\n\n"
        "Ticket: {ticket}\n"
        "Classified as: {output}\n\n"
        'Is that the correct intent? Reply {{"verdict": "pass" or "fail", "reason": "..."}}'
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

RUBRICS = {
    RUBRIC_V1.version: RUBRIC_V1,
    RUBRIC_V2.version: RUBRIC_V2,
    RUBRIC_V3.version: RUBRIC_V3,
    PAIRWISE_RUBRIC_V1.version: PAIRWISE_RUBRIC_V1,
}


def get_rubric(version: str) -> Rubric:
    try:
        return RUBRICS[version]
    except KeyError:
        valid = ", ".join(sorted(RUBRICS))
        raise KeyError(f"unknown rubric version {version!r}. Valid: {valid}") from None
