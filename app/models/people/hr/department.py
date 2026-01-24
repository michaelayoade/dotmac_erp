"""
Department Model - HR Schema.

Organizational departments for employee grouping and reporting.
"""
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, ForeignKey, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.people.base import AuditMixin, ERPNextSyncMixin, SoftDeleteMixin

if TYPE_CHECKING:
    from app.models.people.hr.employee import Employee
    from app.models.finance.core_org.organization import Organization
    from app.models.finance.core_org.cost_center import CostCenter


class Department(Base, AuditMixin, SoftDeleteMixin, ERPNextSyncMixin):
    """
    Department entity for organizational structure.

    Departments can have a parent department for hierarchical structures.
    Each department can be linked to a cost center for GL posting.
    """

    __tablename__ = "department"
    __table_args__ = {"schema": "hr"}

    department_id: Mapped[uuid.UUID] = mapped_column(
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

    # Department identification
    department_code: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        index=True,
    )
    department_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Hierarchy
    parent_department_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.department.department_id"),
        nullable=True,
    )

    # GL Integration
    cost_center_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.cost_center.cost_center_id"),
        nullable=True,
    )

    # Status
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        nullable=True,
        onupdate=func.now(),
    )

    # Relationships
    organization: Mapped["Organization"] = relationship(
        "Organization",
        foreign_keys=[organization_id],
    )
    parent_department: Mapped[Optional["Department"]] = relationship(
        "Department",
        remote_side=[department_id],
        foreign_keys=[parent_department_id],
    )
    child_departments: Mapped[list["Department"]] = relationship(
        "Department",
        back_populates="parent_department",
        foreign_keys=[parent_department_id],
    )
    cost_center: Mapped[Optional["CostCenter"]] = relationship(
        "CostCenter",
        foreign_keys=[cost_center_id],
    )
    employees: Mapped[list["Employee"]] = relationship(
        "Employee",
        back_populates="department",
        foreign_keys="Employee.department_id",
    )
