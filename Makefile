.PHONY: install dev test rank

install:
	uv sync

dev: install
	uv run python -m app.main

test:
	uv run pytest -q

rank:
	uv run python -m app.cli rank --by 24h --top 30
