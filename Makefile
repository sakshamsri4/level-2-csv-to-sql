.DEFAULT_GOAL := help
.PHONY: help setup ingest profile ask app test eval eval-live lint fmt typecheck check clean

help: ## Show available targets
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

setup: ## Create the virtualenv and install everything
	uv sync
	@test -f .env || cp .env.example .env
	@echo "Done. Add your API key to .env, then run: make ingest"

profile: ## Report what is wrong with the raw CSVs before cleaning them
	uv run assay profile

ingest: ## Clean data/raw/*.csv and load into DuckDB
	uv run assay ingest

ask: ## Ask one business question: make ask Q="which route had the highest delay rate last quarter?"
	uv run assay ask "$(Q)"

app: ## Streamlit interface on http://localhost:8501
	# --server.headless: without it Streamlit blocks on an interactive email
	# prompt the first time it is ever run, so `make app` fails on a fresh machine.
	uv run streamlit run src/assay/app.py --server.headless true

test: ## Run the test suite (no network, no API spend)
	uv run pytest

eval: ## Run the eval suite offline — no key, no spend
	uv run assay eval

eval-live: ## Run the eval suite against the real model
	uv run assay eval --live

lint: ## Check formatting and lint rules
	uv run ruff format --check .
	uv run ruff check .

fmt: ## Auto-format and auto-fix
	uv run ruff format .
	uv run ruff check --fix .

typecheck: ## Run mypy in strict mode
	uv run mypy

check: lint typecheck test ## Everything CI would run

clean: ## Remove the warehouse and caches
	rm -rf data/warehouse/* .pytest_cache .ruff_cache .mypy_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
