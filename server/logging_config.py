"""Structured logging configuration using structlog."""

import logging
import sys
from typing import Any, Dict
import structlog
from structlog.types import EventDict


def add_correlation_id(logger: Any, method_name: str, event_dict: EventDict) -> EventDict:
    """Add correlation ID from context if available."""
    # This processor can be extended to pull correlation IDs from async context
    return event_dict


def add_timestamp(logger: Any, method_name: str, event_dict: EventDict) -> EventDict:
    """Add ISO timestamp to log entries."""
    import datetime
    event_dict["timestamp"] = datetime.datetime.utcnow().isoformat() + "Z"
    return event_dict


def setup_logging(log_level: str = "INFO", log_format: str = "json") -> None:
    """
    Configure structured logging for the application.
    
    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
        log_format: Format type ('json' for production, 'console' for development)
    """
    # Convert string level to logging constant
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)
    
    # Configure standard library logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=numeric_level,
    )
    
    # Configure structlog processors
    shared_processors = [
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        add_timestamp,
        add_correlation_id,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    
    if log_format == "json":
        # JSON logging for production
        processors = shared_processors + [
            structlog.processors.JSONRenderer()
        ]
    else:
        # Console logging for development
        processors = shared_processors + [
            structlog.processors.TimeStamper(fmt="ISO"),
            structlog.dev.ConsoleRenderer(colors=True)
        ]
    
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        context_class=dict,
        cache_logger_on_first_use=True,
    )
    
    # Silence some noisy loggers in production
    if log_level != "DEBUG":
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
        logging.getLogger("fastapi").setLevel(logging.WARNING)


def get_logger(name: str = None) -> structlog.BoundLogger:
    """Get a structured logger instance."""
    return structlog.get_logger(name)


class LoggingConfig:
    """Centralized logging configuration for different components."""
    
    SERVER_STARTUP = "server.startup"
    SERVER_SHUTDOWN = "server.shutdown"
    REQUEST_HANDLER = "request.handler"
    CACHE_OPERATIONS = "cache.operations"
    FAILURE_INJECTION = "failure.injection"
    CONCURRENCY = "concurrency.management"
    
    @staticmethod
    def get_component_logger(component: str) -> structlog.BoundLogger:
        """Get a logger bound to a specific component."""
        return structlog.get_logger(component)