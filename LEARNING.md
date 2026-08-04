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

<!-- Add entries above this line. -->
