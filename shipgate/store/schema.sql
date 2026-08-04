-- ShipGate run store. Idempotent: safe to apply on every startup.
-- Milestone 3 extends this with datasets, run_items, and labels.

-- One row per dataset version. The composite primary key is the versioning
-- mechanism: re-registering an unchanged dataset conflicts and does nothing,
-- while any content change produces a new hash and therefore a new row. Old
-- versions are never overwritten, so a historical score stays interpretable.
create table if not exists datasets (
    dataset_id        text        not null,
    dataset_hash      text        not null,
    path              text        not null,
    n                 integer     not null,
    slice_counts      jsonb       not null default '{}'::jsonb,
    registered_at     timestamptz not null default now(),
    primary key (dataset_id, dataset_hash)
);

-- Per-item results, keyed by a hash of every input that can change the outcome:
-- dataset hash, item id, runner fingerprint (model and rubric version included),
-- and target fingerprint. CI containers start empty, so this has to be shared
-- storage or a judge run pays full rate-limited price on every pull request.
create table if not exists result_cache (
    cache_key         text        primary key,
    payload           jsonb       not null,
    created_at        timestamptz not null default now()
);

create table if not exists runs (
    run_id            text primary key,
    dataset_id        text        not null,
    dataset_hash      text        not null,
    git_sha           text,
    runner            text        not null,
    model             text,
    n                 integer     not null,
    score             double precision not null,
    slices            jsonb       not null default '{}'::jsonb,
    cost_usd          double precision,
    p50_latency_ms    double precision,
    cache_hit_rate    double precision,
    -- Errors score 0, so a run with many of them looks like a regression. The
    -- gate reads this alongside the score to tell a broken judge from a bad model.
    error_count       integer     not null default 0,
    trigger           text        not null default 'manual',
    started_at        timestamptz not null default now(),
    finished_at       timestamptz
);

-- `create table if not exists` is idempotent for creation but does nothing for
-- evolution: on a database where `runs` already exists, a new column in the
-- block above is silently ignored and every insert then fails on the missing
-- column. Additive columns therefore need an explicit alter. Keep these
-- `if not exists` and additive-only, so applying the file to any database, fresh
-- or existing, converges to the same shape.
alter table runs add column if not exists error_count integer not null default 0;

-- Baseline resolution (M4.1) looks up the latest run for a dataset hash,
-- so that lookup gets its own index rather than scanning the table.
create index if not exists runs_baseline_idx
    on runs (dataset_id, dataset_hash, started_at desc);

-- Per-item outcomes for a run. The gate needs these to name the worst
-- newly-failing examples, which is the difference between "score dropped 4
-- points" and a message someone can act on.
create table if not exists run_items (
    run_id            text        not null references runs (run_id) on delete cascade,
    item_id           text        not null,
    output            text,
    score             double precision not null,
    slices            jsonb       not null default '[]'::jsonb,
    latency_ms        double precision,
    cache_hit         boolean     not null default false,
    error             text,
    meta              jsonb       not null default '{}'::jsonb,
    primary key (run_id, item_id)
);

-- Hand labels, the ground truth the judge is calibrated against.
-- Keyed on dataset_hash as well as item id on purpose: a label describes an item
-- as it was when a human read it. If the dataset changes, the old labels must not
-- silently carry over to text nobody actually reviewed.
create table if not exists labels (
    dataset_id        text        not null,
    dataset_hash      text        not null,
    item_id           text        not null,
    label             text        not null,
    notes             text,
    labeled_at        timestamptz not null default now(),
    primary key (dataset_id, dataset_hash, item_id)
);
