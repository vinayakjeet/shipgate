# ShipGate: Build Plan

> **Funded at full scope: ~35h (~4.5 days).** No cuts at plan time. Every MUST
> in the brief is scheduled, including all three runner types, parallelism, the
> dashboard, and nightly cron. [The Cut Line](#the-cut-line) is insurance only:
> it triggers if we cross ~45h (30% over), and it names what dies first, in
> order.

Tasks come only from this file (see CONTRIBUTING.md). Check boxes off at the end
of each session. Every task is 90 minutes or less.

Repo: `vinayakjeet/shipgate` (public). Deploy: Render free plus Neon free.
CI: GitHub Actions, free and unmetered on public repos.

---

## Milestone 0: Walking Skeleton (3h)

Goal: the thinnest end-to-end path. Five JSONL rows scored, written to Neon,
visible on a deployed Render URL, run by a GitHub Action. Ugly is fine.

- [x] **M0.1 [MUST] (45m)** Neon project, `runs` table, `SHIPGATE_DB_URL` wired.
  *Acceptance:* the migration applies to Neon and to local postgres; one row
  inserts and reads back from both.
  *Test:* `tests/store/test_connection.py::test_insert_and_read_run`
- [x] **M0.2 [MUST] (60m)** `shipgate run` CLI: reads a 5-row JSONL, exact-match
  runner against a stub target, prints a score, writes one `runs` row.
  *Acceptance:* `uv run python -m shipgate run --dataset fixtures/smoke.jsonl` prints
  `score=0.60 n=5` and the row appears in Neon.
  *Test:* `tests/cli/test_run_smoke.py::test_run_writes_one_row`
- [ ] **M0.3 [MUST] (45m)** Deploy the FastAPI app to Render free with
  `GET /api/runs` reading Neon.
  *Acceptance:* the public URL returns the M0.2 run as JSON in a clean browser.
  *Test:* `tests/app/test_runs_endpoint.py::test_runs_returns_rows` plus manual.
  *Status:* code merged, tested locally, and `/healthz` is live (21.6s cold
  start). Render is still serving the pre-walking-skeleton build, so `/api/runs`
  returns 404. Blocked on a successful Render deploy of the current commit.
- [x] **M0.4 [MUST] (30m)** GitHub Action workflow that runs M0.2 on PR and
  echoes the score into the job log.
  *Acceptance:* a throwaway PR shows the score in the Actions log.
  *Test:* the PR itself.
  *Verified:* PR #1 ran the eval, printed `score=0.60 n=5` in the Actions log,
  and wrote run `r_d53c8908fde64faa` to Neon.

Demo Checkpoint: *"A PR triggers an eval run, the score lands in Neon, and I can
read it from a public URL."*

Learning Checkpoint, concept: **offline evals vs online monitoring.**
1. What class of failure can this walking skeleton catch that production
   monitoring never will, and the reverse?
2. Why does the run row store `dataset_hash` and `git_sha` separately? What
   breaks if you keep only one?

---

## Unscheduled

Filed rather than done, to keep milestone scope honest.

- [ ] **[SHOULD] (15m)** Remove the inherited `/demo/chat` route and its tests.
  It is chassis scaffolding with no role in ShipGate, and it ships in the public
  deploy. Do this in Milestone 7 with the rest of the polish.

---

## Milestone 1: Dataset registry and versioning (4h)

Goal: datasets are content-hashed, sliced, tagged, and diffable, so comparing
across two different datasets is impossible by construction.

- [x] **M1.1 [MUST] (60m)** Dataset schema and loader (`id`, `input`,
  `expected`, `slices`, `meta`), strict validation, clear error on a bad line.
  *Acceptance:* a malformed line fails with file and line number, not a stack
  trace.
  *Test:* `tests/datasets/test_loader.py::{test_valid,test_malformed_line_message}`
- [x] **M1.2 [MUST] (45m)** Content hashing and `manifest.yaml` (id, path, hash,
  n, slice counts, created_at).
  *Acceptance:* reordering lines does not change the hash. Editing any field
  does.
  *Test:* `tests/datasets/test_hash.py::{test_order_invariant,test_edit_changes_hash}`
- [x] **M1.3 [MUST] (45m)** `shipgate register` writes the dataset row to Neon
  and refuses to silently overwrite an existing hash.
  *Acceptance:* re-registering a changed dataset creates a new version row.
  *Test:* `tests/datasets/test_registry.py::test_rehash_creates_new_version`
- [x] **M1.4 [SHOULD] (45m)** `shipgate diff --before <file> --after <file>`:
  items added, removed, changed. Compares files rather than hashes, because the
  store keeps version metadata and not item content (see DECISIONS.md).
  *Acceptance:* correct counts on a fixture pair.
  *Test:* `tests/datasets/test_diff.py::test_added_removed_changed`
- [x] **M1.5 [MUST] (45m)** Build the real eval dataset:
  `datasets/support-intent.jsonl`, 100 code-mixed support tickets, sliced by
  intent, language, and length. Targets support-ticket intent rather than
  Tollgate, which does not exist until Aug 10 (see DECISIONS.md). Same domain
  Nishana fine-tunes on, so the labeling effort is reused.
  *Acceptance:* loads clean, every slice has at least 10 items.
  *Verified:* 100 rows, intents balanced 25 each, smallest slice `lang:hi` at 12.
  Majority-class baseline scores 0.25.
  *Test:* `tests/datasets/test_support_dataset.py::test_slices_populated`

Demo Checkpoint: *"Datasets are versioned by content hash, I can diff two
versions, and the system refuses to compare across them."*

Learning Checkpoint, concept: **eval set rot.**
1. Name three ways this dataset silently stops measuring what you think it
   measures, six weeks from now.
2. What is your refresh policy, concretely? What triggers a new version, and
   what happens to the baseline when it does?

---

## Milestone 2: Runners (6.5h)

Goal: three runner types sharing one protocol, with caching, backoff, and
parallelism, built on the chassis `llm/` client rather than reinvented.

- [ ] **M2.1 [MUST] (45m)** `Runner` protocol and `ExactMatchRunner` (normalized
  string compare), per-item result records.
  *Acceptance:* score and per-item pass/fail correct on a fixture.
  *Test:* `tests/runners/test_exact.py::{test_scoring,test_normalization}`
- [x] **M2.2 [MUST] (90m)** `JudgeRunner` via Gemini free using the chassis
  `llm.ChatClient`: rubric prompt, structured verdict, `rubric_version` recorded.
  *Acceptance:* returns a bounded score per item. A malformed judge response is
  retried, then recorded as `judge_error`, never silently scored 0.
  *Test:* `tests/runners/test_judge.py::{test_verdict_parsing,test_malformed_response_is_error}`
- [x] **M2.3 [MUST] (75m)** Result cache keyed by
  `(dataset_hash, item_id, runner, model, rubric_version, target_sha)`.
  *Acceptance:* a second identical run makes zero provider calls and reports
  `cache_hit_rate == 1.0`.
  *Test:* `tests/runners/test_cache.py::test_second_run_makes_no_calls`
- [ ] **M2.4 [MUST] (60m)** Free-tier backoff: reuse `llm/throttle.py` and
  `llm/retry.py` so a 429 storm completes without dropping items.
  *Acceptance:* simulated 429s on 30% of calls still yields n=100 results.
  *Test:* `tests/runners/test_backoff.py::test_no_items_dropped_under_429s`
- [ ] **M2.5 [MUST] (60m)** Bounded parallelism (asyncio semaphore) sized to the
  provider's rpm. The brief lists parallelism under MUST.
  *Acceptance:* 100 items complete measurably faster with no added 429 failures.
  *Test:* `tests/runners/test_parallel.py::test_concurrency_bound_respected`
- [ ] **M2.6 [MUST] (60m)** `PairwiseRunner` (A/B preference against baseline
  output) with position-bias swap. Third of the three required runner types.
  *Acceptance:* swapping A and B does not change aggregate preference beyond
  noise.
  *Test:* `tests/runners/test_pairwise.py::test_position_bias_swap`

Demo Checkpoint: *"I can score a 100-item dataset three different ways, twice,
and the second run is nearly free."*

Learning Checkpoint, concept: **how I know my judge is right.**
1. Your judge scores 0.86. What would have to be true for that number to be
   meaningless, and how would you detect it?
2. Why does the cache key include `rubric_version`? What silently corrupts if it
   does not?

---

## Milestone 3: Run store and dashboard (5h)

Goal: every run is queryable, sliceable, and plotted over time.

- [ ] **M3.1 [MUST] (60m)** Full schema: `datasets`, `runs`, `run_items`,
  `labels`, plus indices for the dashboard queries.
  *Acceptance:* migration applies clean to an empty Neon database.
  *Test:* `tests/store/test_schema.py::test_migration_idempotent`
- [ ] **M3.2 [MUST] (60m)** Score aggregation: overall, per slice, cost, p50 and
  p95 latency, cache-hit rate, computed once and stored on the run.
  *Acceptance:* aggregates match hand-computed values on a fixture run.
  *Test:* `tests/store/test_aggregation.py::test_slice_and_latency_math`
- [ ] **M3.3 [MUST] (60m)** `GET /api/runs` and `GET /api/runs/{id}` per the
  SPEC.md contract.
  *Acceptance:* response matches the documented shape exactly.
  *Test:* `tests/app/test_api_contract.py::test_runs_response_shape`
- [ ] **M3.4 [MUST] (90m)** Server-rendered dashboard: score over time per
  dataset, per-slice table, cost and latency columns. No SPA (SPEC.md A6).
  *Acceptance:* loads in a clean browser within 3s warm, readable on mobile.
  *Test:* `tests/app/test_dashboard.py::test_renders_with_runs`
- [ ] **M3.5 [SHOULD] (45m)** Seed script generating 30 synthetic
  historical runs so the dashboard has a realistic trend line for the video.
  *Acceptance:* dashboard shows a plausible 30-point series.
  *Test:* `tests/store/test_seed.py::test_seed_creates_runs`

Demo Checkpoint: *"A public dashboard shows score over time per dataset, broken
down by slice, with cost and latency per run."*

Learning Checkpoint, concept: **offline evals vs online monitoring split.**
1. Which of these numbers belongs in ShipGate and which belongs in Spanlight,
   and what rule decides?
2. Why store aggregates on the run row instead of computing them per request?

---

## Milestone 4: The CI gate (4.5h)

Goal: a change that regresses gets blocked, with output that explains why.

- [ ] **M4.1 [MUST] (60m)** Baseline resolution: latest successful run on `main`
  for the same `dataset_hash`, explicit `baseline-invalid` when absent.
  *Acceptance:* S4 from SPEC.md holds. A changed hash refuses comparison.
  *Test:* `tests/gate/test_baseline.py::{test_resolves_latest,test_hash_mismatch_invalid}`
- [ ] **M4.2 [MUST] (60m)** Threshold logic: overall and per-slice guards,
  verdict `pass | fail | baseline-invalid`.
  *Acceptance:* S1, S3, and S7 from SPEC.md all hold on fixtures.
  *Test:* `tests/gate/test_threshold.py::{test_fails_over,test_passes_within_noise,test_slice_guard}`
- [ ] **M4.3 [MUST] (75m)** Verdict renderer: delta table, per-slice breakdown,
  3 worst newly-failing examples, cost and latency. Written to the Actions job
  summary, and reusable as a PR comment body for any repo that uses PRs.
  *Acceptance:* the summary is legible and explains why the gate failed without
  opening the logs.
  *Test:* `tests/gate/test_comment.py::{test_renders_markdown,test_names_failing_slice}`
- [ ] **M4.4 [MUST] (60m)** Package as a composite GitHub Action with the
  SPEC.md inputs and outputs, non-zero exit on `fail`.
  *Acceptance:* a deliberately regressed commit in this repo turns the check red.
  *Test:* the red check is the test. **Capture it, this is Proof Artifact 2.**
- [ ] **M4.5 [MUST] (45m)** Self-gate: every push to this repo runs the action.
  *Acceptance:* pushes show a ShipGate check, green when the score holds and red
  when it drops past threshold.
  *Test:* manual, one green push and one deliberately regressed push.

Demo Checkpoint: *"I can push a change that makes the model worse and watch the
gate turn red and tell me exactly which slice broke."*

Learning Checkpoint, concept: **eval noise vs real regression.**
1. How did you pick 2%? What measurement would justify changing it?
2. A flaky judge fails one PR in ten at random. How would you distinguish that
   from a real intermittent regression?

---

## Milestone 5: Judge calibration, the Proof Artifact (5h)

Goal: prove the judge agrees with a human, and show the rubric tuning that got
it there. This milestone is the point of the project.

- [ ] **M5.1 [MUST] (45m)** `shipgate label` CLI: serves unlabeled items one at
  a time, writes `labels` JSONL plus Neon rows, resumable mid-session.
  *Acceptance:* killing the process loses no labels.
  *Test:* `tests/calibration/test_label_cli.py::test_resume_after_interrupt`
- [ ] **M5.2 [MUST] (90m)** Hand-label ~100 Tollgate examples. **Your time, and
  only yours** (SPEC.md D3). Two sittings, blind to judge output.
  *Acceptance:* 100 labeled items committed.
  *Test:* `tests/calibration/test_labels.py::test_100_labels_present`
- [ ] **M5.3 [MUST] (45m)** Cohen's kappa, raw agreement, confusion matrix.
  *Acceptance:* kappa matches a hand-computed value on a known fixture.
  *Test:* `tests/calibration/test_kappa.py::{test_known_fixture,test_perfect_and_chance}`
- [ ] **M5.4 [MUST] (75m)** Measure rubric v1 kappa, diagnose the
  disagreements, write rubric v2 (criteria-split), re-measure.
  *Acceptance:* v2 kappa >= 0.6 (SPEC.md A4), or a written explanation of why
  not and what you would change next.
  *Test:* `tests/calibration/test_rubric_versions.py::test_v2_beats_v1`
- [ ] **M5.5 [MUST] (45m)** Publish the calibration table (kappa before and
  after, n, agreement %) into README. This is Proof Artifact 1.
  *Acceptance:* table in README with real numbers, no placeholders.
  *Test:* `tests/docs/test_readme.py::test_calibration_table_has_no_tbd`
- [ ] **M5.6 [MUST] (30m)** Measure run-to-run judge variance (same input, 5
  runs) and set the real threshold from it, replacing the A3 placeholder.
  *Acceptance:* thresholds in `shipgate.yaml` justified by measured variance
  recorded in DECISIONS.md.
  *Test:* `tests/calibration/test_variance.py::test_variance_measured`

Demo Checkpoint: *"I can prove my judge agrees with me at kappa 0.7, show the
rubric change that got it there from 0.4, and my threshold is derived from
measured noise rather than vibes."*

Learning Checkpoint, concept: **judge validity.**
1. Kappa 0.7 on 100 examples. What is the confidence interval, and does it
   change your decision?
2. Your labels are single-annotator. What bias does that introduce, and how
   would you detect it with a second annotator?

---

## Milestone 6: Cron and Tollgate wiring (3h)

Goal: ShipGate runs unattended nightly and is adoptable by the other projects.

- [ ] **M6.1 [MUST] (60m)** Publish the consumer contract: a `shipgate.yaml`
  template, the composite action pinned to a tag, and a short adoption guide.
  Tollgate does not exist until Aug 10, so wiring each gated project is a task in
  that project's own backlog rather than this one (see DECISIONS.md).
  *Acceptance:* a second repo can adopt the gate by copying one config file and
  one workflow block. Proven by ShipGate self-gating in M4.5.
  *Test:* `tests/gate/test_action_contract.py::test_template_matches_action_inputs`
- [ ] **M6.2 [MUST] (45m)** Nightly cron workflow across all registered
  datasets, writing runs tagged `trigger=cron`. The brief lists nightly cron
  under MUST, and it is free on a public repo.
  *Acceptance:* two consecutive green nights.
  *Test:* `tests/gate/test_cron_config.py::test_schedule_valid`
- [ ] **M6.3 [SHOULD] (45m)** Failure notification (open a GitHub issue on cron
  failure) so a silent nightly break is visible.
  *Acceptance:* a forced failure opens exactly one issue, not one per dataset.
  *Test:* `tests/gate/test_notify.py::test_single_issue_per_failure`
- [ ] **M6.4 [MUST] (30m)** Spanlight instrumentation: emit `shipgate.run`,
  `shipgate.item`, and `shipgate.judge` spans per the SPEC.md attribute table.
  *Acceptance:* spans visible in Grafana with correct attributes.
  *Test:* `tests/otel/test_spans.py::test_span_attributes`

Demo Checkpoint: *"ShipGate gates its own PRs, runs itself every night, any
repo can adopt it by copying one file, and I can see the traces in Grafana."*

Learning Checkpoint, concept: **refresh policy in practice.**
1. The nightly run drifts down 3% over two weeks with no code change. What are
   your top three hypotheses, in order?
2. When cron fails at 2am on the free tier, how do you tell infrastructure
   failure from real regression without opening the logs?

---

## Milestone 7: Ship (4h)

Goal: it exists publicly, it is explicable in 90 seconds, and a stranger can
verify it.

- [ ] **M7.1 [MUST] (60m)** README: Problem, Architecture, Benchmarks, Technical
  Decisions, What Broke, Run It, all filled.
  *Acceptance:* no TODO placeholders remain.
  *Test:* `tests/docs/test_readme.py::test_no_todo_placeholders`
- [ ] **M7.2 [MUST] (45m)** Capture Proof Artifact 2: video or screenshot of the
  gate failing on a real regressing commit, embedded in README.
  *Acceptance:* the red check, the score delta, and the failing slice are all
  legible.
  *Test:* manual review at 100% zoom.
- [ ] **M7.3 [MUST] (45m)** Benchmark table: score, cost, latency, and cache-hit
  per runner type across the Tollgate dataset.
  *Acceptance:* real measured numbers in README.
  *Test:* `tests/docs/test_readme.py::test_benchmark_table_present`
- [ ] **M7.4 [MUST] (45m)** DECISIONS.md complete: threshold choice, rubric
  design, baseline rule, why no SPA, single-annotator caveat.
  *Acceptance:* at least 5 entries in the repo's decision format.
  *Test:* `tests/docs/test_decisions.py::test_min_entries`
- [ ] **M7.5 [MUST] (30m)** Export the architecture diagram to PNG or
  SVG from the SPEC.md mermaid, polish docs prose.
  *Acceptance:* diagram renders in README on github.com.
  *Test:* visual check on the rendered README.
- [ ] **M7.6 [MUST] (45m)** Six-bullet video script plus clean-browser deploy
  verification (incognito, cold Render and Neon, timed).
  *Acceptance:* script written, cold-start time recorded honestly in README.
  *Test:* manual, incognito.

Demo Checkpoint: *"I can hand someone a URL and a 90-second video and they
understand why this project matters."*

Learning Checkpoint, concept: **the whole system.**
1. Answer all four interview checkpoints from SPEC.md end to end, without notes.
2. Where would this break first at 100x the dataset size, and what would you
   change?

---

## Risk register

| # | Risk | Mitigation |
|---|---|---|
| R1 | Free-tier rate limits (Gemini 15 rpm) make a 100-item judge run take 7+ minutes, so PR feedback gets too slow to be useful. | Caching (M2.3) makes re-runs near-free. Bounded parallelism (M2.5) is sized to rpm. If still slow, gate on a 40-item smoke slice on PRs and run the full set nightly. |
| R2 | Cold starts. Render free spins down and Neon autosuspends, so the first dashboard hit after idle takes 30s or more and looks broken in the demo video. | Accepted, not engineered around (SPEC.md A5). Warm both immediately before recording, and document the real cold-start time in README rather than hiding it. |
| R3 | Judge kappa lands below 0.6 even after tuning, so the gate is not trustworthy. | M5.4 has a written fallback: narrow the task to binary pass/fail, split the rubric into independent criteria, or restrict gating to the exact-match runner and report the judge as advisory only. A failed calibration honestly reported is still a strong artifact. |
| R4 | A flaky gate blocks good PRs, so I start ignoring or bypassing the check, which kills the project's premise. | Thresholds derived from measured variance (M5.6) rather than guessed. `baseline-invalid` is a distinct verdict from `fail`. The per-slice guard prevents average-washing. |
| R5 | Cross-repo integration friction. Secrets, permissions, and the target contract with Tollgate eat hours budgeted for evals. | M6.1 is timeboxed to 60 minutes. The action talks HTTP only, no shared library. If Tollgate is not ready, gate ShipGate against itself (M4.5). The Proof Artifact does not depend on Tollgate existing. |

---

## The Cut Line

Not active. Full scope is funded (~35h). This triggers only if we cross ~45h
(30% over). Cuts in order, first to die at the top. Both Proof Artifacts and the
CI gate survive every scenario, because without those there is no project.

| Order | Cut | Saves | Cost of cutting |
|---|---|---|---|
| 1 | M3.5 synthetic seed data [SHOULD] | 0.75h | Trend line looks sparse in the video. |
| 2 | M6.3 cron failure notifications [SHOULD] | 0.75h | Silent nightly breakage. Acceptable over 28 days. |
| 3 | M1.4 dataset diff [SHOULD] | 0.75h | The hash still prevents bad comparisons, you just cannot see what changed. |
| 4 | M2.6 PairwiseRunner becomes advisory only, ungated | 1.0h | Two gating runner types instead of three. The brief lists three, so this is a real deviation. Flag it in README. |
| 5 | M3.4 dashboard becomes a static table, no chart | 1.5h | The README benchmark table carries the proof instead. |
| 6 | M2.5 parallelism becomes sequential | 1.0h | Runs get slower. Caching still keeps re-runs cheap. |
| 7 | M6.2 nightly cron | 0.75h | Loses drift-over-time. Cut only under real duress, it is cheap and demos well. |
| 8 | M5.2 labels drop from 100 to 60 | 0.5h | Wider kappa confidence interval. Last resort, it directly weakens Proof Artifact 1. |

Never cut: M4 (the gate), M5.3 through M5.5 (kappa and the calibration table),
M7.2 (blocked-PR capture). Those three are the project.

STRETCH, online judge sampling of production traces, remains unscheduled. It
only makes sense once Spanlight is collecting real traffic. Revisit after the
portfolio's tracing project lands.

---

## Inherited from the chassis

Completed in the foundation template this repo forked from. Kept for history,
not active work.

- [x] FastAPI app factory, `/healthz` and `/version`, pydantic-settings, JSON logging
- [x] `llm/` client: retry with backoff, 429 throttle, provider registry, cost
      logging. Reused directly by M2.2 and M2.4.
- [x] `otel_bootstrap.py`, OTLP export gated on env. Reused by M6.4.
- [x] Dockerfile and docker-compose (postgres/pgvector plus redis)
- [x] GitHub Actions CI (ruff and pytest), reusable deploy stub
- [x] Docs skeleton and contributor conventions
