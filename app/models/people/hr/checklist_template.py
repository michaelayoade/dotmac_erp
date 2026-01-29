"""
Checklist template models for HR lifecycle.

Supports onboarding/offboarding checklists with:
- Phased task categories (pre-boarding, day one, first week, etc.)
- Role-based default assignees
- Document collection requirements
- Self-service task assignment
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, Enum, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.people.base import AuditMixin, ERPNextSyncMixin


class ChecklistTemplateType(str, enum.Enum):
    """Checklist template type."""

    ONBOARDING = "ONBOARDING"
    SEPARATION = "SEPARATION"


class OnboardingCategory(str, enum.Enum):
    """Standard categories for phased onboarding tasks."""

    PRE_BOARDING = "PRE_BOARDING"  # Before start date
    DAY_ONE = "DAY_ONE"            # First day tasks
    FIRST_WEEK = "FIRST_WEEK"      # First week tasks
    FIRST_MONTH = "FIRST_MONTH"    # First month tasks
    ONGOING = "ONGOING"            # Continuous/recurring


class AssigneeRole(str, enum.Enum):
    """Standard assignee roles for onboarding tasks."""

    HR = "HR"
    MANAGER = "MANAGER"
    IT = "IT"
    FINANCE = "FINANCE"
    EMPLOYEE = "EMPLOYEE"  # Self-service task
    BUDDY = "BUDDY"        # Assigned mentor/buddy


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
    """
    Checklist template item.

    Defines a task template that gets instantiated as EmployeeOnboardingActivity
    when an employee's onboarding begins.
    """

    __tablename__ = "checklist_template_item"
    __table_args__ = (
        Index("idx_checklist_template_item_template", "template_id"),
        Index("idx_checklist_template_item_category", "template_id", "category"),
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

    # --- New fields for enhanced onboarding ---

    # Phase/category for grouping tasks
    category: Mapped[Optional[str]] = mapped_column(
        String(30),
        nullable=True,
        comment="Task category/phase: PRE_BOARDING, DAY_ONE, FIRST_WEEK, FIRST_MONTH, ONGOING",
    )

    # Default assignee role (who should complete this task)
    default_assignee_role: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        comment="Default assignee role: HR, MANAGER, IT, FINANCE, EMPLOYEE, BUDDY",
    )

    # Due date calculation (days after employee start date)
    days_from_start: Mapped[int] = mapped_column(
        Integer,
        default=0,
        comment="Days after start date when task is due (negative for pre-boarding)",
    )

    # Document collection
    requires_document: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        comment="Whether document upload is required to complete this task",
    )
    document_type: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        comment="Expected document type: ID_COPY, PASSPORT, SIGNED_CONTRACT, BANK_DETAILS, etc.",
    )

    # Instructions for task completion
    instructions: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Detailed instructions for the assignee",
    )

    template: Mapped["ChecklistTemplate"] = relationship(back_populates="items")
