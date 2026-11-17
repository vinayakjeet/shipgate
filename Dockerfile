# syntax=docker/dockerfile:1

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Spanlight is a git dependency, and uv fetches git dependencies with the git CLI,
# which the slim image does not carry.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app
COPY llm ./llm
COPY shipgate ./shipgate
RUN uv sync --frozen --no-dev


FROM python:3.13-slim AS runtime
WORKDIR /app

RUN groupadd --gid 1000 appuser && useradd --uid 1000 --gid appuser --create-home appuser

COPY --from=builder /app/.venv /app/.venv
COPY app ./app
COPY llm ./llm
# The dashboard imports shipgate.store, and the project is not installed as a
# package (package = false), so the source has to be on the path beside app/.
COPY shipgate ./shipgate

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

USER appuser
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz', timeout=3)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
