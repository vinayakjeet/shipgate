from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class DatasetItem(BaseModel):
    """One row of an eval dataset."""

    id: str
    input: dict
    expected: str | None = None
    slices: list[str] = Field(default_factory=list)
    meta: dict = Field(default_factory=dict)


class TargetResponse(BaseModel):
    """What a gated project returns for one item."""

    output: str
    meta: dict = Field(default_factory=dict)


class ItemResult(BaseModel):
    """One scored item. `score` is 0.0 to 1.0 so every runner aggregates the same way."""

    item_id: str
    output: str
    score: float
    slices: list[str] = Field(default_factory=list)
    latency_ms: float | None = None
    cache_hit: bool = False
    error: str | None = None
    meta: dict = Field(default_factory=dict)


class RunRecord(BaseModel):
    """One eval run: a dataset scored by one runner against one target."""

    run_id: str
    dataset_id: str
    dataset_hash: str
    runner: str
    n: int
    score: float
    git_sha: str | None = None
    model: str | None = None
    slices: dict[str, float] = Field(default_factory=dict)
    cost_usd: float | None = None
    p50_latency_ms: float | None = None
    cache_hit_rate: float | None = None
    error_count: int = 0
    trigger: str = "manual"
    started_at: datetime | None = None
    finished_at: datetime | None = None
