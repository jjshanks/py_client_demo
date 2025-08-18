"""
Tests for the client configuration system.

Validates that configuration classes work correctly with proper defaults
and can be serialized/deserialized appropriately.
"""

import httpx
import pytest
from pydantic import ValidationError

from client.config import (
    BulkheadConfig,
    CircuitBreakerConfig,
    ClientConfig,
    LoggingConfig,
    RetryConfig,
    TimeoutConfig,
)


class TestTimeoutConfig:
    """Test timeout configuration."""

    def test_default_timeouts(self):
        """Test default timeout values."""
        config = TimeoutConfig()
        assert config.connect == 60.0
        assert config.read == 15.0
        assert config.write == 15.0
        assert config.pool == 5.0

    def test_custom_timeouts(self):
        """Test custom timeout values."""
        config = TimeoutConfig(connect=30.0, read=10.0, write=10.0, pool=2.0)
        assert config.connect == 30.0
        assert config.read == 10.0
        assert config.write == 10.0
        assert config.pool == 2.0

    def test_to_httpx_timeout(self):
        """Test conversion to httpx.Timeout object."""
        config = TimeoutConfig(connect=30.0, read=10.0, write=10.0, pool=2.0)
        httpx_timeout = config.to_httpx_timeout()

        assert isinstance(httpx_timeout, httpx.Timeout)
        assert httpx_timeout.connect == 30.0
        assert httpx_timeout.read == 10.0
        assert httpx_timeout.write == 10.0
        assert httpx_timeout.pool == 2.0


class TestRetryConfig:
    """Test retry configuration."""

    def test_default_retry_config(self):
        """Test default retry values."""
        config = RetryConfig()
        assert config.max_attempts == 3
        assert config.min_wait_seconds == 1.0
        assert config.max_wait_seconds == 60.0
        assert config.multiplier == 2.0
        assert config.jitter is True

    def test_custom_retry_config(self):
        """Test custom retry values."""
        config = RetryConfig(
            max_attempts=5,
            min_wait_seconds=0.5,
            max_wait_seconds=30.0,
            multiplier=1.5,
            jitter=False,
        )
        assert config.max_attempts == 5
        assert config.min_wait_seconds == 0.5
        assert config.max_wait_seconds == 30.0
        assert config.multiplier == 1.5
        assert config.jitter is False


class TestCircuitBreakerConfig:
    """Test circuit breaker configuration."""

    def test_default_circuit_breaker_config(self):
        """Test default circuit breaker values."""
        config = CircuitBreakerConfig()
        assert config.failure_threshold == 5
        assert config.recovery_timeout == 30.0
        assert config.expected_exception == "APIConnectionError"

    def test_custom_circuit_breaker_config(self):
        """Test custom circuit breaker values."""
        config = CircuitBreakerConfig(
            failure_threshold=3, recovery_timeout=60.0, expected_exception="ServerError"
        )
        assert config.failure_threshold == 3
        assert config.recovery_timeout == 60.0
        assert config.expected_exception == "ServerError"


class TestBulkheadConfig:
    """Test bulkhead configuration."""

    def test_default_bulkhead_config(self):
        """Test default bulkhead values."""
        config = BulkheadConfig()
        assert config.max_concurrency == 50
        assert config.acquisition_timeout == 30.0

    def test_custom_bulkhead_config(self):
        """Test custom bulkhead values."""
        config = BulkheadConfig(max_concurrency=100, acquisition_timeout=60.0)
        assert config.max_concurrency == 100
        assert config.acquisition_timeout == 60.0


class TestLoggingConfig:
    """Test logging configuration."""

    def test_default_logging_config(self):
        """Test default logging values."""
        config = LoggingConfig()
        assert config.level == "INFO"
        assert config.include_request_id is True
        assert config.logger_name == "resilient_client"

    def test_custom_logging_config(self):
        """Test custom logging values."""
        config = LoggingConfig(
            level="DEBUG", include_request_id=False, logger_name="custom_client"
        )
        assert config.level == "DEBUG"
        assert config.include_request_id is False
        assert config.logger_name == "custom_client"


class TestClientConfig:
    """Test complete client configuration."""

    def test_minimal_client_config(self):
        """Test client config with only required fields."""
        config = ClientConfig(base_url="http://localhost:8000")
        assert config.base_url == "http://localhost:8000"

        # Test that all sub-configs have defaults
        assert isinstance(config.timeout, TimeoutConfig)
        assert isinstance(config.retry, RetryConfig)
        assert isinstance(config.circuit_breaker, CircuitBreakerConfig)
        assert isinstance(config.bulkhead, BulkheadConfig)
        assert isinstance(config.logging, LoggingConfig)

        # Test optional httpx settings
        assert config.follow_redirects is True
        assert config.verify_ssl is True
        assert config.user_agent is None

    def test_full_client_config(self):
        """Test client config with all fields customized."""
        config = ClientConfig(
            base_url="https://api.example.com",
            timeout=TimeoutConfig(connect=30.0, read=10.0),
            retry=RetryConfig(max_attempts=5),
            circuit_breaker=CircuitBreakerConfig(failure_threshold=3),
            bulkhead=BulkheadConfig(max_concurrency=25),
            logging=LoggingConfig(level="DEBUG"),
            follow_redirects=False,
            verify_ssl=False,
            user_agent="MyClient/1.0",
        )

        assert config.base_url == "https://api.example.com"
        assert config.timeout.connect == 30.0
        assert config.retry.max_attempts == 5
        assert config.circuit_breaker.failure_threshold == 3
        assert config.bulkhead.max_concurrency == 25
        assert config.logging.level == "DEBUG"
        assert config.follow_redirects is False
        assert config.verify_ssl is False
        assert config.user_agent == "MyClient/1.0"

    def test_config_validation(self):
        """Test configuration validation."""
        # Missing required field should raise validation error
        with pytest.raises(ValidationError):
            ClientConfig()

        # Invalid types should raise validation error
        with pytest.raises(ValidationError):
            ClientConfig(base_url=123)  # Should be string

    def test_config_extra_fields_forbidden(self):
        """Test that extra fields are forbidden."""
        with pytest.raises(ValidationError):
            ClientConfig(base_url="http://localhost", invalid_field="value")
