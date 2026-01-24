"""
Corporate Card Model - Expense Schema.

Company credit/debit cards assigned to employees.
"""
import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Date,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.people.base import AuditMixin, ERPNextSyncMixin

if TYPE_CHECKING:
    from app.models.people.hr.employee import Employee
    from app.models.people.exp.expense_claim import ExpenseClaim


class CardTransactionStatus(str, enum.Enum):
    """Corporate card transaction status."""
    PENDING = "PENDING"
    MATCHED = "MATCHED"           # Matched to expense claim
    APPROVED = "APPROVED"
    DISPUTED = "DISPUTED"
    PERSONAL = "PERSONAL"         # Personal expense to be reimbursed
    CANCELLED = "CANCELLED"


class CorporateCard(Base, AuditMixin, ERPNextSyncMixin):
    """
    Corporate Card - company-issued payment card.

    Tracks card details and limits.
    """

    __tablename__ = "corporate_card"
    __table_args__ = (
        Index("idx_corporate_card_employee", "employee_id"),
        {"schema": "expense"},
    )

    card_id: Mapped[uuid.UUID] = mapped_column(
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

    # Card details (masked)
    card_number_last4: Mapped[str] = mapped_column(
        String(4),
        nullable=False,
    )
    card_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="Card display name",
    )
    card_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="CREDIT, DEBIT, PREPAID",
    )
    issuer: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        comment="Bank or card issuer",
    )

    # Assignment
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=False,
    )
    assigned_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )
    expiry_date: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
    )

    # Limits
    credit_limit: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 2),
        nullable=True,
    )
    single_transaction_limit: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 2),
        nullable=True,
    )
    monthly_limit: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 2),
        nullable=True,
    )
    currency_code: Mapped[str] = mapped_column(
        String(3),
        default="NGN",
    )

    # GL Integration
    liability_account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.account.account_id"),
        nullable=True,
        comment="Credit card payable account",
    )

    # Status
    is_active: Mapped[bool] = mapped_column(
        default=True,
    )
    deactivated_on: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
    )
    deactivation_reason: Mapped[Optional[str]] = mapped_column(
        String(200),
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
    employee: Mapped["Employee"] = relationship("Employee")
    transactions: Mapped[list["CardTransaction"]] = relationship(
        "CardTransaction",
        back_populates="card",
    )

    def __repr__(self) -> str:
        return f"<CorporateCard {self.card_name} (*{self.card_number_last4})>"


class CardTransaction(Base, AuditMixin):
    """
    Card Transaction - corporate card transaction record.

    Can be matched to expense claims or flagged as personal.
    """

    __tablename__ = "card_transaction"
    __table_args__ = (
        Index("idx_card_transaction_card", "card_id"),
        Index("idx_card_transaction_date", "organization_id", "transaction_date"),
        Index("idx_card_transaction_status", "organization_id", "status"),
        {"schema": "expense"},
    )

    transaction_id: Mapped[uuid.UUID] = mapped_column(
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

    # Card
    card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("expense.corporate_card.card_id"),
        nullable=False,
    )

    # Transaction details
    transaction_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )
    posting_date: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
    )
    merchant_name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )
    merchant_category: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
    )

    # Amount
    amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )
    currency_code: Mapped[str] = mapped_column(
        String(3),
        default="NGN",
    )
    original_currency: Mapped[Optional[str]] = mapped_column(
        String(3),
        nullable=True,
        comment="If foreign currency transaction",
    )
    original_amount: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 2),
        nullable=True,
    )

    # Reference
    external_reference: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        comment="Bank reference number",
    )

    # Status
    status: Mapped[CardTransactionStatus] = mapped_column(
        Enum(CardTransactionStatus, name="card_transaction_status"),
        default=CardTransactionStatus.PENDING,
    )

    # Matching
    expense_claim_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("expense.expense_claim.claim_id"),
        nullable=True,
    )
    matched_on: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
    )

    # Personal expense (if employee used card for personal)
    is_personal_expense: Mapped[bool] = mapped_column(
        default=False,
    )
    personal_deduction_from_salary: Mapped[bool] = mapped_column(
        default=False,
    )

    # Notes
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    notes: Mapped[Optional[str]] = mapped_column(
        Text,
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
    card: Mapped["CorporateCard"] = relationship(
        "CorporateCard",
        back_populates="transactions",
    )
    expense_claim: Mapped[Optional["ExpenseClaim"]] = relationship("ExpenseClaim")

    def __repr__(self) -> str:
        return f"<CardTransaction {self.merchant_name} {self.amount}>"
