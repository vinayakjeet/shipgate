from __future__ import annotations

import os
from contextlib import contextmanager

from opentelemetry import trace

TRACER_NAME = "shipgate"


def get_tracer() -> trace.Tracer:
    """Tracer for ShipGate spans.

    Safe to call unconditionally. With no provider configured OpenTelemetry hands
    back a no-op tracer, so instrumented code costs effectively nothing rather
    than needing an `if tracing_enabled` around every call site.
    """
    return trace.get_tracer(TRACER_NAME)


def setup_tracing(service_name: str = "shipgate") -> bool:
    """Export OTLP spans when an endpoint is configured, otherwise do nothing.

    Returns whether tracing was enabled, which the CLI reports so a silent no-op
    is distinguishable from a silent failure.

    The SDK is imported lazily. A CLI run with no OTLP endpoint should not pay to
    construct an exporter and a batch processor it will never use, and on a
    free-tier CI runner that import is a measurable share of a short job.
    """
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        return False

    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(
                endpoint=endpoint,
                headers=_parse_headers(os.environ.get("OTEL_EXPORTER_OTLP_HEADERS")),
            )
        )
    )
    trace.set_tracer_provider(provider)
    return True


def _parse_headers(raw: str | None) -> dict[str, str]:
    """Standard OTEL_EXPORTER_OTLP_HEADERS format: 'key1=value1,key2=value2'."""
    if not raw:
        return {}
    headers = {}
    for pair in raw.split(","):
        if "=" in pair:
            key, _, value = pair.partition("=")
            headers[key.strip()] = value.strip()
    return headers


@contextmanager
def shutdown_tracing():
    """Flush spans before the process exits.

    A CLI run is short and BatchSpanProcessor exports on a timer, so without an
    explicit flush a gated CI job finishes and dies with its spans still queued.
    The trace of a failing gate is exactly the one worth having.
    """
    try:
        yield
    finally:
        provider = trace.get_tracer_provider()
        if hasattr(provider, "shutdown"):
            provider.shutdown()
