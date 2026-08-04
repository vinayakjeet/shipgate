from __future__ import annotations

import uuid

import pytest
from typer.testing import CliRunner

from shipgate.cli import app
from shipgate.config import get_settings
from shipgate.datasets.manifest import build_manifest
from shipgate.store import db
from shipgate.types import DatasetItem

runner = CliRunner()
needs_db = pytest.mark.skipif(
    not get_settings().shipgate_db_url, reason="SHIPGATE_DB_URL not set"
)

A = {"id": "a", "input": {"prompt": "hi"}, "expected": "x", "slices": ["lang:en"]}
B = {"id": "b", "input": {"prompt": "yo"}, "expected": "y", "slices": ["lang:hi"]}


def items(*payloads: dict) -> list[DatasetItem]:
    return [DatasetItem.model_validate(p) for p in payloads]


@pytest.fixture
def dataset_id():
    name = f"registry-probe-{uuid.uuid4().hex[:8]}"
    yield name
    with db.connect() as conn:
        conn.execute("delete from datasets where dataset_id = %s", (name,))
        conn.commit()


@needs_db
def test_rehash_creates_new_version(dataset_id):
    """The acceptance criterion for M1.3. A changed dataset must not overwrite the
    old version, because a historical run row points at the old hash and that
    score has to stay interpretable."""
    original = build_manifest(items(A), dataset_id, "d/probe.jsonl")
    changed = build_manifest(items(A, B), dataset_id, "d/probe.jsonl")
    assert original.hash != changed.hash

    with db.connect() as conn:
        db.migrate(conn)
        assert db.register_dataset(conn, original) is True
        assert db.register_dataset(conn, changed) is True
        conn.commit()

        versions = db.fetch_dataset_versions(conn, dataset_id)

    assert len(versions) == 2
    assert {v["dataset_hash"] for v in versions} == {original.hash, changed.hash}
    assert {v["n"] for v in versions} == {1, 2}


@needs_db
def test_registering_the_same_hash_twice_is_a_no_op(dataset_id):
    """Safe to call on every run, so registration never needs a guard at the
    call site."""
    manifest = build_manifest(items(A), dataset_id, "d/probe.jsonl")

    with db.connect() as conn:
        db.migrate(conn)
        assert db.register_dataset(conn, manifest) is True
        assert db.register_dataset(conn, manifest) is False
        conn.commit()

        assert len(db.fetch_dataset_versions(conn, dataset_id)) == 1


@needs_db
def test_slice_counts_survive_the_round_trip(dataset_id):
    manifest = build_manifest(items(A, B), dataset_id, "d/probe.jsonl")

    with db.connect() as conn:
        db.migrate(conn)
        db.register_dataset(conn, manifest)
        conn.commit()
        stored = db.fetch_dataset_versions(conn, dataset_id)[0]

    assert stored["slice_counts"] == {"lang:en": 1, "lang:hi": 1}


def test_register_writes_a_manifest_next_to_the_dataset(tmp_path):
    dataset = tmp_path / "probe.jsonl"
    dataset.write_text(
        '{"id": "a", "input": {"prompt": "hi"}, "expected": "x", "slices": ["lang:en"]}\n',
        encoding="utf-8",
    )

    result = runner.invoke(app, ["register", "--dataset", str(dataset), "--no-store"])
    assert result.exit_code == 0, result.output
    assert "hash=sha256:" in result.output
    assert "n=1" in result.output

    manifest_file = tmp_path / "manifest.yaml"
    assert manifest_file.exists()
    assert "probe" in manifest_file.read_text(encoding="utf-8")


def test_register_reports_a_bad_dataset_without_stack_trace(tmp_path):
    dataset = tmp_path / "bad.jsonl"
    dataset.write_text("not json\n", encoding="utf-8")

    result = runner.invoke(app, ["register", "--dataset", str(dataset), "--no-store"])
    assert result.exit_code == 2
    assert "bad.jsonl:1" in result.output
    assert "Traceback" not in result.output
