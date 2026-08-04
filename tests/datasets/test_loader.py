from __future__ import annotations

import pytest

from shipgate.datasets.loader import DatasetError, load_jsonl


def write(tmp_path, name: str, *lines: str):
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


ROW_A = '{"id": "a", "input": {"prompt": "hi"}, "expected": "x", "slices": ["lang:en"]}'
ROW_B = '{"id": "b", "input": {"prompt": "yo"}, "expected": "y", "slices": ["lang:hi"]}'


def test_valid(tmp_path):
    items = load_jsonl(write(tmp_path, "ok.jsonl", ROW_A, ROW_B))
    assert [i.id for i in items] == ["a", "b"]
    assert items[0].input == {"prompt": "hi"}
    assert items[0].expected == "x"
    assert items[0].slices == ["lang:en"]
    assert items[0].meta == {}


def test_valid_reads_the_real_smoke_fixture():
    items = load_jsonl("fixtures/smoke.jsonl")
    assert len(items) == 5
    assert {i.expected for i in items} == {"billing", "technical", "account"}


def test_malformed_line_message(tmp_path):
    """A bad row must name the file and line. Hunting a broken line in a 100-row
    dataset from a bare stack trace is the failure mode this prevents."""
    path = write(tmp_path, "bad.jsonl", ROW_A, "{not json", ROW_B)
    with pytest.raises(DatasetError) as exc_info:
        load_jsonl(path)

    message = str(exc_info.value)
    assert "bad.jsonl:2" in message
    assert "invalid JSON" in message


def test_invalid_field_reports_line_number(tmp_path):
    path = write(tmp_path, "schema.jsonl", ROW_A, '{"input": {"prompt": "no id"}}')
    with pytest.raises(DatasetError, match=r"schema\.jsonl:2"):
        load_jsonl(path)


def test_blank_lines_are_skipped_without_shifting_line_numbers(tmp_path):
    path = write(tmp_path, "gaps.jsonl", ROW_A, "", "   ", "{not json")
    items_error = pytest.raises(DatasetError)
    with items_error as exc_info:
        load_jsonl(path)
    assert "gaps.jsonl:4" in str(exc_info.value)


def test_duplicate_ids_are_rejected(tmp_path):
    """Duplicate ids would silently double-weight an item in the score and break
    the per-item result cache, which is keyed on item id."""
    path = write(tmp_path, "dupes.jsonl", ROW_A, ROW_A)
    with pytest.raises(DatasetError, match="duplicate item id 'a'"):
        load_jsonl(path)


def test_empty_dataset_is_rejected(tmp_path):
    with pytest.raises(DatasetError, match="dataset is empty"):
        load_jsonl(write(tmp_path, "empty.jsonl", "", "  "))


def test_missing_file_names_the_path(tmp_path):
    with pytest.raises(DatasetError, match="dataset not found"):
        load_jsonl(tmp_path / "absent.jsonl")


def test_expected_is_optional_for_judge_items(tmp_path):
    path = write(tmp_path, "judge.jsonl", '{"id": "j1", "input": {"prompt": "open ended"}}')
    items = load_jsonl(path)
    assert items[0].expected is None
