"""Durable ERP vehicle-to-tracker mappings."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.fleet.base import FleetBaseMixin

if TYPE_CHECKING:
    from app.models.fleet.vehicle import Vehicle


class VehicleTracker(Base, FleetBaseMixin):
    """Assignment history for an external tracking device and ERP vehicle.

    ``unique_id`` is the device IMEI / Traccar ``uniqueId``. It is deliberately
    separate from ``external_device_id``, which is Traccar's internal device ID.
    Telemetry and position history remain owned by Traccar.
    """

    __tablename__ = "vehicle_tracker"
    __table_args__ = (
        ForeignKeyConstraint(
            ["vehicle_id", "organization_id"],
            ["fleet.vehicle.vehicle_id", "fleet.vehicle.organization_id"],
            name="fk_fleet_vehicle_tracker_vehicle_org",
            ondelete="CASCADE",
        ),
        Index(
            "uq_fleet_vehicle_tracker_active_vehicle",
            "organization_id",
            "vehicle_id",
            unique=True,
            postgresql_where=text("is_active"),
        ),
        Index(
            "uq_fleet_vehicle_tracker_active_unique_id",
            "organization_id",
            "provider",
            "unique_id",
            unique=True,
            postgresql_where=text("is_active"),
        ),
        Index(
            "uq_fleet_vehicle_tracker_active_external_id",
            "organization_id",
            "provider",
            "external_device_id",
            unique=True,
            postgresql_where=text("is_active AND external_device_id IS NOT NULL"),
        ),
        Index(
            "idx_fleet_vehicle_tracker_vehicle_history",
            "organization_id",
            "vehicle_id",
            "assigned_at",
        ),
        CheckConstraint(
            "length(trim(provider)) > 0",
            name="ck_fleet_vehicle_tracker_provider_nonempty",
        ),
        CheckConstraint(
            "length(trim(unique_id)) > 0",
            name="ck_fleet_vehicle_tracker_unique_id_nonempty",
        ),
        CheckConstraint(
            "(is_active AND unassigned_at IS NULL) OR "
            "(NOT is_active AND unassigned_at IS NOT NULL)",
            name="ck_fleet_vehicle_tracker_assignment_state",
        ),
        {"schema": "fleet"},
    )

    tracker_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="TRACCAR",
        server_default="TRACCAR",
    )
    unique_id: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="Tracker IMEI / Traccar uniqueId",
    )
    external_device_id: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="Traccar internal device ID",
    )
    tracker_manufacturer: Mapped[str | None] = mapped_column(String(100))
    tracker_model: Mapped[str | None] = mapped_column(String(100))
    protocol: Mapped[str | None] = mapped_column(String(50))
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    unassigned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    vehicle: Mapped["Vehicle"] = relationship(
        "Vehicle",
        back_populates="trackers",
    )
