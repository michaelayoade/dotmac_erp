"""
Employee Tax Profile Model - NTA 2025 Employee Tax Settings.

Stores employee-specific tax information for Nigerian PAYE calculation:
- Tax Identification Number (TIN)
- State of residence for PAYE remittance
- Rent relief configuration
- Statutory deduction rates (Pension, NHF, NHIS)
- Tax exemption settings
"""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Boolean,
    Date,
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
from app.models.people.base import AuditMixin

if TYPE_CHECKING:
    from app.models.people.hr.employee import Employee


class EmployeeTaxProfile(Base, AuditMixin):
    """
    Employee Tax Profile - Employee-specific tax settings for PAYE.

    Under NTA 2025:
    - Rent Relief: 20% of actual annual rent, capped at ₦500,000
    - Pension: 8% employee contribution (non-taxable)
    - NHF: 2.5% (National Housing Fund, non-taxable)
    - NHIS: Variable (National Health Insurance Scheme, non-taxable)

    The profile allows per-employee customization of rates and tracks
    tax documentation like TIN and rent receipt verification.
    """

    __tablename__ = "employee_tax_profile"
    __table_args__ = (
        UniqueConstraint(
            "employee_id",
            "effective_from",
            name="uq_employee_tax_profile_emp_date",
        ),
        Index("idx_employee_tax_profile_org", "organization_id"),
        Index("idx_employee_tax_profile_emp", "employee_id"),
        Index("idx_employee_tax_profile_tin", "organization_id", "tin"),
        {"schema": "payroll"},
    )

    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=False,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
        index=True,
    )

    # Tax identification
    tin: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
        comment="Tax Identification Number",
    )
    tax_state: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        comment="State for PAYE remittance",
    )
    rsa_pin: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
        comment="Retirement Savings Account PIN",
    )
    pfa_code: Mapped[Optional[str]] = mapped_column(
        String(10),
        nullable=True,
        comment="PFA code from pfa_directory",
    )
    nhf_number: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
        comment="NHF registration number",
    )

    # Rent relief (NTA 2025: 20% of rent, max ₦500,000)
    annual_rent: Mapped[Decimal] = mapped_column(
        Numeric(19, 4),
        nullable=False,
        default=Decimal("0"),
        comment="Declared annual rent for relief calculation",
    )
    rent_receipt_verified: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        comment="Whether rent documentation has been verified",
    )
    rent_relief_amount: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(19, 4),
        nullable=True,
        comment="Calculated rent relief (20% of rent, max 500k)",
    )

    # Statutory deduction rates
    pension_rate: Mapped[Decimal] = mapped_column(
        Numeric(5, 4),
        default=Decimal("0.08"),
        nullable=False,
        comment="Employee pension contribution rate (default 8%)",
    )
    nhf_rate: Mapped[Decimal] = mapped_column(
        Numeric(5, 4),
        default=Decimal("0.025"),
        nullable=False,
        comment="National Housing Fund rate (default 2.5%)",
    )
    nhis_rate: Mapped[Decimal] = mapped_column(
        Numeric(5, 4),
        default=Decimal("0"),
        nullable=False,
        comment="National Health Insurance Scheme rate",
    )

    # Tax exemption
    is_tax_exempt: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        comment="Whether employee is exempt from income tax",
    )
    exemption_reason: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Reason for tax exemption if applicable",
    )

    # Effective period
    effective_from: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )
    effective_to: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
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

    # Constants for NTA 2025
    RENT_RELIEF_RATE = Decimal("0.20")  # 20%
    RENT_RELIEF_MAX = Decimal("500000")  # ₦500,000 per year

    def calculate_rent_relief(self) -> Decimal:
        """
        Calculate rent relief under NTA 2025 rules.

        Returns:
            Rent relief amount (20% of annual rent, max ₦500,000)
        """
        if not self.rent_receipt_verified or self.annual_rent <= 0:
            return Decimal("0")

        calculated = self.annual_rent * self.RENT_RELIEF_RATE
        return min(calculated, self.RENT_RELIEF_MAX)

    def update_rent_relief(self) -> None:
        """Update the stored rent relief amount based on current rent."""
        self.rent_relief_amount = self.calculate_rent_relief()

    @property
    def total_statutory_rate(self) -> Decimal:
        """Total statutory deduction rate (pension + NHF + NHIS)."""
        return self.pension_rate + self.nhf_rate + self.nhis_rate

    @property
    def pension_rate_percent(self) -> Decimal:
        """Pension rate as percentage."""
        return self.pension_rate * 100

    @property
    def nhf_rate_percent(self) -> Decimal:
        """NHF rate as percentage."""
        return self.nhf_rate * 100

    @property
    def nhis_rate_percent(self) -> Decimal:
        """NHIS rate as percentage."""
        return self.nhis_rate * 100

    def __repr__(self) -> str:
        return f"<EmployeeTaxProfile emp={self.employee_id} TIN={self.tin}>"
