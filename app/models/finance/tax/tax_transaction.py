"""
Tax Transaction Model - Tax Schema.
"""

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class TaxTransactionType(str, enum.Enum):
    INPUT = "INPUT"
    OUTPUT = "OUTPUT"
    WITHHOLDING = "WITHHOLDING"
    ACCRUAL = "ACCRUAL"
    PAYMENT = "PAYMENT"
    REFUND = "REFUND"


class TaxTransaction(Base):
    """
    Tax transaction record.
    """

    __tablename__ = "tax_transaction"
    __table_args__ = (
        Index("idx_tax_txn_code", "tax_code_id"),
        Index("idx_tax_txn_date", "transaction_date"),
        {"schema": "tax"},
    )

    transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    fiscal_period_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.fiscal_period.fiscal_period_id"),
        nullable=False,
    )

    tax_code_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tax.tax_code.tax_code_id"),
        nullable=False,
    )
    jurisdiction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tax.tax_jurisdiction.jurisdiction_id"),
        nullable=False,
    )

    transaction_type: Mapped[TaxTransactionType] = mapped_column(
        Enum(TaxTransactionType, name="tax_transaction_type"),
        nullable=False,
    )
    transaction_date: Mapped[date] = mapped_column(Date, nullable=False)

    # Source document
    source_document_type: Mapped[str] = mapped_column(String(30), nullable=False)
    source_document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    source_document_line_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    source_document_reference: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True
    )

    # Counterparty
    counterparty_type: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    counterparty_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    counterparty_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    counterparty_tax_id: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )

    # Amounts
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    base_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)

    # Functional currency
    exchange_rate: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 10), nullable=True
    )
    functional_base_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False
    )
    functional_tax_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False
    )

    # Recovery (for input tax)
    recoverable_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )
    non_recoverable_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )

    # Tax return reference
    tax_return_period: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    tax_return_box: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    is_included_in_return: Mapped[bool] = mapped_column(default=False)

    # Journal entry
    journal_entry_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
