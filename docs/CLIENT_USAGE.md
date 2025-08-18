# Resilient HTTP Client Library - Usage Guide

A professional-grade asynchronous HTTP client library implementing modern resilience patterns including retries, circuit breaker, bulkhead, and comprehensive error handling.

## Quick Start

### Basic Usage

```python
import asyncio
from client import ResilientClient, ClientConfig

async def main():
    # Create client configuration
    config = ClientConfig(base_url="http://localhost:8000")
    
    # Use client with automatic resource management
    async with ResilientClient(config) as client:
        response = await client.get("/msg")
        data = response.json()
        print(f"Message ID: {data['message_id']}")

# Run the example
asyncio.run(main())
```

### Alternative Context Manager

```python
from client import create_resilient_client

async def main():
    config = ClientConfig(base_url="http://localhost:8000")
    
    async with create_resilient_client(config) as client:
        response = await client.get("/msg")
        print(response.json())
```

## Configuration

### Complete Configuration Example

```python
from client import ClientConfig, TimeoutConfig, RetryConfig, CircuitBreakerConfig, BulkheadConfig, LoggingConfig

config = ClientConfig(
    base_url="https://api.example.com",
    
    # HTTP timeouts
    timeout=TimeoutConfig(
        connect=60.0,    # Connection timeout
        read=15.0,       # Read timeout  
        write=15.0,      # Write timeout
        pool=5.0         # Pool timeout
    ),
    
    # Retry behavior
    retry=RetryConfig(
        max_attempts=5,           # Maximum retry attempts
        min_wait_seconds=1.0,     # Minimum wait between retries
        max_wait_seconds=60.0,    # Maximum wait between retries
        multiplier=2.0,           # Exponential backoff multiplier
        jitter=True               # Add random jitter to wait times
    ),
    
    # Circuit breaker
    circuit_breaker=CircuitBreakerConfig(
        failure_threshold=5,      # Failures before opening circuit
        recovery_timeout=30.0,    # Seconds before attempting recovery
    ),
    
    # Concurrency limiting (bulkhead)
    bulkhead=BulkheadConfig(
        max_concurrency=50,       # Maximum concurrent requests
        acquisition_timeout=30.0  # Timeout for acquiring connection slot
    ),
    
    # Logging
    logging=LoggingConfig(
        level="INFO",             # Log level
        include_request_id=True,  # Include request IDs in logs
        logger_name="my_client"   # Custom logger name
    ),
    
    # httpx client options
    follow_redirects=True,
    verify_ssl=True,
    user_agent="MyApp/1.0"
)
```

### Environment-Based Configuration

```python
import os
from client import ClientConfig

# Configuration can be loaded from environment variables
config = ClientConfig(
    base_url=os.getenv("API_BASE_URL", "http://localhost:8000"),
    timeout=TimeoutConfig(
        connect=float(os.getenv("TIMEOUT_CONNECT", "60.0")),
        read=float(os.getenv("TIMEOUT_READ", "15.0"))
    )
)
```

## HTTP Methods

The client supports all standard HTTP methods with automatic resilience patterns:

```python
async with create_resilient_client(config) as client:
    # GET request
    response = await client.get("/users")
    
    # POST request with JSON data
    response = await client.post("/users", json={"name": "John Doe"})
    
    # PUT request with custom headers
    response = await client.put(
        "/users/123", 
        json={"name": "Jane Doe"},
        headers={"Authorization": "Bearer token"}
    )
    
    # DELETE request
    response = await client.delete("/users/123")
    
    # All httpx parameters are supported
    response = await client.get(
        "/search",
        params={"q": "python", "limit": 10},
        headers={"Accept": "application/json"},
        timeout=httpx.Timeout(30.0)  # Override default timeout
    )
```

## Idempotency and Request IDs

### Automatic Request ID Generation

```python
async with create_resilient_client(config) as client:
    # Client automatically generates X-Request-ID for idempotency
    response = await client.post("/create-user", json={"name": "John"})
    
    # If the request is retried, the same X-Request-ID is used
    # The server can safely deduplicate based on this ID
```

### Custom Request IDs

```python
import uuid

async with create_resilient_client(config) as client:
    # Provide your own request ID for idempotency
    custom_id = str(uuid.uuid4())
    response = await client.post("/create-order", 
                                json={"item": "widget"}, 
                                request_id=custom_id)
    
    # Multiple calls with same request_id will be deduplicated by server
    response2 = await client.post("/create-order", 
                                 json={"item": "widget"}, 
                                 request_id=custom_id)
```

