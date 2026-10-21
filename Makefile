.DEFAULT_GOAL := help

COMPOSE ?= docker compose
VENV    ?= $(CURDIR)/backend/.venv
BIN     := $(VENV)/bin

.PHONY: help up down up-app worker scheduler migrate seed reset-demo test lint fmt gen-api openapi synthetic eval e2e

help: ## Show this help
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-12s %s\n", $$1, $$2}'

up: ## Start Postgres, Redis, MinIO (with bucket) and Mailpit
	$(COMPOSE) up -d --wait postgres redis minio mailpit
	$(COMPOSE) run --rm minio-init

down: ## Stop local infrastructure (data volumes are kept)
	$(COMPOSE) down

up-app: ## Start infrastructure plus the application services (profile "app")
	$(COMPOSE) --profile app up -d --build

worker: ## Run a background job worker (all queues) in this terminal
	cd backend && $(BIN)/python -m app.jobs.worker

scheduler: ## Run the periodic job scheduler in this terminal
	cd backend && $(BIN)/python -m app.jobs.scheduler

migrate: ## Apply database migrations
	cd backend && $(BIN)/flask --app wsgi db upgrade

seed: ## Load seed data
	cd backend && $(BIN)/flask --app wsgi seed

reset-demo: ## Truncate all tables and re-seed
	cd backend && $(BIN)/flask --app wsgi reset-demo

test: ## Run the backend test suite
	cd backend && $(BIN)/pytest

lint: ## Lint the backend
	cd backend && $(BIN)/ruff check . && $(BIN)/ruff format --check .

fmt: ## Format the backend
	cd backend && $(BIN)/ruff check --fix . && $(BIN)/ruff format .

openapi: ## Write the OpenAPI snapshot to docs/api/openapi.json
	cd backend && $(BIN)/flask --app wsgi openapi write --format json ../docs/api/openapi.json

gen-api: ## Regenerate frontend API types from the running backend
	cd frontend && npm run gen:api

synthetic: ## Generate the synthetic dataset
	cd backend && $(BIN)/flask --app wsgi synthetic generate

eval: ## Run the AI evaluation harness
	cd backend && $(BIN)/flask --app wsgi ai evaluate

e2e: ## Run the end-to-end tests
	cd frontend && npm run e2e
