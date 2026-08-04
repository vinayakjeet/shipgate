from __future__ import annotations

from collections import Counter

import pytest

from shipgate.datasets.hashing import content_hash
from shipgate.datasets.loader import load_jsonl

DATASET = "datasets/support-intent.jsonl"
INTENTS = {"billing", "technical", "account", "other"}


@pytest.fixture(scope="module")
def items():
    return load_jsonl(DATASET)


def slice_counts(items, prefix: str) -> dict[str, int]:
    return Counter(
        tag.split(":", 1)[1]
        for item in items
        for tag in item.slices
        if tag.startswith(f"{prefix}:")
    )


def test_slices_populated(items):
    """The acceptance criterion for M1.5: every slice carries enough items to be
    worth guarding. A slice of three items produces a score that swings 33 points
    on a single failure, which would make the per-slice gate pure noise."""
    for prefix in ("intent", "lang", "len"):
        counts = slice_counts(items, prefix)
        assert counts, f"no {prefix} slices found"
        assert min(counts.values()) >= 10, f"{prefix} slice too small: {dict(counts)}"


def test_has_one_hundred_items(items):
    assert len(items) == 100


def test_intents_are_balanced(items):
    """Balanced classes keep the majority-class baseline honest at 0.25. An
    unbalanced set would let a constant predictor look good for free."""
    counts = slice_counts(items, "intent")
    assert set(counts) == INTENTS
    assert set(counts.values()) == {25}


def test_every_item_has_a_valid_expected_label(items):
    assert {item.expected for item in items} == INTENTS


def test_every_item_carries_all_three_slice_dimensions(items):
    for item in items:
        prefixes = {tag.split(":", 1)[0] for tag in item.slices}
        assert prefixes == {"intent", "lang", "len"}, f"{item.id} has slices {item.slices}"


def test_intent_slice_matches_expected_label(items):
    """The slice tag and the label cannot drift apart, or per-slice scores would
    be attributed to the wrong bucket."""
    for item in items:
        tagged = next(t.split(":", 1)[1] for t in item.slices if t.startswith("intent:"))
        assert tagged == item.expected, f"{item.id}: slice {tagged} vs expected {item.expected}"


def test_covers_code_mixed_and_devanagari_input(items):
    """Nishana fine-tunes on code-mixed Hinglish, so the eval set has to contain
    it. Pure English would measure the easy half of the problem."""
    langs = slice_counts(items, "lang")
    assert langs["hinglish"] >= 25
    assert langs["hi"] >= 10

    devanagari = [i for i in items if any("ऀ" <= ch <= "ॿ" for ch in i.input["prompt"])]
    assert len(devanagari) >= 10


def test_prompts_are_unique(items):
    prompts = [item.input["prompt"] for item in items]
    assert len(set(prompts)) == len(prompts)


def test_hash_is_stable_for_the_committed_file(items):
    """Pins the dataset identity. If this fails, the file changed, which means
    every stored baseline for this dataset is no longer comparable."""
    assert content_hash(items) == content_hash(load_jsonl(DATASET))
