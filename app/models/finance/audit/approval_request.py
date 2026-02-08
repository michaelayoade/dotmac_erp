"""
Approval Request Model - Pending approval tracking.
"""

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class ApprovalRequestStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    ESCALATED = "ESCALATED"


class ApprovalRequest(Base):
    """
    Approval request for a document.

    Tracks the current state of approval for a document through the workflow.
    """

    __tablename__ = "approval_request"
    __table_args__ = (
        Index("idx_approval_document", "document_type", "document_id"),
        Index("idx_approval_status", "organization_id", "status"),
        {"schema": "audit"},
    )

    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("audit.approval_workflow.workflow_id"),
        nullable=False,
    )

    # Document reference
    document_type: Mapped[str] = mapped_column(String(50), nullable=False)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    document_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    document_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 6),
        nullable=True,
    )
    document_currency_code: Mapped[str | None] = mapped_column(
        String(3),
        nullable=True,
    )

    # Requester
    requested_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Progress
    current_level: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[ApprovalRequestStatus] = mapped_column(
        Enum(ApprovalRequestStatus, name="approval_request_status"),
        nullable=False,
        default=ApprovalRequestStatus.PENDING,
    )

    # Completion
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    final_approver_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    correlation_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Relationships
    workflow: Mapped["ApprovalWorkflow"] = relationship(
        "ApprovalWorkflow",
        back_populates="requests",
    )
    decisions: Mapped[list["ApprovalDecision"]] = relationship(
        "ApprovalDecision",
        back_populates="request",
        order_by="ApprovalDecision.decided_at",
    )


# Forward references
from app.models.finance.audit.approval_decision import ApprovalDecision  # noqa: E402
from app.models.finance.audit.approval_workflow import ApprovalWorkflow  # noqa: E402
