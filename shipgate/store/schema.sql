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
    trigger           text        not null default 'manual',
    started_at        timestamptz not null default now(),
    finished_at       timestamptz
);

-- Baseline resolution (M4.1) looks up the latest run for a dataset hash,
-- so that lookup gets its own index rather than scanning the table.
create index if not exists runs_baseline_idx
    on runs (dataset_id, dataset_hash, started_at desc);
