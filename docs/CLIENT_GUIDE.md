Of course. That's a solid logging strategy that balances visibility with signal-to-noise ratio.

I have now incorporated all of your feedback. The design document has been updated to include:

- An idempotency strategy using UUIDs for the `X-Request-ID` header.
- A more specific, multi-level custom exception hierarchy with a clear `RetryableError` base class.
- Refined retry logic that acts on this new hierarchy and treats 5xx errors as retryable.
- A bulkhead that times out instead of waiting indefinitely.
- A precise definition of what failures cause the circuit breaker to trip.
- The detailed logging strategy you just outlined.

Here is the updated and finalized design document:

---

# **Architecting a Resilient Asynchronous Python Client Library**

---

## **1. The Foundation - Mastering the httpx.AsyncClient Lifecycle**

The creation of a modern, high-performance asynchronous client library begins with its core component: the `httpx.AsyncClient`. Its effective use hinges on one critical principle: managing the client instance's lifecycle. Treating the `AsyncClient` as a long-lived, shared object is a fundamental requirement for building a performant library.

### **The Performance Imperative: Connection Pooling**

The `httpx.AsyncClient` is explicitly designed to mitigate the inefficiency of repeated TCP and TLS handshakes through connection pooling. When an `AsyncClient` instance is created, it manages an underlying pool of TCP connections. If multiple requests are made to the same host, the client will intelligently reuse an existing connection from the pool. This yields substantial performance gains by reducing latency and lowering CPU usage.

### **The Critical Anti-Pattern: The Per-Request AsyncClient**

Instantiating a new `AsyncClient` for each request negates the benefits of connection pooling, leading to severe performance degradation at scale. The root cause is not just the repeated handshakes but the costly **re-creation of the SSL context** with every new client instance.

### **Best Practice: Singleton Pattern and Dependency Injection**

To harness the full power of `httpx`, the `AsyncClient` should be instantiated once and reused throughout the application's lifetime. In the context of modern web frameworks like FastAPI, the recommended approach for managing the client's lifecycle is to use the `lifespan` context manager. This allows for the initialization of the `AsyncClient` on application startup and ensures it is closed gracefully on shutdown.

A well-designed client library should abstract this lifecycle management from the end-user by providing its own primary `Client` class that encapsulates and manages a singleton `httpx.AsyncClient` internally. This follows the Facade pattern and makes the library safer and easier to use correctly.

---

## **2. Implementing Granular and Effective Timeouts**

In distributed systems, failing to implement proper timeouts can lead to resource exhaustion. `httpx` provides a sophisticated, multi-faceted timeout system that enables developers to build truly resilient clients.

### **Dissecting `httpx.Timeout`**

The key to effective timeout management is the `httpx.Timeout` class. It allows for the configuration of four distinct timeout types: `connect`, `read`, `write`, and `pool`. A robust strategy often involves a longer `connect` timeout (e.g., 60s) to accommodate initial network latency and a shorter `read` timeout (e.g., 15s) to quickly detect a stalled response after a connection has been established.

These timeouts define the maximum duration for a _single attempt_. This is distinct from the overall time budget for an operation that includes retries, which is managed at a higher level.

---

## **3. The First Line of Defense - Intelligent Retries**

Transient failures are a fact of life in any distributed system. The retry pattern is the first and most crucial line of defense against this class of intermittent faults.

### **Idempotency with `X-Request-ID`**

Retrying non-idempotent operations (like a `POST` that creates a resource) can lead to data duplication. To combat this, the client must support an idempotency mechanism. The `ResilientClient` will automatically generate a `uuid.uuid4()` for each logical operation. This unique ID will be sent in the `X-Request-ID` header for the initial attempt and **all subsequent retries** of that operation, ensuring the server can safely identify and deduplicate the request.

### **Implementing with `tenacity`**

The `tenacity` library is the standard for implementing retry logic in Python. A robust retry strategy must incorporate **Exponential Backoff and Jitter** (`wait=wait_random_exponential(...)`) to prevent "thundering herd" problems where multiple clients retry in sync.

The retry mechanism will be configured to trigger based on a specific class of exceptions, as defined in our custom hierarchy. This provides a clean, semantic link between the type of error and the decision to retry.

---

## **4. Preventing Cascading Failures - The Circuit Breaker Pattern**

If a downstream service is experiencing a hard outage, persistent retries can amplify the problem. The Circuit Breaker pattern prevents this by monitoring for failures and "tripping" if a threshold is reached, causing subsequent calls to fail immediately without hitting the network.

