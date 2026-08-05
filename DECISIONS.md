# Decisions

Every nontrivial choice gets an entry here at the time it is made, not
reconstructed later from memory. Newest entries at the top.

## Format

```
## YYYY-MM-DD: <short title>
**Context:** what problem or question forced a decision.
**Decision:** what was chosen.
**Alternatives considered:** what else was on the table, and why it lost.
**Consequences:** what this makes easier or harder later.
```

## 2026-08-05: Labels are pre-annotated by a second annotator, then adjudicated

**Context:** the calibration artifact needs human ground truth, and producing 100
labels unassisted is slow. A second annotator can produce candidates far faster,
but labels generated wholesale by a model would make the kappa a measure of
model-versus-model agreement while the README claimed judge-versus-human.

**Decision:** an independent annotator produces candidate intent labels with a
required borderline flag. Every disagreement and every borderline item is then
decided by hand. Pass/fail labels for calibration are produced by a human against
real model predictions. The provenance is stated in the README, because it changes
what the number means.

**Alternatives considered:** unassisted labeling, which is the strongest option and
was rejected on time; accepting generated labels as ground truth, which would make
the central claim false and collapse under the first "who labeled it?" question.

**Consequences:** the kappa measures agreement between the judge and a
human-adjudicated standard rather than a blank-slate annotator, which is weaker
and is disclosed as such. It caught two genuine dataset errors that unassisted
labeling would likely have preserved, since the same person who wrote the labels
was checking them. Agreement was 97 of 100, which is itself evidence the label
definitions are reproducible rather than idiosyncratic.

## 2026-08-05: Calibration labels judge real predictions, not a fixed answer

**Context:** the labeling session originally showed a constant prediction, because
the Milestone 0 target predicts the majority class for every item. That makes the
correct label mechanically derivable from whether the expected intent happens to
equal that constant.

**Decision:** a real classifier runs over the dataset first, and the human labels
whether each genuine prediction is acceptable.

**Alternatives considered:** labeling against the constant, which costs nothing and
produces a kappa near 1.0 that measures the judge's ability to compare two strings
rather than its judgement. It would have looked like a completed artifact.

**Consequences:** calibration costs 100 extra provider calls, which on a
20-per-window free tier is several minutes of waiting. In exchange the 24 items
marked genuinely ambiguous become the cases that actually determine the kappa,
which is the only version of this artifact that survives scrutiny.

## 2026-08-05: The dashboard is server-rendered with no frontend build

**Context:** the dashboard shows score over time per dataset and per slice. The
obvious reach is a small single-page app with a chart library.

**Decision:** one FastAPI route returning hand-written HTML with an inline SVG
sparkline. No template engine, no chart library, no build step.

**Alternatives considered:** a React or Next.js frontend, which is the default
choice and wrong here. The page has no interactivity, so a framework would add a
build pipeline, a deploy target, and a dependency tree to render a table and a
polyline. A chart library would also mean a CDN fetch on a cold free-tier
container, which is the worst moment to add a network round trip.

**Consequences:** the page has nothing external to load and works with JavaScript
disabled. The sparkline is pinned to a fixed 0-to-1 axis rather than auto-scaled,
because auto-scaling renders a flat 0.86 to 0.87 stretch as a dramatic cliff,
which is the exact misreading a drift chart must not encourage. Adding real
interactivity later means revisiting this, and that is the right time to.

## 2026-08-05: The judge model is an alias, and the run record is what pins it

**Context:** `gemini-2.0-flash` returns a quota error on a new free key and
`gemini-2.5-flash` is retired for new users. The working option is
`gemini-flash-latest`, which is an alias that currently resolves to
gemini-3.6-flash and will move without warning.

**Decision:** use `gemini-flash-latest`, and rely on the run record to pin what a
score means. Every run stores the model string, the rubric version, and the
dataset hash.

**Alternatives considered:** pinning a concrete version, which is what an eval
system should normally do, but every concrete free-tier version tested was either
exhausted or retired; using `gemini-3-flash-preview`, which works but carries no
stability promise either.

**Consequences:** the judge model can change under a stored baseline, and a silent
model change looks exactly like a regression. The mitigation is partial and worth
stating plainly: the alias is recorded, so a jump is visible in the run history,
but the alias does not resolve to a version in the API response, so the exact
model behind a historical score is not recoverable. If Gemini ships a stable
pinned free-tier model, switch to it and record the change here.

## 2026-08-05: Rate limit delays are parsed from the error body, not just the header

**Context:** Gemini returns 429 with no `Retry-After` header at all. The wait is
stated only in prose inside the JSON error, and separately in a
`google.rpc.RetryInfo` detail. Measured delays ranged from 20 to 49 seconds.

