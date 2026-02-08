"""
Saga Execution Models - Track saga state for distributed transactions.

Implements the Saga pattern for managing multi-step operations that span
multiple services/databases. Each saga tracks its current step and maintains
compensation data for rollback.
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
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class SagaStatus(str, enum.Enum):
    """Status of a saga execution."""

    PENDING = "PENDING"  # Not yet started
    EXECUTING = "EXECUTING"  # Steps in progress
    COMPLETED = "COMPLETED"  # All steps succeeded
    COMPENSATING = "COMPENSATING"  # Rolling back
    COMPENSATED = "COMPENSATED"  # Rollback complete
    FAILED = "FAILED"  # Failed permanently (compensation also failed)


class StepStatus(str, enum.Enum):
    """Status of a saga step."""

    PENDING = "PENDING"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    COMPENSATING = "COMPENSATING"
    COMPENSATED = "COMPENSATED"


class SagaExecution(Base):
    """
    Tracks the execution state of a saga.

    A saga represents a multi-step distributed transaction with
    automatic compensation (rollback) on failure.
    """

    __tablename__ = "saga_execution"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_saga_idempotency_key"),
        Index("idx_saga_execution_org_status", "organization_id", "status"),
        Index("idx_saga_execution_correlation", "correlation_id"),
        Index("idx_saga_execution_type_status", "saga_type", "status"),
        {"schema": "platform"},
    )

    saga_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    saga_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="Type identifier: AP_INVOICE_POST, AR_INVOICE_POST, etc.",
    )
    idempotency_key: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        comment="Unique key for saga deduplication",
    )
    correlation_id: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="Correlation ID for distributed tracing",
    )

    status: Mapped[SagaStatus] = mapped_column(
        Enum(SagaStatus, name="saga_status", schema="platform"),
        nullable=False,
        default=SagaStatus.PENDING,
    )
    current_step: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="Index of current/last executed step",
    )

    # Saga input payload (e.g., invoice_id, posting_date)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        comment="Input parameters for the saga",
    )
    # Runtime context (accumulated data from completed steps)
    context: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
        comment="Accumulated context from step outputs",
    )

    # Result
    result: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="Final result on completion",
    )
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Error message if failed",
    )

    # Timestamps
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Audit
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )

    # Relationships
    steps: Mapped[list["SagaStep"]] = relationship(
        "SagaStep",
        back_populates="saga",
        cascade="all, delete-orphan",
        order_by="SagaStep.step_number",
    )


class SagaStep(Base):
    """
    Tracks individual step execution within a saga.

    Each step records its input, output, and compensation data
    to enable rollback on failure.
    """

    __tablename__ = "saga_step"
    __table_args__ = (
        Index("idx_saga_step_saga_number", "saga_id", "step_number"),
        {"schema": "platform"},
    )

    step_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    saga_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("platform.saga_execution.saga_id", ondelete="CASCADE"),
        nullable=False,
    )
    step_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Order of step in saga (0-indexed)",
    )
    step_name: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="Human-readable step name",
    )

    status: Mapped[StepStatus] = mapped_column(
        Enum(StepStatus, name="saga_step_status", schema="platform"),
        nullable=False,
        default=StepStatus.PENDING,
    )

    # Step data
    input_data: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="Input parameters for this step",
    )
    output_data: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="Output data from successful execution",
    )
    compensation_data: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="Data needed for compensation (rollback)",
    )

    # Timestamps
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Error tracking
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    retry_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    # Relationships
    saga: Mapped["SagaExecution"] = relationship(
        "SagaExecution",
        back_populates="steps",
    )
