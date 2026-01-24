"""
Location Model - Core Org.
"""
import enum
import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class LocationType(str, enum.Enum):
    HEAD_OFFICE = "HEAD_OFFICE"
    BRANCH = "BRANCH"
    WAREHOUSE = "WAREHOUSE"
    PLANT = "PLANT"
    REMOTE = "REMOTE"


class GeofenceType(str, enum.Enum):
    """Type of geofence boundary."""
    CIRCLE = "CIRCLE"      # Traditional radius-based
    POLYGON = "POLYGON"    # GeoJSON polygon boundary


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

    # Geolocation (for attendance/geofencing)
    latitude: Mapped[Optional[float]] = mapped_column(Numeric(9, 6), nullable=True)
    longitude: Mapped[Optional[float]] = mapped_column(Numeric(9, 6), nullable=True)
    geofence_radius_m: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=500,
        server_default=text("500"),
    )
    geofence_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )

    # Enhanced GeoJSON polygon geofencing
    geofence_type: Mapped[GeofenceType] = mapped_column(
        Enum(GeofenceType, name="geofence_type_enum"),
        nullable=False,
        default=GeofenceType.CIRCLE,
        server_default=text("'CIRCLE'"),
    )
    geofence_polygon: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=True,
        comment="GeoJSON Polygon or MultiPolygon geometry for complex boundaries",
    )

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
