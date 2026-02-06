"""
Salary Component Model - Payroll Schema.

Defines earning and deduction types with GL account mappings.
"""

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Boolean,
    Enum,
    ForeignKey,
    Index,
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
    from app.models.finance.gl.account import Account


class SalaryComponentType(str, enum.Enum):
    """Type of salary component."""

    EARNING = "EARNING"
    DEDUCTION = "DEDUCTION"


class SalaryComponent(Base, AuditMixin, ERPNextSyncMixin):
    """
    Salary Component - earnings and deductions types.

    Each component maps to GL accounts for posting:
    - Earnings: Debit expense account, Credit payroll payable
    - Deductions: Credit liability account (taxes, pension, etc.)
    """

    __tablename__ = "salary_component"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "component_code", name="uq_salary_component_org_code"
        ),
        Index("idx_salary_component_type", "organization_id", "component_type"),
        {"schema": "payroll"},
    )

    component_id: Mapped[uuid.UUID] = mapped_column(
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

    # Component identification
    component_code: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        comment="Unique code, e.g. BASIC, HRA, PAYE",
    )
    component_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    abbr: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
        comment="Short abbreviation for payslips",
    )
    component_type: Mapped[SalaryComponentType] = mapped_column(
        Enum(SalaryComponentType, name="salary_component_type"),
        nullable=False,
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # GL Account Mapping (Critical for posting)
    expense_account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.account.account_id"),
        nullable=True,
        comment="For EARNING: Salary Expense account to debit",
    )
    liability_account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.account.account_id"),
        nullable=True,
        comment="For DEDUCTION: Liability account to credit (PAYE, Pension, etc.)",
    )

    # Tax settings
    is_tax_applicable: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        comment="Include in taxable income calculation",
    )
    is_statutory: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        comment="Statutory deduction (PAYE, Pension, etc.)",
    )
    exempted_from_income_tax: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        comment="Exempt from income tax (certain allowances)",
    )

    # Calculation settings
    depends_on_payment_days: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        comment="Pro-rate based on working days",
    )
    statistical_component: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        comment="For reporting only, not included in totals",
    )
    do_not_include_in_total: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )

    # Status
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
    )
    display_order: Mapped[int] = mapped_column(
        default=0,
        comment="Order on payslip",
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
    expense_account: Mapped[Optional["Account"]] = relationship(
        "Account",
        foreign_keys=[expense_account_id],
    )
    liability_account: Mapped[Optional["Account"]] = relationship(
        "Account",
        foreign_keys=[liability_account_id],
    )

    def __repr__(self) -> str:
        return f"<SalaryComponent {self.component_code} ({self.component_type.value})>"
