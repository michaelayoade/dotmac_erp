"""
Unified Feature Flag Service.

Dynamic, per-org feature flags with registry metadata, caching, and audit.

Resolution order:
1. Org-specific domain_settings row → cached
2. Global domain_settings row (org_id=NULL) → cached
3. FeatureFlagRegistry.default_enabled → cached
4. False (unknown flag)

Replaces:
- app/services/feature_flags.py (hardcoded constants, no per-org)
- app/services/finance/platform/feature_flag.py (uncached, legacy db.query)
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import Depends, HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.domain_settings import (
    DomainSetting,
    SettingDomain,
    SettingScope,
    SettingValueType,
)
from app.models.feature_flag import (
    FeatureFlagCategory,
    FeatureFlagRegistry,
    FeatureFlagStatus,
)
from app.services.settings_cache import settings_cache

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cache helpers — org-scoped keys
# ---------------------------------------------------------------------------

_FEATURES_DOMAIN = SettingDomain.features
_CACHE_TTL = 60  # seconds — matches DOMAIN_TTL_CONFIG[features]
_REGISTRY_CACHE_TTL = 300  # registry metadata changes rarely


def _org_cache_key(org_id: UUID | None, flag_key: str) -> str:
    """Build an org-scoped cache key for a feature flag value."""
    org_part = str(org_id) if org_id else "global"
    return f"ff:{org_part}:{flag_key}"


def _registry_cache_key() -> str:
    return "ff:registry:all"


def _cache_get(key: str) -> Any | None:
    """Read from the settings cache's underlying stores."""
    # Use the in-memory cache directly for feature flag keys
    # (they use a different prefix from domain settings)
    from app.services.settings_cache import _inmemory_cache

    value = _inmemory_cache.get(key)
    if value is not None:
        return value

    from app.services.cache import cache_service

    if cache_service.is_available:
        return cache_service.get(key)
    return None


def _cache_set(key: str, value: Any, ttl: int) -> None:
    from app.services.cache import cache_service
    from app.services.settings_cache import _inmemory_cache

    _inmemory_cache.set(key, value, ttl)
    if cache_service.is_available:
        cache_service.set(key, value, ttl)


def _cache_delete(key: str) -> None:
    from app.services.cache import cache_service
    from app.services.settings_cache import _inmemory_cache

    _inmemory_cache.delete(key)
    if cache_service.is_available:
        cache_service.delete(key)


def _cache_delete_pattern(pattern: str) -> None:
    from app.services.cache import cache_service
    from app.services.settings_cache import _inmemory_cache

    _inmemory_cache.delete_pattern(pattern)
    if cache_service.is_available:
        cache_service.delete_pattern(pattern)


# ---------------------------------------------------------------------------
# Data classes for structured returns
# ---------------------------------------------------------------------------


