"""
Department performance template model.

Stores reusable department-level KRA/KPI defaults that can be expanded into
employee KPIs for a review period.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
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
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.people.base import AuditMixin


class DepartmentPerformanceTemplate(Base, AuditMixin):
    """Department-level KRA/KPI template."""

    __tablename__ = "department_performance_template"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "kpi_code",
            name="uq_dept_perf_template_code",
        ),
        CheckConstraint(
            "weightage >= 0 AND weightage <= 100",
            name="ck_dept_perf_template_weight",
        ),
        CheckConstraint(
            "green_threshold >= amber_threshold",
            name="ck_dept_perf_template_threshold_order",
        ),
        Index(
            "idx_dept_perf_template_dept",
            "organization_id",
            "department_id",
            "is_active",
        ),
        {"schema": "perf"},
    )

    template_id: Mapped[uuid.UUID] = mapped_column(
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

    # Stable, administrator-owned identity. KPI behavior is never inferred
    # from a department name or the display name below.
    kpi_code: Mapped[str] = mapped_column(String(50), nullable=False)

    kra_name: Mapped[str] = mapped_column(String(200), nullable=False)
    kpi_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    measurement_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="NUMBER"
    )
    direction: Mapped[str] = mapped_column(
        String(20), nullable=False, default="HIGHER_IS_BETTER"
    )
    frequency: Mapped[str] = mapped_column(
        String(20), nullable=False, default="MONTHLY"
    )
    calculation_method: Mapped[str] = mapped_column(
        String(30), nullable=False, default="RATIO"
    )
    target_value: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    unit_of_measure: Mapped[str | None] = mapped_column(String(30), nullable=True)
    weightage: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        nullable=False,
        default=Decimal("0.00"),
    )
    scorecard_perspective: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="PROCESS",
    )
    metric_source_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lower_is_better: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    green_threshold: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("95.00")
    )
    amber_threshold: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("80.00")
    )
    band_min_value: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    band_max_value: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )

    # The existing ERP hierarchy is reused: a KPI always belongs to a
    # department and may be narrowed to a designation, position, or employee.
    assignment_scope: Mapped[str] = mapped_column(
        String(20), nullable=False, default="DEPARTMENT"
    )
    designation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.designation.designation_id"),
        nullable=True,
    )
    position_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.position.position_id"),
        nullable=True,
    )
    employee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=True,
    )
    effective_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    config_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
        onupdate=func.now(),
    )

    department = relationship("Department")
    designation = relationship("Designation")
    position = relationship("Position")
    employee = relationship("Employee")
