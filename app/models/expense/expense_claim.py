"""
Expense Claim Model - Expense Schema.

Employee expense claims with AP integration.
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
    Integer,
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
from app.models.mixins import AuditMixin, ERPNextSyncMixin, StatusTrackingMixin

if TYPE_CHECKING:
    from app.models.people.hr.employee import Employee
    from app.models.finance.core_org.project import Project
    from app.models.support.ticket import Ticket
    from app.models.pm.task import Task


class ExpenseClaimStatus(str, enum.Enum):
    """Expense claim workflow status."""
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    PAID = "PAID"
    CANCELLED = "CANCELLED"


class ExpenseCategory(Base, AuditMixin, ERPNextSyncMixin):
    """
    Expense Category - types of expenses with GL mappings.
    """

    __tablename__ = "expense_category"
    __table_args__ = (
        UniqueConstraint("organization_id", "category_code", name="uq_expense_category_code"),
        {"schema": "expense"},
    )

    category_id: Mapped[uuid.UUID] = mapped_column(
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

    # Identification
    category_code: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )
    category_name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # GL Integration
    expense_account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.account.account_id"),
        nullable=True,
        comment="Default GL expense account",
    )

    # Limits
    max_amount_per_claim: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 2),
        nullable=True,
    )
    requires_receipt: Mapped[bool] = mapped_column(
        default=True,
    )

    # Status
    is_active: Mapped[bool] = mapped_column(
        default=True,
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

    def __repr__(self) -> str:
        return f"<ExpenseCategory {self.category_code}: {self.category_name}>"


class ExpenseClaim(Base, AuditMixin, StatusTrackingMixin, ERPNextSyncMixin):
    """
    Expense Claim - employee reimbursement request.

    Links to AP for payment processing.
    """

    __tablename__ = "expense_claim"
    __table_args__ = (
        Index("idx_expense_claim_employee", "employee_id"),
        Index("idx_expense_claim_status", "organization_id", "status"),
        Index("idx_expense_claim_date", "organization_id", "claim_date"),
        Index("idx_expense_claim_journal", "journal_entry_id"),
        Index("idx_expense_claim_reimbursement_journal", "reimbursement_journal_id"),
        Index("idx_expense_claim_supplier_invoice", "supplier_invoice_id"),
        Index("idx_expense_claim_task", "task_id"),
        {"schema": "expense"},
    )

    claim_id: Mapped[uuid.UUID] = mapped_column(
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

    # Reference
    claim_number: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        unique=True,
    )

    # Employee (optional for non-employee expenses)
    employee_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=True,
    )

    # Claim details
    claim_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )
    expense_period_start: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
    )
    expense_period_end: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
    )

    # Purpose
    purpose: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.project.project_id"),
        nullable=True,
        comment="If expense is project-related",
    )
    ticket_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("support.ticket.ticket_id"),
        nullable=True,
        comment="Related support ticket from ERPNext",
    )
    task_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pm.task.task_id"),
        nullable=True,
        comment="Related project task",
    )

    # Totals
    total_claimed_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
        default=Decimal("0.00"),
    )
    total_approved_amount: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 2),
        nullable=True,
    )
    currency_code: Mapped[str] = mapped_column(
        String(3),
        default="NGN",
    )

    # Advance adjustment
    advance_adjusted: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        default=Decimal("0.00"),
        comment="Amount adjusted against cash advance",
    )
    cash_advance_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("expense.cash_advance.advance_id"),
        nullable=True,
    )

    # Net payable
    net_payable_amount: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 2),
        nullable=True,
        comment="approved_amount - advance_adjusted",
    )

    # Cost allocation
    cost_center_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.cost_center.cost_center_id"),
        nullable=True,
    )

    # Status
    status: Mapped[ExpenseClaimStatus] = mapped_column(
        Enum(ExpenseClaimStatus, name="expense_claim_status"),
        default=ExpenseClaimStatus.DRAFT,
    )

    # Approval
    approver_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=True,
    )
    approved_on: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
    )
    rejection_reason: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # AP Integration
    supplier_invoice_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ap.supplier_invoice.invoice_id"),
        nullable=True,
        comment="Created when claim is approved",
    )
    recipient_bank_code: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
        comment="Paystack recipient bank code",
    )
    recipient_account_number: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
        comment="Paystack recipient account number",
    )
    journal_entry_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.journal_entry.journal_entry_id"),
        nullable=True,
        comment="GL entry for expense posting",
    )
    reimbursement_journal_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.journal_entry.journal_entry_id"),
        nullable=True,
        comment="GL entry for reimbursement payment",
    )
    payment_reference: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
    )
    paid_on: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
    )

    # Notes
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
    employee: Mapped[Optional["Employee"]] = relationship(
        "Employee",
        foreign_keys=[employee_id],
    )
    approver: Mapped[Optional["Employee"]] = relationship(
        "Employee",
        foreign_keys=[approver_id],
    )
    items: Mapped[list["ExpenseClaimItem"]] = relationship(
        "ExpenseClaimItem",
        back_populates="claim",
    )
    project: Mapped[Optional["Project"]] = relationship(
        "Project",
        foreign_keys=[project_id],
    )
    ticket: Mapped[Optional["Ticket"]] = relationship(
        "Ticket",
        foreign_keys=[ticket_id],
    )
    task: Mapped[Optional["Task"]] = relationship(
        "Task",
        foreign_keys=[task_id],
    )

    @property
    def item_count(self) -> int:
        """Get number of expense items."""
        return len(self.items)

    def calculate_totals(self) -> None:
        """Recalculate totals from items."""
        self.total_claimed_amount = sum(item.claimed_amount for item in self.items)
        if self.total_approved_amount is not None:
            self.net_payable_amount = self.total_approved_amount - self.advance_adjusted

    def __repr__(self) -> str:
        return f"<ExpenseClaim {self.claim_number}: {self.status.value}>"


class ExpenseClaimItem(Base):
    """
    Expense Claim Item - individual expense within a claim.
    """

    __tablename__ = "expense_claim_item"
    __table_args__ = (
        Index("idx_expense_claim_item_claim", "claim_id"),
        Index("idx_expense_claim_item_category", "category_id"),
        {"schema": "expense"},
    )

    item_id: Mapped[uuid.UUID] = mapped_column(
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

    # Claim
    claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("expense.expense_claim.claim_id"),
        nullable=False,
    )

    # Item details
    expense_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )
    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("expense.expense_category.category_id"),
        nullable=False,
    )
    description: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    # Amounts
    claimed_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )
    approved_amount: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 2),
        nullable=True,
    )

    # GL Override
    expense_account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.account.account_id"),
        nullable=True,
        comment="Override category default",
    )
    cost_center_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.cost_center.cost_center_id"),
        nullable=True,
    )

    # Receipt
    receipt_url: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
    )
    receipt_number: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
    )

    # Vendor
    vendor_name: Mapped[Optional[str]] = mapped_column(
        String(200),
        nullable=True,
    )

    # Travel specific
    is_travel_expense: Mapped[bool] = mapped_column(
        default=False,
    )
    travel_from: Mapped[Optional[str]] = mapped_column(
        String(200),
        nullable=True,
    )
    travel_to: Mapped[Optional[str]] = mapped_column(
        String(200),
        nullable=True,
    )
    distance_km: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 2),
        nullable=True,
    )

    # Notes
    notes: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Sequence
    sequence: Mapped[int] = mapped_column(
        Integer,
        default=0,
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=func.now(),
    )

    # Relationships
    claim: Mapped["ExpenseClaim"] = relationship(
        "ExpenseClaim",
        back_populates="items",
    )
    category: Mapped["ExpenseCategory"] = relationship("ExpenseCategory")

    def __repr__(self) -> str:
        return f"<ExpenseClaimItem {self.description[:30]}>"