## Error Handling

### Exception Hierarchy

The client uses a comprehensive exception hierarchy for precise error handling:

```python
from client.exceptions import (
    MyAPIError,          # Base exception
    APIConnectionError,  # Retryable errors (network, timeouts, 5xx)
    APIStatusError,      # Non-retryable errors (4xx)
    PoolTimeoutError,    # Bulkhead saturation
)

async with create_resilient_client(config) as client:
    try:
        response = await client.get("/api/data")
    except APIConnectionError as e:
        # These errors are automatically retried by the client
        print(f"Connection error after retries: {e}")
    except APIStatusError as e:
        # These are immediate failures (4xx errors)
        print(f"Client error (no retry): {e}")
    except PoolTimeoutError as e:
        # Client is saturated - reduce concurrency
        print(f"Client overloaded: {e}")
    except MyAPIError as e:
        # Catch-all for any other client library errors
        print(f"Client library error: {e}")
```

### Specific Exception Types

```python
from client.exceptions import (
    APITimeoutError,           # Client-side timeouts
    ServerTimeoutError,        # Server 408 responses  
    ServiceUnavailableError,   # Server 502/503/504 responses
    ServerError,               # Other 5xx responses
    AuthenticationError,       # 401/403 responses
    NotFoundError,             # 404 responses
    InvalidRequestError,       # 400/422 responses
)

async with create_resilient_client(config) as client:
    try:
        response = await client.get("/protected-resource")
    except AuthenticationError:
        print("Need to refresh authentication token")
    except NotFoundError:
        print("Resource doesn't exist")
    except ServiceUnavailableError:
        print("Service is temporarily down")
    except APITimeoutError:
        print("Request timed out")
```

## Resilience Patterns in Action

### Testing Retries with the Test Server

```python
import httpx

async def test_retry_behavior():
    config = ClientConfig(base_url="http://localhost:8000")
    
    # Configure test server to fail 2 requests
    async with httpx.AsyncClient() as setup_client:
        await setup_client.post("http://localhost:8000/fail/count/2")
    
    # Client will automatically retry and succeed
    async with create_resilient_client(config) as client:
        response = await client.get("/msg")
        assert response.status_code == 200
        print("Successfully recovered from failures via retries!")
```

### Testing Circuit Breaker

```python
async def test_circuit_breaker():
    config = ClientConfig(
        base_url="http://localhost:8000",
        circuit_breaker=CircuitBreakerConfig(
            failure_threshold=3,
            recovery_timeout=5.0
        )
    )
    
    # Configure sustained failures to trip circuit breaker
    async with httpx.AsyncClient() as setup_client:
        await setup_client.post("http://localhost:8000/fail/count/10")
    
    async with create_resilient_client(config) as client:
        # Generate failures to trip circuit breaker
        for _ in range(3):
            try:
                await client.get("/msg")
            except ServerError:
                pass
        
        # Circuit should now be open - next call fails fast
        try:
            await client.get("/msg")
        except APIConnectionError as e:
            if "Circuit breaker is OPEN" in str(e):
                print("Circuit breaker successfully opened!")
```

### Testing Bulkhead (Concurrency Limiting)

```python
async def test_bulkhead():
    config = ClientConfig(
        base_url="http://localhost:8000",
        bulkhead=BulkheadConfig(
            max_concurrency=5,
            acquisition_timeout=1.0
        )
    )
    
    async with create_resilient_client(config) as client:
        # Start many concurrent slow requests
        tasks = [
            client.get("/msg", params={"delay": 1000})  # 1 second delay
            for _ in range(10)
        ]
        
        try:
            # Some should succeed, others should hit bulkhead timeout
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            successes = [r for r in results if isinstance(r, httpx.Response)]
            pool_timeouts = [r for r in results if isinstance(r, PoolTimeoutError)]
            
            print(f"Successful requests: {len(successes)}")
            print(f"Bulkhead timeouts: {len(pool_timeouts)}")
            
        finally:
            # Cancel any remaining tasks
            for task in tasks:
                if not task.done():
                    task.cancel()
```

## Advanced Usage

### Custom Logging Setup

```python
import logging
import sys

# Setup custom logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)

config = ClientConfig(
    base_url="http://localhost:8000",
    logging=LoggingConfig(
        level="DEBUG",
        logger_name="my_resilient_client"
    )
)

# Client will now produce detailed debug logs
async with create_resilient_client(config) as client:
    response = await client.get("/msg")
```

