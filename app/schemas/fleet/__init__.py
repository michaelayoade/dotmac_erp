"""
Fleet Management Pydantic Schemas.

Schemas for Fleet API endpoints covering vehicles, maintenance,
fuel logs, incidents, documents, and reservations.
"""

from app.schemas.fleet.assignment import (
    AssignmentCreate,
    AssignmentEnd,
    AssignmentRead,
    AssignmentUpdate,
)
from app.schemas.fleet.document import (
    DocumentBrief,
    DocumentCreate,
    DocumentRead,
    DocumentUpdate,
)
from app.schemas.fleet.fuel import (
    FuelEfficiencyReport,
    FuelLogBrief,
    FuelLogCreate,
    FuelLogRead,
    FuelLogUpdate,
)
from app.schemas.fleet.incident import (
    IncidentBrief,
    IncidentCreate,
    IncidentRead,
    IncidentResolve,
    IncidentUpdate,
)
from app.schemas.fleet.maintenance import (
    MaintenanceBrief,
    MaintenanceComplete,
    MaintenanceCreate,
    MaintenanceRead,
    MaintenanceUpdate,
)
from app.schemas.fleet.reservation import (
    ReservationApprove,
    ReservationBrief,
    ReservationCheckin,
    ReservationCheckout,
    ReservationCreate,
    ReservationRead,
    ReservationReject,
    ReservationUpdate,
)
from app.schemas.fleet.vehicle import (
    FleetSummary,
    OdometerUpdate,
    VehicleBase,
    VehicleBrief,
    VehicleCreate,
    VehicleDispose,
    VehicleListResponse,
    VehicleRead,
    VehicleStatusChange,
    VehicleUpdate,
    VehicleWithDetails,
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
