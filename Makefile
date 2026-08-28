.DEFAULT_GOAL := help

.PHONY: help install lint format arch types test test-unit test-bdd test-integration test-e2e security precommit run

help: ## Show this list of available targets
	@grep -E '^[a-zA-Z0-9_-]+:.*## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*## "}; {printf "\033[36m%-16s\033[0m %s\n", $$1, $$2}'

install: ## Sync the environment (from the committed lockfile) and install pre-commit hooks
	uv sync --all-groups --all-extras --frozen
	uv run --frozen pre-commit install

lint: ## Check formatting and lint rules without modifying files
	uv run --frozen ruff check .
	uv run --frozen ruff format --check .

format: ## Auto-fix formatting and lint issues
	uv run --frozen ruff format .
	uv run --frozen ruff check --fix .

arch: ## Check the dependency rule (import-linter, see ADR-0004)
	uv run --frozen lint-imports

types: ## Run both type checkers (pyright + pyrefly)
	uv run --frozen pyright
	uv run --frozen pyrefly check

test: ## Run the test suite (unit + integration + acceptance; e2e excluded by default)
	uv run --frozen pytest

test-unit: ## Run only the unit tests (pure hexagon, no I/O)
	uv run --frozen pytest -m unit

# pytest exits 5 when a marker selects nothing, and make reports that as a failure. For a
# tier that has not been written yet this is noise, not a signal. Applied only to the tiers
# that are genuinely still empty: delete it from a target the moment that tier gets its
# first test, so that an empty tier goes back to being an error rather than a silent pass.
ALLOW_EMPTY_TIER = || test $$? -eq 5

test-bdd: ## Run only the BDD/acceptance tests (pytest-bdd) -- none written yet
	uv run --frozen pytest -m bdd $(ALLOW_EMPTY_TIER)

test-integration: ## Run only the integration tests (real SQLite adapter + port contract)
	uv run --frozen pytest -m integration

test-e2e: ## Run end-to-end tests (spawns the real CLI and a real uvicorn server) -- none written yet
	uv run --frozen pytest -m e2e $(ALLOW_EMPTY_TIER)

security: ## Run security scans (bandit SAST + pip-audit + OSV-Scanner SCA)
	uv run --frozen bandit -c bandit.yaml -r src
	uv run --frozen pip-audit --skip-editable
	osv-scanner --lockfile=./uv.lock

precommit: ## Run all pre-commit hooks against every file
	uv run --frozen pre-commit run --all-files

run: ## Serve the HTTP driving adapter on :8000 (ACADEMY_DATABASE_URL=sqlite+aiosqlite:///academy.db by default)
	uv run --frozen uvicorn --factory academy.config:create_app --reload --port 8000
