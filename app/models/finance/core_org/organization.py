"""
Organization Model - Core Org.
"""

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class SectorType(str, enum.Enum):
    """Organization sector classification."""

    PRIVATE = "PRIVATE"
    PUBLIC = "PUBLIC"
    NGO = "NGO"


class AccountingFramework(str, enum.Enum):
    """Accounting standard the organization follows."""

    IFRS = "IFRS"
    IPSAS = "IPSAS"
    BOTH = "BOTH"


class ConsolidationMethod(str, enum.Enum):
    FULL = "FULL"
    PROPORTIONAL = "PROPORTIONAL"
    EQUITY = "EQUITY"
    NONE = "NONE"


class Organization(Base):
    """
    Organization entity - the top-level entity for multi-tenancy.
    """

    __tablename__ = "organization"
    __table_args__ = {"schema": "core_org"}

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_code: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        unique=True,
    )
    slug: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        unique=True,
        index=True,
        comment="URL-safe identifier for public pages like careers portal",
    )

    # Legal identity
    legal_name: Mapped[str] = mapped_column(String(255), nullable=False)
    trading_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    registration_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    tax_identification_number: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )
    incorporation_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    jurisdiction_country_code: Mapped[str | None] = mapped_column(
        String(2),
        nullable=True,
    )

    # Currency settings
    functional_currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    presentation_currency_code: Mapped[str] = mapped_column(String(3), nullable=False)

    # Fiscal year settings
    fiscal_year_end_month: Mapped[int] = mapped_column(Integer, nullable=False)
    fiscal_year_end_day: Mapped[int] = mapped_column(Integer, nullable=False)

    # Group structure
    parent_organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=True,
    )
    consolidation_method: Mapped[ConsolidationMethod | None] = mapped_column(
        Enum(ConsolidationMethod, name="consolidation_method"),
        nullable=True,
    )
    ownership_percentage: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2),
        nullable=True,
    )

    # Status
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Sector & accounting framework
    sector_type: Mapped[SectorType] = mapped_column(
        Enum(SectorType, name="sector_type", schema="core_org"),
        nullable=False,
        default=SectorType.PRIVATE,
        server_default="PRIVATE",
        comment="Organization sector: PRIVATE, PUBLIC, NGO",
    )
    accounting_framework: Mapped[AccountingFramework] = mapped_column(
        Enum(AccountingFramework, name="accounting_framework", schema="core_org"),
        nullable=False,
        default=AccountingFramework.IFRS,
        server_default="IFRS",
        comment="Accounting standard: IFRS, IPSAS, or BOTH",
    )
    fund_accounting_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        comment="Enable IPSAS fund accounting (public sector)",
    )
    commitment_control_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        comment="Enable budget commitment/encumbrance control",
    )

    # Regional settings
    timezone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    date_format: Mapped[str | None] = mapped_column(String(20), nullable=True)
    number_format: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Contact information
    contact_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Address
    address_line1: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address_line2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    state: Mapped[str | None] = mapped_column(String(100), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    country: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Branding
    logo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    website_url: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # HR Settings
    hr_employee_id_format: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment="Employee ID format, e.g. EMP-{YYYY}-{SEQ}",
    )
    hr_employee_id_prefix: Mapped[str | None] = mapped_column(
        String(10),
        nullable=True,
        comment="Employee ID prefix, e.g. EMP",
    )
    hr_payroll_frequency: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        comment="Payroll frequency: MONTHLY, BIWEEKLY, WEEKLY",
    )
    hr_leave_year_start_month: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Month when leave year starts (1-12)",
    )
    hr_probation_days: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Default probation period in days",
    )
    hr_attendance_mode: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        comment="Attendance mode: MANUAL, BIOMETRIC, GEOFENCED",
    )

    # Payroll GL Account Settings
    salaries_expense_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.account.account_id"),
        nullable=True,
        comment="Expense account for total gross salary (debit)",
    )
    salary_payable_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.account.account_id"),
        nullable=True,
        comment="Payable account for net salary owed to employees (credit)",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )

    # Relationships
    parent_organization: Mapped[Optional["Organization"]] = relationship(
        "Organization",
        remote_side=[organization_id],
        foreign_keys=[parent_organization_id],
    )
    subsidiaries: Mapped[list["Organization"]] = relationship(
        "Organization",
        back_populates="parent_organization",
    )
    business_units: Mapped[list["BusinessUnit"]] = relationship(
        "BusinessUnit",
        back_populates="organization",
    )
    branding: Mapped[Optional["OrganizationBranding"]] = relationship(
        "OrganizationBranding",
        back_populates="organization",
        uselist=False,
    )
    tickets: Mapped[list["Ticket"]] = relationship(
        "Ticket",
        back_populates="organization",
    )
    support_teams: Mapped[list["SupportTeam"]] = relationship(
        "SupportTeam",
        back_populates="organization",
    )
    ticket_categories: Mapped[list["TicketCategory"]] = relationship(
        "TicketCategory",
        back_populates="organization",
    )

    # Payroll GL Account relationships
    salaries_expense_account: Mapped[Optional["Account"]] = relationship(
        "Account",
        foreign_keys=[salaries_expense_account_id],
    )
    salary_payable_account: Mapped[Optional["Account"]] = relationship(
        "Account",
        foreign_keys=[salary_payable_account_id],
    )


# Forward references
from app.models.finance.core_org.business_unit import BusinessUnit  # noqa: E402
from app.models.finance.core_org.organization_branding import (  # noqa: E402
    OrganizationBranding,
)
from app.models.finance.gl.account import Account  # noqa: E402
from app.models.support.category import TicketCategory  # noqa: E402
from app.models.support.team import SupportTeam  # noqa: E402
from app.models.support.ticket import Ticket  # noqa: E402
