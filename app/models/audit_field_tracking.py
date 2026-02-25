"""
Field-Level Change Tracking Model.

User-facing change history with human-readable labels, display values
for FK fields, and selective tracking. Complements the forensic
audit.audit_log with a view-optimized table.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID as SAUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class FieldChangeLog(Base):
    """Individual field-level change record for user-facing audit history."""

    __tablename__ = "field_change_log"
    __table_args__ = (
        Index("ix_field_change_entity", "entity_type", "entity_id"),
        Index("ix_field_change_org_date", "organization_id", "changed_at"),
        {"schema": "audit"},
    )

    log_id: Mapped[UUID] = mapped_column(
        SAUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[UUID] = mapped_column(SAUUID(as_uuid=True), nullable=False)

    # What entity changed
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(60), nullable=False)

    # What field changed
    field_name: Mapped[str] = mapped_column(String(80), nullable=False)
    field_label: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # Old and new values (as strings for uniformity)
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    old_display: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_display: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Who changed it
    changed_by_user_id: Mapped[UUID | None] = mapped_column(
        SAUUID(as_uuid=True), nullable=True
    )
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Context
    change_source: Mapped[str | None] = mapped_column(String(30), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(60), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<FieldChangeLog {self.entity_type}.{self.field_name} on {self.entity_id}>"
        )
