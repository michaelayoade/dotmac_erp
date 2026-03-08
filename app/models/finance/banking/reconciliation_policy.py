from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as SAUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ReconciliationPolicyProfile(Base):
    """Org-scoped reconciliation policy profile.

    Stores the generic reconciliation engine configuration for an organization.
    The engine remains fixed in code; organization behavior varies through this
    structured profile instead of hard-coded vendor branches.
    """

    __tablename__ = "reconciliation_policy_profile"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "name",
            name="uq_reconciliation_policy_profile_org_name",
        ),
        Index(
            "ix_reconciliation_policy_profile_org_active",
            "organization_id",
            "is_active",
        ),
        {"schema": "banking"},
    )

    policy_id: Mapped[UUID] = mapped_column(
        SAUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[UUID] = mapped_column(
        SAUUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False, default="default")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    enabled_provider_keys: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )
    enabled_strategy_keys: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )
    decision_thresholds: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    keyword_config: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    gl_mapping_config: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )

    amount_tolerance_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    date_buffer_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    settlement_window_days: Mapped[int | None] = mapped_column(Integer, nullable=True)

    journal_creation_strategy_keys: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )
    auto_post_strategy_keys: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
