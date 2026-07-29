"""
Department performance template model.

Stores reusable department-level KRA/KPI defaults that can be expanded into
employee KPIs for a review period.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
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
            "department_id",
            "kra_name",
            "kpi_name",
            name="uq_dept_perf_template_kpi",
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

    kra_name: Mapped[str] = mapped_column(String(200), nullable=False)
    kpi_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
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
