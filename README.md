# FastAPI Test Server & Resilient HTTP Client

A complete testing and development environment for robust HTTP client-server interactions, consisting of:

1. **FastAPI Test Server**: Professional-grade test server for validating asynchronous Python HTTP client behavior with configurable failure simulation, idempotency support, and comprehensive observability.

2. **Resilient HTTP Client Library**: Production-ready asynchronous HTTP client implementing modern resilience patterns including retries, circuit breaker, bulkhead, and comprehensive error handling.

## 🚀 Features

### FastAPI Test Server

- **Configurable Failure Simulation**: Inject 500 errors by count or duration
- **Idempotency Support**: Duplicate request detection via `X-Request-ID` header
- **Concurrency Limiting**: Configurable request concurrency with semaphore-based control
- **Request Timeouts**: Configurable per-request timeout handling
- **TTL Cache**: Intelligent caching with both size and time-based eviction
- **Structured Logging**: JSON and console logging with correlation IDs
- **Health Checks**: Built-in health endpoint for monitoring
- **Type Safety**: Full type hints with Pydantic validation
- **Production Ready**: Docker support, CI/CD pipeline, comprehensive testing

### Resilient HTTP Client Library

- **Connection Pooling**: Long-lived httpx.AsyncClient for optimal performance
- **Intelligent Retries**: Exponential backoff with jitter using tenacity
- **Circuit Breaker**: Fast failure prevention during service outages
- **Bulkhead Pattern**: Concurrency limiting with semaphore-based resource protection
- **Automatic Idempotency**: UUID-based request deduplication with X-Request-ID headers
- **Comprehensive Exception Hierarchy**: Semantic error types for precise handling
- **Structured Logging**: Multi-level observability with correlation IDs
- **Production Ready**: Professional-grade patterns with full test coverage

## 📋 API Endpoints

### Core Endpoint

#### `GET /msg`

Returns a unique UUID message. Behavior modified by query parameters and failure state.

**Query Parameters:**

- `delay` (optional): Delay in milliseconds (0-30000)

**Headers:**

- `X-Request-ID` (optional): Idempotency key

**Success Response (200):**

```json
{
  "message_id": "a-unique-uuid-string"
}
```

**Error Response (500):**

```json
{
  "detail": "Induced server failure"
}
```

### Failure Injection

#### `POST /fail/count/{count}`

Configure server to fail for a specific number of requests.

#### `POST /fail/duration/{seconds}`

Configure server to fail for a specific duration.

#### `POST /fail/reset`

Reset all failure configurations.

#### `GET /fail/status`

Get current failure injection status (diagnostic endpoint).

### Health Check

#### `GET /health`

Simple health check endpoint.

```json
{
  "status": "ok",
  "uptime_seconds": 123.45
}
```

## 🛠️ Installation & Setup

### Prerequisites

- Python 3.9 or higher
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

### Local Development

1. **Install uv (if not already installed):**

   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

2. **Clone the repository:**

   ```bash
   git clone <repository-url>
   cd py_client_demo
   ```

3. **Install dependencies:**

   ```bash
   uv sync --dev
   ```

4. **Run the server:**
   ```bash
   uv run python -m server.main serve
   ```

#### Alternative with pip

If you prefer pip:

```bash
pip install -r requirements-dev.txt
python -m server.main serve
```

5. **Visit the API documentation:**
   - Swagger UI: http://localhost:8000/docs
   - ReDoc: http://localhost:8000/redoc

### Docker Deployment

1. **Build and run with Docker Compose:**

   ```bash
   docker-compose -f docker/docker-compose.yml up test-server
   ```

2. **Or build manually:**
   ```bash
   docker build -f docker/Dockerfile -t fastapi-test-server .
   docker run -p 8000:8000 fastapi-test-server
   ```

## ⚙️ Configuration

The server can be configured via environment variables or command-line arguments.

### Environment Variables

