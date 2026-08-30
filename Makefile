.DEFAULT_GOAL := help

.PHONY: help install lint format arch types test test-unit test-bdd test-integration test-e2e \
        coverage security secrets licenses docs docs-serve release-next precommit run clean

help: ## Show this list of available targets
	@grep -E '^[a-zA-Z0-9_-]+:.*## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*## "}; {printf "\033[36m%-16s\033[0m %s\n", $$1, $$2}'

install: ## Sync the environment (from the committed lockfile) and install the git hooks
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

test-bdd: ## Run only the BDD/acceptance tests (pytest-bdd)
	uv run --frozen pytest -m bdd

test-integration: ## Run only the integration tests (real SQLite adapter + port contract)
	uv run --frozen pytest -m integration

test-e2e: ## Run end-to-end tests (spawns the real CLI and a real uvicorn server) -- none written yet
	uv run --frozen pytest -m e2e $(ALLOW_EMPTY_TIER)

coverage: ## Run the test suite with coverage and enforce the floor from .coveragerc
	uv run --frozen pytest --cov=academy --cov-config=.coveragerc --cov-report=term-missing --cov-report=html

security: ## Run every security scan (SAST + CVEs + secrets + licenses)
	uv run --frozen bandit -c bandit.yaml -r src
	uv run --frozen pip-audit --skip-editable
	osv-scanner --lockfile=./uv.lock
	$(MAKE) secrets
	$(MAKE) licenses

secrets: ## Scan the working tree for committed secrets (needs gitleaks on PATH)
	@command -v gitleaks >/dev/null 2>&1 || { \
	  echo "gitleaks is not on PATH."; \
	  echo "Install it with 'choco install gitleaks' (or a release binary from"; \
	  echo "https://github.com/gitleaks/gitleaks/releases). The pre-commit hook and the CI"; \
	  echo "job each manage their own copy, so this target is the only one that needs it"; \
	  echo "installed locally -- but it is a gate, so it fails rather than skipping."; \
	  exit 1; \
	}
	gitleaks dir . --redact --verbose -c .gitleaks.toml

licenses: ## Report dependency licenses and gate the ones that actually ship
	uv run --frozen pip-licenses --format=markdown
	@runtime=$$(uv export --frozen --no-dev --no-emit-project --no-hashes --all-extras --format requirements.txt \
	   | sed -e 's/[[:space:]]*[;#].*$$//' -e 's/[=<>!~].*$$//' -e '/^$$/d' \
	   | paste -sd' ' -); \
	if [ -z "$$runtime" ]; then \
	  echo "No runtime dependencies to check; nothing is distributed yet."; \
	else \
	  echo "Checking: $$runtime"; \
	  uv run --frozen pip-licenses --packages $$runtime \
	    --fail-on="GNU General Public License (GPL);GNU General Public License v2 or later (GPLv2+);GNU General Public License v3 (GPLv3);GNU Affero General Public License v3 or later (AGPLv3+)"; \
	fi

docs: ## Build the documentation site (--strict: warnings are failures)
	uv run --frozen mkdocs build --strict

docs-serve: ## Serve the documentation locally with live reload
	uv run --frozen mkdocs serve

release-next: ## Show the version the next release would get (creates no tag)
	uv run --frozen cz bump --get-next --yes

precommit: ## Run all pre-commit hooks against every file
	uv run --frozen pre-commit run --all-files

run: ## Serve the HTTP driving adapter on :8000 (ACADEMY_DATABASE_URL=sqlite+aiosqlite:///academy.db by default)
	uv run --frozen uvicorn --factory academy.config:create_app --reload --port 8000

clean: ## Remove build, cache and coverage artifacts
	rm -rf site/ dist/ build/ htmlcov/ .coverage coverage.xml \
	       .pytest_cache/ .ruff_cache/ .pyrefly_cache/
