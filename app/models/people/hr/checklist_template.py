"""
Checklist template models for HR lifecycle.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Enum, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.people.base import AuditMixin, ERPNextSyncMixin


class ChecklistTemplateType(str, enum.Enum):
    """Checklist template type."""

    ONBOARDING = "ONBOARDING"
    SEPARATION = "SEPARATION"


class ChecklistTemplate(Base, AuditMixin, ERPNextSyncMixin):
    """Checklist template."""

    __tablename__ = "checklist_template"
    __table_args__ = (
        Index("idx_checklist_template_type", "organization_id", "template_type"),
        {"schema": "hr"},
    )

    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
        index=True,
    )
    template_code: Mapped[str] = mapped_column(String(30), nullable=False)
    template_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    template_type: Mapped[ChecklistTemplateType] = mapped_column(
        Enum(ChecklistTemplateType, name="checklist_template_type"),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(nullable=True, onupdate=func.now())

    items: Mapped[list["ChecklistTemplateItem"]] = relationship(
        "ChecklistTemplateItem",
        back_populates="template",
        cascade="all, delete-orphan",
    )


class ChecklistTemplateItem(Base):
    """Checklist template item."""

    __tablename__ = "checklist_template_item"
    __table_args__ = (
        Index("idx_checklist_template_item_template", "template_id"),
        {"schema": "hr"},
    )

    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.checklist_template.template_id", ondelete="CASCADE"),
        nullable=False,
    )
    item_name: Mapped[str] = mapped_column(String(500), nullable=False)
    is_required: Mapped[bool] = mapped_column(default=True)
    sequence: Mapped[int] = mapped_column(Integer, default=0)

    template: Mapped["ChecklistTemplate"] = relationship(back_populates="items")
