"""
CacheService - Redis-based caching with TTL management.

Provides a centralized caching layer with:
- TTL-based expiration
- Namespace/prefix support
- Serialization/deserialization
- Cache invalidation patterns
"""

from __future__ import annotations

import json
import logging
import os
from datetime import timedelta
from typing import Any, Callable, Optional, TypeVar, Union, cast
from uuid import UUID

import redis

logger = logging.getLogger(__name__)

T = TypeVar('T')


# Global Redis client (lazy initialization)
_REDIS_CLIENT: Optional[redis.Redis] = None


def get_redis_client() -> Optional[redis.Redis]:
    """
    Get or create the Redis client.

    Returns None if REDIS_URL is not configured.
    """
    global _REDIS_CLIENT

    if _REDIS_CLIENT is not None:
        return _REDIS_CLIENT

    url = os.getenv("REDIS_URL")
    if not url:
        logger.debug("REDIS_URL not configured, caching disabled")
        return None

    try:
        client = redis.Redis.from_url(url, decode_responses=True)
        client.ping()
        _REDIS_CLIENT = client
        logger.info("Redis cache connected")
        return _REDIS_CLIENT
    except redis.ConnectionError as e:
        logger.warning("Failed to connect to Redis: %s", e)
        return None


class CacheService:
    """
    Redis-based cache service with TTL management.

    Usage:
        # Simple get/set
        cache.set("my:key", {"data": "value"}, ttl_seconds=300)
        data = cache.get("my:key")

        # Get or compute
        data = cache.get_or_compute(
            "expensive:query:key",
            lambda: expensive_computation(),
            ttl_seconds=600
        )

        # Invalidation
        cache.delete("my:key")
        cache.delete_pattern("org:123:dashboard:*")
    """

    # Default TTLs for different cache types
    TTL_ORG_CONTEXT = 300       # 5 minutes
    TTL_CURRENCY_SETTINGS = 300 # 5 minutes
    TTL_DASHBOARD_STATS = 60    # 1 minute
    TTL_DASHBOARD_BALANCES = 120 # 2 minutes
    TTL_DASHBOARD_TREND = 300   # 5 minutes
    TTL_DEFAULT = 300           # 5 minutes

    def __init__(self, prefix: str = "dotmac"):
        """
        Initialize cache service.

        Args:
            prefix: Key prefix for namespacing (default: "dotmac")
        """
        self.prefix = prefix
        self._client: Optional[redis.Redis] = None

    @property
    def client(self) -> Optional[redis.Redis]:
        """Get Redis client (lazy initialization)."""
        if self._client is None:
            self._client = get_redis_client()
        return self._client

    @property
    def is_available(self) -> bool:
        """Check if cache is available."""
        return self.client is not None

    def _make_key(self, key: str) -> str:
        """Create a full cache key with prefix."""
        return f"{self.prefix}:{key}"

    def _serialize(self, value: Any) -> str:
        """Serialize value for storage."""
        return json.dumps(value, default=str)

    def _deserialize(self, value: str) -> Any:
        """Deserialize value from storage."""
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return value

    def get(self, key: str, default: T = None) -> Optional[T]:
        """
        Get a value from cache.

        Args:
            key: Cache key
            default: Default value if not found

        Returns:
            Cached value or default
        """
        client = self.client
        if client is None:
            return default

        try:
            full_key = self._make_key(key)
            value_any = cast(Any, client.get(full_key))

            if value_any is None:
                logger.debug("Cache miss: %s", key)
                return default

            logger.debug("Cache hit: %s", key)
            if isinstance(value_any, bytes):
                value_str = value_any.decode("utf-8")
            elif isinstance(value_any, str):
                value_str = value_any
            else:
                value_str = str(value_any)
            return cast(Optional[T], self._deserialize(value_str))

        except redis.RedisError as e:
            logger.warning("Cache get error for %s: %s", key, e)
            return default

    def set(
        self,
        key: str,
        value: Any,
        ttl_seconds: Optional[int] = None,
    ) -> bool:
        """
        Set a value in cache.

        Args:
            key: Cache key
            value: Value to cache
            ttl_seconds: Time-to-live in seconds (default: TTL_DEFAULT)

        Returns:
            True if successful
        """
        client = self.client
        if client is None:
            return False

        ttl = ttl_seconds or self.TTL_DEFAULT

        try:
            full_key = self._make_key(key)
            serialized = self._serialize(value)
            client.setex(full_key, ttl, serialized)
            logger.debug("Cache set: %s (ttl=%ds)", key, ttl)
            return True

        except redis.RedisError as e:
            logger.warning("Cache set error for %s: %s", key, e)
            return False

    def delete(self, key: str) -> bool:
        """
        Delete a key from cache.

        Args:
            key: Cache key

        Returns:
            True if deleted
        """
        client = self.client
        if client is None:
            return False

        try:
            full_key = self._make_key(key)
            deleted_any = cast(Any, client.delete(full_key))
            logger.debug("Cache delete: %s (deleted=%d)", key, deleted_any)
            return int(deleted_any) > 0

        except redis.RedisError as e:
            logger.warning("Cache delete error for %s: %s", key, e)
            return False

    def delete_pattern(self, pattern: str) -> int:
        """
        Delete keys matching a pattern.

        Args:
            pattern: Glob pattern (e.g., "org:123:dashboard:*")

        Returns:
            Number of keys deleted
        """
        client = self.client
        if client is None:
            return 0

        try:
            full_pattern = self._make_key(pattern)
            keys = list(client.scan_iter(match=full_pattern, count=100))

            if not keys:
                return 0

            deleted_any = cast(Any, client.delete(*keys))
            logger.debug("Cache delete pattern: %s (deleted=%d)", pattern, deleted_any)
            return int(deleted_any)

        except redis.RedisError as e:
            logger.warning("Cache delete_pattern error for %s: %s", pattern, e)
            return 0

    def get_or_compute(
        self,
        key: str,
        compute_fn: Callable[[], T],
        ttl_seconds: Optional[int] = None,
    ) -> T:
        """
        Get from cache or compute and cache.

        Args:
            key: Cache key
            compute_fn: Function to compute value if not cached
            ttl_seconds: TTL for cached value

        Returns:
            Cached or computed value
        """
        # Try cache first
        cached = self.get(key)
        if cached is not None:
            return cached

        # Compute
        value = compute_fn()

        # Cache if value is not None
        if value is not None:
            self.set(key, value, ttl_seconds)

        return value

    def invalidate_org(self, organization_id: UUID) -> int:
        """
        Invalidate all cache entries for an organization.

        Args:
            organization_id: Organization ID

        Returns:
            Number of keys invalidated
        """
        return self.delete_pattern(f"org:{organization_id}:*")

    def invalidate_dashboard(self, organization_id: UUID) -> int:
        """
        Invalidate dashboard cache for an organization.

        Args:
            organization_id: Organization ID

        Returns:
            Number of keys invalidated
        """
        return self.delete_pattern(f"org:{organization_id}:dashboard:*")


# Key builders for common cache patterns
class CacheKeys:
    """Cache key builders for common patterns."""

    @staticmethod
    def org_context(org_id: UUID) -> str:
        return f"org:{org_id}:context"

    @staticmethod
    def org_currency(org_id: UUID) -> str:
        return f"org:{org_id}:currency"

    @staticmethod
    def dashboard_stats(org_id: UUID, year: int) -> str:
        return f"org:{org_id}:dashboard:stats:y{year}"

    @staticmethod
    def dashboard_balances(org_id: UUID, year: int) -> str:
        return f"org:{org_id}:dashboard:balances:y{year}"

    @staticmethod
    def dashboard_trend(org_id: UUID, year: int) -> str:
        return f"org:{org_id}:dashboard:trend:y{year}"


# Module-level singleton
cache_service = CacheService()
