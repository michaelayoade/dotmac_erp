"""
Tax Jurisdiction Model - Tax Schema.
"""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class TaxJurisdiction(Base):
    """
    Tax jurisdiction master.
    """

    __tablename__ = "tax_jurisdiction"
    __table_args__ = (
        UniqueConstraint("organization_id", "jurisdiction_code", name="uq_tax_jurisdiction"),
        {"schema": "tax"},
    )

    jurisdiction_id: Mapped[uuid.UUID] = mapped_column(
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

    jurisdiction_code: Mapped[str] = mapped_column(String(30), nullable=False)
    jurisdiction_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Geographic details
    country_code: Mapped[str] = mapped_column(String(3), nullable=False)
    state_province: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    jurisdiction_level: Mapped[str] = mapped_column(String(30), nullable=False)

    # Corporate income tax rates
    current_tax_rate: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)
    tax_rate_effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    future_tax_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6), nullable=True)
    future_rate_effective_from: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    # Small business/reduced rate
    has_reduced_rate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reduced_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6), nullable=True)
    reduced_rate_threshold: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 6), nullable=True)

    # Tax filing
    fiscal_year_end_month: Mapped[int] = mapped_column(Numeric(2, 0), nullable=False, default=12)
    filing_due_months: Mapped[int] = mapped_column(Numeric(2, 0), nullable=False, default=6)
    extension_months: Mapped[Optional[int]] = mapped_column(Numeric(2, 0), nullable=True)

    # Currency
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)

    # Tax authority
    tax_authority_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    tax_id_number: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # Accounts
    current_tax_payable_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    current_tax_expense_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    deferred_tax_asset_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    deferred_tax_liability_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    deferred_tax_expense_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )
