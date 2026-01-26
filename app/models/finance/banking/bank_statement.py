"""
Bank Statement Models.

Represents imported bank statements and their transaction lines.
"""

import enum
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, List, Optional
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as SAUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import settings
from app.db import Base

if TYPE_CHECKING:
    from app.models.finance.banking.bank_account import BankAccount


class BankStatementStatus(str, enum.Enum):
    """Status of the bank statement."""

    imported = "imported"  # Just imported, not yet processed
    processing = "processing"  # Being matched/reconciled
    reconciled = "reconciled"  # Fully reconciled
    closed = "closed"  # Closed, no more changes


class StatementLineType(str, enum.Enum):
    """Type of statement line transaction."""

    credit = "credit"  # Money in (deposits, transfers in)
    debit = "debit"  # Money out (payments, transfers out)


class BankStatement(Base):
    """
    Bank Statement header.

    Represents an imported bank statement for a specific period.
    """

    __tablename__ = "bank_statements"
    __table_args__ = (
        UniqueConstraint(
            "bank_account_id", "statement_number",
            name="uq_bank_statement_number"
        ),
        Index("ix_bank_statement_period", "bank_account_id", "statement_date"),
        {"schema": "banking"},
    )

    # Primary key
    statement_id: Mapped[UUID] = mapped_column(
        SAUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    # Organization
    organization_id: Mapped[UUID] = mapped_column(
        SAUUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    # Bank account reference
    bank_account_id: Mapped[UUID] = mapped_column(
        SAUUID(as_uuid=True),
        ForeignKey("banking.bank_accounts.bank_account_id", ondelete="CASCADE"),
        nullable=False,
    )

    # Statement identification
    statement_number: Mapped[str] = mapped_column(String(50), nullable=False)
    statement_date: Mapped[date] = mapped_column(Date, nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)

    # Balances
    opening_balance: Mapped[Decimal] = mapped_column(
        Numeric(19, 4),
        nullable=False,
    )
    closing_balance: Mapped[Decimal] = mapped_column(
        Numeric(19, 4),
        nullable=False,
    )
    total_credits: Mapped[Decimal] = mapped_column(
        Numeric(19, 4),
        nullable=False,
        default=Decimal("0"),
    )
    total_debits: Mapped[Decimal] = mapped_column(
        Numeric(19, 4),
        nullable=False,
        default=Decimal("0"),
    )

    # Currency
    currency_code: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default=settings.default_functional_currency_code,
    )

    # Status
    status: Mapped[BankStatementStatus] = mapped_column(
        Enum(BankStatementStatus, name="bank_statement_status", schema="banking"),
        nullable=False,
        default=BankStatementStatus.imported,
    )

    # Import metadata
    import_source: Mapped[str] = mapped_column(String(50), nullable=True)  # e.g., "CSV", "OFX", "MT940"
    import_filename: Mapped[str] = mapped_column(String(255), nullable=True)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
    )
    imported_by: Mapped[Optional[UUID]] = mapped_column(SAUUID(as_uuid=True), nullable=True)

    # Line counts
    total_lines: Mapped[int] = mapped_column(nullable=False, default=0)
    matched_lines: Mapped[int] = mapped_column(nullable=False, default=0)
    unmatched_lines: Mapped[int] = mapped_column(nullable=False, default=0)

    # Notes
    notes: Mapped[str] = mapped_column(Text, nullable=True)

    # Audit fields
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    # Relationships
    bank_account: Mapped["BankAccount"] = relationship(
        "BankAccount",
        foreign_keys=[bank_account_id],
        lazy="joined",
    )
    lines: Mapped[List["BankStatementLine"]] = relationship(
        "BankStatementLine",
        back_populates="statement",
        cascade="all, delete-orphan",
        order_by="BankStatementLine.transaction_date, BankStatementLine.line_number",
    )

    def __repr__(self) -> str:
        return f"<BankStatement {self.statement_number} ({self.statement_date})>"

    @property
    def is_balanced(self) -> bool:
        """Check if statement balances correctly."""
        calculated = self.opening_balance + self.total_credits - self.total_debits
        return calculated == self.closing_balance

    @property
    def reconciliation_progress(self) -> float:
        """Return reconciliation progress as percentage."""
        if self.total_lines == 0:
            return 100.0
        return (self.matched_lines / self.total_lines) * 100


class BankStatementLine(Base):
    """
    Bank Statement Line.

    Individual transaction from a bank statement.
    """

    __tablename__ = "bank_statement_lines"
    __table_args__ = (
        Index("ix_statement_line_date", "statement_id", "transaction_date"),
        Index("ix_statement_line_matched", "statement_id", "is_matched"),
        {"schema": "banking"},
    )

    # Primary key
    line_id: Mapped[UUID] = mapped_column(
        SAUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    # Statement reference
    statement_id: Mapped[UUID] = mapped_column(
        SAUUID(as_uuid=True),
        ForeignKey("banking.bank_statements.statement_id", ondelete="CASCADE"),
        nullable=False,
    )

    # Line identification
    line_number: Mapped[int] = mapped_column(nullable=False)
    transaction_id: Mapped[str] = mapped_column(String(100), nullable=True)  # Bank's transaction ID

    # Transaction details
    transaction_date: Mapped[date] = mapped_column(Date, nullable=False)
    value_date: Mapped[date] = mapped_column(Date, nullable=True)  # Settlement date
    transaction_type: Mapped[StatementLineType] = mapped_column(
        Enum(StatementLineType, name="statement_line_type", schema="banking"),
        nullable=False,
    )

    # Amount
    amount: Mapped[Decimal] = mapped_column(
        Numeric(19, 4),
        nullable=False,
    )
    running_balance: Mapped[Decimal] = mapped_column(
        Numeric(19, 4),
        nullable=True,
    )

    # Description/Reference
    description: Mapped[str] = mapped_column(String(500), nullable=True)
    reference: Mapped[str] = mapped_column(String(100), nullable=True)
    payee_payer: Mapped[str] = mapped_column(String(200), nullable=True)  # Counter-party name
    bank_reference: Mapped[str] = mapped_column(String(100), nullable=True)
    check_number: Mapped[str] = mapped_column(String(20), nullable=True)

    # Categorization (from bank)
    bank_category: Mapped[str] = mapped_column(String(100), nullable=True)
    bank_code: Mapped[str] = mapped_column(String(20), nullable=True)  # Bank's transaction code

    # Matching status
    is_matched: Mapped[bool] = mapped_column(Boolean, default=False)
    matched_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    matched_by: Mapped[Optional[UUID]] = mapped_column(SAUUID(as_uuid=True), nullable=True)

    # GL matching (for matched transactions)
    matched_journal_line_id: Mapped[Optional[UUID]] = mapped_column(
        SAUUID(as_uuid=True),
        nullable=True,
    )

    # Additional data from import
    raw_data: Mapped[dict] = mapped_column(JSONB, nullable=True)

    # Notes
    notes: Mapped[str] = mapped_column(Text, nullable=True)

    # Audit
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
    )

    # Relationships
    statement: Mapped["BankStatement"] = relationship(
        "BankStatement",
        back_populates="lines",
    )

    def __repr__(self) -> str:
        return f"<BankStatementLine {self.line_number}: {self.transaction_type.value} {self.amount}>"

    @property
    def signed_amount(self) -> Decimal:
        """Return amount with sign based on transaction type."""
        if self.transaction_type == StatementLineType.credit:
            return self.amount
        return -self.amount