| Variable                   | Default   | Description                             |
| -------------------------- | --------- | --------------------------------------- |
| `SERVER_HOST`              | `0.0.0.0` | Host to bind to                         |
| `SERVER_PORT`              | `8000`    | Port to bind to                         |
| `SERVER_MAX_CONCURRENCY`   | `50`      | Maximum concurrent requests             |
| `SERVER_REQUEST_TIMEOUT`   | `30`      | Request timeout in seconds              |
| `SERVER_CACHE_MAX_SIZE`    | `1000`    | Maximum cache entries                   |
| `SERVER_CACHE_TTL_SECONDS` | `300`     | Cache TTL in seconds                    |
| `SERVER_LOG_LEVEL`         | `INFO`    | Log level (DEBUG, INFO, WARNING, ERROR) |
| `SERVER_LOG_FORMAT`        | `json`    | Log format (json, console)              |
| `SERVER_ENABLE_DOCS`       | `true`    | Enable API documentation                |
| `SERVER_CORS_ENABLED`      | `false`   | Enable CORS middleware                  |
| `SERVER_CORS_ORIGINS`      | `null`    | Comma-separated allowed origins         |

### Command Line Interface

```bash
# Start server with custom settings
uv run python -m server.main serve --host 0.0.0.0 --port 8080 --max-concurrency 100

# Show current configuration
uv run python -m server.main config-info

# Development mode with auto-reload
uv run python -m server.main serve --reload
```

#### Alternative with pip

```bash
python -m server.main serve --host 0.0.0.0 --port 8080 --max-concurrency 100
python -m server.main config-info
python -m server.main serve --reload
```

### Example .env File

```bash
SERVER_HOST=0.0.0.0
SERVER_PORT=8000
SERVER_MAX_CONCURRENCY=50
SERVER_REQUEST_TIMEOUT=30
SERVER_CACHE_MAX_SIZE=1000
SERVER_CACHE_TTL_SECONDS=300
SERVER_LOG_LEVEL=INFO
SERVER_LOG_FORMAT=json
SERVER_ENABLE_DOCS=true
SERVER_CORS_ENABLED=false
```

## 📦 Using the Resilient Client Library

### Quick Start

```python
import asyncio
from client import ResilientClient, ClientConfig

async def main():
    config = ClientConfig(base_url="http://localhost:8000")

    async with ResilientClient(config) as client:
        # Automatic retries, circuit breaker, and idempotency
        response = await client.get("/msg")
        print(f"Message: {response.json()}")

asyncio.run(main())
```

### Advanced Configuration

```python
from client import ClientConfig, RetryConfig, CircuitBreakerConfig, BulkheadConfig

config = ClientConfig(
    base_url="http://localhost:8000",
    retry=RetryConfig(max_attempts=5, min_wait_seconds=0.5),
    circuit_breaker=CircuitBreakerConfig(failure_threshold=3),
    bulkhead=BulkheadConfig(max_concurrency=20)
)

async with ResilientClient(config) as client:
    # Client automatically handles:
    # - Connection pooling for performance
    # - Retries with exponential backoff
    # - Circuit breaker for fast failure
    # - Concurrency limiting
    # - Request ID generation for idempotency
    response = await client.post("/api/data", json={"key": "value"})
```

See [CLIENT_USAGE.md](docs/CLIENT_USAGE.md) for complete documentation.

## 🧪 Testing Your HTTP Client

### Basic Usage Example

```python
import asyncio
import httpx

async def test_client():
    async with httpx.AsyncClient() as client:
        # Test basic functionality
        response = await client.get("http://localhost:8000/msg")
        print(f"Message: {response.json()}")

        # Test idempotency
        headers = {"X-Request-ID": "test-123"}
        response1 = await client.get("http://localhost:8000/msg", headers=headers)
        response2 = await client.get("http://localhost:8000/msg", headers=headers)

        # Should return the same message_id
        assert response1.json()["message_id"] == response2.json()["message_id"]

        # Test failure injection
        await client.post("http://localhost:8000/fail/count/2")

        # Next 2 requests should fail
        for _ in range(2):
            response = await client.get("http://localhost:8000/msg")
            assert response.status_code == 500

        # Third request should succeed
        response = await client.get("http://localhost:8000/msg")
        assert response.status_code == 200

asyncio.run(test_client())
```