@dataclass
class FeatureFlagView:
    """A feature flag with resolved enabled state for display."""

    flag_key: str
    label: str
    description: str
    category: FeatureFlagCategory
    status: FeatureFlagStatus
    default_enabled: bool
    enabled: bool
    is_org_override: bool
    owner: str | None
    expires_at: Any  # datetime | None
    sort_order: int


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class FeatureFlagService:
    """
    Unified feature flag service.

    All flag definitions live in ``feature_flag_registry``.
    All flag values live in ``domain_settings`` (domain='features').
    """

    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def is_enabled(self, org_id: UUID, flag_key: str) -> bool:
        """
        Check if a feature is enabled for an organization.

        Resolution: org override → global override → registry default → False.
        """
        cache_key = _org_cache_key(org_id, flag_key)
        cached = _cache_get(cache_key)
        if cached is not None:
            if cached == "__FF_TRUE__":
                return True
            if cached == "__FF_FALSE__":
                return False

        enabled = self._resolve_flag(org_id, flag_key)
        _cache_set(cache_key, "__FF_TRUE__" if enabled else "__FF_FALSE__", _CACHE_TTL)
        return enabled

    def _resolve_flag(self, org_id: UUID, flag_key: str) -> bool:
        """Resolve a flag value from DB without cache."""
        # 1. Org-specific override
        org_setting = self.db.scalar(
            select(DomainSetting).where(
                DomainSetting.domain == _FEATURES_DOMAIN,
                DomainSetting.key == flag_key,
                DomainSetting.organization_id == org_id,
                DomainSetting.is_active.is_(True),
            )
        )
        if org_setting is not None:
            return _setting_to_bool(org_setting)

        # 2. Global setting (org_id IS NULL)
        global_setting = self.db.scalar(
            select(DomainSetting).where(
                DomainSetting.domain == _FEATURES_DOMAIN,
                DomainSetting.key == flag_key,
                DomainSetting.organization_id.is_(None),
                DomainSetting.is_active.is_(True),
            )
        )
        if global_setting is not None:
            return _setting_to_bool(global_setting)

        # 3. Registry default
        registry = self.db.scalar(
            select(FeatureFlagRegistry).where(
                FeatureFlagRegistry.flag_key == flag_key,
                FeatureFlagRegistry.status != FeatureFlagStatus.ARCHIVED,
            )
        )
        if registry is not None:
            return registry.default_enabled

        # 4. Unknown flag
        return False

    def get_all_flags(
        self,
        org_id: UUID,
        *,
        include_archived: bool = False,
    ) -> list[FeatureFlagView]:
        """
        Get all flags with resolved state for an organization.

        Returns a list of FeatureFlagView sorted by category then sort_order.
        """
        # Load registry
        stmt = select(FeatureFlagRegistry)
        if not include_archived:
            stmt = stmt.where(
                FeatureFlagRegistry.status != FeatureFlagStatus.ARCHIVED
            )
        stmt = stmt.order_by(
            FeatureFlagRegistry.category,
            FeatureFlagRegistry.sort_order,
            FeatureFlagRegistry.label,
        )
        flags = list(self.db.scalars(stmt).all())

        if not flags:
            return []

        # Batch-load org-specific and global settings for efficiency
        flag_keys = [f.flag_key for f in flags]
        org_settings = self._load_settings(org_id, flag_keys)
        global_settings = self._load_settings(None, flag_keys)

        result = []
        for flag in flags:
            org_val = org_settings.get(flag.flag_key)
            global_val = global_settings.get(flag.flag_key)

            if org_val is not None:
                enabled = _setting_to_bool(org_val)
                is_org_override = True
            elif global_val is not None:
                enabled = _setting_to_bool(global_val)
                is_org_override = False
            else:
                enabled = flag.default_enabled
                is_org_override = False

            result.append(
                FeatureFlagView(
                    flag_key=flag.flag_key,
                    label=flag.label,
                    description=flag.description,
                    category=flag.category,
                    status=flag.status,
                    default_enabled=flag.default_enabled,
                    enabled=enabled,
                    is_org_override=is_org_override,
                    owner=flag.owner,
                    expires_at=flag.expires_at,
                    sort_order=flag.sort_order,
                )
            )
        return result

    def _load_settings(
        self, org_id: UUID | None, keys: list[str]
    ) -> dict[str, DomainSetting]:
        """Batch-load domain_settings rows for a set of keys."""
        stmt = select(DomainSetting).where(
            DomainSetting.domain == _FEATURES_DOMAIN,
            DomainSetting.key.in_(keys),
            DomainSetting.is_active.is_(True),
        )
        if org_id is not None:
            stmt = stmt.where(DomainSetting.organization_id == org_id)
        else:
            stmt = stmt.where(DomainSetting.organization_id.is_(None))

        return {s.key: s for s in self.db.scalars(stmt).all()}

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def toggle(
        self,
        org_id: UUID,
        flag_key: str,
        enabled: bool,
        *,
        changed_by_id: UUID | None = None,
        scope: str = "org",
    ) -> None:
        """
        Enable or disable a flag for an org (or globally).

        Args:
            org_id: Organization (used for org-scoped toggles)
            flag_key: The flag key
            enabled: New value
            changed_by_id: Audit trail
            scope: "org" for org-specific, "global" for system-wide default
        """
        # Validate flag exists and is not archived
        registry = self.db.scalar(
            select(FeatureFlagRegistry).where(
                FeatureFlagRegistry.flag_key == flag_key,
            )
        )
        if not registry:
            raise ValueError(f"Unknown feature flag: {flag_key}")
        if registry.status == FeatureFlagStatus.ARCHIVED:
            raise ValueError(f"Cannot toggle archived flag: {flag_key}")

        target_org_id = org_id if scope == "org" else None
        target_scope = SettingScope.ORG_SPECIFIC if scope == "org" else SettingScope.GLOBAL

        # Upsert domain_settings
        existing = self.db.scalar(
            select(DomainSetting).where(
                DomainSetting.domain == _FEATURES_DOMAIN,
                DomainSetting.key == flag_key,
                DomainSetting.organization_id == target_org_id
                if target_org_id
                else DomainSetting.organization_id.is_(None),
            )
        )

        if existing:
            existing.value_text = "true" if enabled else "false"
            existing.is_active = True
        else:
            setting = DomainSetting(
                domain=_FEATURES_DOMAIN,
                key=flag_key,
                organization_id=target_org_id,
                scope=target_scope,
                value_type=SettingValueType.boolean,
                value_text="true" if enabled else "false",
                is_active=True,
            )
            self.db.add(setting)

        self.db.flush()

        # Invalidate caches
        self._invalidate_flag_cache(org_id, flag_key)

        label = registry.label
        state = "enabled" if enabled else "disabled"
        logger.info(
            "Feature flag '%s' (%s) %s for org=%s by user=%s",
            flag_key,
            label,
            state,
            target_org_id or "GLOBAL",
            changed_by_id,
        )

    def register_flag(
        self,
        flag_key: str,
        label: str,
        description: str = "",
        category: FeatureFlagCategory = FeatureFlagCategory.MODULE,
        default_enabled: bool = False,
        owner: str | None = None,
        sort_order: int = 0,
        *,
        created_by_id: UUID | None = None,
    ) -> FeatureFlagRegistry:
        """
        Register a new feature flag (or update existing).

        This creates/updates the registry entry. It does NOT toggle
        the flag for any org — that's done via ``toggle()``.
        """
        existing = self.db.scalar(
            select(FeatureFlagRegistry).where(
                FeatureFlagRegistry.flag_key == flag_key,
            )
        )

        if existing:
            existing.label = label
            existing.description = description
            existing.category = category
            existing.default_enabled = default_enabled
            existing.owner = owner
            existing.sort_order = sort_order
            if existing.status == FeatureFlagStatus.ARCHIVED:
                existing.status = FeatureFlagStatus.ACTIVE
            self.db.flush()
            logger.info("Updated feature flag registry: %s", flag_key)
            return existing

        flag = FeatureFlagRegistry(
            flag_key=flag_key,
            label=label,
            description=description,
            category=category,
            default_enabled=default_enabled,
            owner=owner,
            sort_order=sort_order,
            created_by_id=created_by_id,
        )
        self.db.add(flag)
        self.db.flush()
        logger.info("Registered new feature flag: %s", flag_key)
        return flag

    def update_metadata(
        self,
        flag_key: str,
        **fields: Any,
    ) -> FeatureFlagRegistry:
        """
        Update registry metadata for a flag.

        Allowed fields: label, description, category, status, owner,
        expires_at, sort_order, default_enabled.
        """
        flag = self.db.scalar(
            select(FeatureFlagRegistry).where(
                FeatureFlagRegistry.flag_key == flag_key,
            )
        )
        if not flag:
            raise ValueError(f"Unknown feature flag: {flag_key}")

        allowed = {
            "label",
            "description",
            "category",
            "status",
            "owner",
            "expires_at",
            "sort_order",
            "default_enabled",
        }
        for key, value in fields.items():
            if key not in allowed:
                raise ValueError(f"Cannot update field: {key}")
            setattr(flag, key, value)

        self.db.flush()
        _cache_delete(_registry_cache_key())
        return flag

    def archive_flag(self, flag_key: str) -> None:
        """Archive a flag. Archived flags always resolve to False."""
        self.db.execute(
            update(FeatureFlagRegistry)
            .where(FeatureFlagRegistry.flag_key == flag_key)
            .values(status=FeatureFlagStatus.ARCHIVED)
        )
        self.db.flush()
        # Invalidate all caches for this flag
        _cache_delete_pattern(f"ff:*:{flag_key}")
        _cache_delete(_registry_cache_key())
        logger.info("Archived feature flag: %s", flag_key)

    def get_expired_flags(self) -> list[FeatureFlagRegistry]:
        """Get active flags past their expiry date."""
        from datetime import UTC, datetime

        now = datetime.now(UTC)
        stmt = select(FeatureFlagRegistry).where(
            FeatureFlagRegistry.status == FeatureFlagStatus.ACTIVE,
            FeatureFlagRegistry.expires_at.isnot(None),
            FeatureFlagRegistry.expires_at < now,
        )
        return list(self.db.scalars(stmt).all())

    def get_registry_entry(self, flag_key: str) -> FeatureFlagRegistry | None:
        """Get a single registry entry by key."""
        return self.db.scalar(
            select(FeatureFlagRegistry).where(
                FeatureFlagRegistry.flag_key == flag_key,
            )
        )

    # ------------------------------------------------------------------
    # Cache
    # ------------------------------------------------------------------

    def _invalidate_flag_cache(self, org_id: UUID, flag_key: str) -> None:
        """Invalidate all cached entries for a flag."""
        _cache_delete(_org_cache_key(org_id, flag_key))
        _cache_delete(_org_cache_key(None, flag_key))  # global key too
        # Also invalidate the domain settings cache for this key
        settings_cache.invalidate_setting(_FEATURES_DOMAIN, flag_key)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setting_to_bool(setting: DomainSetting) -> bool:
    """Convert a domain_settings row to a boolean."""
    if setting.value_text is not None:
        return setting.value_text.lower() in ("true", "1", "yes", "on")
    return False


