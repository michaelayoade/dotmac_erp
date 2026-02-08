"""
Designation Model - HR Schema.

Job titles/positions within the organization.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.people.base import AuditMixin, ERPNextSyncMixin, SoftDeleteMixin

if TYPE_CHECKING:
    from app.models.finance.core_org.organization import Organization
    from app.models.people.hr.employee import Employee


class Designation(Base, AuditMixin, SoftDeleteMixin, ERPNextSyncMixin):
    """
    Designation entity for job titles/positions.

    Designations define roles within the organization hierarchy.
    Can be linked to salary grades for payroll processing.
    """

    __tablename__ = "designation"
    __table_args__ = {"schema": "hr"}

    designation_id: Mapped[uuid.UUID] = mapped_column(
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

    # Designation identification
    designation_code: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        index=True,
    )
    designation_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Status
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
        onupdate=func.now(),
    )

    # Relationships
    organization: Mapped["Organization"] = relationship(
        "Organization",
        foreign_keys=[organization_id],
    )
    employees: Mapped[list["Employee"]] = relationship(
        "Employee",
        back_populates="designation",
        foreign_keys="Employee.designation_id",
    )
