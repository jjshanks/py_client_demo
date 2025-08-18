"""
Circuit breaker implementation for the resilient HTTP client.

Implements the circuit breaker pattern to prevent cascading failures by
monitoring for connection errors and temporarily failing fast when
a service is determined to be unhealthy.
"""

import asyncio
import logging
import time
from enum import Enum
from functools import wraps
from typing import Callable, Optional, TypeVar

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
    Circuit breaker implementation that monitors for APIConnectionError exceptions.

    Only connection-related errors (timeouts, network issues, 5xx server errors)
    will trip the circuit breaker. Client errors (4xx) are considered normal
    operation and do not affect the circuit state.
    """

    def __init__(self, config: CircuitBreakerConfig):
        self.config = config
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time: Optional[float] = None
        self.lock = asyncio.Lock()
        self.logger = logging.getLogger(f"{__name__}.CircuitBreaker")

    async def call(self, func: Callable[..., T], *args, **kwargs) -> T:
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
        async with self.lock:
            # Check if we should fail fast
            if self.state == CircuitState.OPEN:
                if time.time() - self.last_failure_time < self.config.recovery_timeout:
                    # Still in timeout period, fail fast
                    raise APIConnectionError("Circuit breaker is OPEN - failing fast")
                else:
                    # Timeout period elapsed, try half-open
                    self.state = CircuitState.HALF_OPEN
                    self.logger.info(
                        "Circuit breaker moving to HALF_OPEN state for recovery test"
                    )

        try:
            # Execute the function
            result = await func(*args, **kwargs)

            # Success - reset circuit if needed
            async with self.lock:
                if self.state in (CircuitState.HALF_OPEN, CircuitState.OPEN):
                    self.state = CircuitState.CLOSED
                    self.failure_count = 0
                    self.last_failure_time = None
                    self.logger.info("Circuit breaker CLOSED - service recovered")

            return result

        except APIConnectionError:
            # Connection error - count as failure
            async with self.lock:
                self.failure_count += 1
                self.last_failure_time = time.time()

                if self.failure_count >= self.config.failure_threshold:
                    if self.state != CircuitState.OPEN:
                        self.state = CircuitState.OPEN
                        timeout = self.config.recovery_timeout
                        failures = self.failure_count
                        msg = (
                            f"Circuit breaker OPENED after {failures} failures - "
                            f"failing fast for {timeout}s"
                        )
                        self.logger.warning(msg)

            # Re-raise the connection error
            raise

        except Exception:
            # Non-connection errors (like 4xx status codes) don't affect circuit breaker
            # These are considered normal application errors, not service health issues
            raise


def circuit_breaker(config: CircuitBreakerConfig):
    """
    Decorator to apply circuit breaker protection to async functions.

    Args:
        config: Circuit breaker configuration

    Returns:
        Decorator function
    """
    breaker = CircuitBreaker(config)

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            return await breaker.call(func, *args, **kwargs)

        return wrapper

    return decorator
