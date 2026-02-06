"""
Fleet Management Pydantic Schemas.

Schemas for Fleet API endpoints covering vehicles, maintenance,
fuel logs, incidents, documents, and reservations.
"""

from app.schemas.fleet.vehicle import (
    VehicleBase,
    VehicleCreate,
    VehicleUpdate,
    VehicleRead,
    VehicleBrief,
    VehicleWithDetails,
    VehicleListResponse,
    FleetSummary,
    VehicleStatusChange,
    OdometerUpdate,
    VehicleDispose,
)
from app.schemas.fleet.maintenance import (
    MaintenanceCreate,
    MaintenanceUpdate,
    MaintenanceRead,
    MaintenanceBrief,
    MaintenanceComplete,
)
from app.schemas.fleet.fuel import (
    FuelLogCreate,
    FuelLogUpdate,
    FuelLogRead,
    FuelLogBrief,
    FuelEfficiencyReport,
)
from app.schemas.fleet.incident import (
    IncidentCreate,
    IncidentUpdate,
    IncidentRead,
    IncidentBrief,
    IncidentResolve,
)
from app.schemas.fleet.document import (
    DocumentCreate,
    DocumentUpdate,
    DocumentRead,
    DocumentBrief,
)
from app.schemas.fleet.reservation import (
    ReservationCreate,
    ReservationUpdate,
    ReservationRead,
    ReservationBrief,
    ReservationApprove,
    ReservationReject,
    ReservationCheckout,
    ReservationCheckin,
)
from app.schemas.fleet.assignment import (
    AssignmentCreate,
    AssignmentUpdate,
    AssignmentRead,
    AssignmentEnd,
)

__all__ = [
    # Vehicle
    "VehicleBase",
    "VehicleCreate",
    "VehicleUpdate",
    "VehicleRead",
    "VehicleBrief",
    "VehicleWithDetails",
    "VehicleListResponse",
    "FleetSummary",
    "VehicleStatusChange",
    "OdometerUpdate",
    "VehicleDispose",
    # Maintenance
    "MaintenanceCreate",
    "MaintenanceUpdate",
    "MaintenanceRead",
    "MaintenanceBrief",
    "MaintenanceComplete",
    # Fuel
    "FuelLogCreate",
    "FuelLogUpdate",
    "FuelLogRead",
    "FuelLogBrief",
    "FuelEfficiencyReport",
    # Incident
    "IncidentCreate",
    "IncidentUpdate",
    "IncidentRead",
    "IncidentBrief",
    "IncidentResolve",
    # Document
    "DocumentCreate",
    "DocumentUpdate",
    "DocumentRead",
    "DocumentBrief",
    # Reservation
    "ReservationCreate",
    "ReservationUpdate",
    "ReservationRead",
    "ReservationBrief",
    "ReservationApprove",
    "ReservationReject",
    "ReservationCheckout",
    "ReservationCheckin",
    # Assignment
    "AssignmentCreate",
    "AssignmentUpdate",
    "AssignmentRead",
    "AssignmentEnd",
]
