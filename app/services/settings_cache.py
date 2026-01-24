"""
Settings Cache Service.

Provides caching for domain settings to reduce database queries.
Uses Redis when available, with in-memory fallback for single-instance deployments.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.models.domain_settings import DomainSetting, SettingDomain, SettingValueType
from app.services.cache import cache_service, CacheService

logger = logging.getLogger(__name__)


# TTL configuration per domain (in seconds)
#
# TTL Strategy:
# - Shorter TTL = More responsive to changes, more DB queries
# - Longer TTL = Better performance, slower to reflect changes
#
# The cache is invalidated on writes (create/update/delete), so these TTLs
# only affect how long stale data persists if invalidation fails or if
# settings are changed directly in the database.
#
DOMAIN_TTL_CONFIG: Dict[SettingDomain, int] = {
    # === HIGH FREQUENCY / SECURITY CRITICAL (60s) ===
    # These are checked on nearly every request or have security implications.
    # Short TTL ensures changes take effect quickly.
    SettingDomain.features: 60,
    # Feature flags: Checked on every API request to gated modules (inventory,
    # fixed assets, leases). A 60s TTL means disabling a feature takes effect
    # within 1 minute across all workers.

    SettingDomain.auth: 60,
    # Auth settings: JWT TTLs, cookie config, TOTP settings. Security-critical
    # so changes must propagate quickly. A misconfigured auth setting could
    # lock users out, so we want fast correction.

    # === MODERATE FREQUENCY (300s / 5 minutes) ===
    # These are read periodically but not on every request. Changes are
    # typically administrative and can tolerate a few minutes delay.
    SettingDomain.email: 300,
    # Email/SMTP: Only read when sending emails. Config changes (new SMTP
    # server, credentials) can wait 5 minutes to take effect.

    SettingDomain.scheduler: 300,
    # Celery/scheduler: Read at task scheduling time. Changing broker URLs
    # or beat intervals typically requires worker restart anyway.

    SettingDomain.automation: 300,
    # Workflow/recurring: Read when processing automations. Webhook timeouts,
    # max actions per event - operational tuning that doesn't need instant updates.

    SettingDomain.reporting: 300,
    # Report settings: Page size, export format. Only read when generating
    # reports, which is infrequent.

    # === LOW FREQUENCY (600s / 10 minutes) ===
    # These rarely change and are not time-sensitive.
    SettingDomain.audit: 600,
    # Audit settings: Which methods to audit, skip paths. Changes are rare
    # and typically part of compliance reviews, not urgent operations.

    SettingDomain.payments: 600,
    # Payment gateway config: Paystack keys, webhook secrets. Changed during
    # initial setup or key rotation - both are planned activities where a
    # 10-minute propagation delay is acceptable.
}

DEFAULT_TTL = 300  # 5 minutes - balanced default for undefined domains


class InMemoryCache:
    """
    Simple in-memory cache with TTL support and automatic cleanup.

    Used as fallback when Redis is not available.
    Thread-safe for basic operations via dict atomicity.

    Features:
    - TTL-based expiration
    - Max size limit to prevent unbounded memory growth
    - Periodic cleanup of expired entries
    - LRU-like eviction when max size is reached
    """

    # Cleanup every N set operations
    CLEANUP_INTERVAL = 100

    # Default max entries (0 = unlimited)
    DEFAULT_MAX_SIZE = 10000

    def __init__(self, max_size: int = DEFAULT_MAX_SIZE):
        self._cache: Dict[str, tuple[Any, float]] = {}  # key -> (value, expiry_time)
        self._max_size = max_size
        self._operation_count = 0

    def get(self, key: str) -> Optional[Any]:
        """Get value if exists and not expired."""
        entry = self._cache.get(key)
        if entry is None:
            return None

        value, expiry = entry
        if time.time() > expiry:
            # Expired - remove and return None
            self._cache.pop(key, None)
            return None

        return value

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        """Set value with TTL."""
        expiry = time.time() + ttl_seconds
        self._cache[key] = (value, expiry)

        # Periodic cleanup
        self._operation_count += 1
        if self._operation_count >= self.CLEANUP_INTERVAL:
            self._cleanup_expired()
            self._operation_count = 0

        # Enforce max size
        if self._max_size > 0 and len(self._cache) > self._max_size:
            self._evict_oldest()

    def delete(self, key: str) -> bool:
        """Delete a key."""
        return self._cache.pop(key, None) is not None

    def delete_pattern(self, pattern: str) -> int:
        """Delete keys matching pattern (simple prefix matching)."""
        # Simple implementation: treat pattern as prefix if it ends with *
        if pattern.endswith("*"):
            prefix = pattern[:-1]
            keys_to_delete = [k for k in self._cache.keys() if k.startswith(prefix)]
        else:
            keys_to_delete = [pattern] if pattern in self._cache else []

        for key in keys_to_delete:
            self._cache.pop(key, None)

        return len(keys_to_delete)

    def clear(self) -> None:
        """Clear all entries."""
        self._cache.clear()
        self._operation_count = 0

    def _cleanup_expired(self) -> int:
        """
        Remove all expired entries.

        Returns:
            Number of entries removed
        """
        now = time.time()
        expired_keys = [
            key for key, (_, expiry) in self._cache.items()
            if now > expiry
        ]
        for key in expired_keys:
            self._cache.pop(key, None)

        if expired_keys:
            logger.debug("Cache cleanup: removed %d expired entries", len(expired_keys))

        return len(expired_keys)

    def _evict_oldest(self) -> int:
        """
        Evict oldest entries when max size is exceeded.

        Removes entries closest to expiration first.

        Returns:
            Number of entries evicted
        """
        if self._max_size <= 0:
            return 0

        # First, cleanup expired entries
        self._cleanup_expired()

        # If still over limit, evict entries closest to expiration
        overage = len(self._cache) - self._max_size
        if overage <= 0:
            return 0

        # Sort by expiry time (soonest first) and evict
        sorted_items = sorted(
            self._cache.items(),
            key=lambda item: item[1][1]  # Sort by expiry time
        )

        evicted = 0
        for key, _ in sorted_items[:overage]:
            self._cache.pop(key, None)
            evicted += 1

        if evicted:
            logger.debug("Cache eviction: removed %d entries (max_size=%d)", evicted, self._max_size)

        return evicted

    def stats(self) -> Dict[str, Any]:
        """
        Get cache statistics.

        Returns:
            Dict with size, max_size, and expired_count
        """
        now = time.time()
        expired_count = sum(1 for _, (_, expiry) in self._cache.items() if now > expiry)
        return {
            "size": len(self._cache),
            "max_size": self._max_size,
            "expired_pending": expired_count,
        }


# Module-level in-memory cache instance
_inmemory_cache = InMemoryCache()


class SettingsCache:
    """
    Settings-specific cache with domain awareness.

    Provides:
    - Per-domain TTL configuration
    - Bulk domain caching
    - Automatic value extraction from settings
    - Invalidation helpers

    Usage:
        # Get a single setting value
        value = settings_cache.get_setting_value(
            db, SettingDomain.features, "enable_inventory"
        )

        # Get all settings for a domain (cached)
        all_features = settings_cache.get_domain_settings(
            db, SettingDomain.features
        )

        # Invalidate after update
        settings_cache.invalidate_setting(SettingDomain.features, "enable_inventory")
        settings_cache.invalidate_domain(SettingDomain.features)
    """

    def __init__(self, redis_cache: CacheService, inmemory_cache: InMemoryCache):
        self._redis = redis_cache
        self._inmemory = inmemory_cache

    def _get_ttl(self, domain: SettingDomain) -> int:
        """Get TTL for a domain."""
        return DOMAIN_TTL_CONFIG.get(domain, DEFAULT_TTL)

    def _make_key(self, domain: SettingDomain, key: Optional[str] = None) -> str:
        """Create cache key for settings."""
        if key:
            return f"settings:{domain.value}:{key}"
        return f"settings:{domain.value}:_all"

    def _cache_get(self, cache_key: str) -> Optional[Any]:
        """Get from cache (Redis first, then in-memory)."""
        # Try Redis first
        if self._redis.is_available:
            value = self._redis.get(cache_key)
            if value is not None:
                logger.debug("Settings cache hit (Redis): %s", cache_key)
                return value

        # Fall back to in-memory
        value = self._inmemory.get(cache_key)
        if value is not None:
            logger.debug("Settings cache hit (in-memory): %s", cache_key)
        return value

    def _cache_set(self, cache_key: str, value: Any, ttl: int) -> None:
        """Set in cache (both Redis and in-memory for resilience)."""
        # Always set in-memory for fast local access
        self._inmemory.set(cache_key, value, ttl)

        # Also set in Redis if available
        if self._redis.is_available:
            self._redis.set(cache_key, value, ttl)

        logger.debug("Settings cache set: %s (ttl=%ds)", cache_key, ttl)

    def _extract_value(self, setting: DomainSetting) -> Any:
        """Extract the actual value from a setting."""
        if setting.value_json is not None:
            return setting.value_json
        if setting.value_text is not None:
            # Convert based on value_type
            if setting.value_type == SettingValueType.boolean:
                return setting.value_text.lower() in ("true", "1", "yes", "on")
            if setting.value_type == SettingValueType.integer:
                try:
                    return int(setting.value_text)
                except (TypeError, ValueError):
                    return setting.value_text
            return setting.value_text
        return None

    def get_setting_value(
        self,
        db: Session,
        domain: SettingDomain,
        key: str,
        default: Any = None,
    ) -> Any:
        """
        Get a single setting value with caching.

        Args:
            db: Database session
            domain: Setting domain
            key: Setting key
            default: Default value if not found

        Returns:
            Setting value or default
        """
        cache_key = self._make_key(domain, key)

        # Check cache
        cached = self._cache_get(cache_key)
        if cached is not None:
            # Handle cached "not found" sentinel
            if cached == "__NOT_FOUND__":
                return default
            return cached

        # Query database
        setting = (
            db.query(DomainSetting)
            .filter(
                DomainSetting.domain == domain,
                DomainSetting.key == key,
                DomainSetting.is_active.is_(True),
            )
            .first()
        )

        ttl = self._get_ttl(domain)

        if not setting:
            # Cache the "not found" to avoid repeated DB queries
            self._cache_set(cache_key, "__NOT_FOUND__", ttl)
            return default

        value = self._extract_value(setting)
        self._cache_set(cache_key, value, ttl)
        return value

    def get_domain_settings(
        self,
        db: Session,
        domain: SettingDomain,
    ) -> Dict[str, Any]:
        """
        Get all settings for a domain with caching.

        Args:
            db: Database session
            domain: Setting domain

        Returns:
            Dict mapping setting keys to values
        """
        cache_key = self._make_key(domain)

        # Check cache
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        # Query database
        settings = (
            db.query(DomainSetting)
            .filter(
                DomainSetting.domain == domain,
                DomainSetting.is_active.is_(True),
            )
            .all()
        )

        result = {}
        for setting in settings:
            result[setting.key] = self._extract_value(setting)

        ttl = self._get_ttl(domain)
        self._cache_set(cache_key, result, ttl)

        return result

    def invalidate_setting(self, domain: SettingDomain, key: str) -> None:
        """
        Invalidate cache for a specific setting.

        Args:
            domain: Setting domain
            key: Setting key
        """
        # Invalidate specific key
        cache_key = self._make_key(domain, key)
        self._inmemory.delete(cache_key)
        if self._redis.is_available:
            self._redis.delete(cache_key)

        # Also invalidate the bulk domain cache
        domain_key = self._make_key(domain)
        self._inmemory.delete(domain_key)
        if self._redis.is_available:
            self._redis.delete(domain_key)

        logger.debug("Settings cache invalidated: %s/%s", domain.value, key)

    def invalidate_domain(self, domain: SettingDomain) -> None:
        """
        Invalidate all cached settings for a domain.

        Args:
            domain: Setting domain
        """
        pattern = f"settings:{domain.value}:*"
        self._inmemory.delete_pattern(pattern)
        if self._redis.is_available:
            self._redis.delete_pattern(pattern)

        logger.debug("Settings cache invalidated for domain: %s", domain.value)

    def invalidate_all(self) -> None:
        """Invalidate all settings cache."""
        self._inmemory.delete_pattern("settings:*")
        if self._redis.is_available:
            self._redis.delete_pattern("settings:*")

        logger.debug("All settings cache invalidated")

    def clear_inmemory(self) -> None:
        """Clear only the in-memory cache (useful for testing)."""
        self._inmemory.clear()


# Module-level singleton
settings_cache = SettingsCache(cache_service, _inmemory_cache)


# Convenience functions for common patterns
def get_cached_setting(
    db: Session,
    domain: SettingDomain,
    key: str,
    default: Any = None,
) -> Any:
    """
    Convenience function to get a cached setting value.

    Args:
        db: Database session
        domain: Setting domain
        key: Setting key
        default: Default value if not found

    Returns:
        Setting value or default
    """
    return settings_cache.get_setting_value(db, domain, key, default)


def invalidate_setting_cache(domain: SettingDomain, key: Optional[str] = None) -> None:
    """
    Convenience function to invalidate settings cache.

    Args:
        domain: Setting domain
        key: Setting key (if None, invalidates entire domain)
    """
    if key:
        settings_cache.invalidate_setting(domain, key)
    else:
        settings_cache.invalidate_domain(domain)
