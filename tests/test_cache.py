"""Unit tests for cache implementation."""

import asyncio
import time

import pytest

from server.cache import CacheEntry, IdempotencyCache, TTLCache


class TestCacheEntry:
    """Test CacheEntry functionality."""

    def test_cache_entry_creation(self):
        """Test cache entry creation and basic properties."""
        entry = CacheEntry(value="test", created_at=time.time())
        assert entry.value == "test"
        assert isinstance(entry.created_at, float)

    def test_cache_entry_expiration(self):
        """Test cache entry expiration logic."""
        now = time.time()
        entry = CacheEntry(value="test", created_at=now - 10)  # 10 seconds old

        assert entry.is_expired(5)   # Should be expired (older than 5s TTL)
        assert not entry.is_expired(15)  # Should not be expired (within 15s TTL)


class TestTTLCache:
    """Test TTL cache functionality."""

    @pytest.fixture
    def cache(self):
        """Create a test cache with small limits for testing."""
        return TTLCache(max_size=3, ttl_seconds=1)

    @pytest.mark.asyncio
    async def test_basic_get_set(self, cache):
        """Test basic cache get/set operations."""
        # Test cache miss
        result = await cache.get("missing")
        assert result is None

        # Test cache set and hit
        await cache.set("key1", "value1")
        result = await cache.get("key1")
        assert result == "value1"

    @pytest.mark.asyncio
    async def test_size_limit_eviction(self, cache):
        """Test that cache evicts LRU entries when size limit is reached."""
        # Fill cache to capacity
        await cache.set("key1", "value1")
        await cache.set("key2", "value2")
        await cache.set("key3", "value3")

        # Verify all entries are present
        assert await cache.get("key1") == "value1"
        assert await cache.get("key2") == "value2"
        assert await cache.get("key3") == "value3"

        # Add one more entry, should evict oldest (key1)
        await cache.set("key4", "value4")

        # key1 should be evicted, others should remain
        assert await cache.get("key1") is None
        assert await cache.get("key2") == "value2"
        assert await cache.get("key3") == "value3"
        assert await cache.get("key4") == "value4"

    @pytest.mark.asyncio
    async def test_lru_ordering(self, cache):
        """Test that accessing entries updates their LRU position."""
        # Fill cache
        await cache.set("key1", "value1")
        await cache.set("key2", "value2")
        await cache.set("key3", "value3")

        # Access key1 to make it most recently used
        await cache.get("key1")

        # Add new entry, should evict key2 (oldest unused)
        await cache.set("key4", "value4")

        # key1 should still be present (was accessed recently)
        # key2 should be evicted
        assert await cache.get("key1") == "value1"
        assert await cache.get("key2") is None
        assert await cache.get("key3") == "value3"
        assert await cache.get("key4") == "value4"

    @pytest.mark.asyncio
    async def test_ttl_expiration(self, cache):
        """Test TTL-based expiration."""
        # Set a value
        await cache.set("key1", "value1")

        # Should be available immediately
        assert await cache.get("key1") == "value1"

        # Wait for TTL to expire (cache has 1 second TTL)
        await asyncio.sleep(1.1)

        # Should be expired and return None
        assert await cache.get("key1") is None

    @pytest.mark.asyncio
    async def test_overwrite_existing_key(self, cache):
        """Test that setting an existing key overwrites the value."""
        await cache.set("key1", "original")
        assert await cache.get("key1") == "original"

        await cache.set("key1", "updated")
        assert await cache.get("key1") == "updated"

    @pytest.mark.asyncio
    async def test_clear_cache(self, cache):
        """Test cache clearing functionality."""
        await cache.set("key1", "value1")
        await cache.set("key2", "value2")

        assert await cache.size() == 2

        await cache.clear()

        assert await cache.size() == 0
        assert await cache.get("key1") is None
        assert await cache.get("key2") is None

    @pytest.mark.asyncio
    async def test_cache_stats(self, cache):
        """Test cache statistics reporting."""
        # Empty cache stats
        stats = await cache.stats()
        assert stats["size"] == 0
        assert stats["max_size"] == 3
        assert stats["ttl_seconds"] == 1

        # Add some entries
        await cache.set("key1", "value1")
        await cache.set("key2", "value2")

        stats = await cache.stats()
        assert stats["size"] == 2
        assert stats["expired_entries"] == 0
        assert stats["oldest_entry_age_seconds"] >= 0
        assert stats["newest_entry_age_seconds"] >= 0

    @pytest.mark.asyncio
    async def test_concurrent_access(self, cache):
        """Test thread safety under concurrent access."""
        async def worker(worker_id: int):
            """Worker function that performs cache operations."""
            for i in range(10):
                key = f"worker_{worker_id}_key_{i}"
                value = f"worker_{worker_id}_value_{i}"
                await cache.set(key, value)
                result = await cache.get(key)
                assert result == value

        # Run multiple workers concurrently
        workers = [worker(i) for i in range(5)]
        await asyncio.gather(*workers)

        # Cache size should not exceed max_size even with concurrent operations
        final_size = await cache.size()
        assert final_size <= 3  # max_size is 3


class TestIdempotencyCache:
    """Test idempotency cache wrapper."""

    @pytest.fixture
    def idempotency_cache(self):
        """Create a test idempotency cache."""
        return IdempotencyCache(max_size=10, ttl_seconds=1)

    @pytest.mark.asyncio
    async def test_store_and_retrieve_response(self, idempotency_cache):
        """Test storing and retrieving responses by request ID."""
        request_id = "test-request-123"
        message_id = "uuid-abc-123"

        # Store response
        await idempotency_cache.store_response(request_id, message_id)

        # Retrieve response
        cached_response = await idempotency_cache.get_response(request_id)
        assert cached_response == message_id

    @pytest.mark.asyncio
    async def test_cache_miss(self, idempotency_cache):
        """Test cache miss for non-existent request ID."""
        result = await idempotency_cache.get_response("non-existent")
        assert result is None

    @pytest.mark.asyncio
    async def test_ttl_expiration(self, idempotency_cache):
        """Test that responses expire after TTL."""
        request_id = "test-request-456"
        message_id = "uuid-def-456"

        # Store response
        await idempotency_cache.store_response(request_id, message_id)

        # Should be available immediately
        assert await idempotency_cache.get_response(request_id) == message_id

        # Wait for expiration
        await asyncio.sleep(1.1)

        # Should be expired
        assert await idempotency_cache.get_response(request_id) is None

    @pytest.mark.asyncio
    async def test_cache_stats(self, idempotency_cache):
        """Test cache statistics delegation."""
        stats = await idempotency_cache.stats()
        assert "size" in stats
        assert "max_size" in stats
        assert "ttl_seconds" in stats
