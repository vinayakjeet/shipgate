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
