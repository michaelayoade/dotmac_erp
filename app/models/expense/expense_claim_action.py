"""
Expense Claim Action Model - Expense Schema.

Tracks unique financial actions for expense claims.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ExpenseClaimActionType(str, enum.Enum):
    SUBMIT = "SUBMIT"
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    MARK_PAID = "MARK_PAID"
    LINK_ADVANCE = "LINK_ADVANCE"
    POST_GL = "POST_GL"
    CREATE_SUPPLIER_INVOICE = "CREATE_SUPPLIER_INVOICE"


class ExpenseClaimActionStatus(str, enum.Enum):
    STARTED = "STARTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ExpenseClaimAction(Base):
    """
    One-time action marker for expense claims.
    """

    __tablename__ = "expense_claim_action"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "claim_id",
            "action_type",
            name="uq_expense_claim_action",
        ),
        Index("idx_expense_claim_action_claim", "claim_id"),
        {"schema": "expense"},
    )

    action_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("expense.expense_claim.claim_id"),
        nullable=False,
    )
    action_type: Mapped[ExpenseClaimActionType] = mapped_column(
        Enum(ExpenseClaimActionType, name="expense_claim_action_type"),
        nullable=False,
    )
    status: Mapped[ExpenseClaimActionStatus] = mapped_column(
        Enum(ExpenseClaimActionStatus, name="expense_claim_action_status"),
        nullable=False,
        default=ExpenseClaimActionStatus.STARTED,
    )
    action_key: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
