.PHONY: help test lint type-check format security semgrep check migrate dev docker-up docker-down docker-logs worker beat css coverage clean schema-skill mcp-health pg-observe-setup

# Default target
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ─── Quality ──────────────────────────────────────────────

lint: ## Run ruff linter
	poetry run ruff check app/

format: ## Format code with ruff
	poetry run ruff format app/
	poetry run ruff check --fix app/

type-check: ## Run mypy type checker
	poetry run mypy app/ --ignore-missing-imports

security: ## Run bandit security scan
	poetry run bandit -r app/ -c pyproject.toml -q

semgrep: ## Run semgrep custom rules (DotMac anti-patterns)
	poetry run semgrep --config .semgrep/ app/ --exclude='tests/' --exclude='alembic/' --exclude='scripts/' --no-git-ignore

check: lint type-check security semgrep ## Run all quality checks (lint + type-check + security + semgrep)

# ─── Testing ──────────────────────────────────────────────

test: ## Run test suite
	poetry run pytest tests/ -q

test-v: ## Run test suite (verbose)
	poetry run pytest tests/ -v

test-cov: ## Run tests with coverage report
	poetry run pytest tests/ --cov=app --cov-report=term-missing

test-fast: ## Run tests, stop on first failure
	poetry run pytest tests/ -x --tb=short

test-e2e: ## Run end-to-end browser tests
	poetry run pytest tests/e2e/ -v --headed

# ─── Database ─────────────────────────────────────────────

migrate: ## Apply all pending migrations + regenerate schema skill
	poetry run alembic upgrade head
	@echo "Regenerating schema skill..."
	@poetry run python scripts/generate_schema_skill.py

migrate-new: ## Create a new migration (usage: make migrate-new msg="add users table")
	poetry run alembic revision --autogenerate -m "$(msg)"

migrate-down: ## Rollback last migration
	poetry run alembic downgrade -1

migrate-history: ## Show migration history
	poetry run alembic history --verbose

# ─── Claude Code ─────────────────────────────────────────────

schema-skill: ## Regenerate database schema skill for Claude Code
	poetry run python scripts/generate_schema_skill.py

mcp-health: ## Validate MCP DB config and read-only connectivity
	poetry run python scripts/check_mcp_db.py

pg-observe-setup: ## Enable pg_stat_statements and grant monitoring permissions (run docker compose up -d db first if new)
	docker exec -i dotmac_erp_db psql -U postgres -d dotmac_erp < scripts/setup_pg_observability.sql

# ─── Development ──────────────────────────────────────────

dev: ## Run dev server with hot reload
	python -m uvicorn app.main:app --reload --port 8000

worker: ## Run Celery worker
	celery -A app.celery_app worker --loglevel=info

beat: ## Run Celery beat scheduler
	celery -A app.celery_app beat --loglevel=info

css: ## Build Tailwind CSS
	npm run build:css

css-watch: ## Watch and rebuild Tailwind CSS
	npm run watch:css

# ─── Docker ───────────────────────────────────────────────

docker-up: ## Start all Docker containers
	docker compose up -d

docker-down: ## Stop all Docker containers
	docker compose down

docker-logs: ## Tail Docker container logs
	docker compose logs -f --tail=100

docker-rebuild: ## Rebuild and restart app container
	docker compose build app && docker compose up -d app

docker-shell: ## Open shell in app container
	docker exec -it dotmac_erp_app bash

docker-migrate: ## Run migrations inside Docker + regenerate schema skill
	docker exec dotmac_erp_app alembic upgrade head
	@echo "Regenerating schema skill..."
	@poetry run python scripts/generate_schema_skill.py

# ─── Pre-commit ───────────────────────────────────────────

pre-commit-install: ## Install pre-commit hooks
	poetry run pre-commit install

pre-commit-run: ## Run pre-commit on all files
	poetry run pre-commit run --all-files

# ─── Cleanup ──────────────────────────────────────────────

clean: ## Remove build artifacts and caches
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf htmlcov/ .coverage
