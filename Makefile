.PHONY: bench test lint conventions

bench:
	uv run python scripts/benchmark.py --judge-sample 20

test:
	uv run pytest -q

lint:
	uv run ruff check .

conventions:
	uv run python scripts/check_conventions.py
