# ShipGate

The system that decides whether an AI change ships.

Versioned eval datasets, three runner types, a calibrated LLM judge, and a GitHub
Action that fails the check when quality regresses past a threshold. Runs entirely
on free tiers.

[Dashboard](https://shipgate-890d.onrender.com/) ·
[Adoption guide](docs/ADOPTING.md) ·
[Decisions](DECISIONS.md) ·
[What broke](LEARNING.md)

## Problem

Prompt and model changes ship on vibes. A change looks fine in three manual spot
checks, merges, and quietly breaks a slice of users nobody tested. The usual
answer, an eval script that prints a score, has two holes: nobody agrees what the
score means, and nothing stops the merge.

ShipGate closes both. Scores are anchored to a dataset version, and the gate is a
required check that exits non-zero.

The harder half is the judge. Grading open-ended output needs a model, and a model
grading a model is a noisy instrument with no obvious error bar. So the judge here
is measured against hand-labeled ground truth and reported with a kappa, before
anyone is asked to trust it to block a merge.

## Proof

**1. Judge calibration.** Agreement between the LLM judge and 100 hand-labeled
examples, before and after tuning the rubric.

_Pending: populated by `shipgate label` then `shipgate calibrate`._

| Rubric | Kappa | Agreement | n |
|---|---|---|---|
| v1, naive | tbd | tbd | 100 |
| v2, criteria-split | tbd | tbd | 100 |

**2. A regression, blocked.** Actions run
[30931868809](https://github.com/vinayakjeet/shipgate/actions/runs/30931868809):

```
## ShipGate: FAILED
overall score dropped 0.250 (threshold 0.020)

| metric         | baseline | current | delta  |
| overall        | 0.250    | 0.000   | -0.250 |
| intent:billing | 1.000    | 0.000   | -1.000 **regressed** |
| lang:hi        | 0.250    | 0.000   | -0.250 **regressed** |

- `sup-001` scored 0.00, output: 'unknown'
```

The same commit against a clean target passes. Same workflow, same database,
opposite verdicts.

## Architecture

```mermaid
flowchart TB
  subgraph dev["Local"]
    CLI["shipgate CLI<br/>run · gate · register · label"]
    DS[("datasets/*.jsonl<br/>content-hashed")]
  end
  subgraph gh["GitHub Actions"]
    ACT["gate action"]
    CRON["nightly cron"]
  end
  subgraph render["Render free"]
    API["FastAPI<br/>dashboard + read API"]
  end
  subgraph neon["Neon free"]
    DB[("datasets · runs<br/>run_items · labels<br/>result_cache")]
  end
  GEM["Gemini free<br/>judge"]

  CLI --> DS
  DS --> ACT
  ACT --> GEM
  ACT --> DB
  ACT -->|"exit code + summary"| CHECK["required check"]
  CRON --> ACT
  API --> DB
  ACT -.->|"OTLP spans"| SPAN["Spanlight"]
```

**Runners.** `exact` for anything with a canonical answer, free and deterministic.
`judge` for open-ended output, where exact match cannot tell `billing` from
`this is a billing issue`. `pairwise` for preference between two candidates.

**Datasets** are content-addressed. The hash is order invariant, so reordering
rows does not invalidate a baseline, while editing any field does. Baselines are
scoped to the hash, because a score measured on different content is not
comparable.

**The cache** is keyed on every input that can change an outcome, including the
rubric version, and lives in Postgres rather than on disk because CI containers
start empty.

## Benchmarks

Regenerate with `uv run python scripts/benchmark.py`.

_Pending: populated by the benchmark script._

## Technical Decisions

Full log in [DECISIONS.md](DECISIONS.md). The four that shaped the design:

**Only real regressions block.** A missing baseline, or a run where a tenth of the
items failed to score, exits zero and complains loudly. Blocking a good change for
an infrastructure reason is how a gate gets bypassed, which costs more than the
one regression it might have caught.

**Per-slice guards, not just an average.** A model can hold 0.86 overall while one
language slice collapses to 0.60. The overall number is structurally blind to
that. The guard fires per slice and names the one that broke.

**Errors are counted separately from scores.** A failed item scores 0, so a run
full of judge errors is numerically identical to a real regression. Without a
separate error count, a JSON parsing bug reads as "the model got worse".

**Pairwise comparisons are judged twice, in both orders.** LLM judges prefer
whichever candidate appears first. Averaging both orderings means a consistent
judge scores 1.0 or 0.0, while a purely position-biased one contradicts itself and
lands on 0.5, which is the signal that the comparison is worthless.

## What Broke

Longer log in [LEARNING.md](LEARNING.md).

**The gate could not fail. Twice.** First, the CLI stored the current run before
resolving the baseline, so every run compared against itself and reported a delta
of exactly zero. After fixing that, a regression failed once and then passed,
because the failing run was still written to the baseline branch and became the
new bar. A gate that blocks a regression once and then blesses it is worse than no
gate, because the green check says everything is fine.

All 27 unit tests passed through both. They hand the comparison a baseline
directly, so they tested the arithmetic perfectly and never tested where the
baseline comes from. The second bug only appeared on the second run, so even a
single end-to-end check would have missed it.

**A migration that silently did nothing.** `create table if not exists` is
idempotent for creation, but on an existing table Postgres skips the entire
statement, new columns included. The test asserted that applying the file twice
does not crash, which was true and beside the point.

**The Gemini free tier does not behave like its documentation.** No `Retry-After`
header, with the delay stated only in prose inside the error body, so the client
fell back to a 5 second cooldown against a limit needing 49 and would have burned
quota indefinitely without succeeding. Token counts arrive split across fields
that do not add up, because reasoning tokens appear only in the total: a real
response read prompt 2, completion 9, total 197. And the configured model did not
exist for a new key at all.

## Run It

Requires [uv](https://docs.astral.sh/uv/) and a Postgres URL.

```bash
uv sync
cp .env.example .env      # add SHIPGATE_DB_URL, and GEMINI_API_KEY for the judge

# score a dataset
uv run python -m shipgate run --dataset datasets/support-intent.jsonl

# gate a change: compares against the baseline, exits 1 on regression
uv run python -m shipgate gate --dataset datasets/support-intent.jsonl

# watch it block
uv run python -m shipgate gate --dataset datasets/support-intent.jsonl \
  --target-label unknown

# hand-label for judge calibration
uv run python -m shipgate label --dataset datasets/support-intent.jsonl

# tests
uv run pytest && uv run ruff check .
```

The dashboard runs locally with `uv run uvicorn app.main:app --reload`.

Gating another project takes two files and one secret. See
[docs/ADOPTING.md](docs/ADOPTING.md).

## Scope

Not an experiment tracker, a labeling platform, a prompt registry, or a monitoring
system. It does not train models, and it never fixes a regression, it only refuses
to let one through. Full list of non-goals in [SPEC.md](SPEC.md).

The brief asked for ShipGate to gate Tollgate by end of build. Tollgate is built
five days later, so ShipGate gates itself instead and ships the adoption contract
each project follows on its own build day. Reasoning in
[DECISIONS.md](DECISIONS.md).
