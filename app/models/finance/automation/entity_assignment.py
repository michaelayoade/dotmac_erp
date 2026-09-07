"""Cross-module assignment produced by automation rules."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.finance.automation.workflow_rule import WorkflowEntityType


class EntityAssignment(Base):
    """Current assignee for an entity, independent of module-specific columns."""

    __tablename__ = "entity_assignment"
    __table_args__ = (
        Index(
            "idx_entity_assignment_current",
            "organization_id",
            "entity_type",
            "entity_id",
            "is_active",
        ),
        Index("idx_entity_assignment_person", "organization_id", "assignee_id"),
        {"schema": "automation"},
    )

    assignment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
    )
    entity_type: Mapped[WorkflowEntityType] = mapped_column(
        Enum(WorkflowEntityType, name="workflow_entity_type", create_type=False),
        nullable=False,
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    assignee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id", ondelete="RESTRICT"), nullable=False
    )
    strategy: Mapped[str] = mapped_column(String(30), nullable=False, default="DIRECT")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now()
    )
