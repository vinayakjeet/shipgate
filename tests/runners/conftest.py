from __future__ import annotations

import asyncio

import pytest


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    """Backoff waits must not slow the suite down."""

    async def _no_sleep(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
