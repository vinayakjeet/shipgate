from __future__ import annotations

from html import escape

# Deliberately hand-rolled rather than a template engine plus a chart library.
# The page is one route with no interactivity, so a dependency would cost more
# than it saves, and inline SVG means nothing external has to load on a cold
# free-tier container.
STYLE = """
:root { color-scheme: light dark; --fg:#111; --muted:#666; --line:#ddd;
        --bg:#fff; --good:#0a7f3f; --bad:#b3261e; }
@media (prefers-color-scheme: dark) {
  :root { --fg:#e8e8e8; --muted:#9a9a9a; --line:#333; --bg:#111;
          --good:#4ade80; --bad:#f87171; }
}
* { box-sizing: border-box; }
body { margin:0; padding:2rem 1rem; background:var(--bg); color:var(--fg);
       font:15px/1.5 ui-sans-serif, system-ui, -apple-system, sans-serif; }
main { max-width: 60rem; margin: 0 auto; }
h1 { font-size:1.35rem; margin:0 0 .25rem; }
h2 { font-size:1.05rem; margin:0; font-family:ui-monospace, monospace; }
.sub { color:var(--muted); margin:0 0 2rem; font-size:.9rem; }
.card { border:1px solid var(--line); border-radius:8px; padding:1rem 1.25rem;
        margin-bottom:1.25rem; }
.head { display:flex; flex-wrap:wrap; gap:1rem; align-items:baseline;
        justify-content:space-between; }
.score { font-size:1.6rem; font-family:ui-monospace, monospace; }
.meta { color:var(--muted); font-size:.85rem; font-family:ui-monospace, monospace; }
.wrap { overflow-x:auto; }
table { border-collapse:collapse; width:100%; margin-top:.75rem; font-size:.9rem; }
th, td { text-align:left; padding:.35rem .75rem .35rem 0; white-space:nowrap; }
th { color:var(--muted); font-weight:500; border-bottom:1px solid var(--line); }
td.n { font-family:ui-monospace, monospace; }
.bad { color:var(--bad); }
.good { color:var(--good); }
.empty { color:var(--muted); }
svg { display:block; margin-top:.5rem; }
"""


def sparkline(scores: list[float], width: int = 240, height: int = 36) -> str:
    """Score over time. Fixed 0 to 1 y-axis on purpose.

    Auto-scaling would make a flat run from 0.86 to 0.87 look like a cliff, which
    is exactly the misreading a drift chart should not encourage.
    """
    if len(scores) < 2:
        return '<p class="empty">not enough runs yet for a trend</p>'

    step = width / (len(scores) - 1)
    points = " ".join(
        f"{i * step:.1f},{height - (max(0.0, min(1.0, s)) * height):.1f}"
        for i, s in enumerate(scores)
    )
    trend_class = "good" if scores[-1] >= scores[0] else "bad"
    return (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'role="img" aria-label="score over the last {len(scores)} runs">'
        f'<polyline points="{points}" fill="none" stroke="currentColor" '
        f'stroke-width="1.5" class="{trend_class}" /></svg>'
    )


def slice_table(slices: dict[str, float]) -> str:
    if not slices:
        return '<p class="empty">no slices recorded</p>'
    rows = "".join(
        f"<tr><td>{escape(tag)}</td>"
        f'<td class="n {"bad" if score < 0.5 else ""}">{score:.2f}</td></tr>'
        for tag, score in sorted(slices.items())
    )
    return (
        '<div class="wrap"><table><thead><tr><th>slice</th><th>score</th></tr>'
        f"</thead><tbody>{rows}</tbody></table></div>"
    )


def _fmt(value, spec: str = "", dash: str = "n/a") -> str:
    return dash if value is None else format(value, spec)


def dataset_card(summary: dict) -> str:
    slices = summary.get("slices") or {}
    errors = summary.get("error_count") or 0
    error_note = (
        f' <span class="bad">errors={errors}</span>' if errors else ""
    )
    return f"""
<section class="card">
  <div class="head">
    <h2>{escape(summary["dataset_id"])}</h2>
    <span class="score">{summary["score"]:.2f}</span>
  </div>
  <p class="meta">
    n={summary["n"]} &middot; {escape(summary["runner"])} &middot;
    {escape(str(summary.get("model") or "unknown"))} &middot;
    p50 {_fmt(summary.get("p50_latency_ms"), ".0f")}ms &middot;
    ${_fmt(summary.get("cost_usd"), ".4f")} &middot;
    cache {_fmt(summary.get("cache_hit_rate"), ".0%")}{error_note}
  </p>
  {sparkline(summary.get("trend", []))}
  {slice_table(slices)}
</section>
"""


def render(summaries: list[dict]) -> str:
    if summaries:
        body = "".join(dataset_card(s) for s in summaries)
    else:
        body = '<p class="empty">No runs recorded yet.</p>'

    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ShipGate</title>
<style>{STYLE}</style>
</head><body><main>
<h1>ShipGate</h1>
<p class="sub">Eval scores over time, per dataset and per slice.</p>
{body}
</main></body></html>"""
