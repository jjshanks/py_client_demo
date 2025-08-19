# Troubleshooting Guide for Resilient HTTP Client

This guide helps junior engineers diagnose and resolve common issues when using the resilient HTTP client library. Each section includes symptoms, root causes, and step-by-step solutions.

## Table of Contents

1. [Common Error Messages](#common-error-messages)
2. [Performance Issues](#performance-issues)
3. [Configuration Problems](#configuration-problems)
4. [Circuit Breaker Issues](#circuit-breaker-issues)
5. [Retry Behavior Problems](#retry-behavior-problems)
6. [Connection Pool Issues](#connection-pool-issues)
7. [Debugging Techniques](#debugging-techniques)
8. [Monitoring and Observability](#monitoring-and-observability)

## Common Error Messages

### `PoolTimeoutError: Failed to acquire connection slot within 30.0s - client is saturated`

**What it means**: The client has reached its maximum concurrency limit and new requests cannot acquire a semaphore slot.

**Root causes**:
- Too many concurrent requests
- Slow upstream service holding connections
- Bulkhead limit set too low for your workload
- Application not properly releasing connections

**Solutions**:

1. **Immediate fix** - Reduce request rate:
   ```python
   import asyncio

   # Add delays between requests
   async def controlled_requests():
       for i in range(100):
           try:
               response = await client.get(f"/api/data/{i}")
               await asyncio.sleep(0.1)  # Rate limiting
           except PoolTimeoutError:
               await asyncio.sleep(1.0)  # Back off on saturation
   ```

2. **Configuration fix** - Increase concurrency limit:
   ```python
   from client import ClientConfig, BulkheadConfig

   config = ClientConfig(
       base_url="https://api.example.com",
       bulkhead=BulkheadConfig(
           max_concurrency=100,  # Increased from default 50
           acquisition_timeout=60.0  # Longer timeout
       )
   )
   ```

3. **Architecture fix** - Use multiple client instances:
   ```python
   # Scale horizontally with multiple clients
   clients = [ResilientClient(config) for _ in range(3)]

   async def distribute_requests(requests):
       tasks = []
       for i, request in enumerate(requests):
           client = clients[i % len(clients)]
           tasks.append(client.get(request["url"]))
       return await asyncio.gather(*tasks)
   ```

---

### `APIConnectionError: Circuit breaker is OPEN - failing fast`

**What it means**: The circuit breaker has detected too many failures and is preventing requests to protect the service.

**Root causes**:
- Upstream service is actually down or degraded
- Network connectivity issues
- Circuit breaker threshold set too low
- Transient failures exceeded threshold

**Solutions**:

1. **Check service health**:
   ```bash
   # Verify the service is accessible
   curl -v https://api.example.com/health

   # Check DNS resolution
   nslookup api.example.com

   # Test connectivity
   telnet api.example.com 443
   ```

2. **Wait for automatic recovery**:
   ```python
   import time

   async def wait_for_recovery():
       while True:
           try:
               response = await client.get("/health")
               print("Service recovered!")
               break
           except APIConnectionError as e:
               if "Circuit breaker is OPEN" in str(e):
                   print("Circuit still open, waiting...")
                   await asyncio.sleep(10)  # Wait for recovery timeout
               else:
                   raise  # Different connection error
   ```

3. **Adjust circuit breaker settings**:
   ```python
   from client import CircuitBreakerConfig

   config = ClientConfig(
       base_url="https://api.example.com",
       circuit_breaker=CircuitBreakerConfig(
           failure_threshold=10,  # More tolerant (default: 5)
           recovery_timeout=60.0  # Longer recovery time (default: 30)
       )
   )
   ```

---

### `APITimeoutError: Request timed out`

**What it means**: A request exceeded the configured timeout values.

**Root causes**:
- Network latency higher than expected
- Server processing time longer than timeout
- Timeout values set too aggressively
- Large response payloads

**Solutions**:

1. **Increase timeout values**:
   ```python
   from client import TimeoutConfig

   config = ClientConfig(
       base_url="https://api.example.com",
       timeout=TimeoutConfig(
           connect=30.0,  # Longer connection timeout
           read=120.0,    # Longer read timeout for large responses
           write=60.0,    # Longer write timeout for uploads
           pool=15.0      # Longer pool timeout
       )
   )
   ```

2. **Implement request pagination**:
   ```python
   async def paginated_requests(base_url, total_items, page_size=100):
       results = []
       for offset in range(0, total_items, page_size):
           response = await client.get(
               f"{base_url}?limit={page_size}&offset={offset}"
           )
           results.extend(response.json()["items"])
       return results
   ```

3. **Use streaming for large responses**:
   ```python
   async def stream_large_response(url):
       async with client.stream('GET', url) as response:
           async for chunk in response.aiter_bytes():
               # Process chunk by chunk to avoid timeout
               process_chunk(chunk)
   ```

---

### `AuthenticationError: The request was not authorized (401, 403)`

**What it means**: The API rejected the request due to authentication or authorization issues.

**Root causes**:
- Invalid or expired API credentials
- Missing authentication headers
- Insufficient permissions for the requested resource
- Credential rotation not completed

**Solutions**:

1. **Verify credentials**:
   ```python
   import os

   # Check environment variables
   api_key = os.getenv("API_KEY")
   if not api_key:
       raise ValueError("API_KEY environment variable not set")

   # Test with explicit headers
   response = await client.get(
       "/api/test",
       headers={"Authorization": f"Bearer {api_key}"}
   )
   ```

2. **Implement token refresh**:
   ```python
   class TokenManager:
       def __init__(self):
           self.token = None
           self.expires_at = 0

       async def get_valid_token(self):
           if time.time() >= self.expires_at:
               await self.refresh_token()
           return self.token

       async def refresh_token(self):
           # Implement your token refresh logic
           auth_response = await auth_client.post("/oauth/token", ...)
           self.token = auth_response.json()["access_token"]
           self.expires_at = time.time() + auth_response.json()["expires_in"]
   ```

3. **Debug permission issues**:
   ```python
   async def debug_permissions(resource_url):
       try:
           response = await client.get(resource_url)
           return response
       except AuthenticationError as e:
           print(f"Auth error for {resource_url}: {e.message}")
           if e.response:
               print(f"Status: {e.response.status_code}")
               print(f"Headers: {e.response.headers}")
               print(f"Body: {e.response.text}")
   ```

---

## Performance Issues

### Slow Request Times

**Symptoms**: Requests taking longer than expected to complete.

**Debugging steps**:

1. **Enable detailed logging**:
   ```python
   import logging

   logging.getLogger("resilient_client").setLevel(logging.DEBUG)
   logging.basicConfig(level=logging.DEBUG)
   ```

2. **Measure pattern overhead**:
   ```python
   import time

   async def measure_request_timing():
       start = time.time()

       # Time individual components
       acquire_start = time.time()
       # ... semaphore acquisition happens in client

       request_start = time.time()
       response = await client.get("/api/data")
       request_end = time.time()

       total_time = time.time() - start
       request_time = request_end - request_start

       print(f"Total time: {total_time:.3f}s")
       print(f"Actual request: {request_time:.3f}s")
       print(f"Pattern overhead: {total_time - request_time:.3f}s")
   ```

3. **Compare with direct httpx**:
   ```python
   import httpx

   # Direct httpx comparison
   async with httpx.AsyncClient() as direct_client:
       start = time.time()
       response = await direct_client.get("https://api.example.com/data")
       direct_time = time.time() - start

   # Resilient client
   start = time.time()
   response = await resilient_client.get("/data")
   resilient_time = time.time() - start

   print(f"Direct: {direct_time:.3f}s, Resilient: {resilient_time:.3f}s")
   ```

### High Memory Usage

**Symptoms**: Memory usage growing over time or higher than expected.

**Debugging steps**:

1. **Check connection pooling**:
   ```python
   # Ensure proper cleanup
   async with ResilientClient(config) as client:
       # Use client
       pass  # Automatic cleanup on exit

   # Or manual cleanup
   client = ResilientClient(config)
   try:
       # Use client
       pass
   finally:
       await client.close()  # Important!
   ```

2. **Monitor connection pools**:
   ```python
   async def monitor_connections():
       if client._client:
           # httpx client connection info
           pool = client._client._transport._pool
           print(f"Active connections: {len(pool._connections)}")
           print(f"Pool size limit: {pool._pool_size}")
   ```

3. **Use memory profiling**:
   ```python
   import tracemalloc

   tracemalloc.start()

   # Your client usage here
   async with ResilientClient(config) as client:
       for i in range(1000):
           await client.get(f"/api/data/{i}")

   current, peak = tracemalloc.get_traced_memory()
   print(f"Current memory usage: {current / 1024 / 1024:.1f} MB")
   print(f"Peak memory usage: {peak / 1024 / 1024:.1f} MB")
   ```

---

## Configuration Problems

### Invalid Configuration Values

**Common mistakes**:

```python
# ❌ Wrong timeout values
timeout=TimeoutConfig(
    connect=60.0,
    read=5.0,     # Read timeout shorter than connect timeout
    write=30.0
)

# ✅ Correct timeout values
timeout=TimeoutConfig(
    connect=10.0,
    read=30.0,    # Read timeout longer than connect
    write=30.0
)

# ❌ Conflicting retry and circuit breaker
retry=RetryConfig(max_attempts=10),
circuit_breaker=CircuitBreakerConfig(failure_threshold=3)
# Circuit will open before all retries are attempted

# ✅ Aligned configuration
retry=RetryConfig(max_attempts=3),
circuit_breaker=CircuitBreakerConfig(failure_threshold=5)
# Circuit allows multiple retry sequences before opening
```

### Environment-Specific Tuning

**High-latency networks** (satellite, international):
```python
config = ClientConfig(
    base_url="https://remote-api.example.com",
    timeout=TimeoutConfig(
        connect=30.0,
        read=120.0,
        write=60.0
    ),
    retry=RetryConfig(
        max_attempts=5,
        max_wait_seconds=300.0
    ),
    circuit_breaker=CircuitBreakerConfig(
        failure_threshold=10,
        recovery_timeout=120.0
    )
)
```

**High-throughput, low-latency** (internal services):
```python
config = ClientConfig(
    base_url="http://internal.service",
    timeout=TimeoutConfig(
        connect=2.0,
        read=5.0,
        write=5.0
    ),
    retry=RetryConfig(
        max_attempts=2,
        max_wait_seconds=10.0
    ),
    circuit_breaker=CircuitBreakerConfig(
        failure_threshold=3,
        recovery_timeout=10.0
    ),
    bulkhead=BulkheadConfig(
        max_concurrency=100
    )
)
```

---

## Circuit Breaker Issues

### Circuit Breaker Opens Too Frequently

**Symptoms**: Circuit breaker constantly switching to OPEN state.

**Causes & Solutions**:

1. **Threshold too low**:
   ```python
   # Increase failure threshold
   circuit_breaker=CircuitBreakerConfig(
       failure_threshold=10,  # Was 5
       recovery_timeout=30.0
   )
   ```

2. **Counting non-retryable errors**:
   ```python
   # Verify only connection errors are counted
   try:
       response = await client.get("/api/data")
   except APIConnectionError:
       # These count toward circuit breaker
       print("Connection error - will affect circuit")
   except APIStatusError:
       # These do NOT count toward circuit breaker
       print("Client error - won't affect circuit")
   ```

### Circuit Breaker Never Opens

**Symptoms**: Service clearly failing but circuit breaker stays CLOSED.

**Debugging**:

1. **Check exception types**:
   ```python
   # Enable circuit breaker logging
   logging.getLogger("circuit_breaker").setLevel(logging.DEBUG)

   # Verify exceptions are connection errors
   try:
       response = await client.get("/api/data")
   except Exception as e:
       print(f"Exception type: {type(e)}")
       print(f"Is APIConnectionError: {isinstance(e, APIConnectionError)}")
   ```

2. **Monitor circuit breaker state**:
   ```python
   async def monitor_circuit_breaker():
       if client._circuit_breaker:
           cb = client._circuit_breaker
           print(f"State: {cb.state}")
           print(f"Failures: {cb.failure_count}/{cb.config.failure_threshold}")
           print(f"Last failure: {cb.last_failure_time}")
   ```

---

## Retry Behavior Problems

### Retries Not Happening

**Debugging steps**:

1. **Verify exception types**:
   ```python
   # Only APIConnectionError triggers retries
   try:
       response = await client.get("/api/data")
   except APIConnectionError:
       print("This will be retried")
   except APIStatusError:
       print("This will NOT be retried")
   ```

2. **Check retry configuration**:
   ```python
   # Ensure retries are configured
   config = ClientConfig(
       retry=RetryConfig(
           max_attempts=3,  # Must be > 1 for retries
           min_wait_seconds=1.0,
           max_wait_seconds=60.0
       )
   )
   ```

### Too Many Retries

**Symptoms**: Requests taking very long due to excessive retries.

**Solutions**:

1. **Reduce retry attempts**:
   ```python
   retry=RetryConfig(
       max_attempts=2,  # Reduced from 3
       max_wait_seconds=30.0  # Reduced from 60
   )
   ```

2. **Fail fast for certain errors**:
   ```python
   # Custom exception handling
   try:
       response = await client.get("/api/data")
   except ServerError as e:
       if e.response and e.response.status_code == 500:
           # Don't retry 500 errors, fail immediately
           raise APIStatusError("Server error - not retrying") from e
       raise  # Let other ServerErrors be retried
   ```

---

## Debugging Techniques

### Enable Comprehensive Logging

```python
import logging

# Configure logging for all components
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Enable specific loggers
logging.getLogger("resilient_client").setLevel(logging.DEBUG)
logging.getLogger("circuit_breaker").setLevel(logging.DEBUG)
logging.getLogger("retry").setLevel(logging.DEBUG)
logging.getLogger("httpx").setLevel(logging.INFO)  # Less verbose
```

### Request Tracing

```python
import uuid

async def traced_request(url):
    request_id = str(uuid.uuid4())
    print(f"[{request_id}] Starting request to {url}")

    try:
        response = await client.get(url, request_id=request_id)
        print(f"[{request_id}] Success: {response.status_code}")
        return response
    except Exception as e:
        print(f"[{request_id}] Failed: {type(e).__name__}: {e}")
        raise
```

### Performance Profiling

```python
import cProfile
import asyncio

def profile_client_usage():
    async def workload():
        async with ResilientClient(config) as client:
            tasks = []
            for i in range(100):
                tasks.append(client.get(f"/api/data/{i}"))
            await asyncio.gather(*tasks)

    # Profile the workload
    cProfile.run('asyncio.run(workload())', 'client_profile.stats')

    # Analyze results
    import pstats
    stats = pstats.Stats('client_profile.stats')
    stats.sort_stats('cumulative').print_stats(20)
```

---

## Monitoring and Observability

### Key Metrics to Track

1. **Circuit Breaker Metrics**:
   ```python
   # Track state transitions
   circuit_state_changes = 0
   current_state = "CLOSED"

   def on_circuit_state_change(new_state):
       global circuit_state_changes, current_state
       circuit_state_changes += 1
       current_state = new_state
       print(f"Circuit breaker: {current_state} (changes: {circuit_state_changes})")
   ```

2. **Retry Metrics**:
   ```python
   retry_counts = {"success": 0, "failure": 0, "total_attempts": 0}

   def track_retry_outcome(success, attempts):
       retry_counts["total_attempts"] += attempts
       if success:
           retry_counts["success"] += 1
       else:
           retry_counts["failure"] += 1
   ```

3. **Pool Utilization**:
   ```python
   async def track_pool_usage():
       if client._semaphore:
           used_slots = client.config.bulkhead.max_concurrency - client._semaphore._value
           utilization = (used_slots / client.config.bulkhead.max_concurrency) * 100
           print(f"Pool utilization: {utilization:.1f}%")
   ```

### Health Check Endpoint

```python
async def health_check():
    """Check client and service health."""
    health_status = {
        "client_initialized": client._client is not None,
        "circuit_breaker_state": client._circuit_breaker.state.value if client._circuit_breaker else "unknown",
        "pool_available_slots": client._semaphore._value if client._semaphore else 0,
    }

    # Test connectivity
    try:
        response = await client.get("/health", timeout=5.0)
        health_status["service_reachable"] = True
        health_status["service_response_time"] = response.elapsed.total_seconds()
    except Exception as e:
        health_status["service_reachable"] = False
        health_status["service_error"] = str(e)

    return health_status
```

### Alerting Guidelines

**Critical Alerts**:
- Circuit breaker OPEN for > 5 minutes
- Pool utilization > 90% for > 2 minutes
- Error rate > 50% for > 1 minute

**Warning Alerts**:
- Circuit breaker opened (immediate notification)
- Retry rate > 20% for > 5 minutes
- Average response time > 2x baseline

**Info Alerts**:
- Configuration changes
- Client initialization/shutdown
- Circuit breaker recovery (OPEN → CLOSED)

---

## Getting Help

If you're still experiencing issues after following this guide:

1. **Check the logs** - Enable DEBUG logging and look for patterns
2. **Review the configuration** - Ensure settings match your environment
3. **Test with simplified config** - Use defaults and gradually add complexity
4. **Monitor metrics** - Track the key metrics mentioned above
5. **Create a minimal reproduction** - Isolate the problem to the smallest possible example

For complex issues, provide:
- Full error messages and stack traces
- Client configuration (redacted of secrets)
- Environment details (network, service characteristics)
- Logs showing the problem pattern
- Steps to reproduce the issue

Remember: Resilience patterns add complexity to help handle real-world failures. Understanding their behavior is key to effective troubleshooting!
