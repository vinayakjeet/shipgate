from __future__ import annotations

import asyncio

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from llm import ChatClient, ChatResponse
from shipgate.runners.base import execute_run
from shipgate.runners.exact import ExactMatchRunner
from shipgate.runners.judge import JudgeRunner
from shipgate.targets import StubTarget
from shipgate.tracing import setup_tracing
from shipgate.types import DatasetItem

ITEMS = [
    DatasetItem(id="a", input={"prompt": "charged twice"}, expected="billing", slices=["i:b"]),
    DatasetItem(id="b", input={"prompt": "app crashes"}, expected="technical", slices=["i:t"]),
]


@pytest.fixture
def spans():
    """A real SDK provider writing into memory, so assertions are about spans
    that were actually emitted rather than about calls being made."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    # The API caches the global provider after first set, so override directly.
    trace._TRACER_PROVIDER = provider
    yield exporter
    trace._TRACER_PROVIDER = None


def by_name(exporter, name: str):
    return [s for s in exporter.get_finished_spans() if s.name == name]


def test_span_attributes(spans):
    """The attribute table SPEC.md promises Spanlight. Renaming any of these
    silently breaks a dashboard in another repo."""
    asyncio.run(execute_run(ExactMatchRunner(), ITEMS, StubTarget(), dataset_hash="sha256:x"))

    run = by_name(spans, "shipgate.run")
    assert len(run) == 1
    attrs = run[0].attributes
    assert attrs["runner"] == "exact"
    assert attrs["dataset_hash"] == "sha256:x"
    assert attrs["n"] == 2
    assert attrs["error_count"] == 0
    assert attrs["score"] == pytest.approx(0.5)

    items = by_name(spans, "shipgate.item")
    assert len(items) == 2
    assert {s.attributes["item_id"] for s in items} == {"a", "b"}
    assert all("score" in s.attributes for s in items)
    assert all("cache_hit" in s.attributes for s in items)


def test_item_spans_are_children_of_the_run_span(spans):
    """Without nesting, a waterfall in Grafana shows a flat pile of items with no
    indication which run they belong to."""
    asyncio.run(execute_run(ExactMatchRunner(), ITEMS, StubTarget(), dataset_hash="h"))

    run = by_name(spans, "shipgate.run")[0]
    for item in by_name(spans, "shipgate.item"):
        assert item.parent is not None
        assert item.parent.span_id == run.context.span_id


def test_failing_items_are_marked_as_errors(spans):
    class Boom:
        name = "boom"
        fingerprint = "boom:v1"

        async def score_item(self, item, target):
            raise RuntimeError("provider exploded")

    asyncio.run(execute_run(Boom(), ITEMS[:1], StubTarget(), dataset_hash="h"))

    item = by_name(spans, "shipgate.item")[0]
    assert item.status.status_code.name == "ERROR"
    assert "provider exploded" in item.attributes["error"]
    assert by_name(spans, "shipgate.run")[0].attributes["error_count"] == 1


def test_judge_span_records_model_tokens_and_verdict(spans, monkeypatch):
    """Cost attribution in Spanlight depends on these, and the model recorded is
    the resolved one, which differs from the requested alias."""

    class Provider:
        name = "scripted"

        async def chat_completion(self, messages, **kwargs):
            return ChatResponse(
                text='{"verdict": "pass", "reason": "ok"}',
                provider=self.name,
                model="gemini-3.6-flash",
                tokens_in=12,
                tokens_out=195,
                cost_usd=0.0,
            )

    import llm.providers.registry as registry

    monkeypatch.setitem(registry._PROVIDERS, "scripted", Provider())
    runner = JudgeRunner(client=ChatClient(max_retry_attempts=1), provider="scripted")

    asyncio.run(execute_run(runner, ITEMS[:1], StubTarget(), dataset_hash="h"))

    judge = by_name(spans, "shipgate.judge")
    assert len(judge) == 1
    attrs = judge[0].attributes
    assert attrs["model"] == "gemini-3.6-flash"
    assert attrs["rubric_version"] == "v1"
    assert attrs["tokens_in"] == 12
    assert attrs["tokens_out"] == 195
    assert attrs["verdict"] == "pass"


def test_cache_hits_are_visible_in_the_trace(spans):
    from shipgate.cache import InMemoryResultCache

    cache = InMemoryResultCache()
    asyncio.run(execute_run(ExactMatchRunner(), ITEMS, StubTarget(), dataset_hash="h", cache=cache))
    spans.clear()
    asyncio.run(execute_run(ExactMatchRunner(), ITEMS, StubTarget(), dataset_hash="h", cache=cache))

    assert all(s.attributes["cache_hit"] for s in by_name(spans, "shipgate.item"))
    assert by_name(spans, "shipgate.run")[0].attributes["cache_hits"] == 2


def test_tracing_is_disabled_without_an_endpoint(monkeypatch):
    """A project that has not configured Grafana must pay nothing, not construct
    an exporter that fails silently in the background."""
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    assert setup_tracing() is False


def test_tracing_enables_with_an_endpoint(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "https://otlp.example.test/v1/traces")
    monkeypatch.setattr(trace, "set_tracer_provider", lambda provider: None)
    assert setup_tracing() is True


def test_instrumented_code_runs_with_no_provider_configured():
    """The no-op tracer path. Every run goes through this when Grafana is not
    configured, so it has to work without any setup at all."""
    trace._TRACER_PROVIDER = None
    results = asyncio.run(execute_run(ExactMatchRunner(), ITEMS, StubTarget(), dataset_hash="h"))
    assert len(results) == 2
