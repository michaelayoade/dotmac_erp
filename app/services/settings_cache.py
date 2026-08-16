"""
Settings Cache Service.

Provides caching for domain settings to reduce database queries.
Uses Redis when available, with in-memory fallback for single-instance deployments.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, cast

from sqlalchemy import case, or_, select
from sqlalchemy.orm import Session

from app.db.session_context import allow_cross_org
from app.models.domain_settings import DomainSetting, SettingDomain
from app.services.cache import CacheService, cache_service

logger = logging.getLogger(__name__)

# Cache-key segment used for the platform-wide (organization_id IS NULL) scope.
#
# Tenant scopes render as ``org=<uuid>`` and this scope renders as the bare
# literal ``global``. The two forms are structurally different segments, so a
# tenant whose identifier happens to be the string "global" would render as
# ``org=global`` and still cannot collide with the platform scope.
_GLOBAL_SCOPE_SEGMENT = "global"


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
DOMAIN_TTL_CONFIG: dict[SettingDomain, int] = {
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
        self._cache: dict[str, tuple[Any, float]] = {}  # key -> (value, expiry_time)
        self._max_size = max_size
        self._operation_count = 0

    def get(self, key: str) -> Any | None:
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
            keys_to_delete = [k for k in self._cache if k.startswith(prefix)]
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
        expired_keys = [key for key, (_, expiry) in self._cache.items() if now > expiry]
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
            key=lambda item: item[1][1],  # Sort by expiry time
        )

        evicted = 0
        for key, _ in sorted_items[:overage]:
            self._cache.pop(key, None)
            evicted += 1

        if evicted:
            logger.debug(
                "Cache eviction: removed %d entries (max_size=%d)",
                evicted,
                self._max_size,
            )

        return evicted

    def stats(self) -> dict[str, Any]:
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
    Settings-specific cache with domain AND tenant awareness.

    Every cached read is scoped to exactly one organization, or explicitly to
    the platform-wide (``organization_id IS NULL``) rows via the separately
    named ``*_global_*`` methods. There is deliberately no "unscoped" read:
    an omitted or ``None`` organization on a tenant read raises instead of
    silently resolving — and then caching — another tenant's value.

    Resolution order matches ``app.services.settings_spec.resolve_value`` /
    ``DomainSettings.get_by_key``: the organization's own row wins, the
    platform-wide row is the fallback, and the caller's declared default is
    used when neither exists.

    Provides:
    - Per-domain TTL configuration
    - Bulk domain caching
    - Value extraction and spec enforcement shared with ``resolve_value``
    - Scope-correct invalidation helpers

    Usage:
        # Get a single setting value for one organization
        value = settings_cache.get_setting_value(
            db, SettingDomain.features, "enable_inventory",
            organization_id=org_id,
        )

        # Get all settings for a domain, for one organization (cached)
        all_features = settings_cache.get_domain_settings(
            db, SettingDomain.features, organization_id=org_id
        )

        # Deliberately read the platform-wide row only
        value = settings_cache.get_global_setting_value(
            db, SettingDomain.features, "enable_inventory"
        )

        # Invalidate after update (organization_id=None => the platform row
        # changed, so every tenant's cached fallback is dropped too)
        settings_cache.invalidate_setting(
            SettingDomain.features, "enable_inventory", organization_id=org_id
        )
        settings_cache.invalidate_domain(SettingDomain.features)
    """

    def __init__(self, redis_cache: CacheService, inmemory_cache: InMemoryCache):
        self._redis = redis_cache
        self._inmemory = inmemory_cache

    def _get_ttl(self, domain: SettingDomain) -> int:
        """Get TTL for a domain."""
        return DOMAIN_TTL_CONFIG.get(domain, DEFAULT_TTL)

    @staticmethod
    def _scope_segment(organization_id: uuid.UUID | None) -> str:
        """Render the tenant scope segment of a cache key."""
        if organization_id is None:
            return _GLOBAL_SCOPE_SEGMENT
        return f"org={organization_id}"

    def _make_key(
        self,
        domain: SettingDomain,
        organization_id: uuid.UUID | None,
        key: str | None = None,
    ) -> str:
        """
        Create a tenant-scoped cache key for settings.

        Layout: ``settings:<domain>:k=<key>:<scope>`` for a single setting and
        ``settings:<domain>:all:<scope>`` for the bulk domain dict. Real setting
        keys always carry the ``k=`` prefix, so no setting named ``all`` can
        collide with the bulk entry. The scope is the LAST segment so that
        prefix-based invalidation can target "this key, every tenant" (a
        platform-row write) and "this domain, every tenant".
        """
        scope = self._scope_segment(organization_id)
        if key:
            return f"settings:{domain.value}:k={key}:{scope}"
        return f"settings:{domain.value}:all:{scope}"

    @staticmethod
    def _require_organization(organization_id: uuid.UUID | str | None) -> uuid.UUID:
        """
        Normalize a caller-supplied organization to a UUID, refusing ``None``.

        A tenant-scoped read with no organization used to silently resolve
        whatever row the database returned first and cache it under a global
        key, serving one tenant's setting to another. Callers that genuinely
        want the platform-wide row must say so by calling the ``*_global_*``
        method instead.
        """
        if organization_id is None:
            raise ValueError(
                "settings_cache requires an explicit organization_id. Use the "
                "get_global_* helpers to read the platform-wide setting row."
            )
        if isinstance(organization_id, uuid.UUID):
            return organization_id
        return uuid.UUID(str(organization_id))

    def _cache_get(self, cache_key: str) -> Any | None:
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
        """
        Turn a settings row into the value a caller sees.

        Extraction and interpretation are both delegated to
        ``app.services.settings_spec``, which owns them for the uncached read
        path (``resolve_value``). Keeping a second copy here meant the two paths
        disagreed twice over: this one preferred ``value_json`` where
        ``extract_db_value`` prefers ``value_text`` (so a row carrying both
        resolved differently depending on who read it), and it applied none of
        the spec's ``allowed`` / ``min_value`` / ``max_value`` rules or its
        default fallback (so a cached read could return an out-of-range or
        wrongly-typed value that ``resolve_value`` would have rejected).

        Keys with no registered spec — the cache is not limited to specced
        settings — still get the row's own ``value_type`` applied, through the
        same coercion function rather than a hand-rolled repeat of it.

        The import is local because ``settings_spec`` reaches this module via
        ``domain_settings``; this is the repo's approved lazy-import pattern for
        that cycle.
        """
        from app.services.settings_spec import (
            coerce_by_value_type,
            coerce_resolved_value,
            extract_db_value,
            get_spec,
        )

        raw = extract_db_value(setting)

        spec = get_spec(setting.domain, setting.key)
        if spec is not None:
            return coerce_resolved_value(spec, raw)

        value, error = coerce_by_value_type(setting.value_type, raw)
        # An unspecced row that cannot be coerced keeps its stored form, which
        # is what this path did before — there is no spec default to fall back
        # to, and caching None would read back as a cache miss every time.
        return raw if error else value

    def _load_setting_row(
        self,
        db: Session,
        domain: SettingDomain,
        key: str,
        organization_id: uuid.UUID | None,
    ) -> DomainSetting | None:
        """
        Load the winning ``DomainSetting`` row for one scope.

        Mirrors ``DomainSettings.get_by_key``: for a tenant scope the
        organization's own row outranks the platform-wide row, ties broken by
        ``updated_at`` descending. For the platform scope only rows with a NULL
        ``organization_id`` are eligible.

        ``allow_cross_org`` is required here for the same reason it is in
        ``get_by_key``: the ORM org-filter listener would otherwise drop the
        NULL-org platform rows (and raise on an unprimed session), so the
        explicit predicates below are the scoping authority.
        """
        stmt = select(DomainSetting).where(
            DomainSetting.domain == domain,
            DomainSetting.key == key,
            DomainSetting.is_active.is_(True),
        )
        if organization_id is None:
            stmt = stmt.where(DomainSetting.organization_id.is_(None)).order_by(
                DomainSetting.updated_at.desc()
            )
        else:
            stmt = stmt.where(
                or_(
                    DomainSetting.organization_id == organization_id,
                    DomainSetting.organization_id.is_(None),
                )
            ).order_by(
                case((DomainSetting.organization_id == organization_id, 0), else_=1),
                DomainSetting.updated_at.desc(),
            )
        with allow_cross_org(db):
            return db.scalar(stmt.limit(1))

    def _load_domain_rows(
        self,
        db: Session,
        domain: SettingDomain,
        organization_id: uuid.UUID | None,
    ) -> dict[str, Any]:
        """Load every effective setting for one scope, org rows overriding global."""
        stmt = select(DomainSetting).where(
            DomainSetting.domain == domain,
            DomainSetting.is_active.is_(True),
        )
        if organization_id is None:
            stmt = stmt.where(DomainSetting.organization_id.is_(None))
        else:
            stmt = stmt.where(
                or_(
                    DomainSetting.organization_id == organization_id,
                    DomainSetting.organization_id.is_(None),
                )
            )
        # Global rows first so an org-specific row overwrites them; within a
        # scope the most recently updated row wins (same tie-break as
        # ``_load_setting_row``, expressed as last-write-wins here).
        stmt = stmt.order_by(
            case((DomainSetting.organization_id.is_(None), 0), else_=1),
            DomainSetting.updated_at.asc(),
        )
        with allow_cross_org(db):
            rows = list(db.scalars(stmt).all())

        result: dict[str, Any] = {}
        for setting in rows:
            result[setting.key] = self._extract_value(setting)
        return result

    def _get_setting_value_for_scope(
        self,
        db: Session,
        domain: SettingDomain,
        key: str,
        organization_id: uuid.UUID | None,
        default: Any,
    ) -> Any:
        from app.services.setting_scopes import is_platform_owned

        if is_platform_owned(domain, key):
            # Platform-owned: one scope, therefore one cache entry. Collapsing
            # the scope here also collapses the CACHE KEY below — otherwise N
            # organizations would each cache the same platform value under
            # their own key, and invalidating a platform write would have to
            # sweep all N. See `app/services/setting_scopes.py`.
            organization_id = None

        cache_key = self._make_key(domain, organization_id, key)

        # Check cache
        cached = self._cache_get(cache_key)
        if cached is not None:
            # Handle cached "not found" sentinel
            if cached == "__NOT_FOUND__":
                return default
            return cached

        setting = self._load_setting_row(db, domain, key, organization_id)

        ttl = self._get_ttl(domain)

        if not setting:
            # Cache the "not found" to avoid repeated DB queries
            self._cache_set(cache_key, "__NOT_FOUND__", ttl)
            return default

        value = self._extract_value(setting)
        self._cache_set(cache_key, value, ttl)
        return value

    def get_setting_value(
        self,
        db: Session,
        domain: SettingDomain,
        key: str,
        default: Any = None,
        *,
        organization_id: uuid.UUID | str,
    ) -> Any:
        """
        Get a single setting value for one organization, with caching.

        Resolution: the organization's own row, else the platform-wide row,
        else ``default``.

        Args:
            db: Database session
            domain: Setting domain
            key: Setting key
            default: Default value if neither an org nor a platform row exists
            organization_id: The organization to resolve for (required)

        Returns:
            Setting value or default

        Raises:
            ValueError: if ``organization_id`` is None
        """
        org_id = self._require_organization(organization_id)
        return self._get_setting_value_for_scope(db, domain, key, org_id, default)

    def get_global_setting_value(
        self,
        db: Session,
        domain: SettingDomain,
        key: str,
        default: Any = None,
    ) -> Any:
        """
        Get the PLATFORM-WIDE setting value (``organization_id IS NULL``).

        Deliberately separate from :meth:`get_setting_value` so that reading
        the unscoped row is always an explicit decision at the call site, never
        the accidental result of a missing argument.
        """
        return self._get_setting_value_for_scope(db, domain, key, None, default)

    def get_domain_settings(
        self,
        db: Session,
        domain: SettingDomain,
        *,
        organization_id: uuid.UUID | str,
    ) -> dict[str, Any]:
        """
        Get all effective settings for a domain, for one organization.

        Org-specific rows override the platform-wide rows of the same key.

        Args:
            db: Database session
            domain: Setting domain
            organization_id: The organization to resolve for (required)

        Returns:
            Dict mapping setting keys to values

        Raises:
            ValueError: if ``organization_id`` is None
        """
        org_id = self._require_organization(organization_id)
        return self._get_domain_settings_for_scope(db, domain, org_id)

    def get_global_domain_settings(
        self,
        db: Session,
        domain: SettingDomain,
    ) -> dict[str, Any]:
        """Get all PLATFORM-WIDE settings for a domain (``organization_id IS NULL``)."""
        return self._get_domain_settings_for_scope(db, domain, None)

    def _get_domain_settings_for_scope(
        self,
        db: Session,
        domain: SettingDomain,
        organization_id: uuid.UUID | None,
    ) -> dict[str, Any]:
        cache_key = self._make_key(domain, organization_id)

        # Check cache
        cached = self._cache_get(cache_key)
        if isinstance(cached, dict):
            return cast(dict[str, Any], cached)

        result = self._load_domain_rows(db, domain, organization_id)

        ttl = self._get_ttl(domain)
        self._cache_set(cache_key, result, ttl)

        return result

    def _delete(self, cache_key: str) -> None:
        self._inmemory.delete(cache_key)
        if self._redis.is_available:
            self._redis.delete(cache_key)

    def _delete_pattern(self, pattern: str) -> None:
        self._inmemory.delete_pattern(pattern)
        if self._redis.is_available:
            self._redis.delete_pattern(pattern)

    def invalidate_setting(
        self,
        domain: SettingDomain,
        key: str,
        *,
        organization_id: uuid.UUID | str | None,
    ) -> None:
        """
        Invalidate cache for a specific setting, scoped to the row that changed.

        Args:
            domain: Setting domain
            key: Setting key
            organization_id: The organization whose row changed, or ``None``
                when the PLATFORM-WIDE row changed. A tenant write drops only
                that tenant's entries — another tenant's cached value is left
                intact. A platform write drops every tenant's entry for the
                key, because every tenant may have cached it as a fallback.
        """
        if organization_id is None:
            # The platform row changed: every tenant's cached value for this
            # key may have been resolved from it.
            self._delete_pattern(f"settings:{domain.value}:k={key}:*")
            self._delete_pattern(f"settings:{domain.value}:all:*")
            logger.debug(
                "Settings cache invalidated (all scopes): %s/%s", domain.value, key
            )
            return

        org_id = self._require_organization(organization_id)
        self._delete(self._make_key(domain, org_id, key))
        # Also invalidate that org's bulk domain cache
        self._delete(self._make_key(domain, org_id))

        logger.debug(
            "Settings cache invalidated: %s/%s (org=%s)", domain.value, key, org_id
        )

    def invalidate_domain(self, domain: SettingDomain) -> None:
        """
        Invalidate all cached settings for a domain, across every scope.

        Args:
            domain: Setting domain
        """
        pattern = f"settings:{domain.value}:*"
        self._delete_pattern(pattern)

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
    *,
    organization_id: uuid.UUID | str,
) -> Any:
    """
    Convenience function to get a cached setting value for one organization.

    Args:
        db: Database session
        domain: Setting domain
        key: Setting key
        default: Default value if not found
        organization_id: The organization to resolve for (required)

    Returns:
        Setting value or default
    """
    return settings_cache.get_setting_value(
        db, domain, key, default, organization_id=organization_id
    )


