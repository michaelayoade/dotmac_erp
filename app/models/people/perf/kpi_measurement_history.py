"""Immutable audit history for KPI measurements and configuration changes."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
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
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.people.base import AuditMixin


class KPIMeasurementHistory(Base, AuditMixin):
    """One append-only revision of an employee KPI measurement."""

    __tablename__ = "kpi_measurement_history"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "kpi_id",
            "revision",
            name="uq_kpi_measurement_history_revision",
        ),
        Index(
            "idx_kpi_measurement_history_period",
            "organization_id",
            "department_template_id",
            "period_start",
            "period_end",
        ),
        Index(
            "idx_kpi_measurement_history_employee",
            "organization_id",
            "employee_id",
            "recorded_at",
        ),
        {"schema": "perf"},
    )

    history_id: Mapped[uuid.UUID] = mapped_column(
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
    kpi_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("perf.kpi.kpi_id"), nullable=False
    )
    department_template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("perf.department_performance_template.template_id"),
        nullable=True,
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hr.employee.employee_id"), nullable=False
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_actual_value: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    actual_value: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    score: Mapped[Decimal | None] = mapped_column(Numeric(7, 2), nullable=True)
    weighted_score: Mapped[Decimal | None] = mapped_column(Numeric(7, 2), nullable=True)
    performance_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    measurement_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, default="MANUAL"
    )
    source_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    approval_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="SUBMITTED"
    )
    submitted_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id"), nullable=True
    )
    approved_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    config_snapshot: Mapped[dict] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=False
    )
    is_recalculation: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    kpi = relationship("KPI")
    department_template = relationship("DepartmentPerformanceTemplate")
    employee = relationship("Employee")


class KPIConfigurationAudit(Base):
    """Append-only before/after record for KPI configuration changes."""

    __tablename__ = "kpi_configuration_audit"
    __table_args__ = (
        Index(
            "idx_kpi_configuration_audit_entity",
            "organization_id",
            "template_id",
            "changed_at",
        ),
        {"schema": "perf"},
    )

    audit_id: Mapped[uuid.UUID] = mapped_column(
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
    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("perf.department_performance_template.template_id"),
        nullable=False,
    )
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    previous_values: Mapped[dict | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=True
    )
    new_values: Mapped[dict] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=False
    )
    changed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id"), nullable=True
    )
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    template = relationship("DepartmentPerformanceTemplate")
