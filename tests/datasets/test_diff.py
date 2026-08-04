from __future__ import annotations

from typer.testing import CliRunner

from shipgate.cli import app
from shipgate.datasets.diff import diff_files, diff_items
from shipgate.types import DatasetItem

runner = CliRunner()

A = {"id": "a", "input": {"prompt": "hi"}, "expected": "x", "slices": ["lang:en"]}
B = {"id": "b", "input": {"prompt": "yo"}, "expected": "y", "slices": ["lang:hi"]}
C = {"id": "c", "input": {"prompt": "hey"}, "expected": "z", "slices": ["lang:en"]}


def items(*payloads: dict) -> list[DatasetItem]:
    return [DatasetItem.model_validate(p) for p in payloads]


def write(tmp_path, name: str, *payloads: dict):
    import json

    path = tmp_path / name
    path.write_text(
        "\n".join(json.dumps(p) for p in payloads) + "\n", encoding="utf-8"
    )
    return path


def test_added_removed_changed():
    """The acceptance criterion for M1.4."""
    edited_b = {**B, "expected": "changed"}
    result = diff_items(items(A, B), items(edited_b, C))

    assert result.added == ["c"]
    assert result.removed == ["a"]
    assert result.changed == ["b"]
    assert result.unchanged == 0


def test_unchanged_items_are_counted_not_listed():
    result = diff_items(items(A, B), items(A, B, C))
    assert result.added == ["c"]
    assert result.changed == []
    assert result.unchanged == 2


def test_reordering_is_not_a_change():
    result = diff_items(items(A, B, C), items(C, B, A))
    assert result.added == []
    assert result.removed == []
    assert result.changed == []
    assert result.unchanged == 3
    assert result.is_identical


def test_editing_slices_registers_as_changed():
    retagged = {**A, "slices": ["lang:en", "risk:pii"]}
    result = diff_items(items(A), items(retagged))
    assert result.changed == ["a"]
    assert not result.is_identical


def test_hashes_are_reported_for_both_sides():
    result = diff_items(items(A), items(A, B))
    assert result.before_hash.startswith("sha256:")
    assert result.after_hash.startswith("sha256:")
    assert result.before_hash != result.after_hash


def test_diff_files_reads_from_disk(tmp_path):
    before = write(tmp_path, "before.jsonl", A, B)
    after = write(tmp_path, "after.jsonl", A, C)
    result = diff_files(before, after)
    assert result.added == ["c"]
    assert result.removed == ["b"]


def test_cli_reports_identical_files(tmp_path):
    before = write(tmp_path, "before.jsonl", A, B)
    after = write(tmp_path, "after.jsonl", B, A)
    result = runner.invoke(app, ["diff", "--before", str(before), "--after", str(after)])
    assert result.exit_code == 0
    assert "identical" in result.output


def test_cli_lists_the_changes(tmp_path):
    before = write(tmp_path, "before.jsonl", A, B)
    after = write(tmp_path, "after.jsonl", A, {**B, "expected": "changed"}, C)
    result = runner.invoke(app, ["diff", "--before", str(before), "--after", str(after)])
    assert result.exit_code == 0
    assert "added=1 removed=0 changed=1 unchanged=1" in result.output
    assert "+ c" in result.output
    assert "~ b" in result.output