**Decision:** `parse_retry_after` falls through three sources in order: the
standard header, the RetryInfo detail, then a regex over the message text.

**Alternatives considered:** trusting the header alone, which is what the chassis
did. Without a value the throttle used its 5 second default, so a limit needing 49
seconds got retried after 5, failed, and consumed quota indefinitely without ever
succeeding. Hardcoding a longer default was rejected because it slows every
provider that reports honestly.

**Consequences:** the throttle now waits what the provider actually asks for,
verified at 20 seconds against a live 429. The parser has to tolerate Gemini
wrapping its error object in a single-element list and other providers returning
`error` as a bare string.

## 2026-08-05: Token counts are derived from total, not the itemised fields

**Context:** a real Gemini response reported prompt 2, completion 9, total 197.
The 186 missing tokens are reasoning tokens, billed but absent from both itemised
fields.

**Decision:** the gemini provider uses `total_aware_usage_parser`, which derives
output as total minus prompt whenever that exceeds the reported completion count.
Selected per provider in `quotas.yaml`.

**Alternatives considered:** reading the itemised fields, which undercounts by
roughly eighteen times and would make the benchmark table in M7 fiction.

**Consequences:** output tokens may be slightly overstated for providers that
report a padded total, which is the safe direction for a cost ceiling. This is
what the chassis `usage_parser` hook was designed for, and it earned itself here.

## 2026-08-04: Render start command calls the venv interpreter, not `uv run`

**Context:** deploys built successfully but Render reported "Port scan timeout
reached, no open ports detected" and kept serving the previous build, so new
routes never appeared while `/healthz` stayed green. The build log showed uvicorn
starting and binding port 10000, which made it look like a Render fault.

**Decision:** the start command is
`.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT`.

**Alternatives considered:** `uv run uvicorn ...`, which is what the chassis
Dockerfile uses and what the backlog assumed. `uv run` re-resolves and syncs the
project before executing, so on a cold container it either exceeds the port-scan
window or fails outright because uv was installed during build and is not
guaranteed to be on the runtime PATH.

**Consequences:** the runtime no longer depends on uv at all, only on the `.venv`
the build produced. This applies to every project in the portfolio that deploys a
Python service to Render, so it belongs in the chassis rather than only here.

## 2026-08-04: Pairwise comparisons are judged twice, in both orders

**Context:** LLM judges have a strong, well-documented preference for whichever
candidate appears first. A single-order pairwise eval largely measures ordering
rather than quality.

**Decision:** every pair is judged twice with positions swapped and the two
verdicts averaged. A consistent judge scores 1.0 or 0.0, while a purely
position-biased judge contradicts itself and scores 0.5. `position_bias_rate`
reports the fraction of comparisons that flipped.

**Alternatives considered:** judging once and accepting the bias, which is
cheaper but produces a number that cannot be defended; randomising the order per
item, which spreads the bias across the dataset instead of measuring it, so the
aggregate looks reasonable while every individual comparison stays untrustworthy.

**Consequences:** pairwise runs cost two judge calls per item, which matters on a
15 rpm free tier. In exchange the position bias becomes an observable number, and
a high rate invalidates the run rather than silently favouring a side.

## 2026-08-04: ShipGate self-gates; wiring the gated projects moves to their own backlogs

**Context:** the brief requires ShipGate to be "wired to at least Tollgate by end
of build." The portfolio schedule builds ShipGate on Aug 5 to 7 and Tollgate on
Aug 10 to 11. Tollgate will not exist during ShipGate's build window, so the
requirement is unbuildable as written.

**Decision:** ShipGate proves the gate by gating its own pull requests, which is
what Proof Artifact 2 needs anyway. Its integration deliverable becomes a
published consumer contract: a `shipgate.yaml` template, the action pinned to a
tag, and an adoption guide. Wiring each gated project is a task in that project's
backlog on its own build day.

**Alternatives considered:** building a throwaway stub of Tollgate purely to have
something to gate, which would prove nothing and duplicate work due six days
later; delaying ShipGate until after Tollgate, which inverts the dependency,
since Tollgate's own proof artifact is "quality-scored by ShipGate" and therefore
needs ShipGate to already exist.

**Consequences:** ShipGate ships self-gated and provably working, with adoption
by the other five projects spread across their build days. Anyone reading the
brief will notice the deviation, so it is stated in the README rather than left
to be discovered.

## 2026-08-04: The eval dataset is support-ticket intent, not a Tollgate-specific set

