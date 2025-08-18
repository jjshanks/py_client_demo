"""Thread-safe state management for failure modes and server state."""

import asyncio
import time
from dataclasses import dataclass
from typing import Optional

import structlog

logger = structlog.get_logger()


@dataclass
class FailureConfig:
    """Configuration for failure injection modes."""

    fail_requests_count: int = 0
    fail_until_timestamp: Optional[float] = None


class FailureStateManager:
    """Thread-safe manager for failure injection state."""

    def __init__(self):
        self._config = FailureConfig()
        self._lock = asyncio.Lock()

    async def should_fail(self) -> bool:
        """
        Check if the current request should fail based on failure configuration.

        Returns True if either:
        - fail_requests_count > 0, OR
        - fail_until_timestamp is in the future
        """
        async with self._lock:
            current_time = time.time()

            # Check count-based failure
            count_should_fail = self._config.fail_requests_count > 0

            # Check duration-based failure
            duration_should_fail = (
                self._config.fail_until_timestamp is not None
                and current_time < self._config.fail_until_timestamp
            )

            should_fail = count_should_fail or duration_should_fail

            if should_fail:
                logger.debug(
                    "Failure check result",
                    should_fail=True,
                    count_remaining=self._config.fail_requests_count,
                    duration_remaining=max(
                        0, (self._config.fail_until_timestamp or 0) - current_time
                    ),
                    reason="count" if count_should_fail else "duration",
                )

                # Decrement count if it's active (as per spec)
                if count_should_fail:
                    self._config.fail_requests_count -= 1
                    logger.debug(
                        "Decremented failure count",
                        new_count=self._config.fail_requests_count,
                    )

            return should_fail

    async def set_fail_count(self, count: int) -> None:
        """Set the number of requests that should fail."""
        async with self._lock:
            self._config.fail_requests_count = max(0, count)
            logger.info(
                "Failure mode activated (count)",
                fail_requests_count=self._config.fail_requests_count,
            )

    async def set_fail_duration(self, duration_seconds: int) -> None:
        """Set the duration for which requests should fail."""
        async with self._lock:
            current_time = time.time()
            self._config.fail_until_timestamp = current_time + duration_seconds
            logger.info(
                "Failure mode activated (duration)",
                fail_until_timestamp=self._config.fail_until_timestamp,
                duration_seconds=duration_seconds,
            )

    async def reset_failures(self) -> None:
        """Reset all failure configurations."""
        async with self._lock:
            self._config.fail_requests_count = 0
            self._config.fail_until_timestamp = None
            logger.info("All failure modes reset")

    async def get_status(self) -> dict:
        """Get current failure state for debugging/monitoring."""
        async with self._lock:
            current_time = time.time()
            duration_remaining = 0
            if self._config.fail_until_timestamp:
                duration_remaining = max(
                    0, self._config.fail_until_timestamp - current_time
                )

            return {
                "fail_requests_count": self._config.fail_requests_count,
                "fail_until_timestamp": self._config.fail_until_timestamp,
                "duration_remaining_seconds": duration_remaining,
                "currently_failing": await self._would_fail_without_decrement(),
            }

    async def _would_fail_without_decrement(self) -> bool:
        """Check if we would fail without decrementing the counter."""
        current_time = time.time()
        count_would_fail = self._config.fail_requests_count > 0
        duration_would_fail = (
            self._config.fail_until_timestamp is not None
            and current_time < self._config.fail_until_timestamp
        )
        return count_would_fail or duration_would_fail


class ServerState:
    """Global server state container."""

    def __init__(self):
        self.failure_manager = FailureStateManager()
        self.concurrency_semaphore: Optional[asyncio.Semaphore] = None
        self._startup_time = time.time()

    def initialize_concurrency_semaphore(self, max_concurrency: int) -> None:
        """Initialize the concurrency limiting semaphore."""
        self.concurrency_semaphore = asyncio.Semaphore(max_concurrency)
        logger.info(
            "Concurrency semaphore initialized", max_concurrency=max_concurrency
        )

    def get_uptime_seconds(self) -> float:
        """Get server uptime in seconds."""
        return time.time() - self._startup_time
