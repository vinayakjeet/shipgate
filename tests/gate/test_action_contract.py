from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ACTION = Path("action/action.yml")
WORKFLOW = Path(".github/workflows/eval.yml")


@pytest.fixture(scope="module")
def action() -> dict:
    return yaml.safe_load(ACTION.read_text(encoding="utf-8"))


def test_action_is_valid_yaml_and_composite(action):
    assert action["runs"]["using"] == "composite"
    assert action["name"] == "ShipGate"


def test_template_matches_action_inputs(action):
    """The documented contract in SPEC.md is what other projects copy. If the
    action stops accepting one of those inputs, every gated repo breaks at once."""
    documented = {"dataset", "dataset-id", "runner", "baseline-ref",
                  "threshold-overall", "threshold-slice"}
    assert documented <= set(action["inputs"])


def test_only_dataset_is_required(action):
    """Everything else has a default, so adoption is one line plus a secret."""
    required = {k for k, v in action["inputs"].items() if v.get("required")}
    assert required == {"dataset"}


def test_outputs_cover_the_verdict_and_the_numbers(action):
    assert {"verdict", "score", "baseline_score", "delta", "run_id", "failing_slices"} <= set(
        action["outputs"]
    )


def test_defaults_match_the_gate_defaults(action):
    """A default that drifts from the CLI would mean the action silently gates at
    a different threshold than the docs and tests describe."""
    from shipgate.gate.verdict import evaluate
    from shipgate.types import RunRecord

    result = evaluate(
        RunRecord(run_id="r", dataset_id="d", dataset_hash="h", runner="exact", n=1, score=1.0),
        {"run_id": "b", "score": 1.0, "slices": {}},
    )
    assert float(action["inputs"]["threshold-overall"]["default"]) == result.threshold_overall
    assert float(action["inputs"]["threshold-slice"]["default"]) == result.threshold_slice
    assert action["inputs"]["baseline-ref"]["default"] == "main"


def test_workflow_publishes_the_summary_even_on_failure():
    """The summary matters most when the gate fails, so the publish step cannot
    be skipped by the failing step above it."""
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["gate"]["steps"]
    publish = next(s for s in steps if s.get("name") == "Publish the verdict")
    assert publish["if"] == "always()"


def test_workflow_passes_the_database_secret():
    """Without it every run reports baseline-invalid instead of gating, which
    looks like a working pipeline and checks nothing."""
    raw = WORKFLOW.read_text(encoding="utf-8")
    assert "secrets.SHIPGATE_DB_URL" in raw
