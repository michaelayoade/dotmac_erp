"""
Salary Structure Model - Payroll Schema.

Defines pay structure templates with earnings and deductions.
"""

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Boolean,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.people.base import AuditMixin, ERPNextSyncMixin

if TYPE_CHECKING:
    from app.models.finance.core_org.organization import Organization
    from app.models.people.payroll.salary_component import SalaryComponent


class PayrollFrequency(str, enum.Enum):
    """Pay frequency options."""

    WEEKLY = "WEEKLY"
    BIWEEKLY = "BIWEEKLY"
    SEMIMONTHLY = "SEMIMONTHLY"
    MONTHLY = "MONTHLY"


class SalaryStructure(Base, AuditMixin, ERPNextSyncMixin):
    """
    Salary Structure - pay structure template.

    Defines a reusable template with earnings and deductions
    that can be assigned to employees.
    """

    __tablename__ = "salary_structure"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "structure_code", name="uq_salary_structure_org_code"
        ),
        {"schema": "payroll"},
    )

    structure_id: Mapped[uuid.UUID] = mapped_column(
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

    # Structure identification
    structure_code: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )
    structure_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Payroll settings
    payroll_frequency: Mapped[PayrollFrequency] = mapped_column(
        Enum(PayrollFrequency, name="payroll_frequency"),
        default=PayrollFrequency.MONTHLY,
    )
    currency_code: Mapped[str] = mapped_column(
        String(3),
        default="NGN",
    )

    # Status
    is_active: Mapped[bool] = mapped_column(
        Boolean,
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
    earnings: Mapped[list["SalaryStructureEarning"]] = relationship(
        "SalaryStructureEarning",
        back_populates="salary_structure",
        cascade="all, delete-orphan",
        order_by="SalaryStructureEarning.display_order",
    )
    deductions: Mapped[list["SalaryStructureDeduction"]] = relationship(
        "SalaryStructureDeduction",
        back_populates="salary_structure",
        cascade="all, delete-orphan",
        order_by="SalaryStructureDeduction.display_order",
    )

    def __repr__(self) -> str:
        return f"<SalaryStructure {self.structure_code}>"


class SalaryStructureEarning(Base):
    """
    Salary Structure Earning - earning line in structure template.
    """

    __tablename__ = "salary_structure_earning"
    __table_args__ = (
        Index(
            "idx_struct_earning_struct",
            "structure_id",
        ),
        {"schema": "payroll"},
    )

    earning_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    structure_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("payroll.salary_structure.structure_id", ondelete="CASCADE"),
        nullable=False,
    )
    component_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("payroll.salary_component.component_id"),
        nullable=False,
    )

    # Amount settings
    amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        default=Decimal("0"),
        comment="Fixed amount if not formula-based",
    )
    amount_based_on_formula: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )
    formula: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Formula expression, e.g. base * 0.25",
    )
    condition: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Condition for applying this earning",
    )

    # Display
    display_order: Mapped[int] = mapped_column(default=0)

    # Relationships
    salary_structure: Mapped["SalaryStructure"] = relationship(
        "SalaryStructure",
        back_populates="earnings",
    )
    component: Mapped["SalaryComponent"] = relationship(
        "SalaryComponent",
        foreign_keys=[component_id],
    )


class SalaryStructureDeduction(Base):
    """
    Salary Structure Deduction - deduction line in structure template.
    """

    __tablename__ = "salary_structure_deduction"
    __table_args__ = (
        Index(
            "idx_struct_deduction_struct",
            "structure_id",
        ),
        {"schema": "payroll"},
    )

    deduction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    structure_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("payroll.salary_structure.structure_id", ondelete="CASCADE"),
        nullable=False,
    )
    component_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("payroll.salary_component.component_id"),
        nullable=False,
    )

    # Amount settings
    amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        default=Decimal("0"),
        comment="Fixed amount if not formula-based",
    )
    amount_based_on_formula: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )
    formula: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Formula expression, e.g. gross * 0.08",
    )
    condition: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Condition for applying this deduction",
    )

    # Display
    display_order: Mapped[int] = mapped_column(default=0)

    # Relationships
    salary_structure: Mapped["SalaryStructure"] = relationship(
        "SalaryStructure",
        back_populates="deductions",
    )
    component: Mapped["SalaryComponent"] = relationship(
        "SalaryComponent",
        foreign_keys=[component_id],
    )
