"""
Feature Flag Registry Model.

Stores metadata about feature flags (what exists, descriptions, lifecycle).
Actual enabled/disabled values per org are stored in domain_settings.
"""

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class FeatureFlagStatus(str, enum.Enum):
    """Lifecycle status of a feature flag."""

    ACTIVE = "ACTIVE"
    DEPRECATED = "DEPRECATED"
    ARCHIVED = "ARCHIVED"


class FeatureFlagCategory(str, enum.Enum):
    """Category for grouping feature flags in the admin UI."""

    MODULE = "MODULE"
    FINANCE = "FINANCE"
    COMPLIANCE = "COMPLIANCE"
    INTEGRATION = "INTEGRATION"
    EXPERIMENTAL = "EXPERIMENTAL"


class FeatureFlagRegistry(Base):
    """
    Registry of all feature flags in the system.

    This table defines WHAT flags exist with their metadata. The actual
    per-org enabled/disabled values are stored in ``domain_settings``
    (domain='features'), which is already cached and audited.

    Resolution order for ``is_enabled(org_id, flag_key)``:
    1. Org-specific ``domain_settings`` row (org_id = target org)
    2. Global ``domain_settings`` row (org_id = NULL)
    3. ``default_enabled`` from this registry
    4. ``False`` if flag not in registry
    """

    __tablename__ = "feature_flag_registry"
    __table_args__ = (
        UniqueConstraint("flag_key", name="uq_feature_flag_registry_key"),
        Index("ix_feature_flag_registry_category", "category"),
        Index("ix_feature_flag_registry_status", "status"),
    )

    flag_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    flag_key: Mapped[str] = mapped_column(
        String(120), nullable=False, comment="Unique key, e.g. 'enable_inventory'"
    )
    label: Mapped[str] = mapped_column(
        String(200), nullable=False, comment="Human-readable name"
    )
    description: Mapped[str] = mapped_column(
        Text, nullable=False, default="", comment="What this flag controls"
    )
    category: Mapped[FeatureFlagCategory] = mapped_column(
        Enum(FeatureFlagCategory), nullable=False, default=FeatureFlagCategory.MODULE
    )
    status: Mapped[FeatureFlagStatus] = mapped_column(
        Enum(FeatureFlagStatus), nullable=False, default=FeatureFlagStatus.ACTIVE
    )
    default_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, comment="Default when no DB override"
    )
    owner: Mapped[str | None] = mapped_column(
        String(120), nullable=True, comment="Team or person responsible"
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="Optional sunset date"
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="Display order within category"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("people.id", ondelete="SET NULL"),
        nullable=True,
    )
