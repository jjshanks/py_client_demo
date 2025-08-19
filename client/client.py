"""
Resilient HTTP client implementation.

This module contains the main ResilientClient class that implements all
resilience patterns including connection pooling, retries, circuit breaker,
bulkhead, and comprehensive error handling.
"""

import asyncio
import logging
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any, Optional

import httpx
from tenacity import RetryError

from .circuit_breaker import CircuitBreaker
from .config import ClientConfig
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
from .retry_policies import RetryPolicy


class ResilientClient:
    """
    A resilient asynchronous HTTP client implementing modern resilience patterns.

    Features:
    - Long-lived httpx.AsyncClient with connection pooling
    - Automatic idempotency via X-Request-ID headers
    - Exponential backoff retries with jitter
    - Circuit breaker pattern for fast failure
    - Bulkhead pattern for concurrency limiting
    - Comprehensive exception hierarchy
    - Structured logging with correlation IDs
    """

    def __init__(self, config: ClientConfig):
        self.config = config
        self._client: Optional[httpx.AsyncClient] = None
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._circuit_breaker: Optional[CircuitBreaker] = None
        self._retry_policy: Optional[RetryPolicy] = None
        self._closed = False

        # Setup logging
        self.logger = logging.getLogger(config.logging.logger_name)
        self.logger.setLevel(getattr(logging, config.logging.level.upper()))

    async def __aenter__(self) -> "ResilientClient":
        """Async context manager entry."""
        await self._ensure_initialized()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.close()

    async def _ensure_initialized(self) -> None:
        """Initialize the client if not already initialized."""
        if self._client is None:
            # Create httpx client with connection pooling
            self._client = httpx.AsyncClient(
                base_url=self.config.base_url,
                timeout=self.config.timeout.to_httpx_timeout(),
                follow_redirects=self.config.follow_redirects,
                verify=self.config.verify_ssl,
                headers=(
                    {"User-Agent": self.config.user_agent}
                    if self.config.user_agent
                    else None
                ),
            )

            # Initialize resilience components
            self._semaphore = asyncio.Semaphore(self.config.bulkhead.max_concurrency)
            self._circuit_breaker = CircuitBreaker(self.config.circuit_breaker)
            self._retry_policy = RetryPolicy(self.config.retry)

            self.logger.info(
                f"ResilientClient initialized with base_url={self.config.base_url}"
            )

    async def close(self) -> None:
        """Close the HTTP client and clean up resources."""
        if self._client and not self._closed:
            await self._client.aclose()
            self._closed = True
            self.logger.info("ResilientClient closed")

    def _map_httpx_exception(
        self, exc: Exception, response: Optional[httpx.Response] = None
    ) -> MyAPIError:
        """
        Map httpx exceptions to our custom exception hierarchy.

        Args:
            exc: The original httpx exception
            response: Optional HTTP response object

        Returns:
            Mapped exception from our hierarchy
        """
        if isinstance(exc, httpx.TimeoutException):
            return APITimeoutError("Request timed out", response=response)

        if isinstance(exc, (httpx.NetworkError, httpx.ConnectError)):
            return APIConnectionError(f"Network error: {exc}", response=response)

        if isinstance(exc, httpx.HTTPStatusError):
            status_code = exc.response.status_code

            # Map status codes to specific exceptions according to CLIENT_GUIDE.md
            if status_code in (401, 403):
                return AuthenticationError(
                    f"Authentication failed: {status_code}", response=exc.response
                )
            elif status_code == 404:
                return NotFoundError("Resource not found", response=exc.response)
            elif status_code in (400, 422):
                return InvalidRequestError(
                    f"Invalid request: {status_code}", response=exc.response
                )
            elif status_code == 408:
                return ServerTimeoutError("Server timeout", response=exc.response)
            elif status_code in (502, 503, 504):
                return ServiceUnavailableError(
                    f"Service unavailable: {status_code}", response=exc.response
                )
            elif 500 <= status_code < 600:
                return ServerError(
                    f"Server error: {status_code}", response=exc.response
                )
            else:
                # Other status codes default to generic status error
                return APIStatusError(
                    f"HTTP error: {status_code}", response=exc.response
                )

        # Fallback for other httpx errors
        return MyAPIError(f"HTTP client error: {exc}", response=response)

    async def _acquire_semaphore_with_timeout(self, request_id: str) -> None:
        """
        Acquire semaphore slot with timeout (bulkhead pattern).

        Args:
            request_id: Request ID for logging correlation

        Raises:
            PoolTimeoutError: If semaphore cannot be acquired within timeout
        """
        self.logger.debug(
            f"Operation [{request_id}] is waiting for a free connection slot"
        )

        try:
            if self._semaphore is None:
                raise RuntimeError("Client not initialized")
            await asyncio.wait_for(
                self._semaphore.acquire(),
                timeout=self.config.bulkhead.acquisition_timeout,
            )

            # Log successful acquisition with remaining slots
            remaining_slots = self._semaphore._value
            self.logger.debug(
                f"Operation [{request_id}] acquired a connection slot. "
                f"{remaining_slots} slots remaining"
            )

        except asyncio.TimeoutError as e:
            raise PoolTimeoutError(
                f"Failed to acquire connection slot within "
                f"{self.config.bulkhead.acquisition_timeout}s - client is saturated"
            ) from e

    async def _make_request_with_resilience(
        self, method: str, url: str, request_id: str, **kwargs: Any
    ) -> httpx.Response:
        """
        Make an HTTP request with full resilience pattern implementation.

        Args:
            method: HTTP method
            url: Request URL
            request_id: Unique request ID for idempotency and correlation
            **kwargs: Additional arguments passed to httpx

        Returns:
            HTTP response object

        Raises:
            Various MyAPIError subclasses based on failure type
        """
        await self._ensure_initialized()

        # Add X-Request-ID header for idempotency
        headers = kwargs.get("headers", {})
        headers["X-Request-ID"] = request_id
        kwargs["headers"] = headers

        # Bulkhead pattern: acquire semaphore with timeout
        await self._acquire_semaphore_with_timeout(request_id)

        try:
            # Define the HTTP operation with retry logic inside circuit breaker
            async def http_operation_with_retries() -> httpx.Response:
                # Define the actual HTTP operation
                async def single_http_request() -> httpx.Response:
                    try:
                        # Make the HTTP request
                        if self._client is None:
                            raise RuntimeError("Client not initialized")
                        response = await self._client.request(method, url, **kwargs)

                        # Check for HTTP error status codes
                        response.raise_for_status()

                        return response

                    except Exception as e:
                        # Map all httpx exceptions to our custom hierarchy
                        response_attr = getattr(e, "response", None)
                        raise self._map_httpx_exception(
                            e,
                            response_attr
                            if isinstance(response_attr, httpx.Response)
                            else None,
                        ) from e

                # Apply retry policy to the single request operation
                if self._retry_policy is None:
                    raise RuntimeError("Client not initialized")
                retry_wrapped = self._retry_policy.wrap_operation(
                    single_http_request, request_id
                )

                # Execute with retries
                return await retry_wrapped()

            # Apply circuit breaker protection to the entire retry loop
            # This ensures retry sequence counts as one operation for circuit breaker
            if self._circuit_breaker is None:
                raise RuntimeError("Client not initialized")
            return await self._circuit_breaker.call(http_operation_with_retries)

        except RetryError as e:
            # All retries exhausted - raise the last exception
            last_exc = e.last_attempt.exception()
            if last_exc is not None:
                raise last_exc from e
            raise MyAPIError("Retry failed with no exception") from e

        finally:
            # Always release semaphore slot
            if self._semaphore is not None:
                self._semaphore.release()

    async def get(
        self, url: str, request_id: Optional[str] = None, **kwargs: Any
    ) -> httpx.Response:
        """
        Make a GET request with full resilience patterns.

        Args:
            url: Request URL (relative to base_url or absolute)
            request_id: Optional request ID (auto-generated if not provided)
            **kwargs: Additional arguments passed to httpx

        Returns:
            HTTP response object
        """
        if request_id is None:
            request_id = str(uuid.uuid4())
            if self.config.logging.include_request_id:
                self.logger.debug(
                    f"Generated new idempotency key [{request_id}] for GET operation"
                )

        return await self._make_request_with_resilience(
            "GET", url, request_id, **kwargs
        )

    async def post(
        self, url: str, request_id: Optional[str] = None, **kwargs: Any
    ) -> httpx.Response:
        """
        Make a POST request with full resilience patterns.

        Args:
            url: Request URL (relative to base_url or absolute)
            request_id: Optional request ID (auto-generated if not provided)
            **kwargs: Additional arguments passed to httpx

        Returns:
            HTTP response object
        """
        if request_id is None:
            request_id = str(uuid.uuid4())
            if self.config.logging.include_request_id:
                self.logger.debug(
                    f"Generated new idempotency key [{request_id}] for POST operation"
                )

        return await self._make_request_with_resilience(
            "POST", url, request_id, **kwargs
        )

    async def put(
        self, url: str, request_id: Optional[str] = None, **kwargs: Any
    ) -> httpx.Response:
        """
        Make a PUT request with full resilience patterns.

        Args:
            url: Request URL (relative to base_url or absolute)
            request_id: Optional request ID (auto-generated if not provided)
            **kwargs: Additional arguments passed to httpx

        Returns:
            HTTP response object
        """
        if request_id is None:
            request_id = str(uuid.uuid4())
            if self.config.logging.include_request_id:
                self.logger.debug(
                    f"Generated new idempotency key [{request_id}] for PUT operation"
                )

        return await self._make_request_with_resilience(
            "PUT", url, request_id, **kwargs
        )

    async def delete(
        self, url: str, request_id: Optional[str] = None, **kwargs: Any
    ) -> httpx.Response:
        """
        Make a DELETE request with full resilience patterns.

        Args:
            url: Request URL (relative to base_url or absolute)
            request_id: Optional request ID (auto-generated if not provided)
            **kwargs: Additional arguments passed to httpx

        Returns:
            HTTP response object
        """
        if request_id is None:
            request_id = str(uuid.uuid4())
            if self.config.logging.include_request_id:
                self.logger.debug(
                    f"Generated new idempotency key [{request_id}] for DELETE operation"
                )

        return await self._make_request_with_resilience(
            "DELETE", url, request_id, **kwargs
        )


@asynccontextmanager
async def create_resilient_client(
    config: ClientConfig,
) -> AsyncGenerator[ResilientClient, None]:
    """
    Async context manager for creating and managing a resilient client.

    Args:
        config: Client configuration

    Yields:
        Configured ResilientClient instance
    """
    client = ResilientClient(config)
    try:
        await client._ensure_initialized()
        yield client
    finally:
        await client.close()