def get_setting_value(
    db: Session,
    domain: SettingDomain,
    key: str,
    default: Any = None,
    *,
    organization_id: uuid.UUID | str,
) -> Any:
    """Backward-compatible alias for get_cached_setting."""
    return settings_cache.get_setting_value(
        db, domain, key, default, organization_id=organization_id
    )


def get_global_cached_setting(
    db: Session,
    domain: SettingDomain,
    key: str,
    default: Any = None,
) -> Any:
    """
    Convenience function to read the PLATFORM-WIDE setting row.

    Use only where there genuinely is no tenant — e.g. process-wide bootstrap.
    """
    return settings_cache.get_global_setting_value(db, domain, key, default)


def invalidate_setting_cache(
    domain: SettingDomain,
    key: str | None = None,
    *,
    organization_id: uuid.UUID | str | None,
) -> None:
    """
    Convenience function to invalidate settings cache.

    Args:
        domain: Setting domain
        key: Setting key (if None, invalidates entire domain, all scopes)
        organization_id: Organization whose row changed, or ``None`` for the
            platform-wide row (see :meth:`SettingsCache.invalidate_setting`)
    """
    if key:
        settings_cache.invalidate_setting(domain, key, organization_id=organization_id)
    else:
        settings_cache.invalidate_domain(domain)
