"""
Vehicle Model - Fleet Schema.

Core entity representing vehicles in the organization's fleet.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.finance.core_org.location import Location
from app.models.fleet.base import FleetBaseMixin
from app.models.fleet.enums import (
    AssignmentType,
    DisposalMethod,
    FuelType,
    OwnershipType,
    VehicleStatus,
    VehicleType,
)
from app.models.people.base import AuditMixin, SoftDeleteMixin

if TYPE_CHECKING:
    from app.models.fleet.fuel_log import FuelLogEntry
    from app.models.fleet.maintenance import MaintenanceRecord
    from app.models.fleet.vehicle_assignment import VehicleAssignment
    from app.models.fleet.vehicle_document import VehicleDocument
    from app.models.fleet.vehicle_incident import VehicleIncident
    from app.models.fleet.vehicle_reservation import VehicleReservation
    from app.models.people.hr.employee import Employee


class Vehicle(Base, FleetBaseMixin, AuditMixin, SoftDeleteMixin):
    """
    Organization vehicle registry.

    Represents a single vehicle in the fleet with its specifications,
    ownership details, current status, and assignment information.

    Supports:
    - Multiple ownership types (owned, leased, rented)
    - Assignment to employees, departments, or pool
    - GPS/telematics integration
    - Full lifecycle tracking from acquisition to disposal
    """

    __tablename__ = "vehicle"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "registration_number",
            name="uq_fleet_vehicle_org_reg",
        ),
        UniqueConstraint(
            "organization_id",
            "vehicle_code",
            name="uq_fleet_vehicle_org_code",
        ),
        Index("idx_fleet_vehicle_status", "organization_id", "status"),
        Index("idx_fleet_vehicle_type", "organization_id", "vehicle_type"),
        Index("idx_fleet_vehicle_assignment", "organization_id", "assignment_type"),
        Index("idx_fleet_vehicle_employee", "assigned_employee_id"),
        CheckConstraint(
            "current_odometer >= 0",
            name="ck_fleet_vehicle_odometer_positive",
        ),
        {"schema": "fleet"},
    )

    # Primary key
    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # ─────────────────────────────────────────────────────────────
    # Identifiers
    # ─────────────────────────────────────────────────────────────
    vehicle_code: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        comment="Internal fleet code (e.g., FLT-001)",
    )
    registration_number: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="License plate / registration number",
    )
    vin: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment="Vehicle Identification Number (chassis number)",
    )
    engine_number: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment="Engine serial number",
    )

    # ─────────────────────────────────────────────────────────────
    # Specifications
    # ─────────────────────────────────────────────────────────────
    make: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="Manufacturer (e.g., Toyota, Ford)",
    )
    model: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="Model name (e.g., Camry, F-150)",
    )
    year: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Manufacturing year",
    )
    color: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
    )
    vehicle_type: Mapped[VehicleType] = mapped_column(
        default=VehicleType.SEDAN,
    )
    fuel_type: Mapped[FuelType] = mapped_column(
        default=FuelType.PETROL,
    )
    transmission: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        comment="MANUAL, AUTOMATIC, CVT, etc.",
    )
    engine_capacity_cc: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Engine capacity in cubic centimeters",
    )
    seating_capacity: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=5,
    )
    fuel_tank_capacity_liters: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 2),
        nullable=True,
    )
    expected_fuel_efficiency: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 2),
        nullable=True,
        comment="Expected km/liter",
    )

    # ─────────────────────────────────────────────────────────────
    # Ownership
    # ─────────────────────────────────────────────────────────────
    ownership_type: Mapped[OwnershipType] = mapped_column(
        default=OwnershipType.OWNED,
    )
    purchase_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )
    purchase_price: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2),
        nullable=True,
    )
    lease_start_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )
    lease_end_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )
    lease_monthly_cost: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2),
        nullable=True,
    )
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ap.supplier.supplier_id"),
        nullable=True,
        comment="Dealer or leasing company",
    )
    license_expiry_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment="Vehicle license expiry date",
    )
    location_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.location.location_id"),
        nullable=True,
        comment="Branch/location where the vehicle is based",
    )

    # ─────────────────────────────────────────────────────────────
    # Assignment
    # ─────────────────────────────────────────────────────────────
    assignment_type: Mapped[AssignmentType] = mapped_column(
        default=AssignmentType.POOL,
    )
    assigned_employee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=True,
        comment="Current assigned employee (for PERSONAL type)",
    )
    assigned_department_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.department.department_id"),
        nullable=True,
        comment="Current assigned department",
    )
    assigned_cost_center_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.cost_center.cost_center_id"),
        nullable=True,
    )

    # ─────────────────────────────────────────────────────────────
    # Status & Tracking
    # ─────────────────────────────────────────────────────────────
    status: Mapped[VehicleStatus] = mapped_column(
        default=VehicleStatus.ACTIVE,
    )
    current_odometer: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="Current odometer reading in kilometers",
    )
    last_odometer_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    # GPS/Telematics (optional integration)
    has_gps_tracker: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    gps_device_id: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )
    last_known_location: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )
    last_location_update: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # ─────────────────────────────────────────────────────────────
    # Disposal
    # ─────────────────────────────────────────────────────────────
    disposal_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )
    disposal_method: Mapped[DisposalMethod | None] = mapped_column(
        nullable=True,
    )
    disposal_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2),
        nullable=True,
        comment="Sale/scrap value received",
    )
    disposal_notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # ─────────────────────────────────────────────────────────────
    # Notes
    # ─────────────────────────────────────────────────────────────
    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # ─────────────────────────────────────────────────────────────
    # Relationships
    # ─────────────────────────────────────────────────────────────
    assigned_employee: Mapped[Optional["Employee"]] = relationship(
        "Employee",
        foreign_keys=[assigned_employee_id],
        lazy="joined",
    )
    location: Mapped[Optional["Location"]] = relationship(
        "Location",
        foreign_keys=[location_id],
        lazy="joined",
    )

    assignments: Mapped[list["VehicleAssignment"]] = relationship(
        "VehicleAssignment",
        back_populates="vehicle",
        cascade="all, delete-orphan",
        order_by="desc(VehicleAssignment.start_date)",
        lazy="selectin",
    )

    documents: Mapped[list["VehicleDocument"]] = relationship(
        "VehicleDocument",
        back_populates="vehicle",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    maintenance_records: Mapped[list["MaintenanceRecord"]] = relationship(
        "MaintenanceRecord",
        back_populates="vehicle",
        cascade="all, delete-orphan",
        order_by="desc(MaintenanceRecord.scheduled_date)",
        lazy="dynamic",
    )

    fuel_logs: Mapped[list["FuelLogEntry"]] = relationship(
        "FuelLogEntry",
        back_populates="vehicle",
        cascade="all, delete-orphan",
        order_by="desc(FuelLogEntry.log_date)",
        lazy="dynamic",
    )

    incidents: Mapped[list["VehicleIncident"]] = relationship(
        "VehicleIncident",
        back_populates="vehicle",
        cascade="all, delete-orphan",
        order_by="desc(VehicleIncident.incident_date)",
        lazy="dynamic",
    )

    reservations: Mapped[list["VehicleReservation"]] = relationship(
        "VehicleReservation",
        back_populates="vehicle",
        cascade="all, delete-orphan",
        order_by="desc(VehicleReservation.start_datetime)",
        lazy="dynamic",
    )

    # ─────────────────────────────────────────────────────────────
    # Computed Properties
    # ─────────────────────────────────────────────────────────────
    @property
    def display_name(self) -> str:
        """Human-readable vehicle identifier."""
        return f"{self.year} {self.make} {self.model} ({self.registration_number})"

    @property
    def short_name(self) -> str:
        """Short identifier for lists."""
        return f"{self.make} {self.model} - {self.registration_number}"

    @property
    def age_years(self) -> int:
        """Vehicle age in years."""
        return date.today().year - self.year

    @property
    def is_leased(self) -> bool:
        """Check if vehicle is leased."""
        return self.ownership_type == OwnershipType.LEASED

    @property
    def is_available(self) -> bool:
        """Check if vehicle is available for use."""
        return self.status == VehicleStatus.ACTIVE

    @property
    def is_pool_vehicle(self) -> bool:
        """Check if vehicle is in the pool for reservations."""
        return self.assignment_type == AssignmentType.POOL
