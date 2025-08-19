"""
Custom exception hierarchy for the resilient HTTP client library.

This module defines a comprehensive exception hierarchy that provides clear
error contracts to users while maintaining semantic meaning for retry logic
and circuit breaker behavior.

**Why Custom Exceptions Matter:**

Exception hierarchies are crucial for resilient systems because they enable:
1. Semantic error handling - different exceptions mean different recovery strategies
2. Automated retry decisions - which errors should trigger retries
3. Circuit breaker logic - which errors indicate service health issues
4. Clear debugging - specific exception types help identify root causes

**Exception Hierarchy Design:**

```
MyAPIError (base exception)
├── APIConnectionError (retryable - infrastructure issues)
│   ├── APITimeoutError (client-side timeouts)
│   ├── ServerTimeoutError (server returned 408)
│   ├── ServiceUnavailableError (502, 503, 504)
│   └── ServerError (other 5xx errors)
├── APIStatusError (non-retryable - application issues)
│   ├── AuthenticationError (401, 403)
│   ├── NotFoundError (404)
│   └── InvalidRequestError (400, 422)
└── PoolTimeoutError (bulkhead pattern failure)
```

**Exception Handling Strategy:**

```python
try:
    response = await client.get("/api/data")
except APIConnectionError:
    # These will be automatically retried by the client
    # and may trip the circuit breaker
    log.warning("Infrastructure issue - automatic retry will happen")
    raise
except APIStatusError as e:
    # These indicate client-side issues - fix the request
    if isinstance(e, AuthenticationError):
        log.error("Check API credentials")
    elif isinstance(e, NotFoundError):
        log.info("Resource doesn't exist")
    raise
except PoolTimeoutError:
    # System is overloaded - back off or scale up
    log.error("Client is saturated - reduce load or increase concurrency")
    raise
```

**Educational Example - Exception Flow:**

```
HTTP Request → httpx exception mapping → semantic exceptions

httpx.ConnectError → APIConnectionError → Retry + Circuit Breaker
httpx.TimeoutException → APITimeoutError → Retry + Circuit Breaker
httpx.HTTPStatusError(401) → AuthenticationError → No Retry
httpx.HTTPStatusError(500) → ServerError → Retry + Circuit Breaker
Semaphore timeout → PoolTimeoutError → No Retry (client issue)
```

**Best Practices for Junior Engineers:**

1. Always catch specific exception types, not the base Exception
2. Log different exception types with appropriate log levels
3. Only retry APIConnectionError and subclasses
4. Use exception attributes (response, request) for debugging
5. Don't swallow exceptions - re-raise or convert appropriately
"""

from typing import Any, Optional


class MyAPIError(Exception):
    """
    Base exception for all library errors.

    All exceptions in the resilient client inherit from this base class,
    providing consistent interfaces for error handling and debugging.

    **Key Features:**
    - Preserves original HTTP response and request for debugging
    - Provides consistent message formatting
    - Enables broad exception catching when needed

    **Usage Example:**
    ```python
    try:
        response = await client.get("/api/data")
    except MyAPIError as e:
        log.error(f"API call failed: {e.message}")
        if e.response:
            log.debug(f"Status: {e.response.status_code}")
            log.debug(f"Headers: {e.response.headers}")
    ```

    Attributes:
        message: Human-readable error description
        response: HTTP response object (if available)
        request: HTTP request object (if available)
    """

    def __init__(
        self,
        message: str = "",
        response: Optional[Any] = None,
        request: Optional[Any] = None,
    ):
        self.message = message
        self.response = response
        self.request = request
        super().__init__(message)


class APIConnectionError(MyAPIError):
    """
    Base for all retryable errors indicating infrastructure issues.

    These errors represent problems with the network, connectivity, or
    server-side issues that may be resolved by retrying the request.
    The retry mechanism and circuit breaker both monitor for these exceptions.

    **When This Occurs:**
    - Network connectivity issues
    - DNS resolution failures
    - TCP connection timeouts
    - Server overload (5xx errors)
    - Gateway/proxy errors

    **Automatic Behavior:**
    - Triggers retry with exponential backoff
    - Counts toward circuit breaker failure threshold
    - Logged as warnings (expected in distributed systems)

    **Recovery Strategies:**
    - Automatic retries will handle transient issues
    - Circuit breaker will fast-fail if service is consistently down
    - Monitor for patterns indicating systemic issues

    Subclasses represent specific types of connection issues for more
    granular error handling and monitoring.
    """

    pass


class APITimeoutError(APIConnectionError):
    """
    A client-side timeout occurred during the request.

    **Common Causes:**
    - Slow network connections
    - Server processing delays
    - Large response payloads
    - Network congestion

    **Tuning Options:**
    - Increase timeout values in TimeoutConfig
    - Reduce request payload size
    - Implement request pagination
    - Consider async patterns for long operations

    **Monitoring Tips:**
    - Track timeout patterns by endpoint
    - Monitor p95/p99 response times
    - Alert on timeout rate spikes
    """

    pass


