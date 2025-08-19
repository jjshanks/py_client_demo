"""Configuration management using Pydantic Settings."""

from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


class ServerConfig(BaseSettings):
    """Server configuration with environment variable support."""

    # Core server settings
    host: str = Field(default="0.0.0.0", description="Host to bind the server to")
    port: int = Field(default=8000, description="Port to bind the server to")

    # Concurrency and timeout settings
    max_concurrency: int = Field(
        default=50,
        description="Maximum number of simultaneous requests the server will process",
        ge=1,
        le=1000,
    )
    request_timeout: int = Field(
        default=30,
        description="Maximum time in seconds for processing a single request",
        ge=1,
        le=300,
    )

    # Cache settings
    cache_max_size: int = Field(
        default=1000,
        description="Maximum number of entries in the idempotency cache",
        ge=1,
        le=100000,
    )
    cache_ttl_seconds: int = Field(
        default=300,
        description="Time-To-Live for cache entries in seconds",
        ge=1,
        le=86400,  # 24 hours max
    )

    # Logging settings
    log_level: str = Field(
        default="INFO",
        description="Logging level (DEBUG, INFO, WARNING, ERROR)",
        pattern="^(DEBUG|INFO|WARNING|ERROR)$",
    )
    log_format: str = Field(
        default="json",
        description="Log format (json or console)",
        pattern="^(json|console)$",
    )

    # Optional settings for advanced use cases
    enable_docs: bool = Field(
        default=True, description="Enable FastAPI automatic documentation"
    )
    cors_enabled: bool = Field(default=False, description="Enable CORS middleware")
    cors_origins: Optional[str] = Field(
        default=None, description="Comma-separated list of allowed CORS origins"
    )

    @field_validator("cors_origins")
    @classmethod
    def validate_cors_origins(cls, v: Optional[str]) -> Optional[list[str]]:
        """Convert comma-separated string to list."""
        if v is None:
            return None
        return [origin.strip() for origin in v.split(",") if origin.strip()]

    model_config = {
        "env_prefix": "SERVER_",
        "case_sensitive": False,
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }


def get_config() -> ServerConfig:
    """Get server configuration instance."""
    return ServerConfig()
