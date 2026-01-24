"""
Workflow Rule Model.

Defines rules for automated workflow triggers and actions.
"""
import enum
import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
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


class WorkflowEntityType(str, enum.Enum):
    """Entity types that can trigger workflows."""
    INVOICE = "INVOICE"
    BILL = "BILL"
    EXPENSE = "EXPENSE"
    JOURNAL = "JOURNAL"
    PAYMENT = "PAYMENT"
    CUSTOMER = "CUSTOMER"
    SUPPLIER = "SUPPLIER"
    QUOTE = "QUOTE"
    SALES_ORDER = "SALES_ORDER"
    PURCHASE_ORDER = "PURCHASE_ORDER"
    BANK_TRANSACTION = "BANK_TRANSACTION"
    RECONCILIATION = "RECONCILIATION"


class TriggerEvent(str, enum.Enum):
    """Events that can trigger a workflow."""
    ON_CREATE = "ON_CREATE"
    ON_UPDATE = "ON_UPDATE"
    ON_DELETE = "ON_DELETE"
    ON_STATUS_CHANGE = "ON_STATUS_CHANGE"
    ON_FIELD_CHANGE = "ON_FIELD_CHANGE"
    ON_APPROVAL = "ON_APPROVAL"
    ON_REJECTION = "ON_REJECTION"
    ON_DUE_DATE = "ON_DUE_DATE"
    ON_OVERDUE = "ON_OVERDUE"
    ON_THRESHOLD = "ON_THRESHOLD"


class ActionType(str, enum.Enum):
    """Types of actions that can be executed."""
    SEND_EMAIL = "SEND_EMAIL"
    SEND_NOTIFICATION = "SEND_NOTIFICATION"
    VALIDATE = "VALIDATE"
    UPDATE_FIELD = "UPDATE_FIELD"
    CREATE_TASK = "CREATE_TASK"
    WEBHOOK = "WEBHOOK"
    BLOCK = "BLOCK"


class WorkflowRule(Base):
    """
    Workflow automation rule.

    Defines what triggers a workflow and what action to take.
    """

    __tablename__ = "workflow_rule"
    __table_args__ = (
        UniqueConstraint("organization_id", "rule_name", name="uq_workflow_rule_name"),
        Index("idx_workflow_rule_org", "organization_id"),
        Index("idx_workflow_rule_entity", "entity_type"),
        Index("idx_workflow_rule_trigger", "trigger_event"),
        Index("idx_workflow_rule_active", "is_active"),
        {"schema": "automation"},
    )

    rule_id: Mapped[uuid.UUID] = mapped_column(
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

    # Rule identification
    rule_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Trigger configuration
    entity_type: Mapped[WorkflowEntityType] = mapped_column(
        Enum(WorkflowEntityType, name="workflow_entity_type"),
        nullable=False,
    )
    trigger_event: Mapped[TriggerEvent] = mapped_column(
        Enum(TriggerEvent, name="workflow_trigger_event"),
        nullable=False,
    )
    trigger_conditions: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default="{}",
        comment="Conditions that must be met: field comparisons, status values, etc.",
    )

    # Action configuration
    action_type: Mapped[ActionType] = mapped_column(
        Enum(ActionType, name="workflow_action_type"),
        nullable=False,
    )
    action_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default="{}",
        comment="Action-specific config: email template, recipients, webhook URL, etc.",
    )

    # Execution settings
    priority: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=100,
        comment="Lower number = higher priority",
    )
    stop_on_match: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="Stop evaluating other rules if this one matches",
    )
    execute_async: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        comment="Execute action asynchronously via Celery",
    )

    # Statistics
    execution_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    success_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    failure_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    last_executed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Status
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    # Audit
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )

    # Relationships
    executions: Mapped[list["WorkflowExecution"]] = relationship(
        "WorkflowExecution",
        back_populates="rule",
        cascade="all, delete-orphan",
        order_by="desc(WorkflowExecution.triggered_at)",
    )


# Forward reference
from app.models.finance.automation.workflow_execution import WorkflowExecution  # noqa: E402
