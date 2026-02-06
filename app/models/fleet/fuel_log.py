"""
Fuel Log Entry Model - Fleet Schema.

Tracks fuel purchases and consumption.
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, Date, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.fleet.base import FleetBaseMixin
from app.models.fleet.enums import FuelType
from app.models.people.base import AuditMixin

if TYPE_CHECKING:
    from app.models.fleet.vehicle import Vehicle
    from app.models.people.hr.employee import Employee


class FuelLogEntry(Base, FleetBaseMixin, AuditMixin):
    """
    Fuel purchase and consumption record.

    Tracks individual fuel purchases with:
    - Quantity, price, and total cost
    - Odometer reading for efficiency calculation
    - Driver/employee who made the purchase
    - Optional link to expense claim for reimbursement
    """

    __tablename__ = "fuel_log_entry"
    __table_args__ = (
        Index("idx_fleet_fuel_vehicle_date", "vehicle_id", "log_date"),
        Index("idx_fleet_fuel_employee", "employee_id"),
        Index("idx_fleet_fuel_org_date", "organization_id", "log_date"),
        {"schema": "fleet"},
    )

    # Primary key
    fuel_log_id: Mapped[uuid.UUID] = mapped_column(
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
    employee_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=True,
        comment="Driver who purchased fuel",
    )

    # Fuel details
    log_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )
    fuel_type: Mapped[FuelType] = mapped_column(
        nullable=False,
    )
    quantity_liters: Mapped[Decimal] = mapped_column(
        Numeric(10, 3),
        nullable=False,
    )
    price_per_liter: Mapped[Decimal] = mapped_column(
        Numeric(10, 4),
        nullable=False,
    )
    total_cost: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )

    # Odometer
    odometer_reading: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    # Station/vendor details
    station_name: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
    )
    station_location: Mapped[Optional[str]] = mapped_column(
        String(200),
        nullable=True,
    )
    receipt_number: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
    )

    # Full tank indicator (for accurate efficiency calculation)
    is_full_tank: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        comment="True if filled to full tank (needed for efficiency calc)",
    )

    # Expense link
    expense_claim_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("expense.expense_claim.claim_id"),
        nullable=True,
        comment="Link to expense claim if reimbursable",
    )

    # Notes
    notes: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Relationships
    vehicle: Mapped["Vehicle"] = relationship(
        "Vehicle",
        back_populates="fuel_logs",
    )
    employee: Mapped[Optional["Employee"]] = relationship(
        "Employee",
        foreign_keys=[employee_id],
        lazy="joined",
    )

    @property
    def calculated_total(self) -> Decimal:
        """Calculate total from quantity and price."""
        return self.quantity_liters * self.price_per_liter
