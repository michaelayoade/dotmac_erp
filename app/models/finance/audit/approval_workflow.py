"""
Approval Workflow Model - Workflow definitions for document approvals.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class ApprovalWorkflow(Base):
    """
    Approval workflow configuration.

    Defines approval levels, thresholds, and rules for different document types.

    approval_levels JSONB structure:
    [
        {
            "level": 1,
            "approver_type": "ROLE" | "USER" | "DEPARTMENT_HEAD" | "COST_CENTER_OWNER",
            "approver_id": "uuid",
            "can_delegate": true,
            "required_count": 1,
            "sod_rule": "CANNOT_BE_CREATOR" | "CANNOT_BE_PREVIOUS_APPROVER" | null
        }
    ]
    """

    __tablename__ = "approval_workflow"
    __table_args__ = (
        UniqueConstraint("organization_id", "workflow_code", name="uq_workflow_code"),
        {"schema": "audit"},
    )

    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
    )

    workflow_code: Mapped[str] = mapped_column(String(50), nullable=False)
    workflow_name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Scope
    document_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="INVOICE, JOURNAL, PAYMENT, PO, ADJUSTMENT, PERIOD_REOPEN, AUDIT_LOCK",
    )

    # Thresholds
    threshold_amount: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 6),
        nullable=True,
    )
    threshold_currency_code: Mapped[Optional[str]] = mapped_column(
        String(3),
        nullable=True,
    )

    # Levels (JSONB array)
    approval_levels: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Relationships
    requests: Mapped[list["ApprovalRequest"]] = relationship(
        "ApprovalRequest",
        back_populates="workflow",
    )


# Forward reference
from app.models.finance.audit.approval_request import ApprovalRequest  # noqa: E402
