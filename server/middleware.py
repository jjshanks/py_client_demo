"""FastAPI middleware for concurrency, timeouts, logging, and error handling."""

import asyncio
import time
import uuid
from typing import Callable
from fastapi import Request, Response, HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
import structlog

logger = structlog.get_logger()


class ConcurrencyMiddleware(BaseHTTPMiddleware):
    """Middleware to limit concurrent requests using a semaphore."""
    
    def __init__(self, app, semaphore: asyncio.Semaphore):
        super().__init__(app)
        self.semaphore = semaphore
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Skip concurrency limiting for health check
        if request.url.path == "/health":
            return await call_next(request)
        
        # Wait to acquire semaphore slot
        logger.debug(
            "Waiting for concurrency slot",
            path=request.url.path,
            method=request.method,
            available_slots=self.semaphore._value
        )
        
        async with self.semaphore:
            logger.debug(
                "Acquired concurrency slot",
                path=request.url.path,
                method=request.method,
                remaining_slots=self.semaphore._value
            )
            
            try:
                response = await call_next(request)
                return response
            finally:
                logger.debug(
                    "Released concurrency slot",
                    path=request.url.path,
                    method=request.method
                )


class TimeoutMiddleware(BaseHTTPMiddleware):
    """Middleware to enforce request timeouts."""
    
    def __init__(self, app, timeout_seconds: int):
        super().__init__(app)
        self.timeout_seconds = timeout_seconds
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Skip timeout for health check (should always be fast)
        if request.url.path == "/health":
            return await call_next(request)
        
        try:
            # Wrap the request processing in a timeout
            response = await asyncio.wait_for(
                call_next(request),
                timeout=self.timeout_seconds
            )
            return response
        except asyncio.TimeoutError:
            logger.warning(
                "Request timeout",
                path=request.url.path,
                method=request.method,
                timeout_seconds=self.timeout_seconds
            )
            return JSONResponse(
                status_code=408,
                content={"detail": "Request timeout"}
            )


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware for structured request/response logging."""
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Generate correlation ID for this request
        correlation_id = str(uuid.uuid4())
        
        # Extract key headers
        request_id = request.headers.get("X-Request-ID")
        user_agent = request.headers.get("User-Agent", "")
        
        # Log incoming request
        start_time = time.time()
        logger.debug(
            "Request received",
            correlation_id=correlation_id,
            method=request.method,
            path=request.url.path,
            query_params=str(request.query_params),
            request_id=request_id,
            user_agent=user_agent,
            client_host=request.client.host if request.client else None
        )
        
        # Process request
        try:
            response = await call_next(request)
            
            # Log successful response
            duration_ms = (time.time() - start_time) * 1000
            logger.debug(
                "Request completed",
                correlation_id=correlation_id,
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                duration_ms=round(duration_ms, 2),
                request_id=request_id
            )
            
            # Add correlation ID to response headers for tracing
            response.headers["X-Correlation-ID"] = correlation_id
            return response
            
        except Exception as exc:
            # Log failed request
            duration_ms = (time.time() - start_time) * 1000
            logger.error(
                "Request failed",
                correlation_id=correlation_id,
                method=request.method,
                path=request.url.path,
                duration_ms=round(duration_ms, 2),
                error_type=type(exc).__name__,
                error_message=str(exc),
                request_id=request_id
            )
            raise


class ErrorHandlingMiddleware(BaseHTTPMiddleware):
    """Middleware for consistent error handling and responses."""
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        try:
            return await call_next(request)
        except HTTPException:
            # FastAPI HTTPExceptions should pass through unchanged
            raise
        except asyncio.TimeoutError:
            # This should be handled by TimeoutMiddleware, but just in case
            return JSONResponse(
                status_code=408,
                content={"detail": "Request timeout"}
            )
        except Exception as exc:
            # Catch any unexpected errors and return a generic 500
            logger.error(
                "Unexpected server error",
                path=request.url.path,
                method=request.method,
                error_type=type(exc).__name__,
                error_message=str(exc)
            )
            return JSONResponse(
                status_code=500,
                content={"detail": "Internal server error"}
            )