"""
Approval Decision Model - Individual approval/rejection decisions.
"""

import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class ApprovalDecisionAction(str, enum.Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    DELEGATE = "DELEGATE"
    ESCALATE = "ESCALATE"
    REQUEST_INFO = "REQUEST_INFO"


class ApprovalDecision(Base):
    """
    Individual approval decision within a workflow.

    Records each approval/rejection decision made during the workflow.
    """

    __tablename__ = "approval_decision"
    __table_args__ = (
        Index("idx_decision_request", "request_id"),
        {"schema": "audit"},
    )

    decision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("audit.approval_request.request_id"),
        nullable=False,
    )

    level: Mapped[int] = mapped_column(Integer, nullable=False)
    approver_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    delegated_from_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    action: Mapped[ApprovalDecisionAction] = mapped_column(
        Enum(ApprovalDecisionAction, name="approval_decision_action"),
        nullable=False,
    )
    comments: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    mfa_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Relationships
    request: Mapped["ApprovalRequest"] = relationship(
        "ApprovalRequest",
        back_populates="decisions",
    )


# Forward reference
from app.models.finance.audit.approval_request import ApprovalRequest  # noqa: E402
