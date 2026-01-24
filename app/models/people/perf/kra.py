"""
KRA (Key Result Area) Model - Performance Schema.

Defines areas of responsibility and goals.
"""
import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
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
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.people.base import AuditMixin, ERPNextSyncMixin

if TYPE_CHECKING:
    pass


class KRA(Base, AuditMixin, ERPNextSyncMixin):
    """
    KRA - Key Result Area.

    Defines measurable areas of responsibility.
    Can be organization-wide, department-specific, or designation-specific.
    """

    __tablename__ = "kra"
    __table_args__ = (
        UniqueConstraint("organization_id", "kra_code", name="uq_kra_code"),
        Index("idx_kra_dept", "organization_id", "department_id"),
        Index("idx_kra_desig", "organization_id", "designation_id"),
        {"schema": "perf"},
    )

    kra_id: Mapped[uuid.UUID] = mapped_column(
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

    # Identification
    kra_code: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )
    kra_name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Scope (can be org-wide, department, or designation specific)
    department_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.department.department_id"),
        nullable=True,
        comment="If set, KRA applies to this department only",
    )
    designation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.designation.designation_id"),
        nullable=True,
        comment="If set, KRA applies to this designation only",
    )

    # Weighting
    default_weightage: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        default=Decimal("0.00"),
        comment="Default weight percentage (0-100)",
    )

    # Category
    category: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        comment="PERFORMANCE, BEHAVIOR, SKILL, LEARNING, etc.",
    )

    # Measurement
    measurement_criteria: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="How this KRA is measured",
    )

    # Status
    is_active: Mapped[bool] = mapped_column(
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

    def __repr__(self) -> str:
        return f"<KRA {self.kra_code}: {self.kra_name}>"
