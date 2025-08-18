"""TTL-aware cache implementation with size and time-based eviction."""

import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Optional

import structlog

logger = structlog.get_logger()


@dataclass
class CacheEntry:
    """Cache entry with value and timestamp."""
    value: Any
    created_at: float

    def is_expired(self, ttl_seconds: int) -> bool:
        """Check if this entry has expired based on TTL."""
        return time.time() - self.created_at > ttl_seconds


class TTLCache:
    """
    Thread-safe cache with both size-based and TTL-based eviction.
    
    Features:
    - LRU eviction when max size is reached
    - TTL-based expiration with automatic cleanup
    - Thread-safe operations using asyncio.Lock
    - O(1) get/set operations
    - Efficient batch cleanup of expired entries
    """

    def __init__(self, max_size: int, ttl_seconds: int):
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self._data: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = asyncio.Lock()
        self._last_cleanup = time.time()
        # Run cleanup every 60 seconds or when 10% of TTL has passed, whichever is shorter
        self._cleanup_interval = min(60, max(10, ttl_seconds * 0.1))

    async def get(self, key: str) -> Optional[Any]:
        """
        Get a value from the cache.
        
        Returns None if key doesn't exist or has expired.
        Automatically cleans up expired entries periodically.
        """
        async with self._lock:
            await self._maybe_cleanup_expired()

            entry = self._data.get(key)
            if entry is None:
                logger.debug("Cache miss", key=key, reason="not_found")
                return None

            if entry.is_expired(self.ttl_seconds):
                # Entry has expired, remove it
                del self._data[key]
                logger.debug("Cache miss", key=key, reason="expired",
                           entry_age=time.time() - entry.created_at)
                return None

            # Move to end (most recently used)
            self._data.move_to_end(key)
            logger.debug("Cache hit", key=key, entry_age=time.time() - entry.created_at)
            return entry.value

    async def set(self, key: str, value: Any) -> None:
        """
        Set a value in the cache.
        
        Automatically evicts LRU entries if max size is exceeded.
        """
        async with self._lock:
            current_time = time.time()

            # Remove existing entry if present
            if key in self._data:
                del self._data[key]

            # Add new entry
            self._data[key] = CacheEntry(value=value, created_at=current_time)

            # Enforce size limit by removing LRU entries
            evicted_count = 0
            while len(self._data) > self.max_size:
                evicted_key, evicted_entry = self._data.popitem(last=False)  # Remove oldest
                evicted_count += 1
                logger.debug(
                    "Cache eviction (size limit)",
                    evicted_key=evicted_key,
                    entry_age=current_time - evicted_entry.created_at,
                    cache_size=len(self._data)
                )

            if evicted_count > 0:
                logger.debug(
                    "Cache size eviction completed",
                    evicted_count=evicted_count,
                    current_size=len(self._data),
                    max_size=self.max_size
                )

            logger.debug("Cache set", key=key, cache_size=len(self._data))

    async def clear(self) -> None:
        """Clear all cache entries."""
        async with self._lock:
            cleared_count = len(self._data)
            self._data.clear()
            logger.info("Cache cleared", cleared_count=cleared_count)

    async def size(self) -> int:
        """Get current cache size."""
        async with self._lock:
            return len(self._data)

    async def stats(self) -> dict:
        """Get cache statistics for monitoring."""
        async with self._lock:
            current_time = time.time()
            expired_count = 0
            oldest_entry_age = 0
            newest_entry_age = 0

            if self._data:
                # Count expired entries without removing them
                for entry in self._data.values():
                    if entry.is_expired(self.ttl_seconds):
                        expired_count += 1

                # Calculate age statistics
                entries_by_age = sorted(self._data.values(), key=lambda e: e.created_at)
                oldest_entry_age = current_time - entries_by_age[0].created_at
                newest_entry_age = current_time - entries_by_age[-1].created_at

            return {
                "size": len(self._data),
                "max_size": self.max_size,
                "ttl_seconds": self.ttl_seconds,
                "expired_entries": expired_count,
                "oldest_entry_age_seconds": oldest_entry_age,
                "newest_entry_age_seconds": newest_entry_age,
                "last_cleanup_seconds_ago": current_time - self._last_cleanup
            }

    async def _maybe_cleanup_expired(self) -> None:
        """Clean up expired entries if enough time has passed since last cleanup."""
        current_time = time.time()
        if current_time - self._last_cleanup < self._cleanup_interval:
            return

        await self._cleanup_expired()

    async def _cleanup_expired(self) -> None:
        """Remove all expired entries from the cache."""
        current_time = time.time()
        initial_size = len(self._data)

        # Collect expired keys
        expired_keys = []
        for key, entry in self._data.items():
            if entry.is_expired(self.ttl_seconds):
                expired_keys.append(key)

        # Remove expired entries
        for key in expired_keys:
            del self._data[key]

        self._last_cleanup = current_time

        if expired_keys:
            logger.debug(
                "Cache TTL cleanup completed",
                expired_count=len(expired_keys),
                remaining_count=len(self._data),
                initial_size=initial_size
            )


class IdempotencyCache:
    """
    Idempotency cache for storing request responses.
    
    Maps request IDs to response data with TTL and size limits.
    """

    def __init__(self, max_size: int, ttl_seconds: int):
        self._cache = TTLCache(max_size, ttl_seconds)

    async def get_response(self, request_id: str) -> Optional[str]:
        """Get cached response for a request ID."""
        return await self._cache.get(request_id)

    async def store_response(self, request_id: str, message_id: str) -> None:
        """Store a response for a request ID."""
        await self._cache.set(request_id, message_id)

    async def clear(self) -> None:
        """Clear all cached responses."""
        await self._cache.clear()

    async def stats(self) -> dict:
        """Get cache statistics."""
        return await self._cache.stats()
