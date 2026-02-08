"""
Workflow Execution Model.

Tracks execution history of workflow rules.
"""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
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
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class ExecutionStatus(str, enum.Enum):
    """Status of workflow execution."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    BLOCKED = "BLOCKED"


class WorkflowExecution(Base):
    """
    Workflow execution log.

    Tracks each time a workflow rule is triggered and its result.
    """

    __tablename__ = "workflow_execution"
    __table_args__ = (
        Index("idx_workflow_execution_rule", "rule_id"),
        Index("idx_workflow_execution_entity", "entity_type", "entity_id"),
        Index("idx_workflow_execution_triggered", "triggered_at"),
        Index("idx_workflow_execution_status", "status"),
        {"schema": "automation"},
    )

    execution_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    rule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("automation.workflow_rule.rule_id", ondelete="CASCADE"),
        nullable=False,
    )

    # Entity that triggered the workflow
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )

    # Trigger details
    trigger_event: Mapped[str] = mapped_column(String(50), nullable=False)
    trigger_data: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="Data at time of trigger: old values, new values, etc.",
    )

    # Timing
    triggered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    duration_ms: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Execution duration in milliseconds",
    )

    # Status
    status: Mapped[ExecutionStatus] = mapped_column(
        Enum(ExecutionStatus, name="workflow_execution_status"),
        nullable=False,
        default=ExecutionStatus.PENDING,
    )

    # Result
    result: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="Result of action: email sent to, task created, etc.",
    )

    # Error details
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_details: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Retry tracking
    retry_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    max_retries: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=3,
    )

    # User context
    triggered_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="User who triggered the event (if applicable)",
    )

    # Relationship
    rule: Mapped["WorkflowRule"] = relationship(
        "WorkflowRule",
        back_populates="executions",
    )

    @property
    def can_retry(self) -> bool:
        """Check if execution can be retried."""
        return (
            self.status == ExecutionStatus.FAILED
            and self.retry_count < self.max_retries
        )


# Forward reference
from app.models.finance.automation.workflow_rule import WorkflowRule  # noqa: E402
