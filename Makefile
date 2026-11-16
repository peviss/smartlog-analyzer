.DEFAULT_GOAL := help
SHELL := /bin/sh

# src layout: the app package is in src/, the seed in db/.
export PYTHONPATH := src:.

COMPOSE := docker compose
UI := src/frontend
TEST_DATABASE_URL ?= postgresql+psycopg://smartlog:smartlog@localhost:5433/smartlog_test

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

env: ## Create .env from .env.example if missing
	@test -f .env || (cp .env.example .env && echo "Created .env - edit POSTGRES_PASSWORD")

up: env ## Build and start app + db + nginx (http://localhost:8080)
	$(COMPOSE) up -d --build
	@echo "UI:  http://localhost:$${HTTP_PORT:-8080}"
	@echo "API: http://localhost:$${HTTP_PORT:-8080}/docs"

down: ## Stop containers (keeps data)
	$(COMPOSE) down

clean: ## Stop containers and delete the database volume
	$(COMPOSE) down -v

logs: ## Follow application logs
	$(COMPOSE) logs -f app

migrate: ## Apply migrations inside the running app container
	$(COMPOSE) exec app alembic upgrade head

seed: ## Load sample logs inside the running app container
	$(COMPOSE) exec app python -m db.seed

install: ## Install dev dependencies locally
	pip install -r requirements-dev.txt

test-db: ## Start a throwaway Postgres for tests on port 5433
	docker run -d --rm --name smartlog-test-db -p 5433:5432 \
		-e POSTGRES_USER=smartlog -e POSTGRES_PASSWORD=smartlog -e POSTGRES_DB=smartlog_test postgres:16-alpine
	@sleep 3

test: ## Run all tests (needs TEST_DATABASE_URL; see `make test-db`)
	TEST_DATABASE_URL=$(TEST_DATABASE_URL) pytest -v

test-unit: ## Run unit tests only (no database needed)
	pytest tests/unit -v

lint: ## Lint and check formatting
	ruff check . && ruff format --check .

format: ## Auto-format the code
	ruff format . && ruff check --fix .

openapi: ## Regenerate docs/openapi.json and the UI's TypeScript types from it
	python -c "import json; from api.main import create_app; json.dump(create_app().openapi(), open('docs/openapi.json','w'), indent=2)"
	cd $(UI) && npm run gen:api

api-dev: ## Run the API locally with reload on http://localhost:8000 (uses .env)
	uvicorn api.main:app --reload --port 8000

ui-install: ## Install the web UI dependencies
	cd $(UI) && npm ci

ui-dev: ## Run the web UI on http://localhost:5173 (proxies /api to localhost:8000)
	cd $(UI) && npm run dev

ui-test: ## Typecheck and test the web UI
	cd $(UI) && npm run typecheck && npm test

ui-build: ## Build the web UI into src/frontend/dist
	cd $(UI) && npm run build

.PHONY: help env up down clean logs migrate seed install test-db test test-unit lint format openapi \
	api-dev ui-install ui-dev ui-test ui-build
