from __future__ import annotations

import pytest
from typer.testing import CliRunner

from shipgate.calibration.labeling import LabelStore, remaining
from shipgate.cli import app
from shipgate.datasets.loader import load_jsonl

runner = CliRunner()
DATASET = "datasets/support-intent.jsonl"


def test_resume_after_interrupt(tmp_path):
    """The acceptance criterion. A labeling session is 90 minutes of
    irreplaceable human attention, so a crash at minute 80 must cost nothing."""
    store = LabelStore(tmp_path / "labels.jsonl")
    store.append("sup-001", "pass", "sha256:x")
    store.append("sup-002", "fail", "sha256:x")

    # Simulate the process dying: a completely fresh store over the same file.
    reopened = LabelStore(tmp_path / "labels.jsonl")
    assert reopened.load() == {"sup-001": "pass", "sup-002": "fail"}


def test_relabeling_an_item_is_a_correction_not_a_duplicate(tmp_path):
    store = LabelStore(tmp_path / "labels.jsonl")
    store.append("sup-001", "pass", "sha256:x")
    store.append("sup-001", "fail", "sha256:x")

    assert store.load() == {"sup-001": "fail"}


def test_invalid_labels_are_rejected(tmp_path):
    store = LabelStore(tmp_path / "labels.jsonl")
    with pytest.raises(ValueError, match="label must be one of"):
        store.append("sup-001", "maybe", "sha256:x")


def test_missing_file_is_an_empty_session(tmp_path):
    assert LabelStore(tmp_path / "nothing.jsonl").load() == {}


def test_remaining_skips_labeled_items_and_keeps_order():
    items = load_jsonl(DATASET)
    labeled = {"sup-001": "pass", "sup-050": "fail"}

    todo = remaining(items, labeled)

    assert len(todo) == 98
    assert "sup-001" not in {i.id for i in todo}
    # Order is preserved so intents stay grouped, which keeps the labeler's
    # mental rule consistent across a category.
    assert [i.id for i in todo] == [i.id for i in items if i.id not in labeled]


def test_cli_labels_items_from_stdin(tmp_path):
    labels_file = tmp_path / "labels.jsonl"
    result = runner.invoke(
        app,
        ["label", "--dataset", DATASET, "--labels", str(labels_file),
         "--limit", "3", "--no-store", "--target-label", "billing"],
        input="p\nf\np\n",
    )
    assert result.exit_code == 0, result.output
    assert LabelStore(labels_file).load() == {
        "sup-001": "pass",
        "sup-002": "fail",
        "sup-003": "pass",
    }


def test_cli_never_shows_the_judge_verdict(tmp_path):
    """A labeler who sees the judge's answer anchors on it, and the resulting
    agreement number would measure suggestibility rather than correctness."""
    result = runner.invoke(
        app,
        ["label", "--dataset", DATASET, "--labels", str(tmp_path / "l.jsonl"),
         "--limit", "1", "--no-store", "--target-label", "billing"],
        input="p\n",
    )
    lowered = result.output.lower()
    assert "ticket:" in lowered
    assert "expected:" in lowered
    assert "judge" not in lowered
    assert "verdict" not in lowered


def test_cli_quit_stops_without_losing_earlier_labels(tmp_path):
    labels_file = tmp_path / "labels.jsonl"
    result = runner.invoke(
        app,
        ["label", "--dataset", DATASET, "--labels", str(labels_file), "--no-store"],
        input="p\nq\n",
    )
    assert result.exit_code == 0
    assert LabelStore(labels_file).load() == {"sup-001": "pass"}


def test_cli_skip_leaves_the_item_unlabeled(tmp_path):
    labels_file = tmp_path / "labels.jsonl"
    runner.invoke(
        app,
        ["label", "--dataset", DATASET, "--labels", str(labels_file),
         "--limit", "1", "--no-store"],
        input="s\np\n",
    )
    stored = LabelStore(labels_file).load()
    assert "sup-001" not in stored
    assert stored == {"sup-002": "pass"}


def test_cli_reports_when_everything_is_labeled(tmp_path):
    labels_file = tmp_path / "labels.jsonl"
    store = LabelStore(labels_file)
    for item in load_jsonl(DATASET):
        store.append(item.id, "pass", "sha256:x")

    result = runner.invoke(
        app,
        ["label", "--dataset", DATASET, "--labels", str(labels_file), "--no-store",
         "--target-label", "billing"],
    )
    assert "already labeled" in result.output
