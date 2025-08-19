"""
Configuration system for the resilient HTTP client library.

Provides Pydantic-based configuration with sensible defaults for all
resilience patterns including timeouts, retries, circuit breaker, and concurrency.
"""

from typing import Optional

import httpx
from pydantic import BaseModel, ConfigDict, Field


class TimeoutConfig(BaseModel):
    """HTTP timeout configuration."""

    connect: float = Field(default=60.0, description="Connection timeout in seconds")
    read: float = Field(default=15.0, description="Read timeout in seconds")
    write: float = Field(default=15.0, description="Write timeout in seconds")
    pool: float = Field(default=5.0, description="Pool timeout in seconds")

    def to_httpx_timeout(self) -> httpx.Timeout:
        """Convert to httpx.Timeout object."""
        return httpx.Timeout(
            connect=self.connect, read=self.read, write=self.write, pool=self.pool
        )


class RetryConfig(BaseModel):
    """Retry logic configuration."""

    max_attempts: int = Field(default=3, description="Maximum retry attempts")
    min_wait_seconds: float = Field(
        default=1.0, description="Minimum wait time between retries"
    )
    max_wait_seconds: float = Field(
        default=60.0, description="Maximum wait time between retries"
    )
    multiplier: float = Field(default=2.0, description="Exponential backoff multiplier")
    jitter: bool = Field(default=True, description="Add random jitter to wait times")


class CircuitBreakerConfig(BaseModel):
    """Circuit breaker configuration."""

    failure_threshold: int = Field(
        default=5, description="Number of failures before opening circuit"
    )
    recovery_timeout: float = Field(
        default=30.0, description="Seconds to wait before attempting recovery"
    )
    expected_exception: str = Field(
        default="APIConnectionError",
        description="Exception type that triggers circuit breaker",
    )


class BulkheadConfig(BaseModel):
    """Bulkhead (concurrency limiting) configuration."""

    max_concurrency: int = Field(default=50, description="Maximum concurrent requests")
    acquisition_timeout: float = Field(
        default=30.0, description="Timeout for acquiring semaphore slot"
    )


class LoggingConfig(BaseModel):
    """Logging configuration."""

    level: str = Field(
        default="INFO", description="Log level (DEBUG, INFO, WARNING, ERROR)"
    )
    include_request_id: bool = Field(
        default=True, description="Include X-Request-ID in logs"
    )
    logger_name: str = Field(default="resilient_client", description="Logger name")


class ClientConfig(BaseModel):
    """
    Complete configuration for the resilient HTTP client.

    This configuration class brings together all resilience pattern settings
    in a single, validated structure. Understanding these settings is crucial
    for tuning the client's behavior for different environments and requirements.

    **Configuration Philosophy:**

    Each resilience pattern can be tuned independently, but they work together:
    - Timeouts should align with retry intervals (don't timeout mid-retry)
    - Circuit breaker thresholds should account for retry attempts
    - Bulkhead limits should match expected load patterns
    - Logging helps monitor and tune other patterns

    **Environment-Specific Tuning Examples:**

    High-latency network (satellite, international):
    ```python
    config = ClientConfig(
        base_url="https://api.remote.com",
        timeout=TimeoutConfig(connect=30.0, read=60.0),
        retry=RetryConfig(max_attempts=5, max_wait_seconds=300.0),
        circuit_breaker=CircuitBreakerConfig(
            failure_threshold=10,  # More tolerant of failures
            recovery_timeout=60.0  # Longer recovery time
        )
    )
    ```

    High-throughput, low-latency (internal microservices):
    ```python
    config = ClientConfig(
        base_url="http://internal.service",
        timeout=TimeoutConfig(connect=2.0, read=5.0),
        retry=RetryConfig(max_attempts=2, max_wait_seconds=10.0),
        circuit_breaker=CircuitBreakerConfig(
            failure_threshold=3,   # Quick to fail fast
            recovery_timeout=10.0  # Quick recovery testing
        ),
        bulkhead=BulkheadConfig(max_concurrency=100)  # Higher throughput
    )
    ```

    **Validation and Safety:**

    - Pydantic validates all fields and provides helpful error messages
    - extra="forbid" prevents typos in configuration field names
    - Default values provide sensible starting points for most use cases
    - Field descriptions serve as inline documentation

    **Best Practices:**

    1. Start with defaults and tune based on monitoring
    2. Test configuration changes in staging first
    3. Monitor circuit breaker state transitions
    4. Adjust timeouts based on p99 latency metrics
    5. Set bulkhead limits based on upstream service capacity

    Attributes:
        base_url: Base URL for all API requests (required)
        timeout: HTTP timeout configuration for all request phases
        retry: Exponential backoff retry behavior settings
        circuit_breaker: Failure detection and fast-fail settings
        bulkhead: Concurrency limiting and resource protection
        logging: Observability and debugging configuration
        follow_redirects: Whether to automatically follow HTTP redirects
        verify_ssl: Whether to verify SSL certificates (disable only for testing)
        user_agent: Custom User-Agent header for request identification
    """

    base_url: str = Field(description="Base URL for the API")
    timeout: TimeoutConfig = Field(default_factory=TimeoutConfig)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    circuit_breaker: CircuitBreakerConfig = Field(default_factory=CircuitBreakerConfig)
    bulkhead: BulkheadConfig = Field(default_factory=BulkheadConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    # Optional httpx client configuration
    follow_redirects: bool = Field(default=True, description="Follow HTTP redirects")
    verify_ssl: bool = Field(default=True, description="Verify SSL certificates")
    user_agent: Optional[str] = Field(
        default=None, description="Custom User-Agent header"
    )

    model_config = ConfigDict(extra="forbid")
