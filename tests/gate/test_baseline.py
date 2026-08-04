from __future__ import annotations

import uuid

import pytest

from shipgate.config import get_settings
from shipgate.gate.baseline import resolve_baseline
from shipgate.store import db
from shipgate.types import RunRecord

pytestmark = pytest.mark.skipif(
    not get_settings().shipgate_db_url, reason="SHIPGATE_DB_URL not set"
)

HASH_A = "sha256:aaa"
HASH_B = "sha256:bbb"


@pytest.fixture
def dataset_id():
    name = f"baseline-probe-{uuid.uuid4().hex[:8]}"
    yield name
    with db.connect() as conn:
        conn.execute("delete from runs where dataset_id = %s", (name,))
        conn.commit()


def store(dataset_id: str, **kw) -> RunRecord:
    record = RunRecord(
        run_id=f"r_bl_{uuid.uuid4().hex[:12]}",
        dataset_id=dataset_id,
        dataset_hash=kw.pop("dataset_hash", HASH_A),
        git_ref=kw.pop("git_ref", "main"),
        runner="exact",
        n=kw.pop("n", 100),
        score=kw.pop("score", 0.86),
        **kw,
    )
    with db.connect() as conn:
        db.migrate(conn)
        db.insert_run(conn, record)
        conn.commit()
    return record


def test_resolves_latest(dataset_id):
    store(dataset_id, score=0.80)
    newest = store(dataset_id, score=0.86)

    with db.connect() as conn:
        found = resolve_baseline(conn, dataset_id, HASH_A, "main")

    assert found["run_id"] == newest.run_id
    assert found["score"] == pytest.approx(0.86)


def test_hash_mismatch_invalid(dataset_id):
    """S4. Comparing across dataset versions is the easiest way to make a gate
    lie, because an edited dataset moves the score for reasons unrelated to code."""
    store(dataset_id, dataset_hash=HASH_A)

    with db.connect() as conn:
        assert resolve_baseline(conn, dataset_id, HASH_B, "main") is None


def test_only_the_baseline_branch_qualifies(dataset_id):
    """Otherwise a bad experimental commit becomes the bar everything else is
    measured against."""
    store(dataset_id, git_ref="feature/experiment", score=0.20)

    with db.connect() as conn:
        assert resolve_baseline(conn, dataset_id, HASH_A, "main") is None


def test_a_run_full_of_errors_cannot_become_the_baseline(dataset_id):
    """A low score caused by provider failures would silently become the bar,
    letting real regressions through afterwards."""
    store(dataset_id, score=0.30, error_count=40, n=100)

    with db.connect() as conn:
        assert resolve_baseline(conn, dataset_id, HASH_A, "main") is None


def test_a_few_errors_still_qualifies(dataset_id):
    clean_enough = store(dataset_id, score=0.85, error_count=5, n=100)

    with db.connect() as conn:
        found = resolve_baseline(conn, dataset_id, HASH_A, "main")

    assert found["run_id"] == clean_enough.run_id


def test_no_runs_at_all_returns_none(dataset_id):
    with db.connect() as conn:
        assert resolve_baseline(conn, dataset_id, HASH_A, "main") is None


def test_a_run_is_never_its_own_baseline(dataset_id):
    """Regression test for a real bug. The gate stored the current run before
    resolving the baseline, found itself, and reported a delta of exactly zero on
    every comparison. It looked like a working gate and was incapable of failing.
    """
    earlier = store(dataset_id, score=0.86)
    current = store(dataset_id, score=0.20)

    with db.connect() as conn:
        found = resolve_baseline(conn, dataset_id, HASH_A, "main", exclude_run_id=current.run_id)

    assert found["run_id"] == earlier.run_id
    assert found["score"] == pytest.approx(0.86)


def test_excluding_the_only_run_leaves_no_baseline(dataset_id):
    only = store(dataset_id, score=0.86)

    with db.connect() as conn:
        found = resolve_baseline(conn, dataset_id, HASH_A, "main", exclude_run_id=only.run_id)
    assert found is None


def test_a_failed_run_never_becomes_the_baseline(dataset_id):
    """Regression test for a real bug found by dogfooding.

    A failing run was still stored on the baseline branch, so it became the bar
    for the next run. The regression blocked exactly once, then sailed through
    forever after, which is worse than having no gate because it looks like one.
    """
    good = store(dataset_id, score=0.86, verdict="pass")
    store(dataset_id, score=0.00, verdict="fail")

    with db.connect() as conn:
        found = resolve_baseline(conn, dataset_id, HASH_A, "main")

    assert found["run_id"] == good.run_id
    assert found["score"] == pytest.approx(0.86)


def test_an_invalid_run_never_becomes_the_baseline(dataset_id):
    good = store(dataset_id, score=0.86, verdict="pass")
    store(dataset_id, score=0.10, verdict="run-invalid")
    store(dataset_id, score=0.20, verdict="baseline-invalid")

    with db.connect() as conn:
        found = resolve_baseline(conn, dataset_id, HASH_A, "main")

    assert found["run_id"] == good.run_id


def test_ungated_runs_are_still_eligible(dataset_id):
    """Runs recorded by `shipgate run` carry no verdict. They predate gating for
    this dataset, so excluding them would leave a project unable to bootstrap."""
    ungated = store(dataset_id, score=0.86, verdict=None)

    with db.connect() as conn:
        found = resolve_baseline(conn, dataset_id, HASH_A, "main")

    assert found["run_id"] == ungated.run_id


def test_a_clean_run_is_preferred_over_a_newer_broken_one(dataset_id):
    clean = store(dataset_id, score=0.86)
    store(dataset_id, score=0.10, error_count=90, n=100)

    with db.connect() as conn:
        found = resolve_baseline(conn, dataset_id, HASH_A, "main")

    assert found["run_id"] == clean.run_id