### Error Recovery Strategies

```python
from client.exceptions import APIConnectionError, APIStatusError

async def robust_api_call(client, endpoint, max_retries=3):
    """
    Example of custom error recovery strategy on top of client resilience.
    """
    for attempt in range(max_retries):
        try:
            return await client.get(endpoint)
        except APIConnectionError as e:
            # These are already retried by client, so this is final failure
            if attempt == max_retries - 1:
                print(f"Service unavailable after {max_retries} attempts")
                raise
            print(f"Service issue, waiting before next attempt: {e}")
            await asyncio.sleep(5)  # Additional backoff
        except APIStatusError as e:
            # These are not retryable - fail immediately
            print(f"Client error (not retryable): {e}")
            raise
```

### Integration with FastAPI Dependency Injection

```python
from fastapi import FastAPI, Depends
from contextlib import asynccontextmanager

# Global client instance
resilient_client = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan to manage client lifecycle."""
    global resilient_client
    
    config = ClientConfig(base_url="http://api.example.com")
    resilient_client = ResilientClient(config)
    await resilient_client._ensure_initialized()
    
    yield
    
    await resilient_client.close()

app = FastAPI(lifespan=lifespan)

def get_api_client() -> ResilientClient:
    """Dependency to inject resilient client."""
    return resilient_client

@app.get("/proxy-data")
async def proxy_data(client: ResilientClient = Depends(get_api_client)):
    """Endpoint that uses the resilient client."""
    response = await client.get("/external-data")
    return response.json()
```

## Performance Considerations

### Connection Pooling Benefits

The client maintains a long-lived `httpx.AsyncClient` instance that provides significant performance benefits through connection pooling:

- **Avoid per-request SSL handshakes** - Connections are reused for the same host
- **Reduced latency** - Subsequent requests skip connection establishment
- **Lower CPU usage** - No repeated SSL context creation

### Recommended Patterns

```python
# ✅ GOOD: Single long-lived client
async with create_resilient_client(config) as client:
    for _ in range(100):
        response = await client.get("/api/data")

# ❌ BAD: New client per request (negates connection pooling)
for _ in range(100):
    async with create_resilient_client(config) as client:
        response = await client.get("/api/data")
```

### Performance Tuning

```python
# High-throughput configuration
high_perf_config = ClientConfig(
    base_url="http://api.example.com",
    
    # Aggressive timeouts
    timeout=TimeoutConfig(
        connect=30.0,
        read=10.0,
        write=10.0,
        pool=2.0
    ),
    
    # Minimal retries for high throughput
    retry=RetryConfig(
        max_attempts=2,
        min_wait_seconds=0.1,
        max_wait_seconds=1.0
    ),
    
    # Higher concurrency
    bulkhead=BulkheadConfig(
        max_concurrency=100,
        acquisition_timeout=5.0
    ),
    
    # Less verbose logging  
    logging=LoggingConfig(level="WARNING")
)
```

## Testing Your Integration

### Using the Test Server

Start the included test server to validate your client integration:

```bash
# Start test server
uv run python -m server.main serve --reload

# In another terminal, test your client
python your_client_test.py
```

### Failure Injection Testing

```python
import httpx

async def test_resilience_patterns():
    # Setup: Reset server state
    async with httpx.AsyncClient() as setup_client:
        await setup_client.post("http://localhost:8000/fail/reset")
    
    config = ClientConfig(base_url="http://localhost:8000")
    
    async with create_resilient_client(config) as client:
        # Test 1: Successful operation
        response = await client.get("/msg")
        assert response.status_code == 200
        
        # Test 2: Configure server failures and test retries
        async with httpx.AsyncClient() as setup_client:
            await setup_client.post("http://localhost:8000/fail/count/2")
        
        # Should succeed despite initial failures
        response = await client.get("/msg")
        assert response.status_code == 200
        
        # Test 3: Test idempotency
        request_id = "test-idempotent-123"
        resp1 = await client.get("/msg", request_id=request_id)
        resp2 = await client.get("/msg", request_id=request_id)
        
        # Should return same message_id
        assert resp1.json()["message_id"] == resp2.json()["message_id"]
```

## Production Deployment

### Recommended Production Settings