### Complete Test Flow (from specification)

```python
import asyncio
import httpx

async def specification_test_flow():
    """Implements the exact test flow from the specification."""
    async with httpx.AsyncClient(base_url="http://localhost:8000") as client:

        # 1. Wait for healthy
        health = await client.get("/health")
        assert health.status_code == 200

        # 2. Configure failure
        await client.post("/fail/count/3")

        # 3. Validate failure (3 requests should fail)
        for i in range(3):
            response = await client.get("/msg")
            assert response.status_code == 500

        # 4. Validate recovery
        response = await client.get("/msg")
        assert response.status_code == 200

        # 5. Validate idempotency
        headers = {"X-Request-ID": "test-123"}
        response1 = await client.get("/msg", headers=headers)
        uuid_a = response1.json()["message_id"]

        response2 = await client.get("/msg", headers=headers)
        uuid_a_repeat = response2.json()["message_id"]
        assert uuid_a == uuid_a_repeat

        # 6. Validate failure precedence
        await client.post("/fail/count/1")

        # Should fail despite cached response
        response = await client.get("/msg", headers=headers)
        assert response.status_code == 500

        # Should now return cached response
        response = await client.get("/msg", headers=headers)
        assert response.status_code == 200
        assert response.json()["message_id"] == uuid_a

asyncio.run(specification_test_flow())
```

## 🏗️ Architecture

### Server Components

```
server/
├── main.py           # FastAPI app with lifespan management
├── config.py         # Pydantic configuration management
├── state.py          # Thread-safe state management
├── cache.py          # TTL-aware cache implementation
├── middleware.py     # Custom middleware stack
├── logging_config.py # Structured logging setup
└── endpoints/        # API endpoint implementations
    ├── core.py       # /msg endpoint
    ├── failure.py    # /fail/* endpoints
    └── health.py     # /health endpoint
```

### Client Components

```
client/
├── __init__.py       # Public API exports
├── client.py         # ResilientClient with connection pooling
├── config.py         # Configuration classes for all patterns
├── circuit_breaker.py # Circuit breaker pattern implementation
├── retry_policies.py # Retry logic with exponential backoff
└── exceptions.py     # Comprehensive exception hierarchy
```

### Middleware Stack (order matters)

1. **ErrorHandlingMiddleware**: Consistent error responses
2. **RequestLoggingMiddleware**: Structured request/response logging
3. **TimeoutMiddleware**: Request timeout enforcement
4. **ConcurrencyMiddleware**: Semaphore-based concurrency limiting

### State Management

- **FailureStateManager**: Thread-safe failure mode configuration
- **IdempotencyCache**: TTL-aware cache for request deduplication
- **ServerState**: Global state container with dependency injection

## 🧪 Testing

### Quick Testing with Makefile

The project includes a comprehensive Makefile for easy testing and development:

```bash
# Run all tests (except performance) against Docker test server
make docker-test

# Run performance tests against Docker test server  
make docker-test-performance

# Run ALL tests including performance against Docker test server
make docker-test-all

# Run the test server in Docker (interactive)
make docker-server

# Show all available targets
make help
```

### Local Testing

```bash
# Run complete test suite (server + client)
make test

# Run unit tests only
make test-unit

# Run integration tests (requires server running locally)
make test-integration

# Run performance tests (requires server running locally)
make test-performance

# Run with coverage for both components
uv run pytest tests/ --cov=server --cov=client --cov-report=html
```

### Development Workflow

```bash
# Install dependencies
make install

# Run server locally in development mode
make dev

# Code quality checks
make lint
make typecheck
make format

# Run all CI checks
make ci
```

### Manual Testing Commands

```bash
# Server unit tests
uv run pytest tests/test_endpoints.py tests/test_cache.py tests/test_state.py -v

# Client library unit tests  
uv run pytest tests/test_resilient_client.py tests/test_circuit_breaker.py tests/test_retry_policies.py -v

# Run client demo
python examples/client_demo.py
```

### Test Categories

