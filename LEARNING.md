# Learning Log

Things that turned out surprising, wrong, or expensive - so the next project in
the portfolio doesn't relearn them the hard way. Newest entries at the top.

## Format
```
## YYYY-MM-DD: <short title>
**What happened:** the surprise, bug, or wrong assumption.
**Root cause:** why it actually happened.
**Fix / takeaway:** what changed, and what to do differently next time.
```

## 2026-08-04: "Idempotent migration" is not the same as "handles schema change"

**What happened:** adding an `error_count` column to the `runs` block in
`schema.sql` worked on a fresh database and broke every insert on the real one.
`UndefinedColumn: column "error_count" of relation "runs" does not exist`.

**Root cause:** the whole schema is written as `create table if not exists`,
which I had been calling idempotent and testing as such. It is idempotent, but
only for creation. On a database where the table already exists, Postgres skips
the entire statement, new columns included, without any warning. The migration
test passed the whole time because it only ever asserted that running the file
repeatedly does not error, which was true and also beside the point.

**Fix and takeaway:** additive columns need an explicit
`alter table ... add column if not exists`. More usefully, the test I had was
measuring the wrong property. "Applying this twice does not crash" is much weaker
than "applying this to an existing database converges to the intended shape", and
only the second one is what a migration has to guarantee. Worth remembering for
every project in the portfolio that carries a schema forward.

## 2026-08-04: The gate was structurally incapable of failing, twice over

**What happened:** the gate passed a change that scored 0.000 against a baseline
of 0.250. Twice, for two unrelated reasons, and both times the unit tests were
green.

**Root cause, first bug:** the CLI stored the current run and *then* resolved the
baseline. The freshly inserted run matched every filter, so the gate compared each
run against itself. Delta was exactly `+0.000` on every single run, forever.

**Root cause, second bug:** after fixing that, the regression failed correctly the
first time, then passed on the second attempt. A failing run was still written to
the baseline branch, so it became the bar. The gate blocked a regression exactly
once and then treated it as the new normal, which is worse than having no gate,
because the green check says everything is fine.

**Fix:** resolve the baseline before inserting, pass `exclude_run_id` as a second
guard, record the verdict on the run, and require `verdict is null or 'pass'` for
a run to qualify as a baseline.

**Takeaway:** all 27 unit tests passed throughout both bugs, because every one of
them hands `evaluate()` a baseline dictionary directly. They tested the comparison
correctly and never tested *where the baseline comes from*. The bug lived entirely
in the wiring between two well-tested pieces. Only running the real command
against the real database found it, and the second bug only appeared on the
*second* run, so even one end-to-end check would have missed it. When a component
decides whether other things are correct, test it by trying to make it wrong,
repeatedly, not just by checking that its arithmetic works.

<!-- Add entries above this line. -->
