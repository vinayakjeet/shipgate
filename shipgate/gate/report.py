from __future__ import annotations

from shipgate.gate.verdict import GateResult, Verdict

HEADLINE = {
    Verdict.PASS: "ShipGate: pass",
    Verdict.FAIL: "ShipGate: FAILED",
    Verdict.BASELINE_INVALID: "ShipGate: no baseline",
    Verdict.RUN_INVALID: "ShipGate: run invalid",
}


def _signed(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.3f}"


def render_summary(result: GateResult, failing_items: list[dict] | None = None) -> str:
    """Markdown verdict for the Actions job summary.

    Written so someone can tell what broke without opening the logs. That is the
    whole difference between a gate people trust and a gate people disable.
    """
    lines = [f"## {HEADLINE[result.verdict]}", "", result.reason, ""]

    lines += [
        "| metric | baseline | current | delta |",
        "|---|---|---|---|",
        f"| overall | {_fmt(result.baseline_score)} | {result.score:.3f} "
        f"| {_signed(result.delta)} |",
    ]

    for d in result.slice_deltas:
        marker = " **regressed**" if d.tag in result.failing_slices else ""
        lines.append(
            f"| {d.tag} | {d.baseline:.3f} | {d.current:.3f} | {d.delta:+.3f}{marker} |"
        )
    lines.append("")

    if result.error_count:
        lines += [
            f"{result.error_count} of {result.n} items failed to score. Errors count "
            "as 0, so treat the score as a floor rather than a measurement.",
            "",
        ]

    if failing_items:
        lines += ["### Worst failing examples", ""]
        for item in failing_items[:3]:
            output = (item.get("output") or "").strip().replace("\n", " ")
            detail = item.get("error") or f"scored {item['score']:.2f}, output: {output[:120]!r}"
            lines.append(f"- `{item['item_id']}` {detail}")
        lines.append("")

    lines += [
        f"Thresholds: overall {result.threshold_overall:.3f}, "
        f"per slice {result.threshold_slice:.3f}.",
    ]
    if result.baseline_run_id:
        lines.append(f"Baseline run: `{result.baseline_run_id}`")

    return "\n".join(lines)


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"
