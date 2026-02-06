"""
Business Unit Model - Core Org.
"""

import enum
import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
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
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class BusinessUnitType(str, enum.Enum):
    BRANCH = "BRANCH"
    DIVISION = "DIVISION"
    DEPARTMENT = "DEPARTMENT"
    COST_CENTER = "COST_CENTER"
    PROFIT_CENTER = "PROFIT_CENTER"


class BusinessUnit(Base):
    """
    Business unit for organizational hierarchy.
    """

    __tablename__ = "business_unit"
    __table_args__ = (
        UniqueConstraint("organization_id", "unit_code", name="uq_bu_code"),
        Index("idx_bu_parent", "parent_unit_id"),
        Index("idx_bu_path", "hierarchy_path"),
        {"schema": "core_org"},
    )

    business_unit_id: Mapped[uuid.UUID] = mapped_column(
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

    unit_code: Mapped[str] = mapped_column(String(20), nullable=False)
    unit_name: Mapped[str] = mapped_column(String(100), nullable=False)
    unit_type: Mapped[BusinessUnitType] = mapped_column(
        Enum(BusinessUnitType, name="business_unit_type"),
        nullable=False,
    )

    # Hierarchy
    parent_unit_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.business_unit.business_unit_id"),
        nullable=True,
    )
    hierarchy_level: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    hierarchy_path: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Materialized path: /root/parent/child/",
    )

    # Management
    manager_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Effective dating
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    effective_from: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        server_default=func.current_date(),
    )
    effective_to: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )

    # Relationships
    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="business_units",
    )
    parent_unit: Mapped[Optional["BusinessUnit"]] = relationship(
        "BusinessUnit",
        remote_side=[business_unit_id],
        foreign_keys=[parent_unit_id],
    )
    child_units: Mapped[list["BusinessUnit"]] = relationship(
        "BusinessUnit",
        back_populates="parent_unit",
    )


# Forward reference
from app.models.finance.core_org.organization import Organization  # noqa: E402
