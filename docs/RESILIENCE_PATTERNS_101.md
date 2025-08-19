# Resilience Patterns 101: A Complete Guide for Junior Engineers

Modern distributed systems face constant challenges: network failures, service outages, traffic spikes, and cascading failures. Resilience patterns are battle-tested solutions that help systems gracefully handle these challenges instead of failing catastrophically.

This guide introduces the four fundamental resilience patterns implemented in our client library, with practical examples and real-world scenarios that junior engineers will encounter.

## Table of Contents

1. [Why Resilience Patterns Matter](#why-resilience-patterns-matter)
2. [Pattern 1: Timeout Pattern](#pattern-1-timeout-pattern)
3. [Pattern 2: Retry Pattern](#pattern-2-retry-pattern)
4. [Pattern 3: Circuit Breaker Pattern](#pattern-3-circuit-breaker-pattern)
5. [Pattern 4: Bulkhead Pattern](#pattern-4-bulkhead-pattern)
6. [How Patterns Work Together](#how-patterns-work-together)
7. [Common Anti-Patterns to Avoid](#common-anti-patterns-to-avoid)
8. [Real-World Scenarios](#real-world-scenarios)

---

## Why Resilience Patterns Matter

### The Problem: Distributed System Failures

Imagine you're building an e-commerce application that depends on multiple services:

```
Your App → Payment Service → Bank API
         → Inventory Service → Database
         → Shipping Service → Carrier API
         → User Service → Authentication API
```

In a perfect world, all services would be available 100% of the time. In reality:

- **Network failures** happen (cables get cut, routers fail)
- **Services crash** (bugs, out-of-memory, deployment issues)
- **Traffic spikes** overwhelm services (Black Friday, viral content)
- **Cascading failures** occur (one failure triggers others)

### The Cost of No Resilience

**Without resilience patterns**, your application might:

```python
# Naive implementation - BAD
async def get_user_profile(user_id):
    # No timeout - could hang forever
    user_data = await user_service.get(f"/users/{user_id}")

    # No retry - fails on first network hiccup
    payment_data = await payment_service.get(f"/payments/{user_id}")

    # No circuit breaker - keeps hitting failing service
    recommendations = await recommendation_service.get(f"/recommend/{user_id}")

    return combine_profile(user_data, payment_data, recommendations)

# Result: One failing service brings down your entire application
```

**Real consequences**:
- User requests hang for minutes (poor UX)
- Failing services get overwhelmed (makes outages worse)
- Your application becomes unavailable (revenue loss)
- Recovery takes longer (cascading failures)

### The Solution: Resilience Patterns

**With resilience patterns**, the same code becomes:

```python
# Resilient implementation - GOOD
async def get_user_profile(user_id):
    async with ResilientClient(config) as client:
        # Timeout: fail fast if service is slow
        # Retry: handle transient failures automatically
        # Circuit breaker: protect from cascading failures
        # Bulkhead: prevent one slow service from blocking others

        user_data = await client.get(f"/users/{user_id}")
        payment_data = await client.get(f"/payments/{user_id}")

        try:
            recommendations = await client.get(f"/recommend/{user_id}")
        except APIConnectionError:
            # Graceful degradation - app still works without recommendations
            recommendations = get_default_recommendations()

        return combine_profile(user_data, payment_data, recommendations)
```

**Benefits**:
- ✅ Fast failure detection (timeouts)
- ✅ Automatic recovery from transient issues (retries)
- ✅ Protection from cascading failures (circuit breaker)
- ✅ Isolation between services (bulkhead)
- ✅ Graceful degradation when possible

---

## Pattern 1: Timeout Pattern

### What is the Timeout Pattern?

The timeout pattern sets maximum wait times for operations. Instead of waiting indefinitely for a response, the operation fails after a specified duration.

### Why Timeouts Matter

**The Problem**: Without timeouts, slow or hung operations can:
- Consume resources (memory, threads, connections)
- Block other operations
- Create poor user experiences
- Make failure detection impossible

**Example scenario**: A payment service experiences database lock contention, causing requests to hang for 10+ minutes. Without timeouts, your users wait forever for checkout to complete.

### Types of Timeouts

Our client implements **four types of timeouts** for comprehensive protection:

```python
from client import TimeoutConfig

timeout_config = TimeoutConfig(
    connect=10.0,   # 1. Connection timeout
    read=30.0,      # 2. Read timeout
    write=15.0,     # 3. Write timeout
    pool=5.0        # 4. Pool timeout
)
```

#### 1. Connection Timeout
**What it does**: Maximum time to establish a TCP connection.

**When it triggers**: Network issues, DNS resolution problems, server overload.

**Example**:
```python
# Service is down - connection timeout saves you
try:
    response = await client.get("/api/data")  # Fails after 10 seconds
except APITimeoutError:
    print("Service unreachable - failing fast instead of hanging")
```

#### 2. Read Timeout
**What it does**: Maximum time to receive a response after connection is established.

**When it triggers**: Slow database queries, complex computations, large response payloads.

**Example**:
```python
# Complex report generation - read timeout prevents hanging
try:
    report = await client.get("/api/reports/annual")  # Fails after 30 seconds
except APITimeoutError:
    print("Report generation too slow - try async generation instead")
```

#### 3. Write Timeout
**What it does**: Maximum time to send request data to the server.

**When it triggers**: Large file uploads, slow networks, server backpressure.

**Example**:
```python
# Large file upload - write timeout prevents hanging on slow uploads
try:
    result = await client.post("/api/upload", files={"file": large_file})
except APITimeoutError:
    print("Upload too slow - consider chunked upload or async processing")
```

#### 4. Pool Timeout
**What it does**: Maximum time to get a connection from the connection pool.

**When it triggers**: Connection pool exhaustion, too many concurrent requests.

**Example**:
```python
# High traffic - pool timeout prevents resource exhaustion
try:
    response = await client.get("/api/popular-endpoint")
except APITimeoutError:
    print("Connection pool saturated - implement backpressure")
```

### Timeout Configuration Guidelines

**High-latency environments** (satellite internet, international connections):
```python
TimeoutConfig(
    connect=30.0,   # Longer connection establishment
    read=120.0,     # Allow for slow responses
    write=60.0,     # Large upload tolerance
    pool=15.0       # More patience for pool
)
```

**Low-latency environments** (internal microservices):
```python
TimeoutConfig(
    connect=2.0,    # Fast connection expected
    read=5.0,       # Quick responses required
    write=5.0,      # Fast uploads expected
    pool=1.0        # Pool should be readily available
)
```

**Progressive timeout strategy**:
```python
async def adaptive_timeout_request(url, base_timeout=5.0):
    """Implement progressive timeout increases."""
    for attempt in range(3):
        timeout_multiplier = 1 + (attempt * 0.5)  # 1x, 1.5x, 2x
        current_timeout = base_timeout * timeout_multiplier

        try:
            response = await client.get(
                url,
                timeout=TimeoutConfig(read=current_timeout)
            )
            return response
        except APITimeoutError:
            if attempt == 2:  # Last attempt
                raise
            print(f"Timeout at {current_timeout}s, retrying with longer timeout")
```

---

## Pattern 2: Retry Pattern

### What is the Retry Pattern?

The retry pattern automatically re-attempts failed operations, typically with increasing delays between attempts (exponential backoff).

### Why Retries Matter

**The Problem**: Many failures in distributed systems are **transient**:
- Network packet loss (usually < 1% but happens)
- Temporary service overload (traffic spikes)
- Database connection pool exhaustion (momentary)
- DNS resolution hiccups (cache misses)

**Without retries**: These temporary failures cause permanent operation failures, leading to poor user experience and system reliability.

### Exponential Backoff Strategy

Our retry pattern uses **exponential backoff with jitter**:

```
Attempt 1: Immediate
Attempt 2: Wait 1-2 seconds (1s base + random jitter)
Attempt 3: Wait 2-4 seconds (2s base + random jitter)
Attempt 4: Wait 4-8 seconds (4s base + random jitter)
Maximum: Never wait more than 60 seconds
```

**Why exponential?** Each failure might indicate increasing severity - back off more aggressively.

**Why jitter?** Prevents "thundering herd" - if 1000 clients all retry at the same time, they create a traffic spike that prevents recovery.

### Configuration Examples

**Conservative retries** (user-facing operations):
```python
from client import RetryConfig

retry_config = RetryConfig(
    max_attempts=3,          # Limited retries for fast failure
    min_wait_seconds=0.5,    # Quick first retry
    max_wait_seconds=10.0,   # Don't make users wait too long
    multiplier=2.0,          # Standard exponential backoff
    jitter=True              # Prevent thundering herd
)
```

**Aggressive retries** (background processing):
```python
retry_config = RetryConfig(
    max_attempts=5,          # More retries for background work
    min_wait_seconds=1.0,    #
    max_wait_seconds=300.0,  # Can wait up to 5 minutes
    multiplier=2.0,
    jitter=True
)
```

### What Should Be Retried?

**✅ Retry these errors** (transient/infrastructure issues):
- Network timeouts
- Connection refused
- HTTP 502 (Bad Gateway)
- HTTP 503 (Service Unavailable)
- HTTP 504 (Gateway Timeout)
- DNS resolution failures

**❌ Don't retry these errors** (permanent/client issues):
- HTTP 400 (Bad Request) - your request is malformed
- HTTP 401 (Unauthorized) - your credentials are wrong
- HTTP 404 (Not Found) - resource doesn't exist
- HTTP 422 (Unprocessable Entity) - validation error

**Code example**:
```python
from client.exceptions import APIConnectionError, APIStatusError

async def smart_retry_example():
    try:
        response = await client.get("/api/user/123")
        return response.json()
    except APIConnectionError as e:
        # These will be automatically retried by the client
        print(f"Infrastructure issue, retrying: {e}")
        raise  # Let the retry mechanism handle it
    except APIStatusError as e:
        # These will NOT be retried - fix the request instead
        print(f"Client error, fix the request: {e}")
        if "401" in str(e):
            await refresh_auth_token()
            # Now retry with new token
            response = await client.get("/api/user/123")
            return response.json()
        raise  # Other client errors can't be fixed by retrying
```

### Idempotency: The Key to Safe Retries

**The Problem**: What if a retry succeeds but you don't receive the response?

```
Request 1: POST /api/orders (creates order #12345) → Network timeout
Request 2: POST /api/orders (creates order #12346) → Success!

Result: Customer charged twice! 😱
```

**The Solution**: Idempotency keys

```python
import uuid

async def create_order_safely(order_data):
    # Generate unique idempotency key
    idempotency_key = str(uuid.uuid4())

    # Server will check this key to prevent duplicates
    response = await client.post(
        "/api/orders",
        json=order_data,
        request_id=idempotency_key  # Our client adds this as X-Request-ID header
    )

    # Even if this request is retried, the server will return the same
    # order instead of creating a duplicate
    return response.json()
```

### Retry Pattern Best Practices

1. **Set appropriate limits**:
   ```python
   # For user-facing operations
   max_attempts=3  # Fast failure

   # For background jobs
   max_attempts=10  # More resilience
   ```

2. **Use jitter to prevent thundering herd**:
   ```python
   jitter=True  # Always enable in production
   ```

3. **Monitor retry rates**:
   ```python
   # High retry rates indicate service issues
   retry_rate = failed_with_retries / total_requests
   if retry_rate > 0.10:  # 10% retry rate
       alert("High retry rate detected - investigate service health")
   ```

4. **Implement circuit breaker with retries** (see next pattern):
   ```python
   # Circuit breaker prevents wasted retries when service is known to be down
   # Retries handle transient failures when service is healthy
   ```

---

## Pattern 3: Circuit Breaker Pattern

### What is the Circuit Breaker Pattern?

The circuit breaker pattern monitors for failures and "opens" (stops making calls) when failure rates exceed a threshold. After a recovery period, it "half-opens" to test if the service has recovered.

**Analogy**: Like an electrical circuit breaker that trips to prevent fires when circuits are overloaded.

### Why Circuit Breakers Matter

**The Problem**: Without circuit breakers, failing services get overwhelmed:

```
Payment Service starts failing (database issues)
↓
All clients keep retrying failed requests
↓
Payment Service gets 1000x normal traffic (retries)
↓
Payment Service becomes completely unresponsive
↓
Failure spreads to other services (cascading failure)
↓
Entire system goes down
```

**The Solution**: Circuit breakers detect failure and stop making calls:

```
Payment Service starts failing
↓
Circuit breaker detects failures (5 consecutive failures)
↓
Circuit "opens" - all future calls fail fast
↓
Payment Service gets zero traffic (time to recover)
↓
After 30 seconds, circuit tries one request ("half-open")
↓
If successful, circuit "closes" and normal traffic resumes
↓
If failed, circuit stays "open" for another 30 seconds
```

### Circuit Breaker States

The circuit breaker has three states:

#### 1. CLOSED (Normal Operation)
- All requests proceed normally
- Failures are counted
- Success calls reset failure count

```python
# Circuit is CLOSED - normal operation
try:
    response = await client.get("/api/payment/process")
    # Success! Failure count resets to 0
    print("Payment processed successfully")
except APIConnectionError:
    # Failure counted (1/5 toward threshold)
    print("Payment failed, but circuit still closed")
```

#### 2. OPEN (Failing Fast)
- All requests fail immediately without trying the service
- Saves time and resources
- Protects the failing service from additional load

```python
# Circuit is OPEN - failing fast
try:
    response = await client.get("/api/payment/process")
except APIConnectionError as e:
    if "Circuit breaker is OPEN" in str(e):
        print("Payment service is down - using backup payment method")
        return await process_payment_with_backup()
```

#### 3. HALF_OPEN (Recovery Testing)
- Single request allowed to test service recovery
- Success → Circuit returns to CLOSED
- Failure → Circuit returns to OPEN

```python
# Circuit is HALF_OPEN - testing recovery
try:
    response = await client.get("/api/payment/process")
    print("Payment service recovered! Circuit now CLOSED")
except APIConnectionError:
    print("Payment service still failing - circuit back to OPEN")
```

### Configuration Examples

**Sensitive circuit breaker** (fail fast):
```python
from client import CircuitBreakerConfig

circuit_config = CircuitBreakerConfig(
    failure_threshold=3,     # Open after 3 failures
    recovery_timeout=10.0    # Test recovery every 10 seconds
)
```

**Tolerant circuit breaker** (more resilient):
```python
circuit_config = CircuitBreakerConfig(
    failure_threshold=10,    # Open after 10 failures
    recovery_timeout=60.0    # Test recovery every minute
)
```

### Circuit Breaker with Graceful Degradation

**The key insight**: Circuit breakers enable graceful degradation instead of total failure.

```python
async def get_product_recommendations(user_id):
    """Get product recommendations with graceful degradation."""

    try:
        # Try the ML recommendation service
        response = await client.get(f"/api/ml/recommendations/{user_id}")
        return response.json()["recommendations"]

    except APIConnectionError as e:
        if "Circuit breaker is OPEN" in str(e):
            # ML service is down - use fallback logic
            print("ML service down, using fallback recommendations")
            return await get_fallback_recommendations(user_id)
        else:
            # Transient failure - let retries handle it
            raise

async def get_fallback_recommendations(user_id):
    """Simple fallback when ML service is unavailable."""
    # Use cached popular products, user history, or simple rules
    return [
        {"id": 1, "name": "Popular Product 1"},
        {"id": 2, "name": "Popular Product 2"},
        {"id": 3, "name": "Popular Product 3"}
    ]
```

### Monitoring Circuit Breaker State

Circuit breaker state changes are important operational signals:

```python
import logging

# Monitor circuit breaker state changes
logger = logging.getLogger("circuit_monitor")

def monitor_circuit_breaker(client):
    """Log circuit breaker state for monitoring."""
    if client._circuit_breaker:
        cb = client._circuit_breaker
        state = cb.state.value
        failures = cb.failure_count
        threshold = cb.config.failure_threshold

        if state == "OPEN":
            logger.warning(
                f"Circuit breaker OPENED: {failures} failures "
                f"(threshold: {threshold})"
            )
        elif state == "HALF_OPEN":
            logger.info("Circuit breaker testing recovery (HALF_OPEN)")
        elif state == "CLOSED" and failures > 0:
            logger.info(f"Circuit breaker CLOSED: service recovered")

# Use this in production for alerting
```

### Circuit Breaker Anti-Patterns

**❌ Don't use circuit breakers for client errors**:
```python
# Bad - 404 errors don't indicate service health issues
circuit_config = CircuitBreakerConfig(failure_threshold=5)
# If you make 5 requests for non-existent resources, circuit opens unnecessarily
```

**❌ Don't set thresholds too low**:
```python
# Bad - one failure opens the circuit
circuit_config = CircuitBreakerConfig(failure_threshold=1)
# This makes the system too sensitive to transient issues
```

**❌ Don't ignore circuit breaker state**:
```python
# Bad - no fallback when circuit is open
try:
    response = await client.get("/api/data")
except APIConnectionError:
    raise  # User sees error, no graceful degradation
```

---

## Pattern 4: Bulkhead Pattern

### What is the Bulkhead Pattern?

The bulkhead pattern isolates different parts of a system so that failure in one part doesn't cascade to others. It's named after ship bulkheads - compartments that prevent the entire ship from sinking if one compartment floods.

### Why Bulkheads Matter

**The Problem**: Without isolation, one slow or failing dependency can consume all available resources:

```
Your App (50 worker threads)
├── Fast Service A (normally 10ms response time)
├── Slow Service B (currently 30 seconds response time - having issues)
└── Critical Service C (normally 50ms response time)

Without bulkheads:
- All 50 threads get tied up waiting for Service B
- Requests to Service A and C start queuing up
- Your entire application becomes unresponsive
- Users can't access ANY functionality
```

**The Solution**: Isolate dependencies with separate resource pools:

```
Your App
├── Pool for Service A (15 threads) ✅ Still responsive
├── Pool for Service B (10 threads) ❌ Saturated, but isolated
├── Pool for Service C (15 threads) ✅ Still responsive
└── Pool for other work (10 threads) ✅ Still responsive

Result: Service B problems don't affect other services
```

### How Our Bulkhead Works

Our client implements bulkheads using **asyncio.Semaphore**:

```python
from client import BulkheadConfig

bulkhead_config = BulkheadConfig(
    max_concurrency=20,        # Maximum concurrent requests
    acquisition_timeout=5.0    # How long to wait for a slot
)
```

**Example flow**:
```python
# Request 1-20: Acquire semaphore immediately ✅
# Request 21: Waits for a slot to become available ⏳
# Request 22: Waits in queue ⏳
# After 5 seconds: If no slot available, PoolTimeoutError ❌
```

### Bulkhead Configuration Strategies

#### Per-Service Bulkheads

**Best practice**: Use different clients with different bulkhead limits for different services:

```python
# Critical user-facing service - higher priority
user_service_client = ResilientClient(ClientConfig(
    base_url="https://user-service.internal",
    bulkhead=BulkheadConfig(
        max_concurrency=30,      # More slots for critical service
        acquisition_timeout=1.0   # Fail fast if saturated
    )
))

# Analytics service - lower priority
analytics_client = ResilientClient(ClientConfig(
    base_url="https://analytics.internal",
    bulkhead=BulkheadConfig(
        max_concurrency=5,       # Fewer slots for non-critical service
        acquisition_timeout=10.0  # More patience for background work
    )
))

# Background job service - lowest priority
background_client = ResilientClient(ClientConfig(
    base_url="https://background-jobs.internal",
    bulkhead=BulkheadConfig(
        max_concurrency=2,       # Very limited slots
        acquisition_timeout=30.0  # Can wait longer
    )
))
```

#### Dynamic Bulkhead Sizing

**Advanced pattern**: Adjust bulkhead sizes based on service health:

```python
class AdaptiveBulkhead:
    def __init__(self, base_concurrency=20):
        self.base_concurrency = base_concurrency
        self.current_concurrency = base_concurrency
        self.error_rate = 0.0

    def adjust_for_service_health(self, recent_error_rate):
        """Reduce concurrency when service is struggling."""
        self.error_rate = recent_error_rate

        if recent_error_rate > 0.20:  # 20% error rate
            # Reduce load on struggling service
            self.current_concurrency = max(5, self.base_concurrency // 2)
        elif recent_error_rate < 0.05:  # 5% error rate
            # Service is healthy, can handle more load
            self.current_concurrency = min(self.base_concurrency * 2, 100)
        else:
            # Normal load
            self.current_concurrency = self.base_concurrency

        return self.current_concurrency

# Usage
adaptive = AdaptiveBulkhead(base_concurrency=20)
new_limit = adaptive.adjust_for_service_health(error_rate=0.15)
# Reconfigure client with new limit...
```

### Handling Bulkhead Saturation

When bulkheads are saturated, you have several options:

#### 1. Fail Fast
```python
try:
    response = await client.get("/api/data")
except PoolTimeoutError:
    # Client is saturated - inform user immediately
    return {"error": "Service temporarily unavailable - please try again"}
```

#### 2. Graceful Degradation
```python
try:
    # Try primary service
    response = await primary_client.get("/api/recommendations")
    return response.json()
except PoolTimeoutError:
    # Primary service saturated - use simpler fallback
    return get_cached_recommendations()
```

#### 3. Request Queuing with Backpressure
```python
import asyncio
from asyncio import Queue

class RequestQueue:
    def __init__(self, max_queue_size=100):
        self.queue = Queue(maxsize=max_queue_size)
        self.processing = False

    async def add_request(self, request_func):
        """Add request to queue with backpressure."""
        try:
            # Fail fast if queue is full (backpressure)
            await asyncio.wait_for(
                self.queue.put(request_func),
                timeout=1.0
            )
        except asyncio.TimeoutError:
            raise PoolTimeoutError("Request queue full - system overloaded")

    async def process_queue(self, client):
        """Process queued requests with bulkhead protection."""
        while True:
            request_func = await self.queue.get()
            try:
                await request_func(client)
            except PoolTimeoutError:
                # Put request back in queue to retry later
                await self.queue.put(request_func)
                await asyncio.sleep(1.0)  # Back off
```

#### 4. Load Shedding
```python
import random

async def load_shedding_request(request_func, shed_probability=0.1):
    """Randomly reject requests when system is overloaded."""

    # Check system health metrics
    cpu_usage = get_cpu_usage()
    memory_usage = get_memory_usage()

    # Calculate shed probability based on system load
    if cpu_usage > 80 or memory_usage > 85:
        shed_probability = 0.3  # Shed 30% of requests under high load

    if random.random() < shed_probability:
        raise PoolTimeoutError("Request shed due to high system load")

    return await request_func()
```

### Bulkhead Monitoring

Monitor bulkhead utilization to tune configuration:

```python
async def monitor_bulkhead_utilization(client):
    """Monitor and log bulkhead utilization."""
    if client._semaphore:
        total_slots = client.config.bulkhead.max_concurrency
        available_slots = client._semaphore._value
        used_slots = total_slots - available_slots
        utilization = (used_slots / total_slots) * 100

        print(f"Bulkhead utilization: {utilization:.1f}% ({used_slots}/{total_slots})")

        # Alert on high utilization
        if utilization > 80:
            logger.warning(f"High bulkhead utilization: {utilization:.1f}%")

        return {
            "total_slots": total_slots,
            "used_slots": used_slots,
            "utilization_percent": utilization
        }
```

---

## How Patterns Work Together

The real power comes from combining patterns. Here's how they interact in our resilient client:

### The Complete Request Flow

```
1. REQUEST INITIATED
   ↓
2. BULKHEAD: Acquire semaphore slot (wait if necessary)
   ├─ Success: Continue
   └─ Timeout: Raise PoolTimeoutError
   ↓
3. CIRCUIT BREAKER: Check if service is healthy
   ├─ CLOSED: Proceed with request
   ├─ HALF_OPEN: Allow one test request
   └─ OPEN: Fail fast with APIConnectionError
   ↓
4. RETRY LOOP: Execute request with automatic retries
   ├─ TIMEOUT: Applied to each retry attempt
   ├─ Success: Return response
   └─ All retries failed: Raise last exception
   ↓
5. CIRCUIT BREAKER UPDATE: Record success/failure
   ├─ Success: Reset failure count
   └─ Failure: Increment count, maybe open circuit
   ↓
6. BULKHEAD CLEANUP: Release semaphore slot (always)
```

### Pattern Interaction Examples

#### Example 1: Healthy Service
```python
# Normal operation - all patterns working harmoniously
response = await client.get("/api/data")

# Flow:
# 1. Bulkhead: Acquire slot immediately (low load) ✅
# 2. Circuit: CLOSED state, proceed ✅
# 3. Timeout: 30s limit, response in 100ms ✅
# 4. Retry: First attempt succeeds ✅
# 5. Circuit: Record success, stay CLOSED ✅
# 6. Bulkhead: Release slot ✅
```

#### Example 2: Temporarily Slow Service
```python
# Service is slow but functional
response = await client.get("/api/slow-report")

# Flow:
# 1. Bulkhead: Acquire slot (may wait briefly) ✅
# 2. Circuit: CLOSED state, proceed ✅
# 3. Timeout: First attempt times out after 30s ❌
# 4. Retry: Wait 1s, retry succeeds in 25s ✅
# 5. Circuit: Record success, stay CLOSED ✅
# 6. Bulkhead: Release slot (held for ~56s total) ✅
```

#### Example 3: Failing Service (Circuit Opens)
```python
# Service is down - circuit breaker protects system
try:
    response = await client.get("/api/broken-service")
except APIConnectionError as e:
    print(f"Service unavailable: {e}")

# Flow over multiple requests:
# Request 1-5: All fail, circuit stays CLOSED ❌
# Request 6: Circuit opens, future requests fail fast ⚡
# Next 30 seconds: All requests fail immediately (no network calls)
# After 30 seconds: Circuit tries HALF_OPEN test
```

#### Example 4: System Overload (Bulkhead Protection)
```python
# High traffic - bulkhead prevents cascade failure
try:
    response = await client.get("/api/popular-endpoint")
except PoolTimeoutError:
    print("System overloaded - implement backpressure")

# Flow:
# 1. Bulkhead: No slots available, wait 5s ⏳
# 2. Bulkhead: Still no slots, raise PoolTimeoutError ❌
# 3. Circuit: Never reached (failed at bulkhead)
# 4. Network: Never reached (failed at bulkhead)
# Result: System protected from overload ✅
```

### Configuration for Pattern Harmony

**Aligned configuration** ensures patterns work together effectively:

```python
# Well-coordinated configuration
config = ClientConfig(
    base_url="https://api.example.com",

    # Timeouts: Progressive from fast to slow
    timeout=TimeoutConfig(
        connect=5.0,    # Quick connection check
        read=30.0,      # Allow reasonable response time
        write=15.0,     # Moderate upload time
        pool=2.0        # Fast pool access expected
    ),

    # Retries: Limited attempts with reasonable timing
    retry=RetryConfig(
        max_attempts=3,          # 3 tries total
        min_wait_seconds=1.0,    # Start with 1s wait
        max_wait_seconds=30.0,   # Don't wait longer than read timeout
        jitter=True              # Prevent thundering herd
    ),

    # Circuit breaker: Allow several retry sequences before opening
    circuit_breaker=CircuitBreakerConfig(
        failure_threshold=5,     # 5 failures = ~2 retry sequences
        recovery_timeout=60.0    # Give service time to recover
    ),

    # Bulkhead: Reasonable concurrency with fast timeout
    bulkhead=BulkheadConfig(
        max_concurrency=20,      # Allow good throughput
        acquisition_timeout=5.0  # Fail fast if saturated
    )
)
```

**Why this works well**:
- Read timeout (30s) > retry max wait (30s) → retries won't be cut off
- Circuit threshold (5) allows multiple retry sequences before opening
- Bulkhead timeout (5s) is much less than retry timing → fast feedback
- All timeouts are reasonable for typical service response times

---

## Common Anti-Patterns to Avoid

### 1. Timeout Anti-Patterns

**❌ No timeouts at all**:
```python
# Bad - can hang forever
response = await httpx.get("https://api.example.com/data")
```

**❌ Timeouts longer than user patience**:
```python
# Bad - 5 minute timeout for user-facing operation
timeout=TimeoutConfig(read=300.0)  # Users will close browser/app
```

**❌ Inconsistent timeout hierarchy**:
```python
# Bad - read timeout shorter than connect timeout
timeout=TimeoutConfig(
    connect=30.0,
    read=10.0     # This makes no sense
)
```

### 2. Retry Anti-Patterns

**❌ Retrying non-retryable errors**:
```python
# Bad - retrying client errors
try:
    response = await client.post("/api/orders", json=invalid_data)
except InvalidRequestError:
    # Don't retry - fix the data instead!
    await asyncio.sleep(1)
    response = await client.post("/api/orders", json=invalid_data)  # Still fails
```

**❌ Infinite retries**:
```python
# Bad - can retry forever
while True:
    try:
        response = await client.get("/api/data")
        break
    except APIConnectionError:
        await asyncio.sleep(1)  # Infinite loop!
```

**❌ No jitter (thundering herd)**:
```python
# Bad - all clients retry at same time
retry_config = RetryConfig(
    max_attempts=5,
    min_wait_seconds=10.0,
    max_wait_seconds=10.0,  # Fixed timing
    jitter=False            # No randomization
)
# Result: 1000 clients all retry at exactly the same time
```

### 3. Circuit Breaker Anti-Patterns

**❌ Ignoring circuit breaker state**:
```python
# Bad - no graceful degradation
try:
    response = await client.get("/api/recommendations")
    return response.json()
except APIConnectionError:
    raise  # User sees error - no fallback
```

**❌ Too sensitive threshold**:
```python
# Bad - opens after one failure
circuit_config = CircuitBreakerConfig(failure_threshold=1)
# Result: Circuit opens on any transient failure
```

**❌ Counting client errors**:
```python
# Bad - manual circuit breaker counting all errors
failure_count = 0
try:
    response = await client.get("/api/nonexistent")
except NotFoundError:  # 404 error
    failure_count += 1  # This shouldn't count as service failure!
```

### 4. Bulkhead Anti-Patterns

**❌ No resource isolation**:
```python
# Bad - one client for all services
shared_client = ResilientClient(config)
await shared_client.get("/critical-user-service/data")      # Critical
await shared_client.get("/analytics/track-event")          # Non-critical
await shared_client.get("/slow-batch-processing/job")      # Slow

# Result: Slow batch job blocks critical user operations
```

**❌ Bulkhead too small**:
```python
# Bad - only 1 concurrent request allowed
bulkhead_config = BulkheadConfig(max_concurrency=1)
# Result: No parallelism, poor performance
```

**❌ No backpressure handling**:
```python
# Bad - no handling of PoolTimeoutError
try:
    response = await client.get("/api/data")
except PoolTimeoutError:
    pass  # Silently swallow error - user gets no response
```

---

## Real-World Scenarios

### Scenario 1: E-commerce Checkout

**Challenge**: Checkout depends on multiple services - any failure breaks the purchase flow.

**Services involved**:
- User service (authentication)
- Inventory service (stock checking)
- Payment service (charging credit card)
- Shipping service (delivery options)
- Order service (order creation)

**Resilience strategy**:

```python
async def process_checkout(user_id, cart_items, payment_info):
    """Resilient checkout with graceful degradation."""

    # Use separate clients for different service criticalities
    critical_client = ResilientClient(critical_service_config)  # User, Payment, Order
    standard_client = ResilientClient(standard_service_config)  # Inventory, Shipping

    checkout_result = {
        "order_id": None,
        "payment_status": "pending",
        "shipping_estimate": "unknown",
        "warnings": []
    }

    try:
        # Step 1: Verify user (critical - must succeed)
        user = await critical_client.get(f"/users/{user_id}")
        if not user.json()["active"]:
            raise ValueError("User account inactive")

        # Step 2: Check inventory (standard - can degrade gracefully)
        try:
            inventory = await standard_client.post("/inventory/reserve", json=cart_items)
            available_items = inventory.json()["available"]
        except APIConnectionError:
            # Inventory service down - allow purchase but warn user
            available_items = cart_items  # Assume available
            checkout_result["warnings"].append("Could not verify stock - items may be backordered")

        # Step 3: Calculate shipping (standard - can use fallback)
        try:
            shipping = await standard_client.post("/shipping/calculate", json={
                "user_id": user_id,
                "items": available_items
            })
            shipping_cost = shipping.json()["cost"]
            checkout_result["shipping_estimate"] = shipping.json()["delivery_date"]
        except APIConnectionError:
            # Shipping service down - use default rates
            shipping_cost = calculate_default_shipping(available_items)
            checkout_result["warnings"].append("Using standard shipping rates - actual cost may vary")

        # Step 4: Process payment (critical - must succeed)
        payment_request = {
            "user_id": user_id,
            "amount": calculate_total(available_items) + shipping_cost,
            "payment_info": payment_info
        }

        payment_result = await critical_client.post("/payments/charge", json=payment_request)
        checkout_result["payment_status"] = "completed"

        # Step 5: Create order (critical - must succeed)
        order_request = {
            "user_id": user_id,
            "items": available_items,
            "payment_id": payment_result.json()["payment_id"],
            "shipping_cost": shipping_cost
        }

        order = await critical_client.post("/orders", json=order_request)
        checkout_result["order_id"] = order.json()["order_id"]

        return checkout_result

    except APIConnectionError as e:
        if "payment" in str(e).lower():
            # Payment failure - critical error
            raise CheckoutError("Payment processing unavailable - please try again later")
        elif "user" in str(e).lower():
            # User service failure - critical error
            raise CheckoutError("Authentication service unavailable - please try again later")
        elif "order" in str(e).lower():
            # Order service failure after payment - need to handle carefully
            await rollback_payment(payment_result.json()["payment_id"])
            raise CheckoutError("Order creation failed - payment has been refunded")
        else:
            raise CheckoutError(f"Checkout failed: {e}")

# Configure different resilience for different service types
critical_service_config = ClientConfig(
    timeout=TimeoutConfig(connect=5.0, read=15.0),
    retry=RetryConfig(max_attempts=3, max_wait_seconds=10.0),
    circuit_breaker=CircuitBreakerConfig(failure_threshold=5, recovery_timeout=30.0),
    bulkhead=BulkheadConfig(max_concurrency=30)  # High priority
)

standard_service_config = ClientConfig(
    timeout=TimeoutConfig(connect=3.0, read=10.0),
    retry=RetryConfig(max_attempts=2, max_wait_seconds=5.0),
    circuit_breaker=CircuitBreakerConfig(failure_threshold=3, recovery_timeout=15.0),
    bulkhead=BulkheadConfig(max_concurrency=10)  # Lower priority
)
```

### Scenario 2: Social Media Feed

**Challenge**: Feed aggregation requires data from many services, but the feed must load quickly even if some services are slow.

**Services involved**:
- Posts service (user posts)
- Friends service (social graph)
- Media service (images/videos)
- Recommendations service (ML-powered suggestions)
- Ads service (advertising content)

**Resilience strategy**:

```python
async def load_user_feed(user_id, page_size=20):
    """Load social media feed with aggressive timeouts and graceful degradation."""

    # Use different timeout configurations for different service priorities
    fast_client = ResilientClient(ClientConfig(
        timeout=TimeoutConfig(connect=1.0, read=2.0),  # Very aggressive timeouts
        retry=RetryConfig(max_attempts=2, max_wait_seconds=3.0),
        bulkhead=BulkheadConfig(max_concurrency=50)
    ))

    slow_client = ResilientClient(ClientConfig(
        timeout=TimeoutConfig(connect=2.0, read=5.0),  # More tolerant
        retry=RetryConfig(max_attempts=3, max_wait_seconds=8.0),
        bulkhead=BulkheadConfig(max_concurrency=20)
    ))

    feed_data = {
        "posts": [],
        "recommendations": [],
        "ads": [],
        "load_time_ms": 0,
        "degraded_services": []
    }

    start_time = time.time()

    # Launch all requests concurrently for speed
    tasks = {}

    # Core posts - must succeed
    tasks["posts"] = slow_client.get(f"/posts/feed/{user_id}?limit={page_size}")

    # Friends list - important for personalization
    tasks["friends"] = fast_client.get(f"/friends/{user_id}")

    # Recommendations - nice to have
    tasks["recommendations"] = fast_client.get(f"/recommendations/{user_id}?limit=5")

    # Ads - revenue generating but not critical for UX
    tasks["ads"] = fast_client.get(f"/ads/feed/{user_id}?limit=3")

    # Execute all requests concurrently with individual error handling
    results = await execute_with_individual_fallbacks(tasks)

    # Process results with graceful degradation
    if "posts" in results:
        feed_data["posts"] = results["posts"].json()["posts"]
    else:
        # Posts failed - use cached posts or empty feed
        feed_data["posts"] = await get_cached_posts(user_id) or []
        feed_data["degraded_services"].append("posts")

    if "recommendations" in results:
        feed_data["recommendations"] = results["recommendations"].json()["items"]
    else:
        # Recommendations failed - use simple fallback
        feed_data["recommendations"] = await get_trending_posts(limit=5)
        feed_data["degraded_services"].append("recommendations")

    if "ads" in results:
        feed_data["ads"] = results["ads"].json()["ads"]
    else:
        # Ads failed - no ads shown (actually improves UX!)
        feed_data["degraded_services"].append("ads")

    # Personalize based on friends if available
    if "friends" in results:
        friends_list = results["friends"].json()["friends"]
        feed_data["posts"] = personalize_posts(feed_data["posts"], friends_list)
    else:
        feed_data["degraded_services"].append("friends")

    feed_data["load_time_ms"] = int((time.time() - start_time) * 1000)

    return feed_data

async def execute_with_individual_fallbacks(tasks):
    """Execute multiple tasks concurrently, handling failures individually."""
    results = {}

    # Wait for all tasks to complete (success or failure)
    completed_tasks = await asyncio.gather(*tasks.values(), return_exceptions=True)

    # Process results
    for (task_name, task), result in zip(tasks.items(), completed_tasks):
        if isinstance(result, Exception):
            logger.warning(f"Task {task_name} failed: {result}")
            # Don't include in results - will trigger fallback logic
        else:
            results[task_name] = result

    return results
```

### Scenario 3: Microservices Data Aggregation

**Challenge**: A dashboard needs to display data from 10+ microservices, but users expect sub-second load times.

**Resilience strategy**:

```python
async def load_dashboard(user_id):
    """Load dashboard with parallel requests and smart timeouts."""

    # Different clients for different service tiers
    clients = {
        "critical": ResilientClient(ClientConfig(
            timeout=TimeoutConfig(read=3.0),
            bulkhead=BulkheadConfig(max_concurrency=30)
        )),
        "standard": ResilientClient(ClientConfig(
            timeout=TimeoutConfig(read=1.5),  # Shorter timeout for speed
            bulkhead=BulkheadConfig(max_concurrency=20)
        )),
        "optional": ResilientClient(ClientConfig(
            timeout=TimeoutConfig(read=1.0),  # Very short timeout
            bulkhead=BulkheadConfig(max_concurrency=10)
        ))
    }

    # Define service priorities
    service_config = {
        # Critical - dashboard broken without these
        "user_profile": {"client": "critical", "fallback": get_cached_profile},
        "account_balance": {"client": "critical", "fallback": lambda uid: {"balance": "unavailable"}},

        # Standard - important but have fallbacks
        "recent_transactions": {"client": "standard", "fallback": get_cached_transactions},
        "notifications": {"client": "standard", "fallback": lambda uid: []},
        "quick_actions": {"client": "standard", "fallback": get_default_actions},

        # Optional - nice to have
        "recommendations": {"client": "optional", "fallback": lambda uid: []},
        "market_data": {"client": "optional", "fallback": get_cached_market_data},
        "social_feed": {"client": "optional", "fallback": lambda uid: []},
        "promotions": {"client": "optional", "fallback": lambda uid: []}
    }

    dashboard_data = {}

    # Create all tasks
    tasks = {}
    for service_name, config in service_config.items():
        client = clients[config["client"]]
        tasks[service_name] = fetch_service_data(client, service_name, user_id, config["fallback"])

    # Execute with timeout for entire dashboard load
    try:
        # Global timeout - dashboard must load in 2 seconds max
        results = await asyncio.wait_for(
            asyncio.gather(*tasks.values(), return_exceptions=True),
            timeout=2.0
        )

        # Process results
        for service_name, result in zip(service_config.keys(), results):
            if isinstance(result, Exception):
                logger.warning(f"Service {service_name} failed: {result}")
                # Fallback will be handled in fetch_service_data
                dashboard_data[service_name] = None
            else:
                dashboard_data[service_name] = result

    except asyncio.TimeoutError:
        # Global timeout exceeded - return partial data
        logger.warning("Dashboard load exceeded 2s timeout - returning partial data")
        # Cancel remaining tasks
        for task_name, task in tasks.items():
            if not task.done():
                task.cancel()

        # Use fallbacks for any missing data
        for service_name, config in service_config.items():
            if service_name not in dashboard_data or dashboard_data[service_name] is None:
                try:
                    dashboard_data[service_name] = await config["fallback"](user_id)
                except Exception:
                    dashboard_data[service_name] = None

    return dashboard_data

async def fetch_service_data(client, service_name, user_id, fallback_func):
    """Fetch data from a service with automatic fallback."""
    try:
        response = await client.get(f"/{service_name}/{user_id}")
        return response.json()
    except Exception as e:
        logger.info(f"Service {service_name} failed, using fallback: {e}")
        return await fallback_func(user_id)
```

---

## Summary

Resilience patterns are essential tools for building robust distributed systems. They work together to create systems that:

- **Fail fast** when problems are detected (timeouts, circuit breakers)
- **Recover automatically** from transient issues (retries)
- **Isolate failures** to prevent cascading problems (bulkheads, circuit breakers)
- **Degrade gracefully** when dependencies are unavailable (fallback strategies)

### Key Takeaways for Junior Engineers

1. **Start with patterns, not libraries** - Understand the concepts before diving into implementation details
2. **Configure for your environment** - High-latency networks need different settings than local microservices
3. **Monitor pattern behavior** - Logs and metrics tell you if patterns are working correctly
4. **Design for failure** - Assume dependencies will fail and plan graceful degradation
5. **Test failure scenarios** - Use chaos engineering to verify your resilience patterns work

### Next Steps

1. **Practice**: Implement these patterns in your applications
2. **Monitor**: Set up observability for pattern behavior
3. **Tune**: Adjust configuration based on real-world performance
4. **Test**: Use failure injection to verify resilience
5. **Learn**: Study more advanced patterns like saga pattern, CQRS, and event sourcing

Remember: Resilience is not about preventing failures - it's about handling them gracefully when they inevitably occur. These patterns are your tools for building systems that users can depend on, even when the world is falling apart around them.
