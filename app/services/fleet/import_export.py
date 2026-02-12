"""
Fleet Importers.

CSV importers for fleet vehicles and related records.
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.finance.ap.supplier import Supplier
from app.models.finance.core_org.location import Location
from app.models.fleet.enums import (
    AssignmentType,
    DocumentType,
    FuelType,
    MaintenanceStatus,
    MaintenanceType,
    OwnershipType,
    VehicleStatus,
    VehicleType,
)
from app.models.fleet.fuel_log import FuelLogEntry
from app.models.fleet.maintenance import MaintenanceRecord
from app.models.fleet.vehicle import Vehicle
from app.models.fleet.vehicle_assignment import VehicleAssignment
from app.models.fleet.vehicle_document import VehicleDocument
from app.models.people.hr.department import Department
from app.models.people.hr.employee import Employee
from app.services.finance.import_export.base import (
    BaseImporter,
    FieldMapping,
    ImportConfig,
)

logger = logging.getLogger(__name__)


def _first_value(row: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _parse_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(str(value).strip())
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Invalid integer: {value}") from exc


def _parse_date_multi(value: Any) -> Any:
    """Parse dates in common formats (YYYY-MM-DD or DD/MM/YYYY)."""
    if value is None or value == "":
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Invalid date: {value}")


class FleetImportBase(BaseImporter):
    """Shared helpers for fleet importers."""

    def __init__(self, db: Session, config: ImportConfig):
        super().__init__(db, config)

    def _resolve_vehicle_id(
        self, vehicle_code: str | None, registration_number: str | None
    ) -> UUID | None:
        if not vehicle_code and not registration_number:
            return None

        cache_key = f"vehicle:{vehicle_code or registration_number}"
        if cache_key in self._id_cache:
            return self._id_cache[cache_key]

        stmt = select(Vehicle).where(
            Vehicle.organization_id == self.config.organization_id
        )
        if vehicle_code:
            stmt = stmt.where(Vehicle.vehicle_code == vehicle_code)
        elif registration_number:
            stmt = stmt.where(Vehicle.registration_number == registration_number)

        vehicle = self.db.scalar(stmt)
        if vehicle:
            self._id_cache[cache_key] = vehicle.vehicle_id
            return vehicle.vehicle_id
        return None

    def _resolve_employee_id(self, employee_code: str | None) -> UUID | None:
        if not employee_code:
            return None
        cache_key = f"employee:{employee_code}"
        if cache_key in self._id_cache:
            return self._id_cache[cache_key]

        employee = self.db.scalar(
            select(Employee).where(
                Employee.organization_id == self.config.organization_id,
                Employee.employee_code == employee_code,
                Employee.is_deleted == False,  # noqa: E712
            )
        )
        if employee:
            self._id_cache[cache_key] = employee.employee_id
            return employee.employee_id
        return None

    def _resolve_department_id(self, department_code: str | None) -> UUID | None:
        if not department_code:
            return None
        cache_key = f"department:{department_code}"
        if cache_key in self._id_cache:
            return self._id_cache[cache_key]

        department = self.db.scalar(
            select(Department).where(
                Department.organization_id == self.config.organization_id,
                Department.department_code == department_code,
                Department.is_deleted == False,  # noqa: E712
            )
        )
        if department:
            self._id_cache[cache_key] = department.department_id
            return department.department_id
        return None

    def _resolve_supplier_id(
        self, supplier_code: str | None, supplier_name: str | None
    ) -> UUID | None:
        if not supplier_code and not supplier_name:
            return None
        cache_key = f"supplier:{supplier_code or supplier_name}"
        if cache_key in self._id_cache:
            return self._id_cache[cache_key]

        stmt = select(Supplier).where(
            Supplier.organization_id == self.config.organization_id
        )
        if supplier_code:
            stmt = stmt.where(Supplier.supplier_code == supplier_code)
        elif supplier_name:
            stmt = stmt.where(
                (Supplier.legal_name.ilike(f"%{supplier_name}%"))
                | (Supplier.trading_name.ilike(f"%{supplier_name}%"))
            )

        supplier = self.db.scalar(stmt)
        if supplier:
            self._id_cache[cache_key] = supplier.supplier_id
            return supplier.supplier_id
        return None

    def _resolve_location_id(
        self, location_code: str | None, location_name: str | None
    ) -> UUID | None:
        if not location_code and not location_name:
            return None
        cache_key = f"location:{location_code or location_name}"
        if cache_key in self._id_cache:
            return self._id_cache[cache_key]

        stmt = select(Location).where(
            Location.organization_id == self.config.organization_id,
            Location.is_active == True,  # noqa: E712
        )
        if location_code:
            code = str(location_code).strip()
            stmt_exact = stmt.where(Location.location_code == code)
            location = self.db.scalar(stmt_exact)
            if not location:
                code_norm = code.replace("-", "").replace(" ", "").upper()
                stmt_norm = stmt.where(
                    func.upper(
                        func.replace(
                            func.replace(Location.location_code, "-", ""), " ", ""
                        )
                    )
                    == code_norm
                )
                location = self.db.scalar(stmt_norm)
            if location:
                self._id_cache[cache_key] = location.location_id
                return location.location_id
            return None
        elif location_name:
            stmt = stmt.where(Location.location_name.ilike(f"%{location_name}%"))

        location = self.db.scalar(stmt)
        if location:
            self._id_cache[cache_key] = location.location_id
            return location.location_id
        return None


class VehicleImporter(FleetImportBase):
    """Importer for fleet vehicles."""

    entity_name = "Fleet Vehicle"
    model_class = Vehicle

    def get_field_mappings(self) -> list[FieldMapping]:
        return [
            FieldMapping("Vehicle Code", "vehicle_code", required=False),
            FieldMapping("Fleet Code", "vehicle_code_alt", required=False),
            FieldMapping("Vehicle ID", "vehicle_code_alt2", required=False),
            FieldMapping("Registration Number", "registration_number", required=False),
            FieldMapping("Plate Number", "registration_alt", required=False),
            FieldMapping("License Plate", "registration_alt2", required=False),
            FieldMapping("VIN", "vin", required=False),
            FieldMapping("VIN Number", "vin_alt", required=False),
            FieldMapping("Chassis Number", "vin_alt2", required=False),
            FieldMapping("Engine Number", "engine_number", required=False),
            FieldMapping("Make", "make", required=False),
            FieldMapping("Manufacturer", "make_alt", required=False),
            FieldMapping("Model", "model", required=False),
            FieldMapping("Model Name", "model_alt", required=False),
            FieldMapping("Year", "year", required=False, transformer=_parse_int),
            FieldMapping(
                "Model Year", "year_alt", required=False, transformer=_parse_int
            ),
            FieldMapping("Color", "color", required=False),
            FieldMapping(
                "Vehicle Type",
                "vehicle_type",
                required=False,
                transformer=lambda v: self.parse_enum(
                    v, VehicleType, VehicleType.SEDAN
                ),
            ),
            FieldMapping(
                "Fuel Type",
                "fuel_type",
                required=False,
                transformer=lambda v: self.parse_enum(v, FuelType, FuelType.PETROL),
            ),
            FieldMapping("Transmission", "transmission", required=False),
            FieldMapping(
                "Engine Capacity (CC)",
                "engine_capacity_cc",
                required=False,
                transformer=_parse_int,
            ),
            FieldMapping(
                "Seating Capacity",
                "seating_capacity",
                required=False,
                transformer=_parse_int,
            ),
            FieldMapping(
                "Fuel Tank Capacity (Liters)",
                "fuel_tank_capacity_liters",
                required=False,
                transformer=self.parse_decimal,
            ),
            FieldMapping(
                "Expected Fuel Efficiency (km/l)",
                "expected_fuel_efficiency",
                required=False,
                transformer=self.parse_decimal,
            ),
            FieldMapping(
                "Ownership Type",
                "ownership_type",
                required=False,
                transformer=lambda v: self.parse_enum(
                    v, OwnershipType, OwnershipType.OWNED
                ),
            ),
            FieldMapping(
                "Purchase Date",
                "purchase_date",
                required=False,
                transformer=self.parse_date,
            ),
            FieldMapping(
                "Purchase Price",
                "purchase_price",
                required=False,
                transformer=self.parse_decimal,
            ),
            FieldMapping(
                "Lease Start Date",
                "lease_start_date",
                required=False,
                transformer=self.parse_date,
            ),
            FieldMapping(
                "Lease End Date",
                "lease_end_date",
                required=False,
                transformer=self.parse_date,
            ),
            FieldMapping(
                "Lease Monthly Cost",
                "lease_monthly_cost",
                required=False,
                transformer=self.parse_decimal,
            ),
            FieldMapping(
                "Assignment Type",
                "assignment_type",
                required=False,
                transformer=lambda v: self.parse_enum(
                    v, AssignmentType, AssignmentType.POOL
                ),
            ),
            FieldMapping(
                "Assigned Employee Code", "assigned_employee_code", required=False
            ),
            FieldMapping(
                "Assigned Department Code", "assigned_department_code", required=False
            ),
            FieldMapping(
                "Vehicle Status",
                "vehicle_status",
                required=False,
                transformer=lambda v: self.parse_enum(
                    v, VehicleStatus, VehicleStatus.ACTIVE
                ),
            ),
            FieldMapping(
                "License Expiry Date",
                "license_expiry_date",
                required=False,
                transformer=_parse_date_multi,
            ),
            FieldMapping("Location Code", "location_code", required=False),
            FieldMapping("Location Name", "location_name", required=False),
            FieldMapping("Branch", "location_name_alt", required=False),
            FieldMapping(
                "Current Odometer",
                "current_odometer",
                required=False,
                transformer=_parse_int,
            ),
            FieldMapping(
                "Last Odometer Date",
                "last_odometer_date",
                required=False,
                transformer=self.parse_date,
            ),
            FieldMapping(
                "Has GPS Tracker",
                "has_gps_tracker",
                required=False,
                transformer=self.parse_boolean,
            ),
            FieldMapping("GPS Device ID", "gps_device_id", required=False),
            FieldMapping("Vendor Code", "vendor_code", required=False),
            FieldMapping("Vendor Name", "vendor_name", required=False),
        ]

    def get_unique_key(self, row: dict[str, Any]) -> str:
        code = _first_value(row, "Vehicle Code", "Fleet Code", "Vehicle ID")
        reg = _first_value(row, "Registration Number", "Plate Number", "License Plate")
        return code or reg or "unknown"

    def check_duplicate(self, row: dict[str, Any]) -> Vehicle | None:
        code = _first_value(row, "Vehicle Code", "Fleet Code", "Vehicle ID")
        reg = _first_value(row, "Registration Number", "Plate Number", "License Plate")

        stmt = select(Vehicle).where(
            Vehicle.organization_id == self.config.organization_id
        )
        if code:
            stmt = stmt.where(Vehicle.vehicle_code == code)
        elif reg:
            stmt = stmt.where(Vehicle.registration_number == reg)
        else:
            return None
        return self.db.scalar(stmt)

    def create_entity(self, row: dict[str, Any]) -> Vehicle:
        vehicle_code = _first_value(
            row, "vehicle_code", "vehicle_code_alt", "vehicle_code_alt2"
        )
        registration_number = _first_value(
            row, "registration_number", "registration_alt", "registration_alt2"
        )
        vin = _first_value(row, "vin", "vin_alt", "vin_alt2")
        engine_number = _first_value(row, "engine_number")
        make = _first_value(row, "make", "make_alt")
        model = _first_value(row, "model", "model_alt")
        year = row.get("year") or row.get("year_alt")

        if not vehicle_code:
            raise ValueError("Vehicle Code is required")
        if not registration_number:
            raise ValueError("Registration Number is required")
        if not make:
            raise ValueError("Make is required")
        if not model:
            raise ValueError("Model is required")
        if not year:
            raise ValueError("Year is required")

        assignment_type = row.get("assignment_type") or AssignmentType.POOL
        status = row.get("vehicle_status") or VehicleStatus.ACTIVE

        assigned_employee_id = self._resolve_employee_id(
            row.get("assigned_employee_code")
        )
        assigned_department_id = self._resolve_department_id(
            row.get("assigned_department_code")
        )

        if row.get("assigned_employee_code") and not assigned_employee_id:
            raise ValueError(f"Employee not found: {row.get('assigned_employee_code')}")
        if row.get("assigned_department_code") and not assigned_department_id:
            raise ValueError(
                f"Department not found: {row.get('assigned_department_code')}"
            )

        supplier_id = self._resolve_supplier_id(
            row.get("vendor_code"), row.get("vendor_name")
        )
        if (row.get("vendor_code") or row.get("vendor_name")) and not supplier_id:
            raise ValueError("Supplier not found for vendor reference")

        location_name = _first_value(row, "location_name", "location_name_alt")
        location_id = self._resolve_location_id(row.get("location_code"), location_name)
        if (row.get("location_code") or location_name) and not location_id:
            logger.warning(
                "Location not found for branch reference: code=%s name=%s",
                row.get("location_code"),
                location_name,
            )

        return Vehicle(
            vehicle_id=uuid4(),
            organization_id=self.config.organization_id,
            vehicle_code=vehicle_code[:30],
            registration_number=registration_number[:20],
            vin=vin[:50] if vin else None,
            engine_number=engine_number[:50] if engine_number else None,
            make=make[:50],
            model=model[:50],
            year=int(year),
            color=row.get("color"),
            vehicle_type=row.get("vehicle_type") or VehicleType.SEDAN,
            fuel_type=row.get("fuel_type") or FuelType.PETROL,
            transmission=row.get("transmission"),
            engine_capacity_cc=row.get("engine_capacity_cc"),
            seating_capacity=row.get("seating_capacity") or 5,
            fuel_tank_capacity_liters=row.get("fuel_tank_capacity_liters"),
            expected_fuel_efficiency=row.get("expected_fuel_efficiency"),
            ownership_type=row.get("ownership_type") or OwnershipType.OWNED,
            purchase_date=row.get("purchase_date"),
            purchase_price=row.get("purchase_price"),
            lease_start_date=row.get("lease_start_date"),
            lease_end_date=row.get("lease_end_date"),
            lease_monthly_cost=row.get("lease_monthly_cost"),
            vendor_id=supplier_id,
            license_expiry_date=row.get("license_expiry_date"),
            location_id=location_id,
            assignment_type=assignment_type,
            assigned_employee_id=assigned_employee_id,
            assigned_department_id=assigned_department_id,
            status=status,
            current_odometer=row.get("current_odometer") or 0,
            last_odometer_date=row.get("last_odometer_date"),
            has_gps_tracker=row.get("has_gps_tracker") or False,
            gps_device_id=row.get("gps_device_id"),
        )


class VehicleAssignmentImporter(FleetImportBase):
    """Importer for vehicle assignment history."""

    entity_name = "Vehicle Assignment"
    model_class = VehicleAssignment

    def get_field_mappings(self) -> list[FieldMapping]:
        return [
            FieldMapping("Vehicle Code", "vehicle_code", required=False),
            FieldMapping("Registration Number", "registration_number", required=False),
            FieldMapping(
                "Assignment Type",
                "assignment_type",
                required=False,
                transformer=lambda v: self.parse_enum(v, AssignmentType),
            ),
            FieldMapping(
                "Start Date", "start_date", required=False, transformer=self.parse_date
            ),
            FieldMapping(
                "End Date", "end_date", required=False, transformer=self.parse_date
            ),
            FieldMapping("Employee Code", "employee_code", required=False),
            FieldMapping("Department Code", "department_code", required=False),
            FieldMapping(
                "Start Odometer",
                "start_odometer",
                required=False,
                transformer=_parse_int,
            ),
            FieldMapping(
                "End Odometer", "end_odometer", required=False, transformer=_parse_int
            ),
            FieldMapping("Reason", "reason", required=False),
            FieldMapping("Notes", "notes", required=False),
            FieldMapping(
                "Is Active", "is_active", required=False, transformer=self.parse_boolean
            ),
        ]

    def get_unique_key(self, row: dict[str, Any]) -> str:
        vehicle = _first_value(row, "Vehicle Code", "Registration Number") or "unknown"
        start_date = _first_value(row, "Start Date")
        return f"{vehicle}:{start_date or 'unknown'}"

    def check_duplicate(self, row: dict[str, Any]) -> VehicleAssignment | None:
        vehicle_code = _first_value(row, "Vehicle Code")
        registration_number = _first_value(row, "Registration Number")
        vehicle_id = self._resolve_vehicle_id(vehicle_code, registration_number)
        start_date = (
            self.parse_date(row.get("Start Date")) if row.get("Start Date") else None
        )
        if not vehicle_id or not start_date:
            return None
        return self.db.scalar(
            select(VehicleAssignment).where(
                VehicleAssignment.organization_id == self.config.organization_id,
                VehicleAssignment.vehicle_id == vehicle_id,
                VehicleAssignment.start_date == start_date,
            )
        )

    def create_entity(self, row: dict[str, Any]) -> VehicleAssignment:
        vehicle_id = self._resolve_vehicle_id(
            row.get("vehicle_code"), row.get("registration_number")
        )
        if not vehicle_id:
            raise ValueError("Vehicle not found for assignment")

        assignment_type = row.get("assignment_type")
        if not assignment_type:
            raise ValueError("Assignment Type is required")

        start_date = row.get("start_date")
        if not start_date:
            raise ValueError("Start Date is required")

        employee_id = self._resolve_employee_id(row.get("employee_code"))
        department_id = self._resolve_department_id(row.get("department_code"))

        if assignment_type == AssignmentType.PERSONAL and not employee_id:
            raise ValueError("Employee Code is required for PERSONAL assignment")
        if assignment_type == AssignmentType.DEPARTMENT and not department_id:
            raise ValueError("Department Code is required for DEPARTMENT assignment")

        if row.get("employee_code") and not employee_id:
            raise ValueError(f"Employee not found: {row.get('employee_code')}")
        if row.get("department_code") and not department_id:
            raise ValueError(f"Department not found: {row.get('department_code')}")

        return VehicleAssignment(
            assignment_id=uuid4(),
            organization_id=self.config.organization_id,
            vehicle_id=vehicle_id,
            employee_id=employee_id,
            department_id=department_id,
            assignment_type=assignment_type,
            start_date=start_date,
            end_date=row.get("end_date"),
            start_odometer=row.get("start_odometer"),
            end_odometer=row.get("end_odometer"),
            reason=row.get("reason"),
            notes=row.get("notes"),
            is_active=row.get("is_active")
            if row.get("is_active") is not None
            else True,
        )


class FuelLogImporter(FleetImportBase):
    """Importer for fuel log entries."""

    entity_name = "Fuel Log"
    model_class = FuelLogEntry

    def get_field_mappings(self) -> list[FieldMapping]:
        return [
            FieldMapping("Vehicle Code", "vehicle_code", required=False),
            FieldMapping("Registration Number", "registration_number", required=False),
            FieldMapping(
                "Log Date", "log_date", required=False, transformer=self.parse_date
            ),
            FieldMapping(
                "Fuel Type",
                "fuel_type",
                required=False,
                transformer=lambda v: self.parse_enum(v, FuelType),
            ),
            FieldMapping(
                "Quantity Liters",
                "quantity_liters",
                required=False,
                transformer=self.parse_decimal,
            ),
            FieldMapping(
                "Price Per Liter",
                "price_per_liter",
                required=False,
                transformer=self.parse_decimal,
            ),
            FieldMapping(
                "Total Cost",
                "total_cost",
                required=False,
                transformer=self.parse_decimal,
            ),
            FieldMapping(
                "Odometer Reading",
                "odometer_reading",
                required=False,
                transformer=_parse_int,
            ),
            FieldMapping("Employee Code", "employee_code", required=False),
            FieldMapping("Station Name", "station_name", required=False),
            FieldMapping("Station Location", "station_location", required=False),
            FieldMapping("Receipt Number", "receipt_number", required=False),
            FieldMapping(
                "Is Full Tank",
                "is_full_tank",
                required=False,
                transformer=self.parse_boolean,
            ),
            FieldMapping("Notes", "notes", required=False),
        ]

    def get_unique_key(self, row: dict[str, Any]) -> str:
        vehicle = _first_value(row, "Vehicle Code", "Registration Number") or "unknown"
        log_date = _first_value(row, "Log Date")
        return f"{vehicle}:{log_date or 'unknown'}"

    def check_duplicate(self, row: dict[str, Any]) -> FuelLogEntry | None:
        vehicle_code = _first_value(row, "Vehicle Code")
        registration_number = _first_value(row, "Registration Number")
        vehicle_id = self._resolve_vehicle_id(vehicle_code, registration_number)
        log_date = self.parse_date(row.get("Log Date")) if row.get("Log Date") else None
        if not vehicle_id or not log_date:
            return None
        return self.db.scalar(
            select(FuelLogEntry).where(
                FuelLogEntry.organization_id == self.config.organization_id,
                FuelLogEntry.vehicle_id == vehicle_id,
                FuelLogEntry.log_date == log_date,
                FuelLogEntry.odometer_reading == row.get("Odometer Reading"),
            )
        )

    def create_entity(self, row: dict[str, Any]) -> FuelLogEntry:
        vehicle_id = self._resolve_vehicle_id(
            row.get("vehicle_code"), row.get("registration_number")
        )
        if not vehicle_id:
            raise ValueError("Vehicle not found for fuel log")

        log_date = row.get("log_date")
        fuel_type = row.get("fuel_type")
        quantity_liters = row.get("quantity_liters")
        price_per_liter = row.get("price_per_liter")
        odometer = row.get("odometer_reading")

        if not log_date:
            raise ValueError("Log Date is required")
        if not fuel_type:
            raise ValueError("Fuel Type is required")
        if quantity_liters is None:
            raise ValueError("Quantity Liters is required")
        if price_per_liter is None:
            raise ValueError("Price Per Liter is required")
        if odometer is None:
            raise ValueError("Odometer Reading is required")

        total_cost = row.get("total_cost")
        if total_cost is None:
            total_cost = (quantity_liters or Decimal("0")) * (
                price_per_liter or Decimal("0")
            )

        employee_id = self._resolve_employee_id(row.get("employee_code"))
        if row.get("employee_code") and not employee_id:
            raise ValueError(f"Employee not found: {row.get('employee_code')}")

        return FuelLogEntry(
            fuel_log_id=uuid4(),
            organization_id=self.config.organization_id,
            vehicle_id=vehicle_id,
            employee_id=employee_id,
            log_date=log_date,
            fuel_type=fuel_type,
            quantity_liters=quantity_liters,
            price_per_liter=price_per_liter,
            total_cost=total_cost,
            odometer_reading=odometer,
            station_name=row.get("station_name"),
            station_location=row.get("station_location"),
            receipt_number=row.get("receipt_number"),
            is_full_tank=row.get("is_full_tank")
            if row.get("is_full_tank") is not None
            else True,
            notes=row.get("notes"),
        )


class MaintenanceImporter(FleetImportBase):
    """Importer for maintenance records."""

    entity_name = "Maintenance Record"
    model_class = MaintenanceRecord

    def get_field_mappings(self) -> list[FieldMapping]:
        return [
            FieldMapping("Vehicle Code", "vehicle_code", required=False),
            FieldMapping("Registration Number", "registration_number", required=False),
            FieldMapping(
                "Maintenance Type",
                "maintenance_type",
                required=False,
                transformer=lambda v: self.parse_enum(v, MaintenanceType),
            ),
            FieldMapping("Description", "description", required=False),
            FieldMapping(
                "Scheduled Date",
                "scheduled_date",
                required=False,
                transformer=self.parse_date,
            ),
            FieldMapping(
                "Completed Date",
                "completed_date",
                required=False,
                transformer=self.parse_date,
            ),
            FieldMapping(
                "Status",
                "status",
                required=False,
                transformer=lambda v: self.parse_enum(
                    v, MaintenanceStatus, MaintenanceStatus.SCHEDULED
                ),
            ),
            FieldMapping(
                "Odometer At Service",
                "odometer_at_service",
                required=False,
                transformer=_parse_int,
            ),
            FieldMapping(
                "Next Service Odometer",
                "next_service_odometer",
                required=False,
                transformer=_parse_int,
            ),
            FieldMapping(
                "Next Service Date",
                "next_service_date",
                required=False,
                transformer=self.parse_date,
            ),
            FieldMapping(
                "Estimated Cost",
                "estimated_cost",
                required=False,
                transformer=self.parse_decimal,
            ),
            FieldMapping(
                "Actual Cost",
                "actual_cost",
                required=False,
                transformer=self.parse_decimal,
            ),
            FieldMapping("Supplier Code", "supplier_code", required=False),
            FieldMapping("Supplier Name", "supplier_name", required=False),
            FieldMapping("Invoice Number", "invoice_number", required=False),
            FieldMapping("Work Performed", "work_performed", required=False),
            FieldMapping("Parts Replaced", "parts_replaced", required=False),
            FieldMapping("Technician Name", "technician_name", required=False),
            FieldMapping("Notes", "notes", required=False),
        ]

    def get_unique_key(self, row: dict[str, Any]) -> str:
        vehicle = _first_value(row, "Vehicle Code", "Registration Number") or "unknown"
        sched = _first_value(row, "Scheduled Date")
        return f"{vehicle}:{sched or 'unknown'}"

    def check_duplicate(self, row: dict[str, Any]) -> MaintenanceRecord | None:
        vehicle_id = self._resolve_vehicle_id(
            _first_value(row, "Vehicle Code"), _first_value(row, "Registration Number")
        )
        scheduled_date = (
            self.parse_date(row.get("Scheduled Date"))
            if row.get("Scheduled Date")
            else None
        )
        if not vehicle_id or not scheduled_date:
            return None
        return self.db.scalar(
            select(MaintenanceRecord).where(
                MaintenanceRecord.organization_id == self.config.organization_id,
                MaintenanceRecord.vehicle_id == vehicle_id,
                MaintenanceRecord.scheduled_date == scheduled_date,
            )
        )

    def create_entity(self, row: dict[str, Any]) -> MaintenanceRecord:
        vehicle_id = self._resolve_vehicle_id(
            row.get("vehicle_code"), row.get("registration_number")
        )
        if not vehicle_id:
            raise ValueError("Vehicle not found for maintenance record")

        maintenance_type = row.get("maintenance_type")
        description = row.get("description")
        scheduled_date = row.get("scheduled_date")

        if not maintenance_type:
            raise ValueError("Maintenance Type is required")
        if not description:
            raise ValueError("Description is required")
        if not scheduled_date:
            raise ValueError("Scheduled Date is required")

        supplier_id = self._resolve_supplier_id(
            row.get("supplier_code"), row.get("supplier_name")
        )
        if (row.get("supplier_code") or row.get("supplier_name")) and not supplier_id:
            raise ValueError("Supplier not found for maintenance record")

        return MaintenanceRecord(
            maintenance_id=uuid4(),
            organization_id=self.config.organization_id,
            vehicle_id=vehicle_id,
            maintenance_type=maintenance_type,
            description=description,
            scheduled_date=scheduled_date,
            completed_date=row.get("completed_date"),
            status=row.get("status") or MaintenanceStatus.SCHEDULED,
            odometer_at_service=row.get("odometer_at_service"),
            next_service_odometer=row.get("next_service_odometer"),
            next_service_date=row.get("next_service_date"),
            estimated_cost=row.get("estimated_cost"),
            actual_cost=row.get("actual_cost"),
            supplier_id=supplier_id,
            invoice_number=row.get("invoice_number"),
            work_performed=row.get("work_performed"),
            parts_replaced=row.get("parts_replaced"),
            technician_name=row.get("technician_name"),
            notes=row.get("notes"),
        )


class VehicleDocumentImporter(FleetImportBase):
    """Importer for vehicle documents."""

    entity_name = "Vehicle Document"
    model_class = VehicleDocument

    def get_field_mappings(self) -> list[FieldMapping]:
        return [
            FieldMapping("Vehicle Code", "vehicle_code", required=False),
            FieldMapping("Registration Number", "registration_number", required=False),
            FieldMapping(
                "Document Type",
                "document_type",
                required=False,
                transformer=lambda v: self.parse_enum(v, DocumentType),
            ),
            FieldMapping("Description", "description", required=False),
            FieldMapping("Document Number", "document_number", required=False),
            FieldMapping(
                "Issue Date", "issue_date", required=False, transformer=self.parse_date
            ),
            FieldMapping(
                "Expiry Date",
                "expiry_date",
                required=False,
                transformer=self.parse_date,
            ),
            FieldMapping("Provider Name", "provider_name", required=False),
            FieldMapping("Policy Number", "policy_number", required=False),
            FieldMapping(
                "Coverage Amount",
                "coverage_amount",
                required=False,
                transformer=self.parse_decimal,
            ),
            FieldMapping(
                "Premium Amount",
                "premium_amount",
                required=False,
                transformer=self.parse_decimal,
            ),
            FieldMapping(
                "Reminder Days",
                "reminder_days_before",
                required=False,
                transformer=_parse_int,
            ),
            FieldMapping("Notes", "notes", required=False),
        ]

    def get_unique_key(self, row: dict[str, Any]) -> str:
        vehicle = _first_value(row, "Vehicle Code", "Registration Number") or "unknown"
        doc_type = _first_value(row, "Document Type") or "unknown"
        doc_number = _first_value(row, "Document Number") or "unknown"
        return f"{vehicle}:{doc_type}:{doc_number}"

    def check_duplicate(self, row: dict[str, Any]) -> VehicleDocument | None:
        vehicle_id = self._resolve_vehicle_id(
            _first_value(row, "Vehicle Code"), _first_value(row, "Registration Number")
        )
        doc_type = row.get("Document Type")
        doc_number = row.get("Document Number")
        if not vehicle_id or not doc_type or not doc_number:
            return None
        return self.db.scalar(
            select(VehicleDocument).where(
                VehicleDocument.organization_id == self.config.organization_id,
                VehicleDocument.vehicle_id == vehicle_id,
                VehicleDocument.document_type == doc_type,
                VehicleDocument.document_number == doc_number,
            )
        )

    def create_entity(self, row: dict[str, Any]) -> VehicleDocument:
        vehicle_id = self._resolve_vehicle_id(
            row.get("vehicle_code"), row.get("registration_number")
        )
        if not vehicle_id:
            raise ValueError("Vehicle not found for document")

        document_type = row.get("document_type")
        description = row.get("description")
        if not document_type:
            raise ValueError("Document Type is required")
        if not description:
            raise ValueError("Description is required")

        return VehicleDocument(
            document_id=uuid4(),
            organization_id=self.config.organization_id,
            vehicle_id=vehicle_id,
            document_type=document_type,
            document_number=row.get("document_number"),
            description=description,
            issue_date=row.get("issue_date"),
            expiry_date=row.get("expiry_date"),
            provider_name=row.get("provider_name"),
            policy_number=row.get("policy_number"),
            coverage_amount=row.get("coverage_amount"),
            premium_amount=row.get("premium_amount"),
            reminder_days_before=row.get("reminder_days_before") or 30,
            reminder_sent=False,
            notes=row.get("notes"),
        )