**Context:** M1.5 called for a Tollgate eval dataset. Tollgate is a quota-aware
LLM gateway whose own proof artifact is quality scoring by ShipGate, so its eval
set is a general answer-quality set that only makes sense once the gateway
exists and can be routed through.

**Decision:** build `datasets/support-intent.jsonl`: 100 code-mixed customer
support tickets labeled with intent, sliced by intent, language, and length.

**Alternatives considered:** waiting for Tollgate, which blocks judge calibration
and self-gating on a project six days out; a generic open-ended quality set,
which cannot be scored by the exact runner and gives the judge nothing objective
to calibrate against.

**Consequences:** the dataset does triple duty. It gives ShipGate something real
to self-gate on now, it is the hand-labeling set for judge calibration in M5, and
it is the same domain Nishana fine-tunes on for Aug 24 to 25, so the labeling
effort is reused rather than spent once. Classes are balanced at 25 each, which
keeps the majority-class baseline honest at 0.25 and stops a constant predictor
from looking good for free.

## 2026-08-04: `diff` compares two dataset files, not two hashes

**Context:** BACKLOG.md M1.4 specified `shipgate diff <hashA> <hashB>`. The
`datasets` table stores a version's metadata (hash, item count, slice counts) but
not the items themselves, so a hash cannot be expanded back into content. The
command as written is not implementable without also storing every item of every
version.

**Decision:** `shipgate diff --before <file> --after <file>` compares two dataset
files on disk. The diff reports both hashes so the output still ties back to
whatever the run store recorded.

**Alternatives considered:** storing full item content per version in Postgres,
which would make hash-to-hash diffing work but duplicates data that already lives
in git, and grows the free-tier database for a convenience feature; reconstructing
old versions by shelling out to `git show`, which is a nice future addition but
assumes the dataset was always committed at that path.

**Consequences:** the practical workflow is `git show <rev>:path > /tmp/before.jsonl`
then diff against the working copy. Diffing two arbitrary historical hashes is
not possible today. If that becomes necessary, storing items per version is the
change to make, and it should be a deliberate decision about database size rather
than an accident.

## 2026-08-04: Database tests run against Neon, not a local container

**Context:** the store layer needs tests that actually exercise Postgres. The
chassis ships a docker-compose with postgres, but Docker is not installed on the
development machine, so the local container is not an option today.

**Decision:** database-backed tests read `SHIPGATE_DB_URL` and skip themselves
when it is absent. They run against the real Neon instance, either wrapping
writes in a transaction that is rolled back, or deleting the row in a `finally`
block.

**Alternatives considered:** installing Docker (slow, and Render plus Neon are
the real deploy targets anyway, so a local container would be testing a
different thing); sqlite (diverges from Postgres on jsonb, which the `slices`
column depends on); mocking psycopg (would not have caught the jsonb
serialization detail this setup surfaced immediately).

**Consequences:** tests need network and are slower, roughly 15 seconds for the
store suite. A fork without the secret still gets a green run, because the tests
skip rather than fail. Test rows must always be cleaned up, since the database is
shared with real runs.

## 2026-08-04: Invoke the CLI as `python -m shipgate`, not a console script

**Context:** the backlog assumed `uv run shipgate run ...`, which needs an
installed console-script entry point. The chassis sets `[tool.uv] package =
false`, because these projects are applications rather than published libraries.

**Decision:** ship `shipgate/__main__.py` and invoke as
`uv run python -m shipgate run ...`.

**Alternatives considered:** setting `package = true` and adding a build backend
to get a real console script. Rejected because it adds packaging machinery to
all 11 projects to save eight characters at the command line, and the GitHub
Action invokes the module form regardless.

**Consequences:** a slightly longer command. The chassis convention stays
intact, so every forked project behaves the same way. SPEC.md and BACKLOG.md
need their example commands kept in sync with this.

## 2026-08-04: The Milestone 0 target is a majority-class baseline, not a mock

**Context:** the walking skeleton needs something to evaluate before any real
model or gated project exists.

**Decision:** `StubTarget` always predicts the majority class (`billing`). On
the 5-row smoke dataset that scores exactly 0.60.

**Alternatives considered:** a mock returning canned correct answers, which
would score 1.00 and prove nothing; wiring a real provider immediately, which
needs an API key and the rate-limit strategy that Milestone 2 has not built yet.

**Consequences:** the walking skeleton produces a number that means something.
Majority-class is the baseline any real model has to beat, and the per-slice
output already shows the exact failure mode the gate exists to catch: 0.60
overall while `intent:technical` and `intent:account` sit at 0.00.

<!-- Add entries above this line. -->
