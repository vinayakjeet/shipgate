from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from shipgate.store import db

router = APIRouter(prefix="/api", tags=["runs"])


@router.get("/runs")
def list_runs(
    dataset: str | None = Query(None, description="Filter to one dataset id."),
    limit: int = Query(50, ge=1, le=500),
) -> dict:
    """Recent runs, newest first. Defined as a sync route so psycopg runs in the
    threadpool instead of blocking the event loop."""
    try:
        with db.connect() as conn:
            db.migrate(conn)
            rows = db.fetch_runs(conn, dataset_id=dataset, limit=limit)
    except db.DatabaseNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"runs": rows}


@router.get("/runs/{run_id}")
def get_run(run_id: str) -> dict:
    try:
        with db.connect() as conn:
            row = db.fetch_run(conn, run_id)
    except db.DatabaseNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")
    return row
