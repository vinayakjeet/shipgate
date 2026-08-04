from __future__ import annotations

import json

from shipgate.store import db
from shipgate.types import ItemResult


class InMemoryResultCache:
    """Per-process cache. Useful in tests and for a single local run, useless in
    CI, where every job starts with an empty container."""

    def __init__(self) -> None:
        self._entries: dict[str, ItemResult] = {}

    async def get(self, key: str) -> ItemResult | None:
        return self._entries.get(key)

    async def put(self, key: str, result: ItemResult) -> None:
        self._entries[key] = result


class PostgresResultCache:
    """Shared cache in Neon.

    This is the one that matters. A judge run over 100 items on the Gemini free
    tier takes about seven minutes of rate-limited waiting, and CI containers
    start empty, so without shared storage every PR pays that cost again for
    items nobody touched.
    """

    def __init__(self, connection=None) -> None:
        self._conn = connection

    def _with_conn(self, fn):
        if self._conn is not None:
            return fn(self._conn)
        with db.connect() as conn:
            result = fn(conn)
            conn.commit()
            return result

    async def get(self, key: str) -> ItemResult | None:
        def _read(conn):
            return conn.execute(
                "select payload from result_cache where cache_key = %s", (key,)
            ).fetchone()

        row = self._with_conn(_read)
        if row is None:
            return None
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        return ItemResult.model_validate(payload)

    async def put(self, key: str, result: ItemResult) -> None:
        def _write(conn):
            conn.execute(
                """
                insert into result_cache (cache_key, payload)
                values (%s, %s)
                on conflict (cache_key) do nothing
                """,
                (key, json.dumps(result.model_dump())),
            )

        self._with_conn(_write)
