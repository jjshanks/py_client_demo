# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This project consists of two complementary components:

1. **FastAPI Test Server**: A professional-grade test server designed for validating asynchronous Python HTTP client behavior. Provides configurable failure simulation, idempotency support via request IDs, concurrency limiting, and comprehensive observability features.

2. **Resilient HTTP Client Library**: A production-ready asynchronous HTTP client implementing modern resilience patterns including retries, circuit breaker, bulkhead, and comprehensive error handling.

Together, these components form a complete testing and development environment for robust HTTP client-server interactions.

## Architecture

### Server Architecture

The server follows a modular FastAPI architecture:

- **`server/main.py`**: FastAPI app factory with lifespan management and CLI interface using Typer
- **`server/config.py`**: Pydantic-based configuration management with environment variable support
- **`server/state.py`**: Thread-safe state management for failure modes using asyncio locks
- **`server/cache.py`**: TTL-aware cache implementation with both size and time-based eviction
- **`server/middleware.py`**: Custom middleware stack for concurrency, timeouts, logging, and error handling
- **`server/endpoints/`**: API endpoint implementations (core, failure, health)

### Client Architecture

The client library follows a resilience-first design:

- **`client/client.py`**: Main ResilientClient class with connection pooling and pattern integration
- **`client/config.py`**: Pydantic configuration classes for all resilience patterns (Timeout, Retry, CircuitBreaker, Bulkhead, Logging)
- **`client/circuit_breaker.py`**: Circuit breaker implementation with three states (CLOSED, OPEN, HALF_OPEN)
- **`client/retry_policies.py`**: Retry decorator and policy using tenacity with exponential backoff
- **`client/exceptions.py`**: Comprehensive exception hierarchy for semantic error handling

### Middleware Stack Order (critical for proper operation):

1. ErrorHandlingMiddleware (outermost)
2. RequestLoggingMiddleware
3. TimeoutMiddleware
4. ConcurrencyMiddleware (innermost)

### Server Key Components:

- **FailureStateManager**: Thread-safe failure injection with count and duration modes
- **IdempotencyCache**: TTL cache for request deduplication via X-Request-ID header
- **TTLCache**: Generic cache with LRU eviction and TTL expiration

### Client Key Components:

- **ResilientClient**: Main client with long-lived httpx.AsyncClient connection pooling and pattern orchestration
- **CircuitBreaker**: Three-state circuit breaker (CLOSED, OPEN, HALF_OPEN) for fast failure during outages
- **RetryPolicy**: Exponential backoff with jitter using tenacity, supports custom retry conditions
- **BulkheadPattern**: Semaphore-based concurrency limiting for resource protection
- **Exception Hierarchy**: Semantic exception types (APIConnectionError, ServerError, etc.) for precise error handling
- **Automatic Idempotency**: UUID-based X-Request-ID header generation for request deduplication

## Common Development Commands

### Setup and Installation

```bash
# Install dependencies (preferred)
make install

# Alternative manual installation
uv sync --dev

# Set up development environment
make dev-setup
```

### Running the Server

```bash
# Development mode with auto-reload (using Makefile)
make dev

# Manual server commands
uv run python -m server.main serve --reload
uv run python -m server.main serve
uv run python -m server.main serve --max-concurrency 100 --log-level DEBUG
uv run python -m server.main config-info
```

### Testing with Makefile (Recommended)

```bash
# Quick testing - all tests against Docker server
make docker-test

# Run performance tests against Docker server
make docker-test-performance

# Run ALL tests including performance against Docker server
make docker-test-all

# Run test server in Docker (interactive)
make docker-server

# Local testing (requires server running)
make test                    # All tests locally
make test-unit              # Unit tests only
make test-integration       # Integration tests only
make test-performance       # Performance tests only

# Code quality
make lint                   # Lint code
make typecheck             # Type checking
make format                # Format code
make ci                    # All CI checks (lint + typecheck + test)
```

### Manual Testing Commands

```bash
# Run client demo against server
python examples/client_demo.py

# Run specific client tests
uv run pytest tests/test_resilient_client.py -v
uv run pytest tests/test_circuit_breaker.py -v
uv run pytest tests/test_retry_policies.py -v

# Test client performance
uv run pytest tests/test_client_performance.py -v

# Integration tests with server
uv run pytest tests/test_client_integration.py -v

# Run all tests
uv run pytest tests/ -v

# Run tests with coverage (includes both server and client)
uv run pytest tests/ --cov=server --cov=client --cov-report=html

# Run specific test categories
uv run pytest -m "not performance" tests/ -v  # Skip performance tests
uv run pytest -m "integration" tests/ -v      # Only integration tests

# Security scanning
uv tool run bandit -r server/ client/
uv tool run safety check
```

