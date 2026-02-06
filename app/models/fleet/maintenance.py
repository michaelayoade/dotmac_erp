"""
Maintenance Record Model - Fleet Schema.

Tracks vehicle maintenance and service records.
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Date, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.fleet.base import FleetBaseMixin
from app.models.fleet.enums import MaintenanceStatus, MaintenanceType
from app.models.people.base import AuditMixin

if TYPE_CHECKING:
    from app.models.fleet.vehicle import Vehicle
    from app.models.finance.ap.supplier import Supplier


class MaintenanceRecord(Base, FleetBaseMixin, AuditMixin):
    """
    Vehicle maintenance and service record.

    Tracks both scheduled preventive maintenance and unscheduled repairs.
    Supports:
    - Scheduling future maintenance
    - Recording work performed and parts replaced
    - Cost tracking and supplier invoice linking
    - Next service reminders based on date or odometer
    """

    __tablename__ = "maintenance_record"
    __table_args__ = (
        Index("idx_fleet_maint_vehicle_date", "vehicle_id", "scheduled_date"),
        Index("idx_fleet_maint_status", "organization_id", "status"),
        Index("idx_fleet_maint_type", "organization_id", "maintenance_type"),
        Index(
            "idx_fleet_maint_scheduled", "organization_id", "status", "scheduled_date"
        ),
        {"schema": "fleet"},
    )

    # Primary key
    maintenance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # Reference
    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fleet.vehicle.vehicle_id"),
        nullable=False,
    )

    # Maintenance details
    maintenance_type: Mapped[MaintenanceType] = mapped_column(
        nullable=False,
    )
    description: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        comment="Brief description of maintenance needed/performed",
    )
    scheduled_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )
    completed_date: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
    )
    status: Mapped[MaintenanceStatus] = mapped_column(
        default=MaintenanceStatus.SCHEDULED,
    )

    # Odometer tracking
    odometer_at_service: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Odometer reading when service performed",
    )
    next_service_odometer: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Odometer reading for next service (e.g., +5000km)",
    )
    next_service_date: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
        comment="Recommended date for next service",
    )

    # Cost tracking
    estimated_cost: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(18, 2),
        nullable=True,
    )
    actual_cost: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(18, 2),
        nullable=True,
    )
    supplier_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ap.supplier.supplier_id"),
        nullable=True,
        comment="Service provider / garage",
    )
    invoice_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ap.supplier_invoice.invoice_id"),
        nullable=True,
        comment="Link to AP invoice for this service",
    )
    invoice_number: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        comment="External invoice/receipt number",
    )

    # Work details
    work_performed: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Description of work actually performed",
    )
    parts_replaced: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="List of parts replaced",
    )
    technician_name: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
    )

    # Notes
    notes: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Relationships
    vehicle: Mapped["Vehicle"] = relationship(
        "Vehicle",
        back_populates="maintenance_records",
    )
    supplier: Mapped[Optional["Supplier"]] = relationship(
        "Supplier",
        foreign_keys=[supplier_id],
        lazy="joined",
    )

    @property
    def is_overdue(self) -> bool:
        """Check if maintenance is overdue."""
        if self.status in (MaintenanceStatus.COMPLETED, MaintenanceStatus.CANCELLED):
            return False
        return date.today() > self.scheduled_date

    @property
    def cost_variance(self) -> Optional[Decimal]:
        """Calculate difference between estimated and actual cost."""
        if self.estimated_cost is not None and self.actual_cost is not None:
            return self.actual_cost - self.estimated_cost
        return None

    @property
    def is_under_budget(self) -> Optional[bool]:
        """Check if actual cost is under estimate."""
        variance = self.cost_variance
        if variance is not None:
            return variance <= 0
        return None
