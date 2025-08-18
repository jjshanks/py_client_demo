# FastAPI Test Server & Client - Makefile
# Targets for running Docker test server and integration tests

.PHONY: help docker-server docker-test docker-build docker-stop docker-clean test test-unit test-integration lint format typecheck dev install

# Default target
.DEFAULT_GOAL := help

# Configuration
DOCKER_COMPOSE_FILE := docker/docker-compose.yml
DOCKER_SERVICE := test-server
DOCKER_IMAGE := fastapi-test-server
TEST_TIMEOUT := 300

help: ## Display this help message
	@echo "FastAPI Test Server & Client - Available targets:"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# Primary targets
docker-server: ## Run the test server in Docker
	@echo "Starting FastAPI test server in Docker..."
	docker-compose -f $(DOCKER_COMPOSE_FILE) up $(DOCKER_SERVICE)

docker-test: docker-build ## Run all tests (except performance) against Docker test server
	@echo "Starting test server and running all tests..."
	@docker-compose -f $(DOCKER_COMPOSE_FILE) up -d $(DOCKER_SERVICE)
	@echo "Waiting for server to be healthy..."
	@timeout $(TEST_TIMEOUT) sh -c 'until curl -s http://localhost:8000/health >/dev/null 2>&1; do echo "Waiting for server..."; sleep 2; done'
	@echo "Server is healthy, running all tests..."
	@SERVER_BASE_URL=http://localhost:8000 uv run pytest tests/ -v -m "not performance" || (docker-compose -f $(DOCKER_COMPOSE_FILE) down && exit 1)
	@docker-compose -f $(DOCKER_COMPOSE_FILE) down
	@echo "Integration tests completed successfully"

docker-test-performance: docker-build ## Run performance tests against Docker test server
	@echo "Starting test server and running performance tests..."
	@docker-compose -f $(DOCKER_COMPOSE_FILE) up -d $(DOCKER_SERVICE)
	@echo "Waiting for server to be healthy..."
	@timeout $(TEST_TIMEOUT) sh -c 'until curl -s http://localhost:8000/health >/dev/null 2>&1; do echo "Waiting for server..."; sleep 2; done'
	@echo "Server is healthy, running performance tests..."
	@SERVER_BASE_URL=http://localhost:8000 uv run pytest tests/ -v -m "performance" || (docker-compose -f $(DOCKER_COMPOSE_FILE) down && exit 1)
	@docker-compose -f $(DOCKER_COMPOSE_FILE) down
	@echo "Performance tests completed successfully"

docker-test-all: docker-build ## Run ALL tests including performance against Docker test server
	@echo "Starting test server and running all tests including performance..."
	@docker-compose -f $(DOCKER_COMPOSE_FILE) up -d $(DOCKER_SERVICE)
	@echo "Waiting for server to be healthy..."
	@timeout $(TEST_TIMEOUT) sh -c 'until curl -s http://localhost:8000/health >/dev/null 2>&1; do echo "Waiting for server..."; sleep 2; done'
	@echo "Server is healthy, running all tests..."
	@SERVER_BASE_URL=http://localhost:8000 uv run pytest tests/ -v || (docker-compose -f $(DOCKER_COMPOSE_FILE) down && exit 1)
	@docker-compose -f $(DOCKER_COMPOSE_FILE) down
	@echo "All tests completed successfully"

# Docker management
docker-build: ## Build the Docker image
	@echo "Building Docker image..."
	docker-compose -f $(DOCKER_COMPOSE_FILE) build $(DOCKER_SERVICE)

docker-stop: ## Stop running Docker containers
	@echo "Stopping Docker containers..."
	docker-compose -f $(DOCKER_COMPOSE_FILE) down

docker-clean: docker-stop ## Clean up Docker resources (containers, images, volumes)
	@echo "Cleaning up Docker resources..."
	docker-compose -f $(DOCKER_COMPOSE_FILE) down --volumes --remove-orphans
	@if [ "$$(docker images -q $(DOCKER_IMAGE) 2>/dev/null)" != "" ]; then \
		echo "Removing Docker image..."; \
		docker rmi $(DOCKER_IMAGE) 2>/dev/null || true; \
	fi

# Testing targets
test: ## Run all tests
	@echo "Running all tests..."
	uv run pytest tests/ -v

test-unit: ## Run unit tests only (excluding integration tests)
	@echo "Running unit tests..."
	uv run pytest tests/ -v -k "not integration"

test-integration: ## Run integration tests locally (requires running server)
	@echo "Running integration tests..."
	@echo "Note: Make sure the server is running locally on port 8000"
	uv run pytest tests/test_client_integration.py -v

test-performance: ## Run performance tests locally (requires running server)
	@echo "Running performance tests..."
	@echo "Note: Make sure the server is running locally on port 8000"
	uv run pytest tests/ -v -m "performance"

# Code quality
lint: ## Run code linting
	@echo "Running linting..."
	uv run ruff check server/ client/ tests/

format: ## Format code
	@echo "Formatting code..."
	uv run ruff format server/ client/ tests/

typecheck: ## Run type checking
	@echo "Running type checking..."
	uv run mypy server/ client/

# Development
dev: ## Run server locally in development mode
	@echo "Starting server in development mode..."
	uv run python -m server.main serve --reload

install: ## Install dependencies
	@echo "Installing dependencies..."
	uv sync --dev

# Convenience targets
ci: lint typecheck test ## Run all CI checks (lint, typecheck, test)

dev-setup: install ## Set up development environment
	@echo "Setting up development environment..."
	uv run pre-commit install

clean: docker-clean ## Clean up all resources
	@echo "Cleaning up Python cache files..."
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true