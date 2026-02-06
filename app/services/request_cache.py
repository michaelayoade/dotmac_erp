"""
Request-scoped cache using contextvars.

Provides a per-request cache that automatically clears when the request ends.
This prevents duplicate database queries within a single HTTP request.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Any, Callable, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Context variable for request-scoped cache
_request_cache: ContextVar[Optional[dict[str, Any]]] = ContextVar(
    "request_cache",
    default=None,
)


class RequestCache:
    """
    Request-scoped cache using context variables.

    This cache lives only for the duration of a single request.
    It's cleared automatically by the RequestCacheMiddleware.

    Usage:
        # In a request handler or service:
        value = request_cache.get("my_key")
        if value is None:
            value = expensive_db_query()
            request_cache.set("my_key", value)

        # Or use get_or_compute:
        value = request_cache.get_or_compute(
            "my_key",
            lambda: expensive_db_query()
        )
    """

    @staticmethod
    def _get_cache() -> dict[str, Any]:
        """Get or create the request cache dict."""
        cache = _request_cache.get()
        if cache is None:
            cache = {}
            _request_cache.set(cache)
        return cache

    @staticmethod
    def get(key: str, default: T = None) -> Optional[T]:
        """
        Get a value from the request cache.

        Args:
            key: Cache key
            default: Default value if not found

        Returns:
            Cached value or default
        """
        cache = _request_cache.get()
        if cache is None:
            return default

        value = cache.get(key)
        if value is not None:
            logger.debug("Request cache hit: %s", key)
        return value if value is not None else default

    @staticmethod
    def set(key: str, value: Any) -> None:
        """
        Set a value in the request cache.

        Args:
            key: Cache key
            value: Value to cache
        """
        cache = RequestCache._get_cache()
        cache[key] = value
        logger.debug("Request cache set: %s", key)

    @staticmethod
    def delete(key: str) -> bool:
        """
        Delete a key from the request cache.

        Args:
            key: Cache key

        Returns:
            True if deleted
        """
        cache = _request_cache.get()
        if cache is None:
            return False

        if key in cache:
            del cache[key]
            return True
        return False

    @staticmethod
    def clear() -> None:
        """Clear all entries from the request cache."""
        _request_cache.set(None)
        logger.debug("Request cache cleared")

    @staticmethod
    def get_or_compute(
        key: str,
        compute_fn: Callable[[], T],
    ) -> T:
        """
        Get from cache or compute and cache.

        Args:
            key: Cache key
            compute_fn: Function to compute value if not cached

        Returns:
            Cached or computed value
        """
        value = RequestCache.get(key)
        if value is not None:
            return value

        value = compute_fn()
        if value is not None:
            RequestCache.set(key, value)

        return value

    @staticmethod
    def stats() -> dict[str, int]:
        """Get cache statistics for debugging."""
        cache = _request_cache.get()
        if cache is None:
            return {"size": 0}
        return {"size": len(cache)}


# Module-level convenience instance
request_cache = RequestCache()


# Key builders for common request cache patterns
class RequestCacheKeys:
    """Request cache key builders."""

    @staticmethod
    def organization(org_id) -> str:
        return f"org:{org_id}"

    @staticmethod
    def org_currency(org_id) -> str:
        return f"org:{org_id}:currency"

    @staticmethod
    def org_fiscal_year_end(org_id) -> str:
        return f"org:{org_id}:fiscal_year_end"

    @staticmethod
    def current_user(user_id) -> str:
        return f"user:{user_id}"

    @staticmethod
    def db_entity(model_name: str, entity_id) -> str:
        return f"entity:{model_name}:{entity_id}"
