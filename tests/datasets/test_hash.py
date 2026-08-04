from __future__ import annotations

import json

from shipgate.datasets.hashing import content_hash
from shipgate.datasets.manifest import build_manifest, load_manifests, write_manifest
from shipgate.types import DatasetItem


def items(*payloads: dict) -> list[DatasetItem]:
    return [DatasetItem.model_validate(p) for p in payloads]


A = {"id": "a", "input": {"prompt": "hi"}, "expected": "x", "slices": ["lang:en"]}
B = {"id": "b", "input": {"prompt": "yo"}, "expected": "y", "slices": ["lang:hi"]}
C = {"id": "c", "input": {"prompt": "hey"}, "expected": "z", "slices": ["lang:en"]}


def test_order_invariant():
    """Reordering rows must not invalidate a baseline. A dataset is a set of
    items, and a git diff that moves lines around is not a new measurement."""
    assert content_hash(items(A, B, C)) == content_hash(items(C, A, B))


def test_edit_changes_hash():
    edited = {**A, "expected": "different"}
    assert content_hash(items(A, B)) != content_hash(items(edited, B))


def test_editing_input_changes_hash():
    edited = {**A, "input": {"prompt": "changed"}}
    assert content_hash(items(A, B)) != content_hash(items(edited, B))


def test_editing_slices_changes_hash():
    """Slices drive the per-slice guard, so retagging is a real change."""
    edited = {**A, "slices": ["lang:en", "risk:pii"]}
    assert content_hash(items(A, B)) != content_hash(items(edited, B))


def test_adding_an_item_changes_hash():
    assert content_hash(items(A, B)) != content_hash(items(A, B, C))


def test_removing_an_item_changes_hash():
    assert content_hash(items(A, B, C)) != content_hash(items(A, B))


def test_hash_is_stable_across_calls():
    assert content_hash(items(A, B)) == content_hash(items(A, B))


def test_hash_is_prefixed_for_readability():
    digest = content_hash(items(A))
    assert digest.startswith("sha256:")
    assert len(digest) == len("sha256:") + 64


def test_build_manifest_counts_slices():
    manifest = build_manifest(items(A, B, C), "probe", "datasets/probe.jsonl")
    assert manifest.id == "probe"
    assert manifest.n == 3
    assert manifest.slice_counts == {"lang:en": 2, "lang:hi": 1}
    assert manifest.hash == content_hash(items(A, B, C))
    assert manifest.path == "datasets/probe.jsonl"


def test_manifest_roundtrip_preserves_other_datasets(tmp_path):
    path = tmp_path / "manifest.yaml"
    write_manifest(path, build_manifest(items(A), "first", "d/first.jsonl"))
    write_manifest(path, build_manifest(items(B), "second", "d/second.jsonl"))

    stored = load_manifests(path)
    assert set(stored) == {"first", "second"}
    assert stored["first"].n == 1
    assert stored["second"].hash == content_hash(items(B))


def test_manifest_upsert_replaces_same_id(tmp_path):
    path = tmp_path / "manifest.yaml"
    write_manifest(path, build_manifest(items(A), "probe", "d/probe.jsonl"))
    write_manifest(path, build_manifest(items(A, B), "probe", "d/probe.jsonl"))

    stored = load_manifests(path)
    assert set(stored) == {"probe"}
    assert stored["probe"].n == 2


def test_load_manifests_on_missing_file_is_empty(tmp_path):
    assert load_manifests(tmp_path / "absent.yaml") == {}


def test_hash_ignores_json_key_order():
    """The same item written with keys in a different order is the same item."""
    reordered = json.loads(json.dumps({"slices": ["lang:en"], "expected": "x",
                                       "input": {"prompt": "hi"}, "id": "a"}))
    assert content_hash(items(A)) == content_hash(items(reordered))
