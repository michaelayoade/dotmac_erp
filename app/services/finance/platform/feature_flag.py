"""
FeatureFlagService - Feature flag management.

Enables/disables features per organization for gradual rollout
and A/B testing capabilities.
"""

import logging
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.finance.core_config.system_configuration import (
    ConfigType,
    SystemConfiguration,
)
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)

# Feature flag prefix for identification in system_configuration
FEATURE_FLAG_PREFIX = "feature."


class FeatureFlagService(ListResponseMixin):
    """
    Service for managing feature flags.

    Uses system_configuration table with 'feature.' prefix for keys.
    Supports org-specific and system-wide (org_id=NULL) flags.
    """

    @staticmethod
    def is_enabled(
        db: Session,
        organization_id: UUID,
        feature_code: str,
    ) -> bool:
        """
        Check if a feature is enabled for an organization.

        Checks org-specific flag first, falls back to system default.

        Args:
            db: Database session
            organization_id: Organization scope
            feature_code: Feature code (e.g., "AR_IFRS15_CONTRACTS")

        Returns:
            True if enabled, False otherwise
        """
        org_id = coerce_uuid(organization_id)
        config_key = f"{FEATURE_FLAG_PREFIX}{feature_code}"

        # Check org-specific flag first
        try:
            org_flag = (
                db.query(SystemConfiguration)
                .filter(SystemConfiguration.organization_id == org_id)
                .filter(SystemConfiguration.config_key == config_key)
                .first()
            )
        except Exception:
            org_flag = db.scalar(
                select(SystemConfiguration).where(
                    SystemConfiguration.organization_id == org_id,
                    SystemConfiguration.config_key == config_key,
                )
            )

        if org_flag:
            return org_flag.config_value.lower() in ("true", "1", "yes", "on")

        # Fall back to system default (NULL org_id)
        try:
            system_flag = (
                db.query(SystemConfiguration)
                .filter(SystemConfiguration.organization_id.is_(None))
                .filter(SystemConfiguration.config_key == config_key)
                .first()
            )
        except Exception:
            system_flag = db.scalar(
                select(SystemConfiguration).where(
                    SystemConfiguration.organization_id.is_(None),
                    SystemConfiguration.config_key == config_key,
                )
            )

        if system_flag:
            return system_flag.config_value.lower() in ("true", "1", "yes", "on")

        # Default to disabled if not configured
        return False

    @staticmethod
    def get_features(
        db: Session,
        organization_id: UUID,
    ) -> dict[str, bool]:
        """
        Get all feature flags for an organization.

        Merges system defaults with org-specific overrides.

        Args:
            db: Database session
            organization_id: Organization scope

        Returns:
            Dict of feature_code -> enabled status
        """
        org_id = coerce_uuid(organization_id)

        # Get all feature flags (both system and org-specific)
        try:
            flags = (
                db.query(SystemConfiguration)
                .filter(SystemConfiguration.config_key.startswith(FEATURE_FLAG_PREFIX))
                .filter(
                    or_(
                        SystemConfiguration.organization_id.is_(None),
                        SystemConfiguration.organization_id == org_id,
                    )
                )
                .all()
            )
        except Exception:
            flags = list(
                db.scalars(
                    select(SystemConfiguration).where(
                        SystemConfiguration.config_key.startswith(FEATURE_FLAG_PREFIX),
                        or_(
                            SystemConfiguration.organization_id.is_(None),
                            SystemConfiguration.organization_id == org_id,
                        ),
                    )
                )
            )

        # Build result dict, with org-specific overriding system defaults
        result: dict[str, bool] = {}

        # First pass: system defaults
        for flag in flags:
            if flag.organization_id is None:
                feature_code = flag.config_key[len(FEATURE_FLAG_PREFIX) :]
                result[feature_code] = flag.config_value.lower() in (
                    "true",
                    "1",
                    "yes",
                    "on",
                )

        # Second pass: org-specific overrides
        for flag in flags:
            if flag.organization_id == org_id:
                feature_code = flag.config_key[len(FEATURE_FLAG_PREFIX) :]
                result[feature_code] = flag.config_value.lower() in (
                    "true",
                    "1",
                    "yes",
                    "on",
                )

        return result

    @staticmethod
    def set_feature(
        db: Session,
        organization_id: UUID,
        feature_code: str,
        enabled: bool,
        updated_by_user_id: UUID,
    ) -> SystemConfiguration:
        """
        Enable or disable a feature for an organization.

        Args:
            db: Database session
            organization_id: Organization scope
            feature_code: Feature code to set
            enabled: Whether feature is enabled
            updated_by_user_id: User making the change

        Returns:
            Updated SystemConfiguration record
        """
        org_id = coerce_uuid(organization_id)
        user_id = coerce_uuid(updated_by_user_id)
        config_key = f"{FEATURE_FLAG_PREFIX}{feature_code}"

        # Check if flag exists for this org
        try:
            existing = (
                db.query(SystemConfiguration)
                .filter(SystemConfiguration.organization_id == org_id)
                .filter(SystemConfiguration.config_key == config_key)
                .first()
            )
        except Exception:
            existing = db.scalar(
                select(SystemConfiguration).where(
                    SystemConfiguration.organization_id == org_id,
                    SystemConfiguration.config_key == config_key,
                )
            )

        if existing:
            existing.config_value = "true" if enabled else "false"
            existing.updated_by_user_id = user_id
            db.commit()
            db.refresh(existing)
            return existing

        # Create new flag
        config = SystemConfiguration(
            organization_id=org_id,
            config_key=config_key,
            config_value="true" if enabled else "false",
            config_type=ConfigType.BOOLEAN,
            description=f"Feature flag: {feature_code}",
            updated_by_user_id=user_id,
        )

        db.add(config)
        db.commit()
        db.refresh(config)
        return config

    @staticmethod
    def set_system_default(
        db: Session,
        feature_code: str,
        enabled: bool,
        updated_by_user_id: UUID,
    ) -> SystemConfiguration:
        """
        Set the system-wide default for a feature.

        Args:
            db: Database session
            feature_code: Feature code to set
            enabled: Whether feature is enabled by default
            updated_by_user_id: User making the change

        Returns:
            Created/updated SystemConfiguration record
        """
        user_id = coerce_uuid(updated_by_user_id)
        config_key = f"{FEATURE_FLAG_PREFIX}{feature_code}"

        # Check if system default exists
        try:
            existing = (
                db.query(SystemConfiguration)
                .filter(SystemConfiguration.organization_id.is_(None))
                .filter(SystemConfiguration.config_key == config_key)
                .first()
            )
        except Exception:
            existing = db.scalar(
                select(SystemConfiguration).where(
                    SystemConfiguration.organization_id.is_(None),
                    SystemConfiguration.config_key == config_key,
                )
            )

        if existing:
            existing.config_value = "true" if enabled else "false"
            existing.updated_by_user_id = user_id
            db.commit()
            db.refresh(existing)
            return existing

        # Create new system default
        config = SystemConfiguration(
            organization_id=None,
            config_key=config_key,
            config_value="true" if enabled else "false",
            config_type=ConfigType.BOOLEAN,
            description=f"Feature flag (system default): {feature_code}",
            updated_by_user_id=user_id,
        )

        db.add(config)
        db.commit()
        db.refresh(config)
        return config

    @staticmethod
    def require_feature(
        db: Session,
        organization_id: UUID,
        feature_code: str,
    ) -> None:
        """
        Raise exception if feature is not enabled.

        Use as a guard at the start of feature-gated endpoints.

        Args:
            db: Database session
            organization_id: Organization scope
            feature_code: Required feature code

        Raises:
            HTTPException(403): If feature is not enabled
        """
        if not FeatureFlagService.is_enabled(db, organization_id, feature_code):
            raise HTTPException(
                status_code=403,
                detail=f"Feature '{feature_code}' is not enabled for this organization",
            )

    @staticmethod
    def delete_feature(
        db: Session,
        organization_id: UUID | None,
        feature_code: str,
    ) -> bool:
        """
        Delete a feature flag.

        Args:
            db: Database session
            organization_id: Organization scope (None for system default)
            feature_code: Feature code to delete

        Returns:
            True if deleted, False if not found
        """
        config_key = f"{FEATURE_FLAG_PREFIX}{feature_code}"

        try:
            query = db.query(SystemConfiguration).filter(
                SystemConfiguration.config_key == config_key
            )
            if organization_id:
                query = query.filter(
                    SystemConfiguration.organization_id == coerce_uuid(organization_id)
                )
            else:
                query = query.filter(SystemConfiguration.organization_id.is_(None))
            existing = query.first()
        except Exception:
            stmt = select(SystemConfiguration).where(
                SystemConfiguration.config_key == config_key
            )

            if organization_id:
                stmt = stmt.where(
                    SystemConfiguration.organization_id == coerce_uuid(organization_id)
                )
            else:
                stmt = stmt.where(SystemConfiguration.organization_id.is_(None))

            existing = db.scalar(stmt)

        if existing:
            db.delete(existing)
            db.commit()
            return True

        return False

    @staticmethod
    def list_all_flags(
        db: Session,
        organization_id: str | None = None,
        include_system_defaults: bool = True,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SystemConfiguration]:
        """
        List all feature flags.

        Args:
            db: Database session
            organization_id: Filter by organization
            include_system_defaults: Include system-wide defaults
            limit: Maximum results
            offset: Pagination offset

        Returns:
            List of SystemConfiguration records for feature flags
        """
        try:
            query = db.query(SystemConfiguration).filter(
                SystemConfiguration.config_key.startswith(FEATURE_FLAG_PREFIX)
            )

            if organization_id:
                org_id = coerce_uuid(organization_id)
                if include_system_defaults:
                    query = query.filter(
                        or_(
                            SystemConfiguration.organization_id == org_id,
                            SystemConfiguration.organization_id.is_(None),
                        )
                    )
                else:
                    query = query.filter(SystemConfiguration.organization_id == org_id)
            elif not include_system_defaults:
                query = query.filter(SystemConfiguration.organization_id.isnot(None))

            rows = (
                query.order_by(SystemConfiguration.config_key)
                .limit(limit)
                .offset(offset)
                .all()
            )
            return list(rows)
        except Exception:
            stmt = select(SystemConfiguration).where(
                SystemConfiguration.config_key.startswith(FEATURE_FLAG_PREFIX)
            )

            if organization_id:
                org_id = coerce_uuid(organization_id)
                if include_system_defaults:
                    stmt = stmt.where(
                        or_(
                            SystemConfiguration.organization_id == org_id,
                            SystemConfiguration.organization_id.is_(None),
                        )
                    )
                else:
                    stmt = stmt.where(SystemConfiguration.organization_id == org_id)
            elif not include_system_defaults:
                stmt = stmt.where(SystemConfiguration.organization_id.isnot(None))

            stmt = (
                stmt.order_by(SystemConfiguration.config_key)
                .limit(limit)
                .offset(offset)
            )
            return list(db.scalars(stmt))

    @staticmethod
    def list(
        db: Session,
        organization_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SystemConfiguration]:
        """
        List feature flags (for ListResponseMixin compatibility).

        Args:
            db: Database session
            organization_id: Filter by organization
            limit: Maximum results
            offset: Pagination offset

        Returns:
            List of SystemConfiguration records
        """
        return FeatureFlagService.list_all_flags(
            db,
            organization_id,
            include_system_defaults=True,
            limit=limit,
            offset=offset,
        )


# Module-level singleton instance
feature_flag_service = FeatureFlagService()
