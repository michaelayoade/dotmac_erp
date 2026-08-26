"""Composable weekly schedule workspace models."""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, Date, DateTime, Enum, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.people.base import AuditMixin, VersionMixin
from app.models.people.scheduling.shift_schedule import ScheduleStatus

if TYPE_CHECKING:
    from app.models.people.hr.department import Department
    from app.models.people.scheduling.shift_schedule import ShiftSchedule


class ScheduleAuditAction(str, enum.Enum):
    """Actions captured in schedule history."""

    CREATED = "CREATED"
    ASSIGNED = "ASSIGNED"
    MOVED = "MOVED"
    REMOVED = "REMOVED"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    PUBLISHED = "PUBLISHED"
    AMENDED = "AMENDED"
    POLICY_CHANGED = "POLICY_CHANGED"
    OVERRIDE_RECORDED = "OVERRIDE_RECORDED"


class WorkSchedule(Base, AuditMixin, VersionMixin):
    """Weekly schedule header that owns assignment lifecycle and revisions."""

    __tablename__ = "work_schedule"
    __table_args__ = (
        Index(
            "idx_work_schedule_org_dept_period",
            "organization_id",
            "department_id",
            "period_start",
            "period_end",
        ),
        Index("idx_work_schedule_org_status", "organization_id", "status"),
        UniqueConstraint("organization_id", "department_id", "period_start", "period_end", "revision", name="uq_work_schedule_org_dept_period_revision"),
        {"schema": "scheduling"},
    )

    work_schedule_id: Mapped[uuid.UUID] = mapped_column(
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
    department_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.department.department_id"),
        nullable=False,
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[ScheduleStatus] = mapped_column(
        Enum(ScheduleStatus, name="schedule_status", schema="scheduling"),
        nullable=False,
        default=ScheduleStatus.DRAFT,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    parent_schedule_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scheduling.work_schedule.work_schedule_id"), nullable=True
    )
    submitted_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("people.id"), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("people.id"), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("people.id"), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("people.id"), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, onupdate=func.now())

    department: Mapped["Department"] = relationship("Department", foreign_keys=[department_id])
    assignments: Mapped[list["ShiftSchedule"]] = relationship(
        "ShiftSchedule", foreign_keys="[ShiftSchedule.work_schedule_id]"
    )


class SchedulingPolicy(Base, AuditMixin):
    """Data-driven scheduling rule configuration."""

    __tablename__ = "scheduling_policy"
    __table_args__ = (
        Index("idx_scheduling_policy_org_rule", "organization_id", "rule_key"),
        {"schema": "scheduling"},
    )

    scheduling_policy_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core_org.organization.organization_id"), nullable=False, index=True)
    department_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("hr.department.department_id"), nullable=True)
    rule_key: Mapped[str] = mapped_column(String(80), nullable=False)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="warning", server_default="warning")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, onupdate=func.now())


class ScheduleAuditEvent(Base):
    """Immutable schedule history event."""

    __tablename__ = "schedule_audit_event"
    __table_args__ = (Index("idx_schedule_audit_schedule", "work_schedule_id", "created_at"), {"schema": "scheduling"})

    schedule_audit_event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core_org.organization.organization_id"), nullable=False, index=True)
    work_schedule_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("scheduling.work_schedule.work_schedule_id"), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("people.id"), nullable=True)
    action: Mapped[ScheduleAuditAction] = mapped_column(Enum(ScheduleAuditAction, name="schedule_audit_action", schema="scheduling"), nullable=False)
    previous_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    new_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ScheduleNotificationLog(Base):
    """Idempotency log for schedule publication notifications."""

    __tablename__ = "schedule_notification_log"
    __table_args__ = (
        Index("uq_schedule_notification_revision_employee", "organization_id", "work_schedule_id", "revision", "employee_id", unique=True),
        {"schema": "scheduling"},
    )

    schedule_notification_log_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("core_org.organization.organization_id"), nullable=False, index=True)
    work_schedule_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("scheduling.work_schedule.work_schedule_id"), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("hr.employee.employee_id"), nullable=False)
    notification_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
