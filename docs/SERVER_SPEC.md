# FastAPI Test Server Specification

## 1\. Overview 📜

This document specifies a **FastAPI** test server for validating the behavior of asynchronous Python HTTP clients. The server will provide a primary endpoint (`/msg`) that returns unique data but can be configured at runtime to simulate failures (e.g., 500 errors) and latency. It will also support idempotent requests via a request ID header.

Key operational parameters like concurrency limits, request timeouts, and cache settings will be configurable at startup. The server will feature detailed logging for observability and a health check endpoint for easy integration into automated test harnesses. The server's state will be managed in-memory, making it lightweight and easy to deploy for testing purposes.

---

## 2\. Server Configuration

The server must be configurable at launch time via environment variables or command-line arguments.

- **`MAX_CONCURRENCY`**: An integer defining the maximum number of simultaneous requests the server will process. Any requests arriving when this limit is reached should wait until a slot is free.
  - **Implementation**: Use an `asyncio.Semaphore` initialized with this value. A dependency or middleware should acquire the semaphore before processing a request and release it after.
- **`REQUEST_TIMEOUT`**: An integer in seconds defining the maximum time allowed for processing a single request. If a request (including any simulated delay) exceeds this duration, the server should return a `408 Request Timeout` error.
  - **Implementation**: Use `asyncio.wait_for` within a middleware to wrap the request-response cycle.
- **`CACHE_MAX_SIZE`**: An integer defining the maximum number of entries to store in the idempotency cache. When the limit is reached, the oldest entries should be evicted.
- **`CACHE_TTL_SECONDS`**: An integer in seconds defining the Time-To-Live for entries in the idempotency cache. Expired entries should be evicted.

---

## 3\. State Management

The server must maintain a shared state to handle failure injection and idempotent requests. This state must be managed in a thread-safe/task-safe manner.

- **Request ID Cache**: A cache object to store responses for idempotent requests.
  - **Implementation**: A custom class or a library-backed implementation that supports both **max item count** (`CACHE_MAX_SIZE`) and **Time-To-Live** (`CACHE_TTL_SECONDS`) eviction policies. Each cached value must store its creation timestamp.
  - **Access**: A lock (`asyncio.Lock`) must be used when reading from or writing to this cache to prevent race conditions.
- **Failure Mode State**: A data structure to track the current failure modes.
  - `failure_config: dict = {"fail_requests_count": 0, "fail_until_timestamp": None}`
  - **Behavior**: The `count` and `duration` failure modes operate **simultaneously**. The server will induce a failure if _either_ condition is met.
  - **Access**: A lock (`asyncio.Lock`) must be used when modifying this configuration.

---

## 4\. Logging & Observability 📊

The server must provide structured logs to give visibility into its state and decisions, which is crucial for debugging the client. Log levels should be used appropriately.

- **`INFO` Level**:
  - Server startup and shutdown.
  - Activation of a failure mode via a `/fail/...` endpoint, including the parameters.
  - Reset of failure modes.
- **`DEBUG` Level**:
  - A log entry for every request received, including its method, path, and key headers (`X-Request-ID`).
  - When a request is forced to wait due to the `MAX_CONCURRENCY` limit being reached, and when it acquires a semaphore.
  - Idempotency cache hits, misses, and evictions.
  - Each time a failure is intentionally injected into a response for `/msg`.

---

## 5\. API Endpoints

### 5.1. Core Endpoint

#### **`GET /msg`**

Returns a message containing a new UUID. Its behavior is modified by query parameters, headers, and the server's failure state.

- **Headers**:
  - `X-Request-ID` (optional, `str`): An identifier for idempotency.
- **Query Parameters**:
  - `delay` (optional, `int`): The number of milliseconds the server should wait before sending a response. Defaults to `0`.
- **Success Response (`200 OK`)**:
  ```json
  {
    "message_id": "a-unique-uuid-string"
  }
  ```
- **Error Response (`500 Internal Server Error`)**:
  ```json
  {
    "detail": "Induced server failure"
  }
  ```
- **Processing Logic**:
  1.  Acquire the concurrency semaphore.
  2.  Check the server's failure state. A failure is induced if `fail_requests_count > 0` **OR** if `fail_until_timestamp` is in the future.
  3.  If a failure should be induced, decrement the count (if active), and immediately return a `500` status code. **This check happens before the idempotency check.**
  4.  Check for an `X-Request-ID` header.
  5.  If `X-Request-ID` is present, check the `request_id_cache`. If a valid (non-expired) entry exists, return the cached UUID.
  6.  If the `delay` query parameter is present and positive, perform an `asyncio.sleep()` for the specified duration.
  7.  Generate a new `uuid.uuid4()`.
  8.  If an `X-Request-ID` was provided, store the new UUID in the `request_id_cache` with the `X-Request-ID` as the key.
  9.  Return the new UUID in the JSON response.
  10. Release the concurrency semaphore in a `finally` block to ensure it's always released.

### 5.2. Failure Injection Endpoints

These endpoints configure the server to simulate failures for the `/msg` endpoint. Setting one mode does not reset the other; they are additive.

#### **`POST /fail/count/{count}`**

Configures `/msg` to fail for a specific number of subsequent requests.

- **Action**: Sets the internal `fail_requests_count` state variable to the specified `count`.

#### **`POST /fail/duration/{seconds}`**

Configures `/msg` to fail for a specific duration.

- **Action**: Sets the internal `fail_until_timestamp` state variable to `current_time + duration`.

#### **`POST /fail/reset`**

Resets all failure configurations.

- **Action**: Resets `fail_requests_count` to `0` and `fail_until_timestamp` to `None`.

### 5.3. Health Endpoint

#### **`GET /health`**

A simple endpoint to confirm the server is running and responsive.

- **Success Response (`200 OK`)**:
  ```json
  {
    "status": "ok"
  }
  ```
- **Action**: Returns a `200 OK` response immediately. This endpoint is not subject to concurrency limits or failure injections.

---

## 6\. Example Test Flow ✅

1.  **Start Server**: Launch the server with `MAX_CONCURRENCY=50`, `REQUEST_TIMEOUT=10`, `CACHE_MAX_SIZE=1000`, `CACHE_TTL_SECONDS=300`.
2.  **Wait for Healthy**: The test runner polls `GET /health` until it receives a `200 OK`, ensuring the server is ready.
3.  **Configure Failure**: The test runner sends `POST /fail/count/3`.
4.  **Validate Failure**: The client sends three `GET` requests to `/msg`. All three should receive a `500 Internal Server Error`.
5.  **Validate Recovery**: The client sends a fourth `GET` request to `/msg`. It should now receive a `200 OK` with a new UUID.
6.  **Validate Idempotency**:
    - Client sends `GET /msg` with `X-Request-ID: test-123`. It receives `{"message_id": "uuid-A"}`.
    - Client sends another `GET /msg` with the same header. It must again receive `{"message_id": "uuid-A"}`.
7.  **Validate Failure Precedence**:
    - With `uuid-A` in the cache for `test-123`, the test runner sends `POST /fail/count/1`.
    - Client sends `GET /msg` with `X-Request-ID: test-123`. It should receive a `500` error, not the cached response.
    - Client sends the same request again. It should now receive the cached `200 OK` response with `uuid-A`.
