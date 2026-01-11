"""
Expense Entry Model.

Quick expense entry for direct expense recording (petty cash, reimbursements, etc.).
"""
import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, Enum, ForeignKey, Index, Numeric, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class ExpenseStatus(str, enum.Enum):
    """Expense entry status."""
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    POSTED = "POSTED"
    REJECTED = "REJECTED"
    VOID = "VOID"


class PaymentMethod(str, enum.Enum):
    """Payment method for expense."""
    CASH = "CASH"
    PETTY_CASH = "PETTY_CASH"
    CORPORATE_CARD = "CORPORATE_CARD"
    PERSONAL_CARD = "PERSONAL_CARD"
    BANK_TRANSFER = "BANK_TRANSFER"
    OTHER = "OTHER"


class ExpenseEntry(Base):
    """
    Expense entry for quick expense recording.

    This model allows users to quickly record expenses without going through
    the full AP invoice workflow. Suitable for:
    - Petty cash expenses
    - Employee reimbursements
    - Corporate card transactions
    - Direct expense entries
    """

    __tablename__ = "expense_entry"
    __table_args__ = (
        UniqueConstraint("organization_id", "expense_number", name="uq_expense_entry_number"),
        Index("idx_expense_entry_org_date", "organization_id", "expense_date"),
        Index("idx_expense_entry_status", "organization_id", "status"),
        Index("idx_expense_entry_account", "expense_account_id"),
        Index("idx_expense_entry_project", "project_id"),
        Index("idx_expense_entry_cost_center", "cost_center_id"),
        {"schema": "exp"},
    )

    expense_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )

    # Expense identification
    expense_number: Mapped[str] = mapped_column(String(30), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Dates
    expense_date: Mapped[date] = mapped_column(Date, nullable=False)

    # Accounts
    expense_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.account.account_id"),
        nullable=False,
        comment="Expense account (debit)",
    )
    payment_account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.account.account_id"),
        nullable=True,
        comment="Payment source account (credit) - cash, bank, etc.",
    )

    # Amount
    amount: Mapped[Decimal] = mapped_column(
        Numeric(19, 4),
        nullable=False,
    )
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")

    # Tax
    tax_code_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tax.tax_code.tax_code_id"),
        nullable=True,
    )
    tax_amount: Mapped[Decimal] = mapped_column(
        Numeric(19, 4),
        nullable=False,
        default=Decimal("0"),
    )

    # Cost allocation
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.project.project_id"),
        nullable=True,
        comment="Project for cost allocation",
    )
    cost_center_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.cost_center.cost_center_id"),
        nullable=True,
        comment="Cost center for departmental allocation",
    )
    business_unit_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.business_unit.business_unit_id"),
        nullable=True,
        comment="Business unit for segment reporting",
    )

    # Payment details
    payment_method: Mapped[PaymentMethod] = mapped_column(
        Enum(PaymentMethod, name="expense_payment_method"),
        nullable=False,
        default=PaymentMethod.CASH,
    )
    payee: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    receipt_reference: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Status
    status: Mapped[ExpenseStatus] = mapped_column(
        Enum(ExpenseStatus, name="expense_status"),
        nullable=False,
        default=ExpenseStatus.DRAFT,
    )

    # Journal reference (when posted)
    journal_entry_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.journal_entry.journal_entry_id"),
        nullable=True,
    )

    # Approval
    submitted_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    posted_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    posted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Audit
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )

    # Relationships
    expense_account = relationship(
        "Account",
        foreign_keys=[expense_account_id],
        lazy="joined",
    )
    payment_account = relationship(
        "Account",
        foreign_keys=[payment_account_id],
        lazy="joined",
    )
    journal_entry = relationship(
        "JournalEntry",
        foreign_keys=[journal_entry_id],
        lazy="select",
    )
    project = relationship(
        "Project",
        foreign_keys=[project_id],
        lazy="joined",
    )
    cost_center = relationship(
        "CostCenter",
        foreign_keys=[cost_center_id],
        lazy="joined",
    )
    business_unit = relationship(
        "BusinessUnit",
        foreign_keys=[business_unit_id],
        lazy="joined",
    )

    @property
    def total_amount(self) -> Decimal:
        """Total amount including tax."""
        return (self.amount or Decimal("0")) + (self.tax_amount or Decimal("0"))
