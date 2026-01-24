"""
Financial Instrument Model - Financial Instruments Schema.
"""
import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, Enum, ForeignKey, Index, Numeric, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class InstrumentType(str, enum.Enum):
    DEBT_SECURITY = "DEBT_SECURITY"
    EQUITY_SECURITY = "EQUITY_SECURITY"
    LOAN = "LOAN"
    DEPOSIT = "DEPOSIT"
    DERIVATIVE = "DERIVATIVE"
    TRADE_RECEIVABLE = "TRADE_RECEIVABLE"
    BANK_ACCOUNT = "BANK_ACCOUNT"


class InstrumentClassification(str, enum.Enum):
    # Financial assets
    AMORTIZED_COST = "AMORTIZED_COST"
    FVOCI_DEBT = "FVOCI_DEBT"
    FVOCI_EQUITY = "FVOCI_EQUITY"
    FVPL = "FVPL"
    # Financial liabilities
    AMORTIZED_COST_LIABILITY = "AMORTIZED_COST_LIABILITY"
    FVPL_LIABILITY = "FVPL_LIABILITY"


class InstrumentStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    MATURED = "MATURED"
    SOLD = "SOLD"
    IMPAIRED = "IMPAIRED"
    WRITTEN_OFF = "WRITTEN_OFF"


class FinancialInstrument(Base):
    """
    Financial instrument master record (IFRS 9).
    """

    __tablename__ = "financial_instrument"
    __table_args__ = (
        UniqueConstraint("organization_id", "instrument_code", name="uq_financial_instrument"),
        Index("idx_fin_inst_type", "instrument_type"),
        {"schema": "fin_inst"},
    )

    instrument_id: Mapped[uuid.UUID] = mapped_column(
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

    instrument_code: Mapped[str] = mapped_column(String(50), nullable=False)
    instrument_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    instrument_type: Mapped[InstrumentType] = mapped_column(
        Enum(InstrumentType, name="instrument_type"),
        nullable=False,
    )
    classification: Mapped[InstrumentClassification] = mapped_column(
        Enum(InstrumentClassification, name="instrument_classification"),
        nullable=False,
    )
    is_asset: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Counterparty
    counterparty_type: Mapped[str] = mapped_column(String(30), nullable=False)
    counterparty_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    counterparty_name: Mapped[str] = mapped_column(String(200), nullable=False)

    # External references
    isin: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    cusip: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    external_reference: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Principal/notional
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    face_value: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    current_principal: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)

    # Dates
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    settlement_date: Mapped[date] = mapped_column(Date, nullable=False)
    maturity_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    # Interest terms
    is_interest_bearing: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    interest_rate_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    stated_interest_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6), nullable=True)
    effective_interest_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6), nullable=True)
    interest_payment_frequency: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    day_count_convention: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    next_interest_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    # Initial recognition
    acquisition_cost: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    transaction_costs: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False, default=0)
    premium_discount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False, default=0)

    # Current carrying amount
    amortized_cost: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    fair_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 6), nullable=True)
    carrying_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)

    # Impairment (ECL)
    ecl_stage: Mapped[int] = mapped_column(Numeric(1, 0), nullable=False, default=1)
    loss_allowance: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False, default=0)
    is_credit_impaired: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # OCI accumulation (for FVOCI)
    accumulated_oci: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False, default=0)

    # Status
    status: Mapped[InstrumentStatus] = mapped_column(
        Enum(InstrumentStatus, name="instrument_status"),
        nullable=False,
        default=InstrumentStatus.ACTIVE,
    )

    # Accounts
    instrument_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    interest_receivable_account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    interest_income_account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    fv_adjustment_account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    oci_account_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    ecl_expense_account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Audit
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
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
