# Adopting the gate

How another project gets its changes gated by ShipGate. Two files and one secret.

## 1. Write an eval dataset

JSONL, one item per line, at a stable path in your repo.

```jsonl
{"id": "t-0001", "input": {"prompt": "..."}, "expected": "billing", "slices": ["lang:en", "intent:billing"]}
```

| Field | Required | Notes |
|---|---|---|
| `id` | yes | Unique. It is the cache key and the label key, so never reuse one for different content. |
| `input` | yes | Object passed to your target. |
| `expected` | exact runner only | Optional for the judge, which grades open-ended output. |
| `slices` | recommended | `key:value` tags. These drive the per-slice guard. |
| `meta` | no | Anything else you want stored. |

Give every slice at least 10 items. A slice of three swings 33 points on a single
failure, so the per-slice guard becomes noise and you will end up muting it.

## 2. Add `shipgate.yaml`

```yaml
version: 1
datasets:
  - id: tollgate-core
    path: evals/tollgate-core.jsonl
    runner: judge            # exact | judge | pairwise
    threshold_overall: 0.02  # fail if the overall score drops more than this
    threshold_slice: 0.05    # fail if any slice drops more than this
target:
  kind: http                 # http | cmd
  url: ${{ env.EVAL_TARGET_URL }}
```

Thresholds start as placeholders. Measure your own run-to-run variance and set
them above it, otherwise the gate fires on noise and people learn to ignore it.

## 3. Add the workflow

```yaml
name: Eval
on: [push]

jobs:
  gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: vinayakjeet/shipgate/action@v1
        with:
          dataset: evals/tollgate-core.jsonl
          runner: judge
        env:
          SHIPGATE_DB_URL: ${{ secrets.SHIPGATE_DB_URL }}
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
```

`SHIPGATE_DB_URL` is the shared Neon database where baselines live. Without it
every run reports `baseline-invalid`, which looks like a working pipeline and
checks nothing.

## What your target has to answer

One request per item:

```jsonc
// POST <target.url>
{ "input": { "prompt": "..." }, "item_id": "t-0001" }

// 200 OK
{ "output": "...",
  "meta": { "tokens_in": 412, "tokens_out": 88, "latency_ms": 940 } }
```

## Verdicts, and which of them block

| Verdict | Exit | Meaning |
|---|---|---|
| `pass` | 0 | Score held, or the drop is inside the threshold. |
| `fail` | **1** | A real regression, overall or in a slice. |
| `baseline-invalid` | 0 | No clean baseline for this dataset version yet. |
| `run-invalid` | 0 | Too many items failed to score for the number to mean anything. |

Only `fail` blocks. A missing baseline or a rate-limited run is an infrastructure
problem, and failing a good change for one of those teaches people to bypass the
gate, which costs more than the single regression it might have caught.

## Establishing the first baseline

The first run on your default branch reports `baseline-invalid` and passes. That
run becomes the baseline, so the second one gates properly. This is expected, not
a misconfiguration.

Baselines are scoped to the exact dataset hash. Editing your dataset invalidates
the baseline deliberately, because a score measured on different content is not
comparable, and comparing across versions is the easiest way to make a gate lie.

## Outputs

```yaml
- uses: vinayakjeet/shipgate/action@v1
  id: gate
  with: { dataset: evals/core.jsonl }
- run: echo "${{ steps.gate.outputs.verdict }} ${{ steps.gate.outputs.delta }}"
```

`verdict`, `score`, `baseline_score`, `delta`, `run_id`, `failing_slices`.

## Where to look when it fails

The job summary carries the score delta, the per-slice breakdown, and the three
worst failing examples, so the usual case needs no log reading. Run history and
score-over-time live on the dashboard at https://shipgate-890d.onrender.com/.
