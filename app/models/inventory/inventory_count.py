"""
Inventory Count Model - Inventory Schema.
"""
import enum
import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class CountStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    POSTED = "POSTED"
    CANCELLED = "CANCELLED"


class InventoryCount(Base):
    """
    Inventory count/physical inventory header.
    """

    __tablename__ = "inventory_count"
    __table_args__ = (
        UniqueConstraint("organization_id", "count_number", name="uq_inventory_count"),
        {"schema": "inv"},
    )

    count_id: Mapped[uuid.UUID] = mapped_column(
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

    count_number: Mapped[str] = mapped_column(String(30), nullable=False)
    count_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    count_date: Mapped[date] = mapped_column(Date, nullable=False)
    fiscal_period_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.fiscal_period.fiscal_period_id"),
        nullable=False,
    )

    # Scope
    warehouse_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inv.warehouse.warehouse_id"),
        nullable=True,
    )
    location_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    category_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    is_full_count: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_cycle_count: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    status: Mapped[CountStatus] = mapped_column(
        Enum(CountStatus, name="count_status"),
        nullable=False,
        default=CountStatus.DRAFT,
    )

    # Statistics
    total_items: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    items_counted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    items_with_variance: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Adjustment journal
    adjustment_journal_entry_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # SoD tracking
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    approved_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    posted_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    posted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

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
