"""
Holiday List Model - Leave Schema.

Defines company holidays and public holidays.
"""
import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.people.base import AuditMixin, ERPNextSyncMixin

if TYPE_CHECKING:
    pass


class HolidayList(Base, AuditMixin, ERPNextSyncMixin):
    """
    Holiday List - collection of holidays for a year/period.

    Organizations can have multiple holiday lists (e.g., for different regions).
    """

    __tablename__ = "holiday_list"
    __table_args__ = (
        UniqueConstraint("organization_id", "list_code", name="uq_holiday_list_org_code"),
        Index("idx_holiday_list_year", "organization_id", "year"),
        {"schema": "leave"},
    )

    holiday_list_id: Mapped[uuid.UUID] = mapped_column(
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
    list_code: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )
    list_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Period
    year: Mapped[int] = mapped_column(
        nullable=False,
    )
    from_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )
    to_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    # Settings
    weekly_off: Mapped[str] = mapped_column(
        String(50),
        default="Saturday,Sunday",
        comment="Comma-separated list of weekly off days",
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        comment="Default holiday list for the organization",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
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
    holidays: Mapped[list["Holiday"]] = relationship(
        "Holiday",
        back_populates="holiday_list",
        cascade="all, delete-orphan",
        order_by="Holiday.holiday_date",
    )

    @property
    def total_holidays(self) -> int:
        """Count of holidays in this list."""
        return len(self.holidays)

    def __repr__(self) -> str:
        return f"<HolidayList {self.list_code} ({self.year})>"


class Holiday(Base):
    """
    Individual holiday entry within a holiday list.
    """

    __tablename__ = "holiday"
    __table_args__ = (
        UniqueConstraint("holiday_list_id", "holiday_date", name="uq_holiday_list_date"),
        Index("idx_holiday_date", "holiday_list_id", "holiday_date"),
        {"schema": "leave"},
    )

    holiday_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    holiday_list_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("leave.holiday_list.holiday_list_id", ondelete="CASCADE"),
        nullable=False,
    )

    # Holiday details
    holiday_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )
    holiday_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    description: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )

    # Type
    is_public_holiday: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        comment="Public/national holiday vs company-specific",
    )
    is_optional: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        comment="Optional holiday (restricted)",
    )

    # Relationships
    holiday_list: Mapped["HolidayList"] = relationship(
        "HolidayList",
        back_populates="holidays",
    )

    def __repr__(self) -> str:
        return f"<Holiday {self.holiday_date}: {self.holiday_name}>"
