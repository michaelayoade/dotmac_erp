"""
Fleet Management Services.

Business logic for vehicle fleet management.
"""
from app.services.fleet.vehicle_service import VehicleService
from app.services.fleet.maintenance_service import MaintenanceService
from app.services.fleet.fuel_service import FuelService
from app.services.fleet.incident_service import IncidentService
from app.services.fleet.document_service import DocumentService
from app.services.fleet.reservation_service import ReservationService
from app.services.fleet.assignment_service import AssignmentService

__all__ = [
    "VehicleService",
    "MaintenanceService",
    "FuelService",
    "IncidentService",
    "DocumentService",
    "ReservationService",
    "AssignmentService",
]
