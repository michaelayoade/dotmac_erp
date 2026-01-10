"""
Location Model - Core Org.
"""
import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class LocationType(str, enum.Enum):
    HEAD_OFFICE = "HEAD_OFFICE"
    BRANCH = "BRANCH"
    WAREHOUSE = "WAREHOUSE"
    PLANT = "PLANT"
    REMOTE = "REMOTE"


class Location(Base):
    """
    Physical location entity.
    """

    __tablename__ = "location"
    __table_args__ = (
        UniqueConstraint("organization_id", "location_code", name="uq_location_code"),
        {"schema": "core_org"},
    )

    location_id: Mapped[uuid.UUID] = mapped_column(
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

    location_code: Mapped[str] = mapped_column(String(20), nullable=False)
    location_name: Mapped[str] = mapped_column(String(100), nullable=False)
    location_type: Mapped[Optional[LocationType]] = mapped_column(
        Enum(LocationType, name="location_type"),
        nullable=True,
    )

    # Address
    address_line_1: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    address_line_2: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    city: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    state_province: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    postal_code: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    country_code: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