```python
# Production configuration
production_config = ClientConfig(
    base_url=os.getenv("API_BASE_URL"),
    
    timeout=TimeoutConfig(
        connect=60.0,     # Allow for slower initial connections
        read=30.0,        # Reasonable read timeout
        write=30.0,       # Reasonable write timeout
        pool=10.0         # Pool connection timeout
    ),
    
    retry=RetryConfig(
        max_attempts=3,   # Conservative retry count
        min_wait_seconds=1.0,
        max_wait_seconds=30.0,
        jitter=True       # Essential for production
    ),
    
    circuit_breaker=CircuitBreakerConfig(
        failure_threshold=5,
        recovery_timeout=60.0  # Longer recovery time
    ),
    
    bulkhead=BulkheadConfig(
        max_concurrency=20,     # Based on your service capacity
        acquisition_timeout=10.0
    ),
    
    logging=LoggingConfig(
        level="INFO",           # Avoid DEBUG in production
        include_request_id=True
    ),
    
    verify_ssl=True,           # Always verify SSL in production
    user_agent=f"MyService/{VERSION}"
)
```

### Monitoring and Observability

```python
import structlog

# Setup structured logging for better observability
structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer()
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

# The client will now emit structured JSON logs
```

## Best Practices

### 1. Client Lifecycle Management

```python
# ✅ GOOD: Use context managers for automatic cleanup
async with create_resilient_client(config) as client:
    await client.get("/data")

# ✅ GOOD: Manual lifecycle management  
client = ResilientClient(config)
try:
    await client._ensure_initialized()
    await client.get("/data")
finally:
    await client.close()
```

### 2. Error Handling Strategy

```python
async def api_operation(client):
    try:
        return await client.get("/data")
    except APIConnectionError:
        # Service/network issues - already retried by client
        # Consider exponential backoff at application level
        raise
    except APIStatusError as e:
        # Client errors - check request validity
        if isinstance(e, AuthenticationError):
            # Refresh tokens and retry
            pass
        elif isinstance(e, NotFoundError):
            # Handle missing resource
            pass
        else:
            # Other client errors
            raise
```

### 3. Configuration Management

```python
# ✅ GOOD: Environment-specific configs
def create_config_for_environment(env: str) -> ClientConfig:
    if env == "production":
        return ClientConfig(
            base_url=os.getenv("PROD_API_URL"),
            retry=RetryConfig(max_attempts=3),
            logging=LoggingConfig(level="INFO")
        )
    elif env == "staging":
        return ClientConfig(
            base_url=os.getenv("STAGING_API_URL"),
            retry=RetryConfig(max_attempts=5),  # More retries in staging
            logging=LoggingConfig(level="DEBUG")
        )
    else:  # development
        return ClientConfig(
            base_url="http://localhost:8000",
            logging=LoggingConfig(level="DEBUG")
        )
```

### 4. Testing Strategies

```python
# Unit testing with mocked httpx
@patch('httpx.AsyncClient.request')
async def test_my_service(mock_request):
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"data": "test"}
    mock_request.return_value = mock_response
    
    config = ClientConfig(base_url="http://test")
    async with create_resilient_client(config) as client:
        response = await client.get("/data")
        assert response.status_code == 200

# Integration testing against test server
async def test_against_real_server():
    config = ClientConfig(base_url="http://localhost:8000")
    async with create_resilient_client(config) as client:
        response = await client.get("/msg")
        assert response.status_code == 200
```

## Troubleshooting

### Common Issues

1. **PoolTimeoutError**: Client is saturated
   - Solution: Increase `bulkhead.max_concurrency` or reduce concurrent load
   
2. **Circuit breaker constantly open**: Downstream service is unhealthy
   - Solution: Check service health, increase `failure_threshold`, or increase `recovery_timeout`
   
3. **Requests timing out**: Network or server latency issues
   - Solution: Increase timeout values in `TimeoutConfig`
   
4. **Too many retries**: Aggressive retry configuration
   - Solution: Reduce `max_attempts` or increase wait times

### Debug Logging

Enable debug logging to troubleshoot issues:

```python
import logging

logging.basicConfig(level=logging.DEBUG)

config = ClientConfig(
    base_url="http://localhost:8000",
    logging=LoggingConfig(level="DEBUG")
)

# Client will now emit detailed logs for all operations
```

### Health Checking

```python
async def check_client_health(client: ResilientClient):
    """Simple health check for the client."""
    try:
        # Use the test server's health endpoint
        response = await client.get("/health")
        return response.status_code == 200
    except Exception as e:
        print(f"Client health check failed: {e}")
        return False
```