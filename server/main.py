"""Main FastAPI application with lifespan management and CLI."""

import asyncio
from contextlib import asynccontextmanager
from typing import Optional

import structlog
import typer
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from server.cache import IdempotencyCache
from server.config import ServerConfig, get_config
from server.endpoints import core, failure, health
from server.logging_config import setup_logging
from server.middleware import (
    ConcurrencyMiddleware,
    ErrorHandlingMiddleware,
    RequestLoggingMiddleware,
    TimeoutMiddleware,
)
from server.state import ServerState

# Global state instances
server_state: Optional[ServerState] = None
idempotency_cache: Optional[IdempotencyCache] = None
config: Optional[ServerConfig] = None
concurrency_semaphore: Optional[asyncio.Semaphore] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan context manager for startup and shutdown."""
    global server_state, idempotency_cache, config

    # Startup
    config = get_config()

    # Setup logging first
    setup_logging(config.log_level, config.log_format)
    logger = structlog.get_logger("server.startup")

    # Initialize server state
    server_state = ServerState()
    # Set the existing semaphore to server state
    server_state.concurrency_semaphore = concurrency_semaphore

    # Initialize idempotency cache
    idempotency_cache = IdempotencyCache(
        max_size=config.cache_max_size,
        ttl_seconds=config.cache_ttl_seconds
    )

    # Store in app state for dependency injection
    app.state.server_state = server_state
    app.state.idempotency_cache = idempotency_cache

    logger.info(
        "FastAPI test server starting up",
        config={
            "max_concurrency": config.max_concurrency,
            "request_timeout": config.request_timeout,
            "cache_max_size": config.cache_max_size,
            "cache_ttl_seconds": config.cache_ttl_seconds,
            "log_level": config.log_level,
            "log_format": config.log_format
        }
    )

    yield

    # Shutdown
    logger.info("FastAPI test server shutting down")

    if idempotency_cache:
        await idempotency_cache.clear()

    logger.info("Server shutdown complete")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    global concurrency_semaphore

    # Initialize config and semaphore early
    config = get_config()
    concurrency_semaphore = asyncio.Semaphore(config.max_concurrency)

    app = FastAPI(
        title="FastAPI Test Server",
        description="A professional-grade test server for validating async Python HTTP clients",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if config.enable_docs else None,
        redoc_url="/redoc" if config.enable_docs else None
    )

    # Add CORS middleware if enabled
    if config.cors_enabled:
        origins = config.cors_origins or ["*"]
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # Add custom middleware stack (order matters!)
    # 1. Error handling (outermost)
    app.add_middleware(ErrorHandlingMiddleware)

    # 2. Request logging
    app.add_middleware(RequestLoggingMiddleware)

    # 3. Timeout handling
    app.add_middleware(TimeoutMiddleware, timeout_seconds=config.request_timeout)

    # 4. Concurrency limiting (innermost)
    app.add_middleware(ConcurrencyMiddleware, semaphore=concurrency_semaphore)

    return app


# Create the FastAPI app
app = create_app()

# Include routers
app.include_router(core.router)
app.include_router(failure.router)
app.include_router(health.router)




# CLI interface using Typer
cli = typer.Typer(name="test-server", help="FastAPI Test Server for async client validation")


@cli.command()
def serve(
    host: str = typer.Option("0.0.0.0", help="Host to bind to"),
    port: int = typer.Option(8000, help="Port to bind to"),
    max_concurrency: Optional[int] = typer.Option(None, help="Maximum concurrent requests"),
    request_timeout: Optional[int] = typer.Option(None, help="Request timeout in seconds"),
    cache_max_size: Optional[int] = typer.Option(None, help="Maximum cache size"),
    cache_ttl_seconds: Optional[int] = typer.Option(None, help="Cache TTL in seconds"),
    log_level: Optional[str] = typer.Option(None, help="Log level (DEBUG, INFO, WARNING, ERROR)"),
    log_format: Optional[str] = typer.Option(None, help="Log format (json, console)"),
    reload: bool = typer.Option(False, help="Enable auto-reload for development"),
    workers: int = typer.Option(1, help="Number of worker processes")
):
    """Start the FastAPI test server."""

    # Override environment config with CLI arguments if provided
    import os
    if max_concurrency is not None:
        os.environ["SERVER_MAX_CONCURRENCY"] = str(max_concurrency)
    if request_timeout is not None:
        os.environ["SERVER_REQUEST_TIMEOUT"] = str(request_timeout)
    if cache_max_size is not None:
        os.environ["SERVER_CACHE_MAX_SIZE"] = str(cache_max_size)
    if cache_ttl_seconds is not None:
        os.environ["SERVER_CACHE_TTL_SECONDS"] = str(cache_ttl_seconds)
    if log_level is not None:
        os.environ["SERVER_LOG_LEVEL"] = log_level
    if log_format is not None:
        os.environ["SERVER_LOG_FORMAT"] = log_format

    # Override host/port with CLI args
    os.environ["SERVER_HOST"] = host
    os.environ["SERVER_PORT"] = str(port)

    # Start the server
    uvicorn.run(
        "server.main:app",
        host=host,
        port=port,
        reload=reload,
        workers=workers if not reload else 1,  # Reload mode requires single worker
        log_level="info",  # Let our structlog handle the actual logging levels
        access_log=False  # We handle access logging in our middleware
    )


@cli.command()
def config_info():
    """Display current configuration."""
    config = get_config()

    typer.echo("Current FastAPI Test Server Configuration:")
    typer.echo(f"  Host: {config.host}")
    typer.echo(f"  Port: {config.port}")
    typer.echo(f"  Max Concurrency: {config.max_concurrency}")
    typer.echo(f"  Request Timeout: {config.request_timeout}s")
    typer.echo(f"  Cache Max Size: {config.cache_max_size}")
    typer.echo(f"  Cache TTL: {config.cache_ttl_seconds}s")
    typer.echo(f"  Log Level: {config.log_level}")
    typer.echo(f"  Log Format: {config.log_format}")
    typer.echo(f"  Enable Docs: {config.enable_docs}")
    typer.echo(f"  CORS Enabled: {config.cors_enabled}")


if __name__ == "__main__":
    cli()
