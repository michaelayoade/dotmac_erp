"""
Vehicle Incident Model - Fleet Schema.

Tracks vehicle incidents: accidents, theft, violations, etc.
"""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.fleet.base import FleetBaseMixin
from app.models.fleet.enums import IncidentSeverity, IncidentStatus, IncidentType
from app.models.people.base import AuditMixin, SoftDeleteMixin

if TYPE_CHECKING:
    from app.models.fleet.vehicle import Vehicle
    from app.models.people.hr.employee import Employee


class VehicleIncident(Base, FleetBaseMixin, AuditMixin, SoftDeleteMixin):
    """
    Vehicle incident record.

    Tracks accidents, theft, vandalism, and traffic violations.
    Supports:
    - Incident details and description
    - Police and insurance claim tracking
    - Cost tracking (repair, insurance payout)
    - Link to expense claims for costs
    - Full investigation workflow
    """

    __tablename__ = "vehicle_incident"
    __table_args__ = (
        Index("idx_fleet_incident_vehicle_date", "vehicle_id", "incident_date"),
        Index("idx_fleet_incident_status", "organization_id", "status"),
        Index("idx_fleet_incident_type", "organization_id", "incident_type"),
        Index("idx_fleet_incident_driver", "driver_id"),
        {"schema": "fleet"},
    )

    # Primary key
    incident_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # References
    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fleet.vehicle.vehicle_id"),
        nullable=False,
    )
    reported_by_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=False,
        comment="Employee who reported the incident",
    )
    driver_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=True,
        comment="Driver at the time of incident",
    )

    # Incident details
    incident_type: Mapped[IncidentType] = mapped_column(
        nullable=False,
    )
    severity: Mapped[IncidentSeverity] = mapped_column(
        nullable=False,
    )
    incident_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )
    incident_time: Mapped[Optional[str]] = mapped_column(
        String(10),
        nullable=True,
        comment="Time of incident (HH:MM format)",
    )
    location: Mapped[Optional[str]] = mapped_column(
        String(300),
        nullable=True,
    )
    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    # Status workflow
    status: Mapped[IncidentStatus] = mapped_column(
        default=IncidentStatus.REPORTED,
    )

    # Police/legal
    police_report_number: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
    )
    police_report_date: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
    )
    third_party_involved: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    third_party_details: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Details of third party involved",
    )

    # Insurance
    insurance_claim_number: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
    )
    insurance_claim_date: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
    )
    insurance_claim_status: Mapped[Optional[str]] = mapped_column(
        String(30),
        nullable=True,
        comment="PENDING, APPROVED, REJECTED, SETTLED",
    )
    insurance_payout: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(18, 2),
        nullable=True,
    )

    # Costs
    estimated_repair_cost: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(18, 2),
        nullable=True,
    )
    actual_repair_cost: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(18, 2),
        nullable=True,
    )
    other_costs: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(18, 2),
        nullable=True,
        comment="Medical, towing, legal fees, etc.",
    )
    expense_claim_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("expense.expense_claim.claim_id"),
        nullable=True,
    )

    # Resolution
    resolution_date: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
    )
    resolution_notes: Mapped[Optional[str]] = mapped_column(
        Text,
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
        back_populates="incidents",
    )
    reported_by: Mapped["Employee"] = relationship(
        "Employee",
        foreign_keys=[reported_by_id],
        lazy="joined",
    )
    driver: Mapped[Optional["Employee"]] = relationship(
        "Employee",
        foreign_keys=[driver_id],
        lazy="joined",
    )

    @property
    def total_cost(self) -> Decimal:
        """Calculate total incident cost."""
        repair = self.actual_repair_cost or self.estimated_repair_cost or Decimal(0)
        other = self.other_costs or Decimal(0)
        return repair + other

    @property
    def net_cost(self) -> Decimal:
        """Calculate net cost after insurance payout."""
        payout = self.insurance_payout or Decimal(0)
        return self.total_cost - payout

    @property
    def is_closed(self) -> bool:
        """Check if incident is closed."""
        return self.status == IncidentStatus.CLOSED
