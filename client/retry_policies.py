"""
Retry policies and utilities for the resilient HTTP client.

Implements intelligent retry logic using tenacity with exponential backoff,
jitter, and custom exception-based triggering aligned with our exception hierarchy.
"""

import logging
import uuid
from typing import Callable, TypeVar

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

from .config import RetryConfig
from .exceptions import APIConnectionError

T = TypeVar("T")


def create_retry_decorator(config: RetryConfig, request_id: str = None):
    """
    Create a tenacity retry decorator configured for our client.

    Args:
        config: Retry configuration
        request_id: Optional request ID for logging correlation

    Returns:
        Configured tenacity retry decorator
    """
    logger = logging.getLogger(f"{__name__}.retry")

    # Generate request ID if not provided
    if request_id is None:
        request_id = str(uuid.uuid4())

    def log_retry_attempt(retry_state):
        """Custom logging for retry attempts."""
        if retry_state.outcome and retry_state.outcome.failed:
            exception = retry_state.outcome.exception()
            attempt_num = retry_state.attempt_number
            next_wait = retry_state.next_action.sleep if retry_state.next_action else 0

            exc_name = type(exception).__name__
            logger.debug(
                f"Operation [{request_id}] failed with [{exc_name}: {exception}]. "
                f"Retrying in {next_wait:.2f} seconds "
                f"(Attempt {attempt_num} of {config.max_attempts})"
            )

    # Configure wait strategy
    if config.jitter:
        wait_strategy = wait_random_exponential(
            multiplier=config.multiplier,
            min=config.min_wait_seconds,
            max=config.max_wait_seconds,
        )
    else:
        # Use fixed exponential backoff without jitter if disabled
        from tenacity import wait_exponential

        wait_strategy = wait_exponential(
            multiplier=config.multiplier,
            min=config.min_wait_seconds,
            max=config.max_wait_seconds,
        )

    return retry(
        # Only retry on connection errors (retryable exceptions)
        retry=retry_if_exception_type(APIConnectionError),
        # Stop after configured max attempts
        stop=stop_after_attempt(config.max_attempts),
        # Exponential backoff with optional jitter
        wait=wait_strategy,
        # Custom logging before sleep
        before_sleep=log_retry_attempt,
        # Re-raise the last exception if all retries fail
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
        self, func: Callable[..., T], request_id: str = None
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
        return retry_decorator(func)