# ---------------------------------------------------------------------------
# Module-level convenience functions (backward-compatible API)
# ---------------------------------------------------------------------------


def is_feature_enabled(db: Session, org_id: UUID, flag_key: str) -> bool:
    """Check if a feature is enabled for an organization."""
    return FeatureFlagService(db).is_enabled(org_id, flag_key)


def require_feature(feature_key: str) -> Callable:
    """
    FastAPI dependency factory that requires a feature to be enabled.

    Injects ``require_tenant_auth`` as a sub-dependency to get org_id,
    and opens a short-lived DB session for the check.

    Usage::

        @router.get("/items", dependencies=[Depends(require_feature("enable_inventory"))])
        def list_items(...):
            ...
    """
    from app.services.auth_dependencies import require_tenant_auth

    def dependency(auth: dict = Depends(require_tenant_auth)) -> None:
        from app.db import SessionLocal
        from app.services.common import coerce_uuid

        org_id = coerce_uuid(auth["organization_id"])
        db = SessionLocal()
        try:
            if not FeatureFlagService(db).is_enabled(org_id, feature_key):
                label = feature_key.replace("_", " ").replace("enable ", "").title()
                raise HTTPException(
                    status_code=403,
                    detail=f"The '{label}' feature is not enabled. "
                    f"Enable it in Settings > Feature Flags.",
                )
        finally:
            db.close()

    return dependency


def require_feature_web(feature_key: str, db: Session, org_id: UUID) -> None:
    """
    Guard for web routes. Raises HTTPException(403) if disabled.

    Usage::

        require_feature_web("enable_inventory", db, auth.organization_id)
    """
    if not FeatureFlagService(db).is_enabled(org_id, feature_key):
        label = feature_key.replace("_", " ").replace("enable ", "").title()
        raise HTTPException(
            status_code=403,
            detail=f"The '{label}' feature is not enabled. "
            f"Enable it in Settings > Feature Flags.",
        )
