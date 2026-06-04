.PHONY: dev test seed migrate lint acceptance cov

dev:        ## Run bot + web + scheduler (single process)
	uv run python -m planner.main

test:       ## Run the full test suite
	uv run pytest tests -v

cov:        ## Tests with coverage (target 80%+)
	uv run pytest tests --cov=src/planner --cov-report=term-missing

seed:       ## Load YAML seed data into the DB
	uv run python -m seed.load

migrate:    ## Apply Alembic migrations
	uv run alembic upgrade head

lint:       ## Ruff + mypy (domain is strict)
	uv run ruff check src tests
	uv run mypy src/planner/domain --strict

acceptance: ## Print the manual acceptance checklist
	@echo "See docs/acceptance.md — run scenarios A-J in the test chat."
