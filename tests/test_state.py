"""Unit tests for state management."""

import asyncio
import time

import pytest

from server.state import FailureConfig, FailureStateManager, ServerState


class TestFailureConfig:
    """Test FailureConfig dataclass."""

    def test_default_values(self):
        """Test default values of FailureConfig."""
        config = FailureConfig()
        assert config.fail_requests_count == 0
        assert config.fail_until_timestamp is None


class TestFailureStateManager:
    """Test FailureStateManager functionality."""

    @pytest.fixture
    def manager(self):
        """Create a fresh FailureStateManager for each test."""
        return FailureStateManager()

    @pytest.mark.asyncio
    async def test_initial_state(self, manager):
        """Test initial state has no failures configured."""
        assert not await manager.should_fail()

        status = await manager.get_status()
        assert status["fail_requests_count"] == 0
        assert status["fail_until_timestamp"] is None
        assert status["duration_remaining_seconds"] == 0
        assert not status["currently_failing"]

    @pytest.mark.asyncio
    async def test_count_based_failure(self, manager):
        """Test count-based failure mode."""
        # Set fail count
        await manager.set_fail_count(3)

        # Should fail and decrement count
        assert await manager.should_fail()  # count=2 after this call
        assert await manager.should_fail()  # count=1 after this call
        assert await manager.should_fail()  # count=0 after this call

        # Should not fail anymore
        assert not await manager.should_fail()

        # Verify count is zero
        status = await manager.get_status()
        assert status["fail_requests_count"] == 0

    @pytest.mark.asyncio
    async def test_duration_based_failure(self, manager):
        """Test duration-based failure mode."""
        # Set fail duration for 0.5 seconds
        await manager.set_fail_duration(1)

        # Should fail initially
        assert await manager.should_fail()

        # Should still fail (duration doesn't decrement)
        assert await manager.should_fail()

        # Wait for duration to expire
        await asyncio.sleep(1.1)

        # Should not fail anymore
        assert not await manager.should_fail()

    @pytest.mark.asyncio
    async def test_combined_failure_modes(self, manager):
        """Test that count and duration modes work simultaneously."""
        # Set both modes
        await manager.set_fail_count(2)
        await manager.set_fail_duration(1)

        # Should fail due to both conditions
        assert await manager.should_fail()  # count=1, duration still active
        assert await manager.should_fail()  # count=0, duration still active

        # Count is exhausted but duration should still cause failure
        assert await manager.should_fail()  # count=0, duration still active

        # Wait for duration to expire
        await asyncio.sleep(1.1)

        # Now should not fail (both conditions cleared)
        assert not await manager.should_fail()

    @pytest.mark.asyncio
    async def test_reset_failures(self, manager):
        """Test resetting all failure modes."""
        # Set both failure modes
        await manager.set_fail_count(5)
        await manager.set_fail_duration(10)

        # Verify they're active
        assert await manager.should_fail()

        # Reset all failures
        await manager.reset_failures()

        # Should not fail anymore
        assert not await manager.should_fail()

        # Verify status is clean
        status = await manager.get_status()
        assert status["fail_requests_count"] == 0
        assert status["fail_until_timestamp"] is None
        assert not status["currently_failing"]

    @pytest.mark.asyncio
    async def test_negative_count_handling(self, manager):
        """Test that negative counts are handled gracefully."""
        # This shouldn't happen in normal operation, but test defensive programming
        await manager.set_fail_count(-5)

        status = await manager.get_status()
        assert status["fail_requests_count"] == 0  # Should be clamped to 0

    @pytest.mark.asyncio
    async def test_concurrent_access(self, manager):
        """Test thread safety under concurrent access."""
        # Set initial fail count
        await manager.set_fail_count(10)

        async def worker():
            """Worker that checks and potentially decrements failure count."""
            return await manager.should_fail()

        # Run multiple workers concurrently
        results = await asyncio.gather(*[worker() for _ in range(15)])

        # Exactly 10 should have returned True (due to initial count)
        true_count = sum(results)
        assert true_count == 10

        # Final state should have count 0
        status = await manager.get_status()
        assert status["fail_requests_count"] == 0


class TestServerState:
    """Test ServerState functionality."""

    @pytest.fixture
    def server_state(self):
        """Create a fresh ServerState for each test."""
        return ServerState()

    def test_initial_state(self, server_state):
        """Test initial server state."""
        assert server_state.failure_manager is not None
        assert server_state.concurrency_semaphore is None
        assert server_state.get_uptime_seconds() >= 0

    def test_concurrency_semaphore_initialization(self, server_state):
        """Test concurrency semaphore initialization."""
        max_concurrency = 5
        server_state.initialize_concurrency_semaphore(max_concurrency)

        assert server_state.concurrency_semaphore is not None
        assert server_state.concurrency_semaphore._value == max_concurrency

    def test_uptime_tracking(self, server_state):
        """Test uptime calculation."""
        initial_uptime = server_state.get_uptime_seconds()
        assert initial_uptime >= 0

        # Small delay
        time.sleep(0.1)

        later_uptime = server_state.get_uptime_seconds()
        assert later_uptime > initial_uptime
        assert later_uptime - initial_uptime >= 0.1


class TestConcurrencyControl:
    """Test concurrency control with semaphore."""

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrency(self):
        """Test that semaphore properly limits concurrent operations."""
        semaphore = asyncio.Semaphore(2)  # Allow only 2 concurrent operations
        active_count = 0
        max_concurrent = 0
        results = []

        async def worker(worker_id: int):
            nonlocal active_count, max_concurrent

            async with semaphore:
                active_count += 1
                max_concurrent = max(max_concurrent, active_count)
                results.append(f"Worker {worker_id} started")

                # Simulate work
                await asyncio.sleep(0.1)

                active_count -= 1
                results.append(f"Worker {worker_id} finished")

        # Start 5 workers, but semaphore should limit to 2 concurrent
        await asyncio.gather(*[worker(i) for i in range(5)])

        # Verify that never more than 2 were active simultaneously
        assert max_concurrent <= 2
        assert len(results) == 10  # 5 start + 5 finish messages
