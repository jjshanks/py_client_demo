"""
Circuit breaker implementation for the resilient HTTP client.

Implements the circuit breaker pattern to prevent cascading failures by
monitoring for connection errors and temporarily failing fast when
a service is determined to be unhealthy.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable
from enum import Enum
from functools import wraps
from typing import Any, Callable, Optional, TypeVar

from .config import CircuitBreakerConfig
from .exceptions import APIConnectionError

T = TypeVar("T")


class CircuitState(Enum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing fast
    HALF_OPEN = "half_open"  # Testing if service recovered


class CircuitBreaker:
    """
    Circuit breaker implementation that prevents cascading failures.

    The circuit breaker pattern is inspired by electrical circuit breakers that
    protect circuits from damage due to overcurrent. In software, it protects
    calling services from wasting resources on a failing dependency.

    **How It Works:**

    The circuit breaker monitors calls to a remote service and maintains one of
    three states based on recent failure patterns:

    1. **CLOSED (Normal Operation)**:
       - All calls proceed normally
       - Failures are counted toward threshold
       - Success calls reset failure count

    2. **OPEN (Failing Fast)**:
       - All calls fail immediately without trying the remote service
       - Prevents wasting time and resources on known-bad service
       - After recovery timeout, moves to HALF_OPEN for testing

    3. **HALF_OPEN (Recovery Testing)**:
       - Single call is allowed to test if service has recovered
       - Success → Circuit returns to CLOSED
       - Failure → Circuit returns to OPEN for another timeout period

    **Educational Example - State Transitions:**

    ```
    Initial State: CLOSED (0 failures)

    Request 1-4: Network timeouts → CLOSED (4 failures)
    Request 5: Timeout → OPEN (5 failures, threshold reached)

    Next 30 seconds: All requests fail fast (OPEN state)

    After 30s timeout:
    Request N: Moves to HALF_OPEN
    ├─ Success → CLOSED (circuit healed)
    └─ Failure → OPEN (still broken, wait another 30s)
    ```

    **What Counts as a Failure:**

    Only connection-related errors trip the circuit breaker:
    - Network timeouts (APITimeoutError)
    - Connection failures (APIConnectionError)
    - Server errors 5xx (ServerError, ServiceUnavailableError)

    **What Does NOT Count as a Failure:**

    Client errors are considered normal application behavior:
    - 400 Bad Request (InvalidRequestError)
    - 401/403 Authentication (AuthenticationError)
    - 404 Not Found (NotFoundError)

    **Why This Design:**

    - 4xx errors indicate client-side issues, not service health problems
    - 5xx errors indicate the service is struggling and needs protection
    - Timeouts suggest network or service overload
    - Fast failure during outages improves user experience

    **Configuration Tips:**

    - failure_threshold: Start with 5-10 failures for production
    - recovery_timeout: 30-60 seconds gives services time to recover
    - Lower thresholds = more sensitive, higher = more tolerant

    **Thread Safety:**

    Uses asyncio.Lock to ensure thread-safe state transitions in concurrent
    environments. All state changes happen atomically within lock context.

    Attributes:
        config: Circuit breaker configuration settings
        state: Current state (CLOSED, OPEN, HALF_OPEN)
        failure_count: Number of consecutive failures
        last_failure_time: Timestamp of most recent failure
        lock: Asyncio lock for thread-safe state management
        logger: Logger for state transition events
    """

    def __init__(self, config: CircuitBreakerConfig):
        self.config = config
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time: Optional[float] = None
        self.lock = asyncio.Lock()
        self.logger = logging.getLogger(f"{__name__}.CircuitBreaker")

    async def call(
        self, func: Callable[..., Awaitable[T]], *args: Any, **kwargs: Any
    ) -> T:
        """
        Execute a function call protected by the circuit breaker.

        Args:
            func: The async function to call
            *args: Positional arguments for the function
            **kwargs: Keyword arguments for the function

        Returns:
            The result of the function call

        Raises:
            APIConnectionError: If circuit is open or function raises connection error
        """
        # EDUCATIONAL NOTE: We use a lock here to ensure thread-safe state transitions.
        # Multiple concurrent requests could otherwise create race conditions when
        # checking/updating circuit state. The lock ensures atomic state operations.
        async with self.lock:
            # PATTERN: Check circuit state before attempting the operation
            # This is the core "fail fast" behavior that makes circuits valuable
            if self.state == CircuitState.OPEN:
                # EDUCATIONAL: Calculate time since last failure to determine recovery
                # We track the last failure time to implement the timeout-based recovery
                # mechanism. This prevents the circuit from staying open forever.
                if (
                    self.last_failure_time is not None
                    and time.time() - self.last_failure_time
                    < self.config.recovery_timeout
                ):
                    # FAIL FAST: Circuit is still in timeout period
                    # This saves time and resources by not even attempting the call
                    # to a service we know is likely still failing
                    raise APIConnectionError("Circuit breaker is OPEN - failing fast")
                else:
                    # RECOVERY TRANSITION: Move to HALF_OPEN for testing
                    # After the timeout period, we allow ONE call to test if the
                    # service has recovered. This is safer than immediately going
                    # to CLOSED and potentially overwhelming a recovering service.
                    self.state = CircuitState.HALF_OPEN
                    self.logger.info(
                        "Circuit breaker moving to HALF_OPEN state for recovery test"
                    )

        try:
            # EXECUTE: Attempt the protected operation
            # The function call happens outside the lock to avoid holding the lock
            # during potentially slow network operations. This prevents blocking
            # other concurrent requests that just need to check circuit state.
            result = await func(*args, **kwargs)

            # SUCCESS PATH: Handle successful execution
            # Success means the service is healthy, so we can reset the circuit
            async with self.lock:
                # RECOVERY LOGIC: Success resets circuit state to healthy
                # From HALF_OPEN or even OPEN, success means service recovered
                if self.state in (CircuitState.HALF_OPEN, CircuitState.OPEN):
                    self.state = CircuitState.CLOSED
                    self.failure_count = 0  # Reset failure counter
                    self.last_failure_time = None  # Clear failure timestamp
                    self.logger.info("Circuit breaker CLOSED - service recovered")

            return result

        except APIConnectionError:
            # FAILURE PATH: Handle connection/infrastructure errors
            # Only APIConnectionError and subclasses count as circuit breaker failures
            # because they indicate service health issues, not client request problems
            async with self.lock:
                # INCREMENT: Track consecutive failures
                # Each connection error builds toward the failure threshold
                self.failure_count += 1
                self.last_failure_time = time.time()  # Record when failure occurred

                # THRESHOLD CHECK: Open circuit if too many failures
                # Once we hit the threshold, switch to OPEN to fail fast
                if self.failure_count >= self.config.failure_threshold:
                    if self.state != CircuitState.OPEN:
                        # STATE TRANSITION: CLOSED/HALF_OPEN → OPEN
                        # This transition activates fail-fast behavior
                        self.state = CircuitState.OPEN
                        timeout = self.config.recovery_timeout
                        failures = self.failure_count
                        msg = (
                            f"Circuit breaker OPENED after {failures} failures - "
                            f"failing fast for {timeout}s"
                        )
                        self.logger.warning(msg)

            # PROPAGATE: Re-raise the connection error
            # The caller needs to know the operation failed, even though we've
            # updated circuit state. This maintains the original error semantics.
            raise

        except Exception:
            # NON-CIRCUIT ERRORS: 4xx client errors don't affect circuit health
            # These errors indicate problems with the request (auth, validation, etc.)
            # not with service health, so they shouldn't trip the circuit breaker.
            #
            # EDUCATIONAL: This design choice is crucial - if we counted 4xx errors,
            # a misconfigured client could trip the circuit and affect other clients.
            # Only infrastructure issues (5xx, timeouts, network) affect circuit state.
            raise


def circuit_breaker(config: CircuitBreakerConfig) -> Any:
    """
    Decorator to apply circuit breaker protection to async functions.

    Args:
        config: Circuit breaker configuration

    Returns:
        Decorator function
    """
    breaker = CircuitBreaker(config)

    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            return await breaker.call(func, *args, **kwargs)

        return wrapper

    return decorator