### Docker Operations with Makefile (Recommended)

```bash
# Build Docker image
make docker-build

# Run test server in Docker
make docker-server

# Run tests against Docker server
make docker-test                    # All tests except performance
make docker-test-performance        # Performance tests only
make docker-test-all                # ALL tests including performance

# Docker management
make docker-stop                    # Stop containers
make docker-clean                   # Clean up Docker resources

# View all available targets
make help
```

### Manual Docker Commands

```bash
# Build and run with compose
docker-compose -f docker/docker-compose.yml up test-server

# Manual build and run
docker build -f docker/Dockerfile -t fastapi-test-server .
docker run -p 8000:8000 fastapi-test-server
```

## Configuration

### Server Configuration

Environment variables (all prefixed with `SERVER_`):

- `SERVER_MAX_CONCURRENCY`: Semaphore limit for concurrent requests (default: 50)
- `SERVER_REQUEST_TIMEOUT`: Request timeout in seconds (default: 30)
- `SERVER_CACHE_MAX_SIZE`: Maximum idempotency cache entries (default: 1000)
- `SERVER_CACHE_TTL_SECONDS`: Cache TTL in seconds (default: 300)
- `SERVER_LOG_LEVEL`: DEBUG/INFO/WARNING/ERROR (default: INFO)
- `SERVER_LOG_FORMAT`: json/console (default: json)

### Client Configuration

Client configuration is done via Pydantic config classes:

```python
from client import ClientConfig, RetryConfig, CircuitBreakerConfig, BulkheadConfig, TimeoutConfig, LoggingConfig

config = ClientConfig(
    base_url="http://localhost:8000",
    timeout=TimeoutConfig(
        connect=5.0,
        read=30.0,
        write=30.0,
        pool=10.0
    ),
    retry=RetryConfig(
        max_attempts=3,
        min_wait_seconds=1.0,
        max_wait_seconds=60.0,
        exponential_base=2,
        jitter=True
    ),
    circuit_breaker=CircuitBreakerConfig(
        failure_threshold=5,
        recovery_timeout=30.0,
        expected_exception=None
    ),
    bulkhead=BulkheadConfig(
        max_concurrency=10,
        acquisition_timeout=5.0
    ),
    logging=LoggingConfig(
        level="INFO",
        logger_name="resilient_client"
    )
)
```

## Key API Behavior

### Server: `/msg` Endpoint Processing Order:

1. Acquire concurrency semaphore
2. Check failure state (count OR duration) - fails BEFORE idempotency check
3. Check idempotency cache for X-Request-ID header
4. Apply delay if specified
5. Generate new UUID and cache if request ID provided
6. Release semaphore in finally block

### Server: Failure Injection:

- Count and duration modes operate simultaneously (OR condition)
- Failure check decrements count but not duration
- Failures take precedence over cached idempotent responses

### Client: Request Processing Pipeline:

1. **Bulkhead**: Acquire semaphore slot (with timeout)
2. **Circuit Breaker**: Check if circuit is OPEN, fail fast if so
3. **Retry Policy**: Execute request with exponential backoff retry logic
4. **Idempotency**: Automatically add X-Request-ID header if request_id provided
5. **Exception Mapping**: Convert httpx exceptions to semantic API exceptions
6. **Circuit Breaker Update**: Record success/failure for circuit state management
7. **Bulkhead**: Release semaphore slot in finally block

### Client: Resilience Pattern Interactions:

- Circuit breaker can prevent retries from occurring (fast failure)
- Bulkhead timeout occurs before request execution (PoolTimeoutError)
- Retry failures update circuit breaker failure count
- Successful retries reset circuit breaker failure streak
- Each retry attempt is subject to bulkhead concurrency limiting

## Testing

The test suite covers both server and client components:

### Server Tests:
- Unit tests for cache, state management, and endpoints
- Integration tests following the specification test flow
- Docker-based testing in CI/CD pipeline
- Performance benchmarking with throughput and latency metrics

### Client Tests:
- Unit tests for resilience patterns (circuit breaker, retry policies, bulkhead)
- Configuration validation and edge cases
- Exception hierarchy and error mapping
- Integration tests against the live test server
- Performance tests measuring resilience pattern overhead
- Concurrency and load testing scenarios

### Test Categories:
- `test_*_client*.py`: Client library unit and integration tests
- `test_endpoints.py`: Server endpoint behavior tests
- `test_cache.py`, `test_state.py`: Server component unit tests
- Performance tests use `@pytest.mark.performance` marker
