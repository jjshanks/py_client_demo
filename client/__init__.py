"""
Resilient Asynchronous Python HTTP Client Library

A professional-grade HTTP client library implementing modern resilience patterns
including retries, circuit breaker, bulkhead, and comprehensive error handling.
"""

from .client import ResilientClient
from .config import (
    BulkheadConfig,
    CircuitBreakerConfig,
    ClientConfig,
    LoggingConfig,
    RetryConfig,
    TimeoutConfig,
)
from .exceptions import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    InvalidRequestError,
    MyAPIError,
    NotFoundError,
    PoolTimeoutError,
    ServerError,
    ServerTimeoutError,
    ServiceUnavailableError,
)

__all__ = [
    "ResilientClient",
    "ClientConfig",
    "TimeoutConfig",
    "RetryConfig",
    "CircuitBreakerConfig",
    "BulkheadConfig",
    "LoggingConfig",
    "MyAPIError",
    "APIConnectionError",
    "APIStatusError",
    "APITimeoutError",
    "ServerTimeoutError",
    "ServiceUnavailableError",
    "ServerError",
    "AuthenticationError",
    "NotFoundError",
    "InvalidRequestError",
    "PoolTimeoutError",
]

__version__ = "1.0.0"
