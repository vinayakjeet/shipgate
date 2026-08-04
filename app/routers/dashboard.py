from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.dashboard import render
from shipgate.store import db

router = APIRouter(tags=["dashboard"])


@router.get("/", response_class=HTMLResponse)
def dashboard() -> HTMLResponse:
    """Score over time per dataset. Sync so psycopg runs in the threadpool.

    A database outage renders an empty page rather than a 500. The dashboard is
    the first thing anyone opens, and a blank state reads as "no runs yet" while
    a stack trace reads as "this project is broken".
    """
    try:
        with db.connect() as conn:
            db.migrate(conn)
            summaries = db.fetch_dataset_summaries(conn)
    except db.DatabaseNotConfigured:
        summaries = []
    return HTMLResponse(render(summaries))
