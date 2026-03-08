"""
Bank Reconciliation Models.

Represents bank reconciliation records that match bank statements to GL entries.
"""

import enum
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as SAUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import settings
from app.db import Base
from app.models.mixins import TrackedMixin

if TYPE_CHECKING:
    from app.models.finance.banking.bank_account import BankAccount
    from app.models.finance.banking.bank_statement import BankStatementLine


class ReconciliationStatus(str, enum.Enum):
    """Status of the reconciliation."""

    draft = "draft"  # In progress
    pending_review = "pending_review"  # Awaiting approval
    approved = "approved"  # Approved and finalized
    rejected = "rejected"  # Rejected, needs rework


class ReconciliationMatchType(str, enum.Enum):
    """Type of reconciliation match."""

    auto_exact = "auto_exact"  # Automatic exact match
    auto_fuzzy = "auto_fuzzy"  # Automatic fuzzy match
    manual = "manual"  # Manual match
    split = "split"  # One-to-many or many-to-one match
    adjustment = "adjustment"  # Reconciling adjustment


class BankReconciliation(Base, TrackedMixin):
    """
    Bank Reconciliation header.

    Represents a reconciliation session for a bank account at a specific date.
    """

    __tablename__ = "bank_reconciliations"
    __table_args__ = (
        UniqueConstraint(
            "bank_account_id", "reconciliation_date", name="uq_bank_reconciliation_date"
        ),
        Index("ix_bank_reconciliation_status", "bank_account_id", "status"),
        {"schema": "banking"},
    )

    # Field-level change tracking
    __tracked_fields__ = {
        "status": {"label": "Status"},
        "reconciliation_date": {"label": "Reconciliation Date"},
        "statement_closing_balance": {"label": "Statement Balance"},
    }
    __tracking_entity_type__ = "BankReconciliation"
    __tracking_pk_field__ = "reconciliation_id"

    # Primary key
    reconciliation_id: Mapped[UUID] = mapped_column(
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

    # Reconciliation date
    reconciliation_date: Mapped[date] = mapped_column(Date, nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)

    # Opening balances
    statement_opening_balance: Mapped[Decimal] = mapped_column(
        Numeric(19, 4),
        nullable=False,
    )
    gl_opening_balance: Mapped[Decimal] = mapped_column(
        Numeric(19, 4),
        nullable=False,
    )

    # Closing balances
    statement_closing_balance: Mapped[Decimal] = mapped_column(
        Numeric(19, 4),
        nullable=False,
    )
    gl_closing_balance: Mapped[Decimal] = mapped_column(
        Numeric(19, 4),
        nullable=False,
    )

    # Reconciliation amounts
    total_matched: Mapped[Decimal] = mapped_column(
        Numeric(19, 4),
        nullable=False,
        default=Decimal("0"),
    )
    total_unmatched_statement: Mapped[Decimal] = mapped_column(
        Numeric(19, 4),
        nullable=False,
        default=Decimal("0"),
    )
    total_unmatched_gl: Mapped[Decimal] = mapped_column(
        Numeric(19, 4),
        nullable=False,
        default=Decimal("0"),
    )
    total_adjustments: Mapped[Decimal] = mapped_column(
        Numeric(19, 4),
        nullable=False,
        default=Decimal("0"),
    )

    # Reconciliation difference
    reconciliation_difference: Mapped[Decimal] = mapped_column(
        Numeric(19, 4),
        nullable=False,
        default=Decimal("0"),
    )

    # Outstanding items from previous reconciliation
    prior_outstanding_deposits: Mapped[Decimal] = mapped_column(
        Numeric(19, 4),
        nullable=False,
        default=Decimal("0"),
    )
    prior_outstanding_payments: Mapped[Decimal] = mapped_column(
        Numeric(19, 4),
        nullable=False,
        default=Decimal("0"),
    )

    # Current outstanding items
    outstanding_deposits: Mapped[Decimal] = mapped_column(
        Numeric(19, 4),
        nullable=False,
        default=Decimal("0"),
    )
    outstanding_payments: Mapped[Decimal] = mapped_column(
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
    status: Mapped[ReconciliationStatus] = mapped_column(
        Enum(ReconciliationStatus, name="reconciliation_status", schema="banking"),
        nullable=False,
        default=ReconciliationStatus.draft,
    )

    # Approval workflow
    prepared_by: Mapped[UUID | None] = mapped_column(
        SAUUID(as_uuid=True), nullable=True
    )
    prepared_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reviewed_by: Mapped[UUID | None] = mapped_column(
        SAUUID(as_uuid=True), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approved_by: Mapped[UUID | None] = mapped_column(
        SAUUID(as_uuid=True), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Notes
    notes: Mapped[str] = mapped_column(Text, nullable=True)
    review_notes: Mapped[str] = mapped_column(Text, nullable=True)

    # Audit fields
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    # Relationships
    bank_account: Mapped["BankAccount"] = relationship(
        "BankAccount",
        foreign_keys=[bank_account_id],
        lazy="select",
    )
    lines: Mapped[list["BankReconciliationLine"]] = relationship(
        "BankReconciliationLine",
        back_populates="reconciliation",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<BankReconciliation {self.reconciliation_date} - {self.status.value}>"

    @property
    def is_reconciled(self) -> bool:
        """Check if reconciliation is complete (difference is zero)."""
        return self.reconciliation_difference == Decimal("0")

    @property
    def adjusted_book_balance(self) -> Decimal:
        """Calculate adjusted book balance.

        GL Closing Balance + total adjustments (bank charges, interest not yet recorded).
        """
        return self.gl_closing_balance + self.total_adjustments

    @property
    def adjusted_bank_balance(self) -> Decimal:
        """Calculate adjusted bank balance.

        Statement Closing Balance + outstanding deposits - outstanding payments.
        """
        return (
            self.statement_closing_balance
            + self.outstanding_deposits
            - self.outstanding_payments
        )

    def calculate_difference(self) -> Decimal:
        """Calculate and update reconciliation difference."""
        self.reconciliation_difference = (
            self.statement_closing_balance - self.adjusted_book_balance
        )
        return self.reconciliation_difference


class BankReconciliationLine(Base):
    """
    Bank Reconciliation Line.

    Represents a matched pair or outstanding item in the reconciliation.
    """

    __tablename__ = "bank_reconciliation_lines"
    __table_args__ = (
        Index("ix_recon_line_type", "reconciliation_id", "match_type"),
        {"schema": "banking"},
    )

    # Primary key
    line_id: Mapped[UUID] = mapped_column(
        SAUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    # Reconciliation reference
    reconciliation_id: Mapped[UUID] = mapped_column(
        SAUUID(as_uuid=True),
        ForeignKey(
            "banking.bank_reconciliations.reconciliation_id", ondelete="CASCADE"
        ),
        nullable=False,
    )

    # Match type
    match_type: Mapped[ReconciliationMatchType] = mapped_column(
        Enum(
            ReconciliationMatchType, name="reconciliation_match_type", schema="banking"
        ),
        nullable=False,
    )

    # Bank statement line reference
    statement_line_id: Mapped[UUID | None] = mapped_column(
        SAUUID(as_uuid=True),
        ForeignKey("banking.bank_statement_lines.line_id", ondelete="SET NULL"),
        nullable=True,
    )

    # GL journal entry line reference
    journal_line_id: Mapped[UUID | None] = mapped_column(
        SAUUID(as_uuid=True),
        ForeignKey("gl.journal_entry_line.line_id", ondelete="SET NULL"),
        nullable=True,
    )

    # Transaction details (for display/audit)
    transaction_date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=True)
    reference: Mapped[str] = mapped_column(String(100), nullable=True)

    # Amounts
    statement_amount: Mapped[Decimal] = mapped_column(
        Numeric(19, 4),
        nullable=True,
    )
    gl_amount: Mapped[Decimal] = mapped_column(
        Numeric(19, 4),
        nullable=True,
    )
    difference: Mapped[Decimal] = mapped_column(
        Numeric(19, 4),
        nullable=True,
    )

    # For adjustments
    is_adjustment: Mapped[bool] = mapped_column(Boolean, default=False)
    adjustment_type: Mapped[str] = mapped_column(
        String(50), nullable=True
    )  # e.g., "bank_fee", "interest", "error"
    adjustment_account_id: Mapped[UUID | None] = mapped_column(
        SAUUID(as_uuid=True),
        nullable=True,
    )

    # Outstanding item tracking
    is_outstanding: Mapped[bool] = mapped_column(Boolean, default=False)
    outstanding_type: Mapped[str] = mapped_column(
        String(20), nullable=True
    )  # "deposit" or "payment"

    # Match confidence (for auto-matching)
    match_confidence: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        nullable=True,
    )  # 0-100%

    # Match details (for audit)
    match_details: Mapped[dict] = mapped_column(JSONB, nullable=True)

    # Status
    is_cleared: Mapped[bool] = mapped_column(Boolean, default=False)
    cleared_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Notes
    notes: Mapped[str] = mapped_column(Text, nullable=True)

    # Audit
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    created_by: Mapped[UUID | None] = mapped_column(SAUUID(as_uuid=True), nullable=True)

    # Relationships
    reconciliation: Mapped["BankReconciliation"] = relationship(
        "BankReconciliation",
        back_populates="lines",
    )
    statement_line: Mapped["BankStatementLine"] = relationship(
        "BankStatementLine",
        foreign_keys=[statement_line_id],
        lazy="select",
    )

    def __repr__(self) -> str:
        return f"<BankReconciliationLine {self.match_type.value}: {self.statement_amount or self.gl_amount}>"
