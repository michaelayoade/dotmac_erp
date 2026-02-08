"""
Fleet Management Module Models.

This module provides models for vehicle fleet management:
- Vehicles and specifications
- Vehicle assignments
- Maintenance records
- Fuel logs
- Incidents
- Documents (insurance, registration)
- Reservations (pool vehicles)
"""

from app.models.fleet.enums import (
    AssignmentType,
    DisposalMethod,
    DocumentType,
    FuelType,
    IncidentSeverity,
    IncidentStatus,
    IncidentType,
    MaintenanceStatus,
    MaintenanceType,
    OwnershipType,
    ReservationStatus,
    VehicleStatus,
    VehicleType,
)
from app.models.fleet.fuel_log import FuelLogEntry
from app.models.fleet.maintenance import MaintenanceRecord
from app.models.fleet.vehicle import Vehicle
from app.models.fleet.vehicle_assignment import VehicleAssignment
from app.models.fleet.vehicle_document import VehicleDocument
from app.models.fleet.vehicle_incident import VehicleIncident
from app.models.fleet.vehicle_reservation import VehicleReservation

__all__ = [
    # Enums
    "AssignmentType",
    "DisposalMethod",
    "DocumentType",
    "FuelType",
    "IncidentSeverity",
    "IncidentStatus",
    "IncidentType",
    "MaintenanceStatus",
    "MaintenanceType",
    "OwnershipType",
    "ReservationStatus",
    "VehicleStatus",
    "VehicleType",
    # Models
    "Vehicle",
    "VehicleAssignment",
    "VehicleDocument",
    "MaintenanceRecord",
    "FuelLogEntry",
    "VehicleIncident",
    "VehicleReservation",
]