### **Integration with Retries**

The retry logic will be placed _inside_ the operation protected by the circuit breaker. This means a single call that goes through its entire retry loop and ultimately fails will only count as **one** failure for the circuit breaker.

### **Defining "Failure"**

A key design decision is what constitutes a failure that can trip the breaker. The breaker will be configured to only act on legitimate service health issues. Therefore, only exceptions that inherit from our custom `APIConnectionError` (timeouts, network errors, and 5xx server errors) will be counted as failures. Client errors like `404 Not Found` will not affect the circuit breaker's state.

---

## **5. Resource Protection - The Bulkhead Pattern**

The Bulkhead pattern isolates system resources by limiting the number of concurrent requests to a given downstream service, protecting both the client and the server from being overwhelmed.

### **Implementation with `asyncio.Semaphore`**

The `ResilientClient` will use an `asyncio.Semaphore` to limit concurrent requests. The concurrency limit will be a separate configuration parameter on the client, independent of any server-side limits.

### **Bulkhead Timeouts**

A request waiting to acquire a slot from the bulkhead semaphore will not wait indefinitely. The acquisition attempt will be wrapped in a timeout (e.g., using `asyncio.wait_for`). If a slot cannot be acquired within this time, a new, non-retryable `PoolTimeoutError` will be raised, indicating that the client is saturated.

---

## **6. A Unified Exception Hierarchy**

A robust library must never leak exceptions from its underlying dependencies. We will define a custom exception hierarchy that provides a clear error contract to the user.

| Exception Class           | Inherits From        | Description                                       | When It's Raised                        |
| :------------------------ | :------------------- | :------------------------------------------------ | :-------------------------------------- |
| **`MyAPIError`**          | `Exception`          | The base exception for all library errors.        | Never raised directly.                  |
| **`APIConnectionError`**  | `MyAPIError`         | **Base for all retryable errors.**                | Never raised directly.                  |
| `APITimeoutError`         | `APIConnectionError` | A client-side timeout occurred.                   | Wraps `httpx.TimeoutException`.         |
| `ServerTimeoutError`      | `APIConnectionError` | The server returned a 408 timeout.                | On a `408` status code.                 |
| `ServiceUnavailableError` | `APIConnectionError` | The server is temporarily unavailable.            | On `502`, `503`, or `504` status codes. |
| `ServerError`             | `APIConnectionError` | A generic `5xx` server error occurred.            | On any `5xx` status code.               |
| **`APIStatusError`**      | `MyAPIError`         | Base for non-retryable API errors.                | Never raised directly.                  |
| `AuthenticationError`     | `APIStatusError`     | The request was not authorized.                   | On `401` or `403` status codes.         |
| `NotFoundError`           | `APIStatusError`     | The requested resource was not found.             | On a `404` status code.                 |
| `InvalidRequestError`     | `APIStatusError`     | The request was malformed.                        | On `400` or `422` status codes.         |
| **`PoolTimeoutError`**    | `MyAPIError`         | Failed to acquire a connection from the bulkhead. | When semaphore acquisition times out.   |

---

## **7. Logging and Observability**

To provide clear visibility into the client's internal operations, a structured logging strategy will be implemented. This is crucial for debugging client behavior, especially when interacting with a test server designed to simulate failures.

| Log Level     | Event                     | Description                                                                                                 |
| :------------ | :------------------------ | :---------------------------------------------------------------------------------------------------------- |
| **`WARNING`** | Circuit Breaker Opened    | The circuit has tripped due to excessive failures. Subsequent calls will fail fast.                         |
| **`INFO`**    | Circuit Breaker Closed    | The circuit has recovered after a successful probe request. Normal operation resumed.                       |
| **`DEBUG`**   | Retry Attempt             | "Operation `[X-Request-ID]` failed with `[Error]`. Retrying in `[N]` seconds (Attempt `[M]` of `[Total]`)." |
| **`DEBUG`**   | Bulkhead Wait             | "Operation `[X-Request-ID]` is waiting for a free connection slot."                                         |
| **`DEBUG`**   | Bulkhead Acquired         | "Operation `[X-Request-ID]` acquired a connection slot. `[N]` slots remaining."                             |
| **`DEBUG`**   | Idempotency Key Generated | "Generated new idempotency key `[UUID]` for operation."                                                     |
