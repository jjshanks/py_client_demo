"""
Retry policies and utilities for the resilient HTTP client.

Implements intelligent retry logic using tenacity with exponential backoff,
jitter, and custom exception-based triggering aligned with our exception hierarchy.
"""

import logging
import uuid
from typing import Any, Callable, Optional, TypeVar

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

from .config import RetryConfig
from .exceptions import APIConnectionError

T = TypeVar("T")


def create_retry_decorator(
    config: RetryConfig, request_id: Optional[str] = None
) -> Any:
    """
    Create a tenacity retry decorator configured for our client.

    Args:
        config: Retry configuration
        request_id: Optional request ID for logging correlation

    Returns:
        Configured tenacity retry decorator
    """
    # LOGGING SETUP: Create logger for retry behavior tracking
    # This helps monitor retry patterns and tune configuration
    logger = logging.getLogger(f"{__name__}.retry")

    # REQUEST CORRELATION: Ensure we have a request ID for tracing
    # Request IDs allow correlating retry attempts with original requests
    # across log files and distributed tracing systems
    if request_id is None:
        request_id = str(uuid.uuid4())

    def log_retry_attempt(retry_state: Any) -> None:
        """
        Custom logging for retry attempts with educational context.

        EDUCATIONAL: This callback is called by tenacity before each retry sleep.
        It provides visibility into the retry process for debugging and monitoring.
        """
        # RETRY STATE ANALYSIS: Extract failure information
        # The retry_state object contains rich information about the failure
        # and the planned next action (sleep duration, etc.)
        if retry_state.outcome and retry_state.outcome.failed:
            exception = retry_state.outcome.exception()
            attempt_num = retry_state.attempt_number
            next_wait = retry_state.next_action.sleep if retry_state.next_action else 0

            # OBSERVABILITY: Log detailed retry context
            # This information helps identify retry patterns and tune configuration
            exc_name = type(exception).__name__
            logger.debug(
                f"Operation [{request_id}] failed with [{exc_name}: {exception}]. "
                f"Retrying in {next_wait:.2f} seconds "
                f"(Attempt {attempt_num} of {config.max_attempts})"
            )

    # WAIT STRATEGY CONFIGURATION: Choose between jitter and deterministic backoff
    # This is a critical design decision for retry behavior in distributed systems
    if config.jitter:
        # JITTER: Random exponential backoff prevents thundering herd problems
        # When many clients retry simultaneously, jitter spreads out the retries
        # to avoid overwhelming a recovering service. This is crucial at scale.
        #
        # EDUCATIONAL: Without jitter, all clients retry at the same intervals,
        # creating traffic spikes that can prevent service recovery.
        wait_strategy: Any = wait_random_exponential(
            multiplier=config.multiplier,  # Exponential growth rate (usually 2.0)
            min=config.min_wait_seconds,  # Minimum wait time
            max=config.max_wait_seconds,  # Maximum wait time (cap)
        )
    else:
        # DETERMINISTIC BACKOFF: Fixed exponential backoff without randomization
        # This provides predictable timing but can cause thundering herd effects
        # in distributed systems. Generally only used for single-client scenarios.
        from tenacity import wait_exponential

        wait_strategy = wait_exponential(
            multiplier=config.multiplier,
            min=config.min_wait_seconds,
            max=config.max_wait_seconds,
        )

    # TENACITY DECORATOR CONFIGURATION: Assemble the complete retry policy
    # Each parameter controls a specific aspect of retry behavior
    return retry(
        # RETRY CONDITION: Only retry connection/infrastructure errors
        # This is crucial - we don't want to retry client errors (4xx) as they
        # indicate problems with the request itself, not transient failures.
        # Only APIConnectionError and subclasses indicate retryable conditions.
        retry=retry_if_exception_type(APIConnectionError),
        # STOP CONDITION: Limit total retry attempts
        # This prevents infinite retry loops and provides bounded recovery time.
        # After max_attempts, tenacity raises RetryError with the last exception.
        stop=stop_after_attempt(config.max_attempts),
        # WAIT STRATEGY: How long to wait between attempts
        # Exponential backoff with optional jitter spreads load and gives
        # services time to recover between attempts.
        wait=wait_strategy,
        # OBSERVABILITY HOOK: Log attempts for monitoring and debugging
        # This callback provides visibility into retry behavior and helps
        # with configuration tuning and troubleshooting.
        before_sleep=log_retry_attempt,
        # EXCEPTION HANDLING: Re-raise the last exception if all retries fail
        # This ensures the original exception semantics are preserved for callers.
        reraise=True,
    )


class RetryPolicy:
    """
    Retry policy manager that provides consistent retry behavior
    across all client operations.
    """

    def __init__(self, config: RetryConfig):
        self.config = config
        self.logger = logging.getLogger(f"{__name__}.RetryPolicy")

    def wrap_operation(
        self, func: Callable[..., T], request_id: Optional[str] = None
    ) -> Callable[..., T]:
        """
        Wrap an async operation with retry logic.

        Args:
            func: The async function to wrap
            request_id: Optional request ID for logging correlation

        Returns:
            Function wrapped with retry decorator
        """
        retry_decorator = create_retry_decorator(self.config, request_id)
        return retry_decorator(func)  # type: ignore[no-any-return]
