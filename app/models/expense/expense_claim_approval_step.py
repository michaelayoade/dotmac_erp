"""
Expense claim approval step model.

Persists approval workflow steps so multi-approver state survives requests
and prior approval rounds remain auditable after resubmission.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
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


class ExpenseClaimApprovalStep(Base):
    """Persisted approval step for one expense-claim submission round."""

    __tablename__ = "expense_claim_approval_step"
    __table_args__ = (
        UniqueConstraint(
            "claim_id",
            "submission_round",
            "step_number",
            name="uq_expense_claim_approval_step_round",
        ),
        Index(
            "idx_expense_claim_approval_step_claim_round",
            "claim_id",
            "submission_round",
        ),
        Index(
            "idx_expense_claim_approval_step_pending",
            "organization_id",
            "approver_id",
            "decision",
        ),
        {"schema": "expense"},
    )

    approval_step_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("expense.expense_claim.claim_id"),
        nullable=False,
    )
    submission_round: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )
    step_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    approver_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=False,
    )
    approver_name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )
    max_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )
    requires_all_approvals: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    is_escalation: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    decision: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    claim = relationship("ExpenseClaim", back_populates="approval_steps")
