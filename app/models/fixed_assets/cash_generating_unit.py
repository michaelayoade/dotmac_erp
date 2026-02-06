"""
Cash Generating Unit Model - FA Schema.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
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


class CashGeneratingUnit(Base):
    """
    Cash generating unit for impairment testing (IAS 36).
    """

    __tablename__ = "cash_generating_unit"
    __table_args__ = (
        UniqueConstraint("organization_id", "cgu_code", name="uq_cgu"),
        {"schema": "fa"},
    )

    cgu_id: Mapped[uuid.UUID] = mapped_column(
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

    cgu_code: Mapped[str] = mapped_column(String(30), nullable=False)
    cgu_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Hierarchy
    parent_cgu_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fa.cash_generating_unit.cgu_id"),
        nullable=True,
    )

    # Linkage to organizational structure
    business_unit_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.business_unit.business_unit_id"),
        nullable=True,
    )
    reporting_segment_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.reporting_segment.segment_id"),
        nullable=True,
    )

    # Goodwill allocation
    allocated_goodwill: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )

    # Latest impairment test results
    last_test_date: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    recoverable_amount: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 6),
        nullable=True,
    )
    carrying_amount: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 6),
        nullable=True,
    )

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

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
