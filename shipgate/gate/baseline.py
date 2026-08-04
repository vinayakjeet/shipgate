from __future__ import annotations

import psycopg


def resolve_baseline(
    conn: psycopg.Connection,
    dataset_id: str,
    dataset_hash: str,
    baseline_ref: str = "main",
    max_error_rate: float = 0.10,
    exclude_run_id: str | None = None,
) -> dict | None:
    """The most recent trustworthy run on the baseline branch for this exact
    dataset version.

    Three filters, each earning its place:

    - `dataset_hash` must match exactly. Comparing scores across dataset versions
      is the single easiest way to make a gate lie, because an edited dataset
      moves the score for reasons that have nothing to do with the code.
    - `git_ref` must be the baseline branch. Comparing against whatever ran last
      would mean a bad experimental commit becomes the standard everything else
      is measured against.
    - The run must be clean. A baseline where a tenth of the items never scored
      is a low number for infrastructure reasons, and adopting it as the bar
      would quietly let real regressions through afterwards.
    - The run must not have failed the gate. Otherwise a regression blocks once,
      lands in the store anyway, and becomes the standard the next run is
      measured against, so the second attempt sails through. Null verdict means
      the run predates gating, which is still eligible.

    `exclude_run_id` keeps a run from becoming its own baseline. Callers should
    also resolve before inserting, but this is the belt to that pair of braces:
    a gate that compares a run against itself reports a delta of exactly zero
    every time and can never fail, which looks like a working gate right up until
    it matters.

    Returns None when nothing qualifies, which the caller reports as
    `baseline-invalid` rather than treating as a pass.
    """
    return conn.execute(
        """
        select * from runs
        where dataset_id = %s
          and dataset_hash = %s
          and git_ref = %s
          and (n = 0 or error_count::float / n <= %s)
          and (verdict is null or verdict = 'pass')
          and (%s::text is null or run_id <> %s)
        order by started_at desc
        limit 1
        """,
        (dataset_id, dataset_hash, baseline_ref, max_error_rate, exclude_run_id, exclude_run_id),
    ).fetchone()
