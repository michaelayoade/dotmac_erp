"""
Employee Grade Model - HR Schema.

Salary grades/bands for compensation management.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, ForeignKey, Integer, Numeric, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.people.base import AuditMixin, ERPNextSyncMixin

if TYPE_CHECKING:
    from app.models.people.hr.employee import Employee
    from app.models.finance.core_org.organization import Organization


class EmployeeGrade(Base, AuditMixin, ERPNextSyncMixin):
    """
    Employee Grade entity for salary bands.

    Grades define salary ranges and can be linked to:
    - Default salary structures
    - Leave allocations
    - Benefits eligibility
    """

    __tablename__ = "employee_grade"
    __table_args__ = {"schema": "hr"}

    grade_id: Mapped[uuid.UUID] = mapped_column(
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

    # Grade identification
    grade_code: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        index=True,
    )
    grade_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Hierarchy/ordering
    rank: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="Numeric rank for ordering (higher = senior)",
    )

    # Salary range
    min_salary: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(15, 2),
        nullable=True,
    )
    max_salary: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(15, 2),
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
    updated_at: Mapped[Optional[datetime]] = mapped_column(
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
        back_populates="grade",
        foreign_keys="Employee.grade_id",
    )