```bash
# Skip performance tests for faster runs
uv run pytest -m "not performance" tests/ -v

# Run only integration tests
uv run pytest -m "integration" tests/ -v
```

## 🐳 Docker

### Quick Start with Makefile

```bash
# Build Docker image
make docker-build

# Run test server in Docker
make docker-server

# Run all tests against Docker server
make docker-test

# Clean up Docker resources
make docker-clean
```

### Manual Docker Commands

```bash
# Production deployment
docker-compose -f docker/docker-compose.yml up test-server

# Development mode
docker-compose -f docker/docker-compose.yml --profile dev up test-server-dev

# Load testing
docker-compose -f docker/docker-compose.yml --profile load-test up load-test
```

### Environment Overrides

```bash
docker run -p 8000:8000 \
  -e SERVER_MAX_CONCURRENCY=100 \
  -e SERVER_LOG_LEVEL=DEBUG \
  fastapi-test-server
```

## 📊 Monitoring & Observability

### Structured Logging

The server provides structured JSON logging in production and colorized console logging in development.

**Example JSON Log Entry:**

```json
{
  "timestamp": "2024-01-15T10:30:45.123Z",
  "level": "info",
  "logger": "server.startup",
  "message": "FastAPI test server starting up",
  "config": {
    "max_concurrency": 50,
    "request_timeout": 30,
    "cache_max_size": 1000
  }
}
```

### Health Check Integration

The `/health` endpoint can be used with:

- Docker health checks
- Kubernetes liveness/readiness probes
- Load balancer health checks
- Monitoring systems

```bash
# Simple health check
curl http://localhost:8000/health

# With monitoring tools
curl -f http://localhost:8000/health || exit 1
```

## 🔧 Development

### Code Quality

The project uses modern Python tooling:

```bash
# Lint code
uv run ruff check server/ tests/

# Type checking
uv run mypy server/

# Security scanning
uv run bandit -r server/
uv tool run safety check
```

### Pre-commit Hooks

```bash
uv tool install pre-commit
uv tool run pre-commit install
```

## 📈 Performance Characteristics

### Benchmarks

With default configuration (`MAX_CONCURRENCY=50`):

- **Throughput**: ~1000 req/s for basic requests
- **Memory Usage**: ~50MB base + ~1MB per 1000 cached entries
- **Latency**: <5ms median for cached responses, <10ms for new UUIDs

### Tuning Guidelines

- **High Throughput**: Increase `MAX_CONCURRENCY` (50-200)
- **Memory Constrained**: Reduce `CACHE_MAX_SIZE` and `CACHE_TTL_SECONDS`
- **Low Latency**: Disable detailed logging (`LOG_LEVEL=WARNING`)
- **Testing Resilience**: Lower `REQUEST_TIMEOUT` (5-10s)

## 🔒 Security

- **Non-root container**: Runs as dedicated `appuser`
- **Minimal attack surface**: Only necessary dependencies
- **Input validation**: All parameters validated by Pydantic
- **Resource protection**: Concurrency and timeout limits prevent DoS
- **No persistence**: In-memory state only

## 📝 License

MIT License - see LICENSE file for details.

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes
4. Run tests (`uv run pytest`)
5. Commit your changes (`git commit -m 'Add amazing feature'`)
6. Push to the branch (`git push origin feature/amazing-feature`)
7. Open a Pull Request

## 🐛 Troubleshooting

### Common Issues

**Server won't start:**

```bash
# Check configuration
uv run python -m server.main config-info

# Check logs
uv run python -m server.main serve --log-level DEBUG
```

**Tests failing:**

```bash
# Install test dependencies
uv sync --dev

# Run with verbose output
uv run pytest tests/ -v -s
```

**Docker build issues:**

```bash
# Build with verbose output
docker build -f docker/Dockerfile . --progress=plain
```

### Support

- Review the [SERVER_SPEC.md](docs/SERVER_SPEC.md) for detailed behavior
- Check the [CLIENT_GUIDE.md](docs/CLIENT_GUIDE.md) for usage patterns
- Open an issue for bugs or feature requests
