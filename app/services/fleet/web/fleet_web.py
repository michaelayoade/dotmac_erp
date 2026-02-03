"""
Fleet Web Service - Context builders for HTML routes.

Provides methods to build template context for fleet management pages.
"""
import logging
from typing import Any, Dict, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.fleet.enums import (
    AssignmentType,
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
from app.services.common import PaginationParams, coerce_uuid
from app.services.fleet.assignment_service import AssignmentService
from app.services.fleet.document_service import DocumentService
from app.services.fleet.fuel_service import FuelService
from app.services.fleet.incident_service import IncidentService
from app.services.fleet.maintenance_service import MaintenanceService
from app.services.fleet.reservation_service import ReservationService
from app.services.fleet.vehicle_service import VehicleService

logger = logging.getLogger(__name__)


class FleetWebService:
    """Web service methods for fleet management pages."""

    def __init__(self, db: Session):
        self.db = db

    def dashboard_context(self, organization_id: UUID) -> Dict[str, Any]:
        """Build context for fleet dashboard page."""
        org_id = coerce_uuid(organization_id)
        vehicle_service = VehicleService(self.db, org_id)
        maintenance_service = MaintenanceService(self.db, org_id)
        reservation_service = ReservationService(self.db, org_id)
        document_service = DocumentService(self.db, org_id)
        incident_service = IncidentService(self.db, org_id)

        # Get summary statistics
        summary = vehicle_service.get_fleet_summary()

        # Get due maintenance
        due_maintenance = maintenance_service.get_due_maintenance(days_ahead=7)

        # Get expiring documents
        expiring_docs = document_service.get_expiring_documents(days_before=30)

        # Get pending reservations
        pending_reservations = reservation_service.get_pending_reservations()

        # Get active reservations
        active_reservations = reservation_service.get_active_reservations()

        # Get open incidents
        open_incidents = incident_service.get_open_incidents()

        return {
            "summary": summary,
            "due_maintenance": due_maintenance[:5],
            "due_maintenance_count": len(due_maintenance),
            "expiring_documents": expiring_docs[:5],
            "expiring_documents_count": len(expiring_docs),
            "pending_reservations": pending_reservations[:5],
            "pending_reservations_count": len(pending_reservations),
            "active_reservations": active_reservations,
            "open_incidents": open_incidents[:5],
            "open_incidents_count": len(open_incidents),
        }

    def vehicles_list_context(
        self,
        organization_id: UUID,
        *,
        status: Optional[str] = None,
        vehicle_type: Optional[str] = None,
        assignment_type: Optional[str] = None,
        search: Optional[str] = None,
        page: int = 1,
    ) -> Dict[str, Any]:
        """Build context for vehicles list page."""
        org_id = coerce_uuid(organization_id)
        service = VehicleService(self.db, org_id)

        # Parse filters
        status_filter = VehicleStatus(status) if status else None
        type_filter = VehicleType(vehicle_type) if vehicle_type else None
        assignment_filter = AssignmentType(assignment_type) if assignment_type else None

        # Get vehicles
        params = PaginationParams.from_page(page, per_page=25)
        result = service.list_vehicles(
            status=status_filter,
            vehicle_type=type_filter,
            assignment_type=assignment_filter,
            search=search,
            params=params,
        )

        # Get status counts
        status_counts = service.count_by_status()

        return {
            "vehicles": result.items,
            "total": result.total,
            "page": result.page,
            "total_pages": result.total_pages,
            "has_next": result.has_next,
            "has_prev": result.has_prev,
            "status_counts": status_counts,
            "statuses": [s.value for s in VehicleStatus],
            "vehicle_types": [t.value for t in VehicleType],
            "assignment_types": [a.value for a in AssignmentType],
            "current_status": status,
            "current_type": vehicle_type,
            "current_assignment": assignment_type,
            "search": search,
        }

    def vehicle_form_context(self, organization_id: UUID) -> Dict[str, Any]:
        """Build context for vehicle create/edit form."""
        return {
            "vehicle_types": [t.value for t in VehicleType],
            "fuel_types": [f.value for f in FuelType],
            "ownership_types": [o.value for o in OwnershipType],
            "assignment_types": [a.value for a in AssignmentType],
        }

    def vehicle_detail_context(
        self,
        organization_id: UUID,
        vehicle_id: str,
    ) -> Dict[str, Any]:
        """Build context for vehicle detail page."""
        org_id = coerce_uuid(organization_id)
        vid = coerce_uuid(vehicle_id)

        vehicle_service = VehicleService(self.db, org_id)
        maintenance_service = MaintenanceService(self.db, org_id)
        document_service = DocumentService(self.db, org_id)
        fuel_service = FuelService(self.db, org_id)
        incident_service = IncidentService(self.db, org_id)
        reservation_service = ReservationService(self.db, org_id)
        assignment_service = AssignmentService(self.db, org_id)

        vehicle = vehicle_service.get_or_raise(vid)

        # Get recent maintenance
        maintenance_result = maintenance_service.list_records(
            vehicle_id=vid,
            params=PaginationParams(limit=5),
        )

        # Get documents
        documents_result = document_service.list_documents(
            vehicle_id=vid,
            params=PaginationParams(limit=10),
        )

        # Get recent fuel logs
        fuel_result = fuel_service.list_logs(
            vehicle_id=vid,
            params=PaginationParams(limit=10),
        )

        # Get incidents
        incidents_result = incident_service.list_incidents(
            vehicle_id=vid,
            params=PaginationParams(limit=5),
        )

        # Get reservations
        reservations_result = reservation_service.list_reservations(
            vehicle_id=vid,
            params=PaginationParams(limit=5),
        )

        # Get assignment history
        assignments_result = assignment_service.list_assignments(
            vehicle_id=vid,
            params=PaginationParams(limit=5),
        )

        # Calculate fuel efficiency
        efficiency = fuel_service.calculate_efficiency(vid)

        return {
            "vehicle": vehicle,
            "maintenance_records": maintenance_result.items,
            "documents": documents_result.items,
            "fuel_logs": fuel_result.items,
            "incidents": incidents_result.items,
            "reservations": reservations_result.items,
            "assignments": assignments_result.items,
            "fuel_efficiency": efficiency,
            "statuses": [s.value for s in VehicleStatus],
        }

    def maintenance_list_context(
        self,
        organization_id: UUID,
        *,
        status: Optional[str] = None,
        vehicle_id: Optional[str] = None,
        page: int = 1,
    ) -> Dict[str, Any]:
        """Build context for maintenance list page."""
        org_id = coerce_uuid(organization_id)
        service = MaintenanceService(self.db, org_id)

        status_filter = MaintenanceStatus(status) if status else None
        vid = coerce_uuid(vehicle_id, raise_http=False) if vehicle_id else None

        params = PaginationParams.from_page(page, per_page=25)
        result = service.list_records(
            vehicle_id=vid,
            status=status_filter,
            params=params,
        )

        return {
            "maintenance_records": result.items,
            "total": result.total,
            "page": result.page,
            "total_pages": result.total_pages,
            "has_next": result.has_next,
            "has_prev": result.has_prev,
            "statuses": [s.value for s in MaintenanceStatus],
            "maintenance_types": [t.value for t in MaintenanceType],
            "current_status": status,
            "current_vehicle_id": vehicle_id,
        }

    def fuel_logs_context(
        self,
        organization_id: UUID,
        *,
        vehicle_id: Optional[str] = None,
        page: int = 1,
    ) -> Dict[str, Any]:
        """Build context for fuel logs page."""
        org_id = coerce_uuid(organization_id)
        service = FuelService(self.db, org_id)

        vid = coerce_uuid(vehicle_id, raise_http=False) if vehicle_id else None

        params = PaginationParams.from_page(page, per_page=25)
        result = service.list_logs(
            vehicle_id=vid,
            params=params,
        )

        # Get monthly summary
        monthly_summary = service.get_monthly_summary(vehicle_id=vid)

        return {
            "fuel_logs": result.items,
            "total": result.total,
            "page": result.page,
            "total_pages": result.total_pages,
            "has_next": result.has_next,
            "has_prev": result.has_prev,
            "monthly_summary": monthly_summary[:6],
            "fuel_types": [f.value for f in FuelType],
            "current_vehicle_id": vehicle_id,
        }

    def incidents_context(
        self,
        organization_id: UUID,
        *,
        status: Optional[str] = None,
        vehicle_id: Optional[str] = None,
        page: int = 1,
    ) -> Dict[str, Any]:
        """Build context for incidents page."""
        org_id = coerce_uuid(organization_id)
        service = IncidentService(self.db, org_id)

        status_filter = IncidentStatus(status) if status else None
        vid = coerce_uuid(vehicle_id, raise_http=False) if vehicle_id else None

        params = PaginationParams.from_page(page, per_page=25)
        result = service.list_incidents(
            vehicle_id=vid,
            status=status_filter,
            params=params,
        )

        # Get cost summary
        cost_summary = service.get_cost_summary(vehicle_id=vid)

        return {
            "incidents": result.items,
            "total": result.total,
            "page": result.page,
            "total_pages": result.total_pages,
            "has_next": result.has_next,
            "has_prev": result.has_prev,
            "cost_summary": cost_summary,
            "statuses": [s.value for s in IncidentStatus],
            "incident_types": [t.value for t in IncidentType],
            "severities": [s.value for s in IncidentSeverity],
            "current_status": status,
            "current_vehicle_id": vehicle_id,
        }

    def reservations_context(
        self,
        organization_id: UUID,
        *,
        status: Optional[str] = None,
        page: int = 1,
    ) -> Dict[str, Any]:
        """Build context for reservations page."""
        org_id = coerce_uuid(organization_id)
        service = ReservationService(self.db, org_id)
        vehicle_service = VehicleService(self.db, org_id)

        status_filter = ReservationStatus(status) if status else None

        params = PaginationParams.from_page(page, per_page=25)
        result = service.list_reservations(
            status=status_filter,
            params=params,
        )

        # Get pending count
        pending = service.get_pending_reservations()

        # Get pool vehicles for new reservations
        pool_vehicles = vehicle_service.list_vehicles(
            assignment_type=AssignmentType.POOL,
            status=VehicleStatus.ACTIVE,
            params=PaginationParams(limit=100),
        )

        return {
            "reservations": result.items,
            "total": result.total,
            "page": result.page,
            "total_pages": result.total_pages,
            "has_next": result.has_next,
            "has_prev": result.has_prev,
            "pending_count": len(pending),
            "pool_vehicles": pool_vehicles.items,
            "statuses": [s.value for s in ReservationStatus],
            "current_status": status,
        }

    def documents_context(
        self,
        organization_id: UUID,
        *,
        vehicle_id: Optional[str] = None,
        document_type: Optional[str] = None,
        page: int = 1,
    ) -> Dict[str, Any]:
        """Build context for documents page."""
        org_id = coerce_uuid(organization_id)
        service = DocumentService(self.db, org_id)

        vid = coerce_uuid(vehicle_id, raise_http=False) if vehicle_id else None
        type_filter = DocumentType(document_type) if document_type else None

        params = PaginationParams.from_page(page, per_page=25)
        result = service.list_documents(
            vehicle_id=vid,
            document_type=type_filter,
            params=params,
        )

        # Get expiring and expired counts
        expiring = service.get_expiring_documents(days_before=30)
        expired = service.get_expired_documents()

        return {
            "documents": result.items,
            "total": result.total,
            "page": result.page,
            "total_pages": result.total_pages,
            "has_next": result.has_next,
            "has_prev": result.has_prev,
            "expiring_count": len(expiring),
            "expired_count": len(expired),
            "document_types": [t.value for t in DocumentType],
            "current_type": document_type,
            "current_vehicle_id": vehicle_id,
        }
