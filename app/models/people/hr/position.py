"""
Position Model - HR Schema.

First-class organizational positions independent of employee incumbents.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Index, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.people.base import AuditMixin

if TYPE_CHECKING:
    from app.models.finance.core_org.organization import Organization
    from app.models.people.hr.department import Department
    from app.models.people.hr.designation import Designation
    from app.models.people.hr.position_assignment import PositionAssignment


class Position(Base, AuditMixin):
    """
    Organizational position.

    Positions form the reporting hierarchy. Employees occupy positions through
    PositionAssignment rows, so reporting can survive vacancies and transfers.
    """

    __tablename__ = "position"
    __table_args__ = (
        Index("idx_hr_position_org_parent", "organization_id", "parent_position_id"),
        Index("idx_hr_position_org_department", "organization_id", "department_id"),
        Index("idx_hr_position_org_designation", "organization_id", "designation_id"),
        {"schema": "hr"},
    )

    position_id: Mapped[uuid.UUID] = mapped_column(
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
    designation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.designation.designation_id"),
        nullable=True,
    )
    parent_position_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.position.position_id"),
        nullable=True,
    )
    department_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.department.department_id"),
        nullable=True,
    )
    is_vacant: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )
    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
        onupdate=func.now(),
    )

    organization: Mapped[Organization] = relationship(
        "Organization",
        foreign_keys=[organization_id],
    )
    designation: Mapped[Designation | None] = relationship(
        "Designation",
        foreign_keys=[designation_id],
    )
    department: Mapped[Department | None] = relationship(
        "Department",
        foreign_keys=[department_id],
    )
    parent_position: Mapped[Position | None] = relationship(
        "Position",
        remote_side=[position_id],
        foreign_keys=[parent_position_id],
        back_populates="child_positions",
    )
    child_positions: Mapped[list[Position]] = relationship(
        "Position",
        back_populates="parent_position",
        foreign_keys=[parent_position_id],
    )
    assignments: Mapped[list[PositionAssignment]] = relationship(
        "PositionAssignment",
        back_populates="position",
        foreign_keys="[PositionAssignment.position_id]",
    )
