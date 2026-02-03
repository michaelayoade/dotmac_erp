"""
Fuel Service - Fuel logging and efficiency tracking.

Handles fuel log entries and consumption analysis.
"""

import logging
from datetime import date
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models.fleet.fuel_log import FuelLogEntry
from app.models.fleet.vehicle import Vehicle
from app.schemas.fleet.fuel import FuelEfficiencyReport, FuelLogCreate, FuelLogUpdate
from app.services.common import (
    NotFoundError,
    PaginatedResult,
    PaginationParams,
    ValidationError,
    paginate,
)

logger = logging.getLogger(__name__)


class FuelService:
    """Service for fuel log operations."""

    def __init__(self, db: Session, organization_id: UUID):
        self.db = db
        self.organization_id = organization_id

    def get_by_id(self, fuel_log_id: UUID) -> Optional[FuelLogEntry]:
        """Get fuel log by ID."""
        return self.db.get(FuelLogEntry, fuel_log_id)

    def get_or_raise(self, fuel_log_id: UUID) -> FuelLogEntry:
        """Get fuel log or raise NotFoundError."""
        log = self.get_by_id(fuel_log_id)
        if not log or log.organization_id != self.organization_id:
            raise NotFoundError(f"Fuel log {fuel_log_id} not found")
        return log

    def list_logs(
        self,
        *,
        vehicle_id: Optional[UUID] = None,
        employee_id: Optional[UUID] = None,
        from_date: Optional[date] = None,
        to_date: Optional[date] = None,
        params: Optional[PaginationParams] = None,
    ) -> PaginatedResult[FuelLogEntry]:
        """List fuel logs with filtering."""
        stmt = (
            select(FuelLogEntry)
            .where(FuelLogEntry.organization_id == self.organization_id)
            .options(
                selectinload(FuelLogEntry.vehicle),
                selectinload(FuelLogEntry.employee),
            )
            .order_by(FuelLogEntry.log_date.desc())
        )

        if vehicle_id:
            stmt = stmt.where(FuelLogEntry.vehicle_id == vehicle_id)

        if employee_id:
            stmt = stmt.where(FuelLogEntry.employee_id == employee_id)

        if from_date:
            stmt = stmt.where(FuelLogEntry.log_date >= from_date)

        if to_date:
            stmt = stmt.where(FuelLogEntry.log_date <= to_date)

        return paginate(self.db, stmt, params)

    def create(self, data: FuelLogCreate) -> FuelLogEntry:
        """Create a new fuel log entry."""
        # Verify vehicle exists
        vehicle = self.db.get(Vehicle, data.vehicle_id)
        if not vehicle or vehicle.organization_id != self.organization_id:
            raise NotFoundError(f"Vehicle {data.vehicle_id} not found")

        # Validate odometer reading
        if data.odometer_reading < vehicle.current_odometer:
            raise ValidationError(
                f"Odometer reading ({data.odometer_reading}) cannot be less than "
                f"current vehicle odometer ({vehicle.current_odometer})"
            )

        log = FuelLogEntry(
            organization_id=self.organization_id,
            **data.model_dump(),
        )

        self.db.add(log)
        self.db.flush()

        # Update vehicle odometer
        vehicle.current_odometer = data.odometer_reading
        vehicle.last_odometer_date = data.log_date

        logger.info(
            "Created fuel log for vehicle %s: %.2f liters",
            vehicle.vehicle_code,
            data.quantity_liters,
        )
        return log

    def update(self, fuel_log_id: UUID, data: FuelLogUpdate) -> FuelLogEntry:
        """Update a fuel log entry."""
        log = self.get_or_raise(fuel_log_id)

        update_data = data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(log, field, value)

        logger.info("Updated fuel log %s", fuel_log_id)
        return log

    def delete(self, fuel_log_id: UUID) -> None:
        """Delete a fuel log entry."""
        log = self.get_or_raise(fuel_log_id)
        self.db.delete(log)
        logger.info("Deleted fuel log %s", fuel_log_id)

    def calculate_efficiency(
        self,
        vehicle_id: UUID,
        from_date: Optional[date] = None,
        to_date: Optional[date] = None,
    ) -> Optional[FuelEfficiencyReport]:
        """Calculate fuel efficiency for a vehicle over a period."""
        # Get fuel logs for the period with full tank fills only
        stmt = (
            select(FuelLogEntry)
            .where(
                FuelLogEntry.organization_id == self.organization_id,
                FuelLogEntry.vehicle_id == vehicle_id,
                FuelLogEntry.is_full_tank == True,  # noqa: E712
            )
            .order_by(FuelLogEntry.odometer_reading.asc())
        )

        if from_date:
            stmt = stmt.where(FuelLogEntry.log_date >= from_date)
        if to_date:
            stmt = stmt.where(FuelLogEntry.log_date <= to_date)

        logs = list(self.db.scalars(stmt).all())

        if len(logs) < 2:
            return None  # Need at least 2 full-tank fills to calculate

        # Calculate totals
        total_distance = logs[-1].odometer_reading - logs[0].odometer_reading
        # Sum fuel from all fills except the first (that fuel was from before our period)
        total_fuel = sum(
            (log.quantity_liters or Decimal("0") for log in logs[1:]), Decimal("0")
        )
        total_cost = sum(
            (log.total_cost or Decimal("0") for log in logs[1:]), Decimal("0")
        )

        if total_fuel <= 0 or total_distance <= 0:
            return None

        return FuelEfficiencyReport(
            vehicle_id=vehicle_id,
            period_start=logs[0].log_date,
            period_end=logs[-1].log_date,
            total_distance_km=total_distance,
            total_fuel_liters=total_fuel,
            total_cost=total_cost,
            average_efficiency_km_per_liter=Decimal(total_distance) / total_fuel,
            average_cost_per_km=total_cost / Decimal(total_distance),
            fill_count=len(logs),
        )

    def get_monthly_summary(
        self,
        vehicle_id: Optional[UUID] = None,
        year: Optional[int] = None,
        month: Optional[int] = None,
    ) -> List[dict]:
        """Get monthly fuel consumption summary."""
        stmt = select(
            func.date_trunc("month", FuelLogEntry.log_date).label("month"),
            func.sum(FuelLogEntry.quantity_liters).label("total_liters"),
            func.sum(FuelLogEntry.total_cost).label("total_cost"),
            func.count(FuelLogEntry.fuel_log_id).label("fill_count"),
        ).where(FuelLogEntry.organization_id == self.organization_id)

        if vehicle_id:
            stmt = stmt.where(FuelLogEntry.vehicle_id == vehicle_id)

        if year:
            stmt = stmt.where(func.extract("year", FuelLogEntry.log_date) == year)

        if month:
            stmt = stmt.where(func.extract("month", FuelLogEntry.log_date) == month)

        stmt = stmt.group_by(func.date_trunc("month", FuelLogEntry.log_date)).order_by(
            func.date_trunc("month", FuelLogEntry.log_date).desc()
        )

        results = self.db.execute(stmt).all()
        return [
            {
                "month": r.month,
                "total_liters": r.total_liters,
                "total_cost": r.total_cost,
                "fill_count": r.fill_count,
            }
            for r in results
        ]