class ServerTimeoutError(APIConnectionError):
    """
    The server returned an HTTP 408 Request Timeout.

    **What This Means:**
    - Server actively rejected request due to timeout
    - Different from client-side timeout (APITimeoutError)
    - Server may be overloaded or have strict time limits

    **Typical Scenarios:**
    - Long-running queries or computations
    - Server-side rate limiting
    - Gateway/proxy timeouts
    - Database query timeouts

    **Recovery Approaches:**
    - Retry with exponential backoff
    - Break large requests into smaller chunks
    - Implement async patterns for long operations
    """

    pass


class ServiceUnavailableError(APIConnectionError):
    """
    The server is temporarily unavailable (HTTP 502, 503, 504).

    **Status Code Meanings:**
    - 502 Bad Gateway: Proxy received invalid response from upstream
    - 503 Service Unavailable: Server temporarily overloaded or down
    - 504 Gateway Timeout: Proxy timeout waiting for upstream

    **Common Scenarios:**
    - Server deployments or restarts
    - Database connectivity issues
    - High load/traffic spikes
    - Dependent service failures

    **Automatic Recovery:**
    - Retries with exponential backoff
    - Circuit breaker may open after threshold failures
    - Consider implementing client-side load balancing
    """

    pass


class ServerError(APIConnectionError):
    """
    A generic 5xx server error occurred.

    **Covers HTTP status codes:**
    - 500 Internal Server Error
    - 501 Not Implemented
    - 505 HTTP Version Not Supported
    - Other 5xx codes not specifically handled

    **Implications:**
    - Server-side bug or misconfiguration
    - Temporary server issues
    - Resource exhaustion
    - Unhandled exceptions in server code

    **Response Strategy:**
    - Automatic retries (may be temporary issue)
    - Log details for server team investigation
    - Monitor error rates for patterns
    - Circuit breaker protection for consistent failures
    """

    pass


class APIStatusError(MyAPIError):
    """
    Base for non-retryable HTTP status errors (client errors).

    These errors indicate problems with the request itself rather than
    infrastructure issues. Retrying without changing the request will
    likely result in the same error.

    **Characteristics:**
    - Usually HTTP 4xx status codes
    - Indicate client-side problems
    - Should NOT be retried automatically
    - Do NOT count toward circuit breaker failures

    **Common Causes:**
    - Invalid authentication credentials
    - Malformed request data
    - Requesting non-existent resources
    - Permission/authorization issues

    **Handling Strategy:**
    - Fix the request before retrying
    - Log as application errors (not warnings)
    - Alert developers to fix application logic
    """

    pass


class AuthenticationError(APIStatusError):
    """
    The request was not authorized (HTTP 401, 403).

    **Status Code Meanings:**
    - 401 Unauthorized: Missing or invalid authentication
    - 403 Forbidden: Valid auth but insufficient permissions

    **Common Causes:**
    - Expired API tokens or credentials
    - Invalid API keys
    - Insufficient user permissions
    - Credential rotation not completed

    **Resolution Steps:**
    1. Verify API credentials are correct
    2. Check token expiration and refresh if needed
    3. Confirm user has required permissions
    4. Review API documentation for auth requirements

    **Not Retryable:** Retrying with same credentials will fail.
    """

    pass


class NotFoundError(APIStatusError):
    """
    The requested resource was not found (HTTP 404).

    **Common Scenarios:**
    - Resource ID doesn't exist
    - URL path is incorrect
    - Resource was deleted
    - Permissions prevent access (some APIs return 404 instead of 403)

    **Debugging Tips:**
    - Verify resource IDs are correct
    - Check API documentation for correct endpoints
    - Confirm resource wasn't deleted
    - Test with known-good resource IDs

    **Not Retryable:** Resource either doesn't exist or won't appear by retrying.
    """

    pass


class InvalidRequestError(APIStatusError):
    """
    The request was malformed (HTTP 400, 422).

    **Status Code Meanings:**
    - 400 Bad Request: Syntax error, invalid request format
    - 422 Unprocessable Entity: Valid syntax but semantic errors

    **Common Issues:**
    - Invalid JSON syntax
    - Missing required fields
    - Invalid field values or types
    - Constraint violations

    **Resolution:**
    - Validate request data before sending
    - Check API documentation for required fields
    - Review error response for specific validation errors
    - Fix data validation in application code

    **Not Retryable:** Request must be corrected before retrying.
    """

    pass


class PoolTimeoutError(MyAPIError):
    """
    Failed to acquire a connection slot from the bulkhead pattern.

    **What This Means:**
    - Client has reached maximum concurrency limit
    - All semaphore slots are in use by other requests
    - New requests couldn't acquire a slot within timeout

    **Common Causes:**
    - Application making too many concurrent requests
    - Slow upstream service holding connections
    - Bulkhead concurrency limit set too low
    - Load spikes overwhelming the client

    **Resolution Strategies:**
    1. **Immediate:** Reduce request rate or implement backpressure
    2. **Short-term:** Increase bulkhead max_concurrency if appropriate
    3. **Long-term:** Scale client instances or improve request patterns

    **Monitoring:**
    - Track pool timeout rates
    - Monitor concurrent request counts
    - Alert on pool saturation patterns

    **Not Retryable by resilience patterns:** This is a client-side resource
    limitation, not a remote service issue.
    """

    pass
