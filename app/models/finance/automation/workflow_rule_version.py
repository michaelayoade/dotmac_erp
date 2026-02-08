"""
Workflow Rule Version Model.

Stores a full snapshot of a workflow rule's configuration
whenever it is updated, providing an audit trail of changes.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class WorkflowRuleVersion(Base):
    """
    Immutable snapshot of a workflow rule at a point in time.

    Created automatically by ``WorkflowService.update_rule()``
    before applying changes, so the previous state is preserved.
    """

    __tablename__ = "workflow_rule_version"
    __table_args__ = (
        Index("idx_rule_version_rule", "rule_id"),
        Index("idx_rule_version_created", "created_at"),
        {"schema": "automation"},
    )

    version_id: Mapped[uuid.UUID] = mapped_column(
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
    version_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Sequential version counter per rule",
    )

    # Full snapshot of rule configuration at this version
    rule_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    trigger_event: Mapped[str] = mapped_column(String(50), nullable=False)
    trigger_conditions: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    action_type: Mapped[str] = mapped_column(String(50), nullable=False)
    action_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    cooldown_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    schedule_config: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # Audit
    changed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    change_summary: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Human-readable description of what changed",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
