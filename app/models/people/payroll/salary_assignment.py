"""
Salary Structure Assignment Model - Payroll Schema.

Links employees to their salary structures.
"""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Date,
    ForeignKey,
    Index,
    Numeric,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.people.base import AuditMixin, ERPNextSyncMixin

if TYPE_CHECKING:
    from app.models.people.hr.employee import Employee
    from app.models.people.payroll.salary_structure import SalaryStructure


class SalaryStructureAssignment(Base, AuditMixin, ERPNextSyncMixin):
    """
    Salary Structure Assignment - assigns a salary structure to an employee.

    Effective dating allows tracking salary changes over time.
    """

    __tablename__ = "salary_structure_assignment"
    __table_args__ = (
        Index(
            "idx_ssa_emp_date",
            "employee_id",
            "from_date",
        ),
        Index(
            "idx_ssa_org_emp",
            "organization_id",
            "employee_id",
        ),
        {"schema": "payroll"},
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
        index=True,
    )

    # Employee reference
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=False,
    )

    # Salary structure
    structure_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("payroll.salary_structure.structure_id"),
        nullable=False,
    )

    # Effective dating
    from_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        comment="Assignment effective from",
    )
    to_date: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
        comment="Assignment effective until (null = current)",
    )

    # Base pay override
    base: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        default=Decimal("0"),
        comment="Base salary amount",
    )
    variable: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        default=Decimal("0"),
        comment="Variable pay component",
    )

    # Tax settings
    income_tax_slab: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        comment="Tax slab/bracket for PAYE calculation",
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
    employee: Mapped["Employee"] = relationship(
        "Employee",
        foreign_keys=[employee_id],
    )
    salary_structure: Mapped["SalaryStructure"] = relationship(
        "SalaryStructure",
        foreign_keys=[structure_id],
    )

    def __repr__(self) -> str:
        return f"<SalaryStructureAssignment {self.employee_id} -> {self.structure_id}>"
