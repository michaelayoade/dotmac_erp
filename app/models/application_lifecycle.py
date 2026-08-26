"""Durable ERP-local application lifecycle execution evidence."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

try:
    from datetime import UTC  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover
    UTC = timezone.utc

from app.db import Base


class ApplicationLifecycleOperation(Base):
    """One exact product-local plan and its eventual transition evidence."""

    __tablename__ = "application_lifecycle_operations"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "idempotency_key",
            name="uq_app_lifecycle_org_idempotency_key",
        ),
        CheckConstraint(
            "desired_state IN ('active', 'inactive')",
            name="ck_app_lifecycle_desired_state",
        ),
        CheckConstraint(
            "current_state IN ('active', 'inactive')",
            name="ck_app_lifecycle_current_state",
        ),
        CheckConstraint(
            "operation_state IN ('planned', 'applied', 'cancelled')",
            name="ck_app_lifecycle_operation_state",
        ),
        CheckConstraint(
            "outcome IN ('blocked', 'refused', 'succeeded')",
            name="ck_app_lifecycle_outcome",
        ),
        ForeignKeyConstraint(
            ["organization_id", "person_id"],
            ["people.organization_id", "people.id"],
            name="fk_app_lifecycle_person_org",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_app_lifecycle_org_person_created",
            "organization_id",
            "person_id",
            "created_at",
        ),
    )

    operation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "core_org.organization.organization_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    desired_state: Mapped[str] = mapped_column(String(16), nullable=False)
    provider_binding: Mapped[str] = mapped_column(String(80), nullable=False)
    issuer: Mapped[str] = mapped_column(String(512), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    target: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    target_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    expected_state: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    expected_state_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    plan_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    current_state: Mapped[str] = mapped_column(String(16), nullable=False)
    actions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    operation_state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="planned"
    )
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    failure_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    changed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    result_state: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    result_state_digest: Mapped[str | None] = mapped_column(String(71), nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


__all__ = ["ApplicationLifecycleOperation"]
