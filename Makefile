.PHONY: setup db-up db-down db-reset db-shell dev load load-parallel test test-unit test-integration lint hooks

setup:          ## Install dependencies, create .env and install git hooks
	uv sync
	@test -f .env || cp .env.example .env
	@$(MAKE) hooks

db-up:          ## Start Postgres in Docker and wait until it is healthy
	docker compose up -d --wait db

db-down:        ## Stop Postgres (keeps data)
	docker compose down

db-reset:       ## Delete all data and re-run db/init/*.sql
	docker compose down -v
	docker compose up -d --wait db

db-shell:       ## Open psql inside the container
	docker compose exec db psql -U postgres -d coffee

dev:            ## Run the API with auto-reload on http://localhost:8000
	uv run fastapi dev app/main.py

load:           ## Run the data loader (auto: sequential below 50k lines)
	uv run python scripts/load_data.py data/coffee_health_raw.csv

load-parallel:  ## Run the loader splitting the file across 4 worker processes
	uv run python scripts/load_data.py data/coffee_health_raw.csv --workers 4

test:           ## Run the full test suite (needs the database running)
	uv run pytest

test-unit:      ## Run tests that do not need the database
	uv run pytest tests/unit

test-integration: ## Run tests that use the real database
	uv run pytest tests/integration

lint:           ## Lint, check formatting and type-check
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy

hooks:          ## Install the versioned git hooks (unit tests before commit)
	git config core.hooksPath .githooks
	chmod +x .githooks/pre-commit
	@echo "Git hooks installed: .githooks/pre-commit runs the unit tests."
