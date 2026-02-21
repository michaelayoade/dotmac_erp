"""
Fleet Web Service - Context builders for HTML routes.

Provides methods to build template context for fleet management pages.
"""

import logging
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

if TYPE_CHECKING:
    from fastapi import Request
    from fastapi.responses import RedirectResponse

from sqlalchemy import inspect, or_
from sqlalchemy import select as sa_select
from sqlalchemy.orm import Session

from app.models.expense.expense_claim import ExpenseClaim, ExpenseClaimStatus
from app.models.finance.core_org.location import Location
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
from app.models.people.hr.employee import Employee, EmployeeStatus
from app.services.common import NotFoundError, PaginationParams, coerce_uuid
from app.services.common_filters import build_active_filters
from app.services.fleet.assignment_service import AssignmentService
from app.services.fleet.document_service import DocumentService
from app.services.fleet.fuel_service import FuelService
from app.services.fleet.incident_service import IncidentService
from app.services.fleet.maintenance_service import MaintenanceService
from app.services.fleet.reservation_service import ReservationService
from app.services.fleet.vehicle_service import VehicleService
from app.services.recent_activity import get_recent_activity_for_record

logger = logging.getLogger(__name__)


class FleetWebService:
    """Web service methods for fleet management pages."""

    def __init__(self, db: Session):
        self.db = db

    def _fleet_tables_ready(self) -> bool:
        """Check if fleet schema tables exist to avoid hard failures."""
        try:
            inspector = inspect(self.db.get_bind())
            return inspector.has_table("vehicle", schema="fleet")
        except Exception:
            logger.exception("Failed to inspect fleet schema tables")
            return False

    def _get_locations(self, organization_id: UUID) -> list[Location]:
        """Get active locations (branches) for dropdowns."""
        stmt = (
            sa_select(Location)
            .where(
                Location.organization_id == organization_id,
                Location.is_active == True,  # noqa: E712
            )
            .order_by(Location.location_name.asc())
        )
        return list(self.db.scalars(stmt).all())

    def _get_employees(self, organization_id: UUID) -> list[Employee]:
        """Get active employees for dropdowns."""
        stmt = (
            sa_select(Employee)
            .where(
                Employee.organization_id == organization_id,
                Employee.status == EmployeeStatus.ACTIVE,
                Employee.is_deleted == False,  # noqa: E712
            )
            .order_by(Employee.employee_code.asc())
        )
        return list(self.db.scalars(stmt).all())

    def _empty_list_context(self) -> dict[str, Any]:
        return {
            "total": 0,
            "page": 1,
            "total_pages": 1,
            "has_next": False,
            "has_prev": False,
        }

    @staticmethod
    def expense_claim_typeahead(
        db: Session,
        organization_id: str,
        query: str,
        limit: int = 8,
    ) -> dict[str, list[dict[str, str]]]:
        """Search expense claims for fleet linkage fields."""
        org_id = coerce_uuid(organization_id)
        search_term = f"%{query.strip()}%"
        stmt = (
            sa_select(ExpenseClaim)
            .where(
                ExpenseClaim.organization_id == org_id,
                ExpenseClaim.status != ExpenseClaimStatus.CANCELLED,
            )
            .where(
                or_(
                    ExpenseClaim.claim_number.ilike(search_term),
                    ExpenseClaim.purpose.ilike(search_term),
                )
            )
            .order_by(ExpenseClaim.claim_date.desc(), ExpenseClaim.claim_number.desc())
            .limit(limit)
        )
        claims = list(db.scalars(stmt).all())

        items: list[dict[str, str]] = []
        for claim in claims:
            purpose = (claim.purpose or "").strip()
            status = claim.status.value if claim.status else ""
            label = claim.claim_number
            if purpose:
                short_purpose = purpose if len(purpose) <= 60 else f"{purpose[:57]}..."
                label = f"{claim.claim_number} - {short_purpose}"
            if status:
                label = f"{label} ({status})"

            items.append(
                {
                    "ref": str(claim.claim_id),
                    "label": label,
                    "name": claim.claim_number,
                    "claim_number": claim.claim_number,
                }
            )

        return {"items": items}

    # ─────────────────────────────────────────────────────────────
    # Dashboard
    # ─────────────────────────────────────────────────────────────

    def dashboard_context(self, organization_id: UUID) -> dict[str, Any]:
        """Build context for fleet dashboard page."""
        if not self._fleet_tables_ready():
            return {
                "summary": {},
                "due_maintenance": [],
                "due_maintenance_count": 0,
                "expiring_documents": [],
                "expiring_documents_count": 0,
                "pending_reservations": [],
                "pending_reservations_count": 0,
                "active_reservations": [],
                "open_incidents": [],
                "open_incidents_count": 0,
            }
        org_id = coerce_uuid(organization_id)
        vehicle_service = VehicleService(self.db, org_id)
        maintenance_service = MaintenanceService(self.db, org_id)
        reservation_service = ReservationService(self.db, org_id)
        document_service = DocumentService(self.db, org_id)
        incident_service = IncidentService(self.db, org_id)

        # Get summary statistics
        summary = vehicle_service.get_fleet_summary()

        # Get top items for dashboard widgets (SQL LIMIT)
        due_maintenance = maintenance_service.get_due_maintenance(days_ahead=7, limit=5)
        expiring_docs = document_service.get_expiring_documents(days_before=30, limit=5)
        pending_reservations = reservation_service.get_pending_reservations(limit=5)
        active_reservations = reservation_service.get_active_reservations(limit=10)
        open_incidents = incident_service.get_open_incidents(limit=5)

        # Get full counts separately (lightweight count queries)
        all_due = maintenance_service.get_due_maintenance(days_ahead=7)
        all_expiring = document_service.get_expiring_documents(days_before=30)
        all_pending = reservation_service.get_pending_reservations()

        return {
            "summary": summary,
            "due_maintenance": due_maintenance,
            "due_maintenance_count": len(all_due),
            "expiring_documents": expiring_docs,
            "expiring_documents_count": len(all_expiring),
            "pending_reservations": pending_reservations,
            "pending_reservations_count": len(all_pending),
            "active_reservations": active_reservations,
            "open_incidents": open_incidents,
            "open_incidents_count": len(open_incidents),
        }

    # ─────────────────────────────────────────────────────────────
    # Vehicles
    # ─────────────────────────────────────────────────────────────

    def vehicle_list_context(
        self,
        organization_id: UUID,
        *,
        status: str | None = None,
        vehicle_type: str | None = None,
        department_id: UUID | None = None,
        offset: int = 0,
        limit: int = 25,
    ) -> dict[str, Any]:
        """Build context for vehicles list page."""
        if not self._fleet_tables_ready():
            context = self._empty_list_context()
            context.update(
                {
                    "vehicles": [],
                    "status_counts": {},
                    "statuses": list(VehicleStatus),
                    "vehicle_types": list(VehicleType),
                    "assignment_types": list(AssignmentType),
                    "current_status": status,
                    "current_type": vehicle_type,
                    "current_department_id": department_id,
                }
            )
            return context
        org_id = coerce_uuid(organization_id)
        service = VehicleService(self.db, org_id)

        # Parse filters
        status_filter = VehicleStatus(status) if status else None
        type_filter = VehicleType(vehicle_type) if vehicle_type else None

        # Get vehicles
        params = PaginationParams(offset=offset, limit=limit)
        result = service.list_vehicles(
            status=status_filter,
            vehicle_type=type_filter,
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
            "statuses": list(VehicleStatus),
            "vehicle_types": list(VehicleType),
            "assignment_types": list(AssignmentType),
            "current_status": status,
            "current_type": vehicle_type,
            "current_department_id": department_id,
        }

    def vehicle_form_context(
        self,
        organization_id: UUID,
        vehicle_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Build context for vehicle create/edit form."""
        if not self._fleet_tables_ready():
            return {
                "vehicle_types": list(VehicleType),
                "fuel_types": list(FuelType),
                "ownership_types": list(OwnershipType),
                "assignment_types": list(AssignmentType),
                "locations": [],
                "employees": [],
                "vehicle": None,
            }
        org_id = coerce_uuid(organization_id)
        context: dict[str, Any] = {
            "vehicle_types": list(VehicleType),
            "fuel_types": list(FuelType),
            "ownership_types": list(OwnershipType),
            "assignment_types": list(AssignmentType),
            "locations": self._get_locations(org_id),
            "employees": self._get_employees(org_id),
            "vehicle": None,
        }

        if vehicle_id:
            service = VehicleService(self.db, org_id)
            context["vehicle"] = service.get_or_raise(vehicle_id)

        return context

    def vehicle_detail_context(
        self,
        organization_id: UUID,
        vehicle_id: UUID,
    ) -> dict[str, Any]:
        """Build context for vehicle detail page."""
        if not self._fleet_tables_ready():
            raise NotFoundError("Fleet module not initialized.")
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
            "recent_activity": get_recent_activity_for_record(
                self.db,
                org_id,
                record=vehicle,
                limit=10,
            ),
            "maintenance_records": maintenance_result.items,
            "documents": documents_result.items,
            "fuel_logs": fuel_result.items,
            "incidents": incidents_result.items,
            "reservations": reservations_result.items,
            "assignments": assignments_result.items,
            "fuel_efficiency": efficiency,
            "statuses": [s.value for s in VehicleStatus],
        }

    # ─────────────────────────────────────────────────────────────
    # Maintenance
    # ─────────────────────────────────────────────────────────────

    def maintenance_list_context(
        self,
        organization_id: UUID,
        *,
        vehicle_id: UUID | None = None,
        status: str | None = None,
        maintenance_type: str | None = None,
        offset: int = 0,
        limit: int = 25,
    ) -> dict[str, Any]:
        """Build context for maintenance list page."""
        if not self._fleet_tables_ready():
            context = self._empty_list_context()
            context.update(
                {
                    "maintenance_records": [],
                    "statuses": [s.value for s in MaintenanceStatus],
                    "maintenance_types": [t.value for t in MaintenanceType],
                    "current_status": status,
                    "current_maintenance_type": maintenance_type,
                    "current_vehicle_id": vehicle_id,
                }
            )
            return context
        org_id = coerce_uuid(organization_id)
        service = MaintenanceService(self.db, org_id)

        status_filter = MaintenanceStatus(status) if status else None
        type_filter = MaintenanceType(maintenance_type) if maintenance_type else None

        params = PaginationParams(offset=offset, limit=limit)
        result = service.list_records(
            vehicle_id=vehicle_id,
            status=status_filter,
            maintenance_type=type_filter,
            params=params,
        )

        active_filters = build_active_filters(
            params={
                "status": status,
                "maintenance_type": maintenance_type,
                "vehicle_id": str(vehicle_id) if vehicle_id else None,
            }
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
            "current_maintenance_type": maintenance_type,
            "current_vehicle_id": vehicle_id,
            "active_filters": active_filters,
        }

    def maintenance_form_context(
        self,
        organization_id: UUID,
        *,
        vehicle_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Build context for maintenance create form."""
        if not self._fleet_tables_ready():
            return {
                "vehicles": [],
                "maintenance_types": [t.value for t in MaintenanceType],
                "selected_vehicle_id": vehicle_id,
            }
        org_id = coerce_uuid(organization_id)
        vehicle_service = VehicleService(self.db, org_id)

        # Get active vehicles for dropdown
        vehicles_result = vehicle_service.list_vehicles(
            status=VehicleStatus.ACTIVE,
            params=PaginationParams(limit=200),
        )

        context: dict[str, Any] = {
            "vehicles": vehicles_result.items,
            "maintenance_types": [t.value for t in MaintenanceType],
            "selected_vehicle_id": vehicle_id,
        }

        return context

    def maintenance_detail_context(
        self,
        organization_id: UUID,
        record_id: UUID,
    ) -> dict[str, Any]:
        """Build context for maintenance detail page."""
        if not self._fleet_tables_ready():
            raise NotFoundError("Fleet module not initialized.")
        org_id = coerce_uuid(organization_id)
        service = MaintenanceService(self.db, org_id)
        record = service.get_or_raise(record_id)

        return {
            "record": record,
            "recent_activity": get_recent_activity_for_record(
                self.db,
                org_id,
                record=record,
                limit=10,
            ),
            "statuses": [s.value for s in MaintenanceStatus],
        }

    # ─────────────────────────────────────────────────────────────
    # Fuel Logs
    # ─────────────────────────────────────────────────────────────

    def fuel_list_context(
        self,
        organization_id: UUID,
        *,
        vehicle_id: UUID | None = None,
        offset: int = 0,
        limit: int = 25,
    ) -> dict[str, Any]:
        """Build context for fuel logs page."""
        if not self._fleet_tables_ready():
            context = self._empty_list_context()
            context.update(
                {
                    "fuel_logs": [],
                    "monthly_summary": [],
                    "fuel_types": [f.value for f in FuelType],
                    "current_vehicle_id": vehicle_id,
                }
            )
            return context
        org_id = coerce_uuid(organization_id)
        service = FuelService(self.db, org_id)

        params = PaginationParams(offset=offset, limit=limit)
        result = service.list_logs(
            vehicle_id=vehicle_id,
            params=params,
        )

        # Get monthly summary
        monthly_summary = service.get_monthly_summary(vehicle_id=vehicle_id)

        active_filters = build_active_filters(
            params={"vehicle_id": str(vehicle_id) if vehicle_id else None}
        )
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
            "active_filters": active_filters,
        }

    def fuel_form_context(
        self,
        organization_id: UUID,
        *,
        vehicle_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Build context for fuel log create form."""
        if not self._fleet_tables_ready():
            return {
                "vehicles": [],
                "fuel_types": [f.value for f in FuelType],
                "selected_vehicle_id": vehicle_id,
            }
        org_id = coerce_uuid(organization_id)
        vehicle_service = VehicleService(self.db, org_id)

        vehicles_result = vehicle_service.list_vehicles(
            status=VehicleStatus.ACTIVE,
            params=PaginationParams(limit=200),
        )

        return {
            "vehicles": vehicles_result.items,
            "fuel_types": [f.value for f in FuelType],
            "selected_vehicle_id": vehicle_id,
        }

    # ─────────────────────────────────────────────────────────────
    # Incidents
    # ─────────────────────────────────────────────────────────────

    def incident_list_context(
        self,
        organization_id: UUID,
        *,
        vehicle_id: UUID | None = None,
        status: str | None = None,
        severity: str | None = None,
        offset: int = 0,
        limit: int = 25,
    ) -> dict[str, Any]:
        """Build context for incidents list page."""
        if not self._fleet_tables_ready():
            context = self._empty_list_context()
            context.update(
                {
                    "incidents": [],
                    "cost_summary": {},
                    "statuses": [s.value for s in IncidentStatus],
                    "incident_types": [t.value for t in IncidentType],
                    "severities": [s.value for s in IncidentSeverity],
                    "current_status": status,
                    "current_severity": severity,
                    "current_vehicle_id": vehicle_id,
                }
            )
            return context
        org_id = coerce_uuid(organization_id)
        service = IncidentService(self.db, org_id)

        status_filter = IncidentStatus(status) if status else None
        severity_filter = IncidentSeverity(severity) if severity else None

        params = PaginationParams(offset=offset, limit=limit)
        result = service.list_incidents(
            vehicle_id=vehicle_id,
            status=status_filter,
            severity=severity_filter,
            params=params,
        )

        # Get cost summary
        cost_summary = service.get_cost_summary(vehicle_id=vehicle_id)

        active_filters = build_active_filters(
            params={
                "vehicle_id": str(vehicle_id) if vehicle_id else None,
                "status": status,
                "severity": severity,
            }
        )
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
            "current_severity": severity,
            "current_vehicle_id": vehicle_id,
            "active_filters": active_filters,
        }

    def incident_form_context(
        self,
        organization_id: UUID,
        *,
        vehicle_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Build context for incident report form."""
        if not self._fleet_tables_ready():
            return {
                "vehicles": [],
                "incident_types": [t.value for t in IncidentType],
                "severities": [s.value for s in IncidentSeverity],
                "selected_vehicle_id": vehicle_id,
            }
        org_id = coerce_uuid(organization_id)
        vehicle_service = VehicleService(self.db, org_id)

        vehicles_result = vehicle_service.list_vehicles(
            include_disposed=False,
            params=PaginationParams(limit=200),
        )

        return {
            "vehicles": vehicles_result.items,
            "incident_types": [t.value for t in IncidentType],
            "severities": [s.value for s in IncidentSeverity],
            "selected_vehicle_id": vehicle_id,
        }

    def incident_detail_context(
        self,
        organization_id: UUID,
        incident_id: UUID,
    ) -> dict[str, Any]:
        """Build context for incident detail page."""
        if not self._fleet_tables_ready():
            raise NotFoundError("Fleet module not initialized.")
        org_id = coerce_uuid(organization_id)
        service = IncidentService(self.db, org_id)
        incident = service.get_or_raise(incident_id)

        return {
            "incident": incident,
            "recent_activity": get_recent_activity_for_record(
                self.db,
                org_id,
                record=incident,
                limit=10,
            ),
            "statuses": [s.value for s in IncidentStatus],
        }

    # ─────────────────────────────────────────────────────────────
    # Reservations
    # ─────────────────────────────────────────────────────────────

    def reservation_list_context(
        self,
        organization_id: UUID,
        *,
        vehicle_id: UUID | None = None,
        status: str | None = None,
        offset: int = 0,
        limit: int = 25,
    ) -> dict[str, Any]:
        """Build context for reservations list page."""
        if not self._fleet_tables_ready():
            context = self._empty_list_context()
            context.update(
                {
                    "reservations": [],
                    "pending_count": 0,
                    "pool_vehicles": [],
                    "statuses": [s.value for s in ReservationStatus],
                    "current_status": status,
                    "current_vehicle_id": vehicle_id,
                }
            )
            return context
        org_id = coerce_uuid(organization_id)
        service = ReservationService(self.db, org_id)
        vehicle_service = VehicleService(self.db, org_id)

        status_filter = ReservationStatus(status) if status else None

        params = PaginationParams(offset=offset, limit=limit)
        result = service.list_reservations(
            vehicle_id=vehicle_id,
            status=status_filter,
            params=params,
        )

        # Get pending count
        pending = service.get_pending_reservations()

        # Get pool vehicles for reference
        pool_vehicles = vehicle_service.list_vehicles(
            assignment_type=AssignmentType.POOL,
            status=VehicleStatus.ACTIVE,
            params=PaginationParams(limit=100),
        )

        active_filters = build_active_filters(
            params={
                "vehicle_id": str(vehicle_id) if vehicle_id else None,
                "status": status,
            }
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
            "current_vehicle_id": vehicle_id,
            "active_filters": active_filters,
        }

    def reservation_form_context(
        self,
        organization_id: UUID,
    ) -> dict[str, Any]:
        """Build context for reservation create form."""
        if not self._fleet_tables_ready():
            return {"pool_vehicles": []}
        org_id = coerce_uuid(organization_id)
        vehicle_service = VehicleService(self.db, org_id)

        # Get available pool vehicles
        pool_vehicles = vehicle_service.list_vehicles(
            assignment_type=AssignmentType.POOL,
            status=VehicleStatus.ACTIVE,
            params=PaginationParams(limit=100),
        )

        return {
            "pool_vehicles": pool_vehicles.items,
        }

    def reservation_detail_context(
        self,
        organization_id: UUID,
        reservation_id: UUID,
    ) -> dict[str, Any]:
        """Build context for reservation detail page."""
        if not self._fleet_tables_ready():
            raise NotFoundError("Fleet module not initialized.")
        org_id = coerce_uuid(organization_id)
        service = ReservationService(self.db, org_id)
        reservation = service.get_or_raise(reservation_id)

        return {
            "reservation": reservation,
            "recent_activity": get_recent_activity_for_record(
                self.db,
                org_id,
                record=reservation,
                limit=10,
            ),
            "statuses": [s.value for s in ReservationStatus],
        }

    # ─────────────────────────────────────────────────────────────
    # Documents
    # ─────────────────────────────────────────────────────────────

    def document_list_context(
        self,
        organization_id: UUID,
        *,
        vehicle_id: UUID | None = None,
        document_type: str | None = None,
        expired_only: bool = False,
        expiring_soon: bool = False,
        offset: int = 0,
        limit: int = 25,
    ) -> dict[str, Any]:
        """Build context for documents list page."""
        if not self._fleet_tables_ready():
            context = self._empty_list_context()
            context.update(
                {
                    "documents": [],
                    "expiring_count": 0,
                    "expired_count": 0,
                    "document_types": [t.value for t in DocumentType],
                    "current_type": document_type,
                    "current_vehicle_id": vehicle_id,
                }
            )
            return context
        org_id = coerce_uuid(organization_id)
        service = DocumentService(self.db, org_id)

        type_filter = DocumentType(document_type) if document_type else None

        params = PaginationParams(offset=offset, limit=limit)
        result = service.list_documents(
            vehicle_id=vehicle_id,
            document_type=type_filter,
            expired_only=expired_only,
            expiring_soon=expiring_soon,
            params=params,
        )

        # Get expiring and expired counts
        expiring = service.get_expiring_documents(days_before=30)
        expired = service.get_expired_documents()

        active_filters = build_active_filters(
            params={
                "vehicle_id": str(vehicle_id) if vehicle_id else None,
                "document_type": document_type,
            }
        )
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
            "active_filters": active_filters,
        }

    def document_form_context(
        self,
        organization_id: UUID,
        *,
        vehicle_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Build context for document create form."""
        if not self._fleet_tables_ready():
            return {
                "vehicles": [],
                "document_types": [t.value for t in DocumentType],
                "selected_vehicle_id": vehicle_id,
            }
        org_id = coerce_uuid(organization_id)
        vehicle_service = VehicleService(self.db, org_id)

        vehicles_result = vehicle_service.list_vehicles(
            include_disposed=False,
            params=PaginationParams(limit=200),
        )

        return {
            "vehicles": vehicles_result.items,
            "document_types": [t.value for t in DocumentType],
            "selected_vehicle_id": vehicle_id,
        }

    def document_detail_context(
        self,
        organization_id: UUID,
        document_id: UUID,
    ) -> dict[str, Any]:
        """Build context for document detail page."""
        if not self._fleet_tables_ready():
            raise NotFoundError("Fleet module not initialized.")
        org_id = coerce_uuid(organization_id)
        service = DocumentService(self.db, org_id)
        doc = service.get_or_raise(document_id)

        return {
            "document": doc,
            "recent_activity": get_recent_activity_for_record(
                self.db,
                org_id,
                record=doc,
                limit=10,
            ),
            "document_types": [t.value for t in DocumentType],
        }

    # ─── POST form handlers ──────────────────────────────────────────────

    async def create_vehicle_response(
        self,
        request: "Request",
        organization_id: Any,
        user_id: Any,
        db: Session,
    ) -> "RedirectResponse":
        """Handle POST to create a new vehicle from form data."""
        from fastapi.responses import RedirectResponse

        from app.schemas.fleet.vehicle import VehicleCreate
        from app.services.fleet.vehicle_service import VehicleService

        form = await request.form()
        org_id = coerce_uuid(organization_id)
        try:
            reg_number = str(form.get("registration_number", ""))
            # Auto-generate vehicle_code from registration number
            vehicle_code = f"VEH-{reg_number.replace(' ', '').replace('-', '').upper()}"

            data = VehicleCreate(
                vehicle_code=vehicle_code,
                registration_number=reg_number,
                make=str(form.get("make", "")),
                model=str(form.get("model", "")),
                year=int(str(form.get("year", "2024"))),
                vehicle_type=VehicleType(str(form.get("vehicle_type", "sedan"))),
                fuel_type=FuelType(str(form.get("fuel_type", "petrol"))),
                ownership_type=OwnershipType(str(form.get("ownership_type", "owned"))),
                color=str(form.get("color", "")) or None,
                vin=str(form.get("vin_number", "")) or None,
                engine_number=str(form.get("engine_number", "")) or None,
                seating_capacity=int(str(form.get("seating_capacity", "5") or "5")),
                current_odometer=int(str(form.get("current_odometer_km", "0") or "0")),
                license_expiry_date=date.fromisoformat(str(form["license_expiry_date"]))
                if form.get("license_expiry_date")
                else None,
                purchase_date=date.fromisoformat(str(form["acquisition_date"]))
                if form.get("acquisition_date")
                else None,
                purchase_price=Decimal(str(form["purchase_price"]))
                if form.get("purchase_price")
                else None,
                location_id=UUID(str(form["location_id"]))
                if form.get("location_id")
                else None,
                vendor_id=UUID(str(form["supplier_id"]))
                if form.get("supplier_id")
                else None,
                assigned_employee_id=UUID(str(form["assigned_employee_id"]))
                if form.get("assigned_employee_id")
                else None,
                assignment_type=AssignmentType.POOL
                if "is_pool_vehicle" in form
                else AssignmentType.PERSONAL,
                notes=str(form.get("notes", "")) or None,
            )
            svc = VehicleService(db, org_id)
            vehicle = svc.create(data)
            db.commit()
            return RedirectResponse(
                url=f"/fleet/vehicles/{vehicle.vehicle_id}",
                status_code=303,
            )
        except (ValueError, RuntimeError) as exc:
            logger.warning("Vehicle creation failed: %s", exc)
            return RedirectResponse(
                url=f"/fleet/vehicles/new?error={exc}",
                status_code=303,
            )

    async def update_vehicle_response(
        self,
        request: "Request",
        organization_id: Any,
        vehicle_id: Any,
        db: Session,
    ) -> "RedirectResponse":
        """Handle POST to update an existing vehicle from form data."""
        from app.schemas.fleet.vehicle import VehicleUpdate
        from app.services.fleet.vehicle_service import VehicleService

        form = await request.form()
        org_id = coerce_uuid(organization_id)
        vid = coerce_uuid(vehicle_id)
        try:
            vtype_raw = str(form.get("vehicle_type", "")) or None
            ftype_raw = str(form.get("fuel_type", "")) or None
            otype_raw = str(form.get("ownership_type", "")) or None
            data = VehicleUpdate(
                registration_number=str(form.get("registration_number", "")) or None,
                vehicle_type=VehicleType(vtype_raw) if vtype_raw else None,
                fuel_type=FuelType(ftype_raw) if ftype_raw else None,
                color=str(form.get("color", "")) or None,
                vin=str(form.get("vin_number", "")) or None,
                engine_number=str(form.get("engine_number", "")) or None,
                seating_capacity=int(str(form.get("seating_capacity", "") or "0"))
                or None,
                ownership_type=OwnershipType(otype_raw) if otype_raw else None,
                purchase_date=date.fromisoformat(str(form["acquisition_date"]))
                if form.get("acquisition_date")
                else None,
                purchase_price=Decimal(str(form["purchase_price"]))
                if form.get("purchase_price")
                else None,
                license_expiry_date=date.fromisoformat(str(form["license_expiry_date"]))
                if form.get("license_expiry_date")
                else None,
                location_id=UUID(str(form["location_id"]))
                if form.get("location_id")
                else None,
                vendor_id=UUID(str(form["supplier_id"]))
                if form.get("supplier_id")
                else None,
                assigned_employee_id=UUID(str(form["assigned_employee_id"]))
                if form.get("assigned_employee_id")
                else None,
                assignment_type=AssignmentType.POOL
                if "is_pool_vehicle" in form
                else AssignmentType.PERSONAL,
                notes=str(form.get("notes", "")) or None,
            )
            svc = VehicleService(db, org_id)
            svc.update(vid, data)
            db.commit()
            return RedirectResponse(
                url=f"/fleet/vehicles/{vehicle_id}",
                status_code=303,
            )
        except (ValueError, RuntimeError) as exc:
            logger.warning("Vehicle update failed: %s", exc)
            return RedirectResponse(
                url=f"/fleet/vehicles/{vehicle_id}/edit?error={exc}",
                status_code=303,
            )

    async def create_entity_response(
        self,
        request: "Request",
        auth: Any,
        db: Session,
        entity_type: str,
    ) -> Any:
        """Generic handler for fleet entity form creation.

        Parses form data, calls the appropriate service, and redirects.
        """
        from datetime import date, datetime
        from decimal import Decimal, InvalidOperation

        from fastapi import HTTPException
        from fastapi.responses import RedirectResponse

        form = await request.form()
        form_data = dict(form)
        org_id = auth.organization_id
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")

        user_id = getattr(auth, "user_id", None) or getattr(auth, "person_id", None)

        # Build a cleaned data dict, coercing types
        data: dict[str, Any] = {}
        for key, val in form_data.items():
            if key.startswith("csrf") or key == "_method":
                continue
            str_val = str(val).strip() if val else ""
            if not str_val:
                data[key] = None
                continue
            # UUID fields
            if key.endswith("_id"):
                try:
                    data[key] = UUID(str_val)
                except ValueError:
                    data[key] = str_val
            # Date fields
            elif key.endswith("_date") or key in (
                "scheduled_date",
                "issue_date",
                "expiry_date",
                "incident_date",
                "log_date",
            ):
                try:
                    data[key] = date.fromisoformat(str_val)
                except ValueError:
                    data[key] = str_val
            # Datetime fields
            elif key.endswith("_datetime"):
                try:
                    data[key] = datetime.fromisoformat(str_val)
                except ValueError:
                    data[key] = str_val
            # Numeric fields
            elif key in (
                "estimated_repair_cost",
                "estimated_cost",
                "price_per_liter",
                "total_cost",
                "quantity_liters",
                "odometer_reading",
                "odometer_at_service",
                "estimated_distance_km",
                "coverage_amount",
                "premium_amount",
                "reminder_days_before",
            ):
                try:
                    data[key] = Decimal(str_val)
                except (ValueError, InvalidOperation):
                    data[key] = str_val
            # Boolean fields
            elif key in ("third_party_involved", "is_full_tank"):
                data[key] = str_val.lower() in ("true", "1", "on", "yes")
            else:
                data[key] = str_val

        # Map entity type to service + schema + list URL
        from app.schemas.fleet.document import DocumentCreate
        from app.schemas.fleet.fuel import FuelLogCreate
        from app.schemas.fleet.incident import IncidentCreate
        from app.schemas.fleet.maintenance import MaintenanceCreate
        from app.schemas.fleet.reservation import ReservationCreate

        entity_map: dict[str, dict[str, Any]] = {
            "incident": {
                "service_cls": IncidentService,
                "schema_cls": IncidentCreate,
                "list_url": "/fleet/incidents",
                "extra_fields": {"reported_by_id": user_id},
            },
            "reservation": {
                "service_cls": ReservationService,
                "schema_cls": ReservationCreate,
                "list_url": "/fleet/reservations",
                "extra_fields": {"employee_id": user_id},
            },
            "document": {
                "service_cls": DocumentService,
                "schema_cls": DocumentCreate,
                "list_url": "/fleet/documents",
                "extra_fields": {},
            },
            "fuel": {
                "service_cls": FuelService,
                "schema_cls": FuelLogCreate,
                "list_url": "/fleet/fuel",
                "extra_fields": {"employee_id": user_id},
            },
            "maintenance": {
                "service_cls": MaintenanceService,
                "schema_cls": MaintenanceCreate,
                "list_url": "/fleet/maintenance",
                "extra_fields": {},
            },
        }

        cfg = entity_map.get(entity_type)
        if not cfg:
            raise HTTPException(
                status_code=400, detail=f"Unknown entity type: {entity_type}"
            )

        # Add inferred fields
        data.update(cfg["extra_fields"])

        try:
            schema = cfg["schema_cls"](**data)
            service = cfg["service_cls"](db, org_id)
            service.create(schema)
            db.commit()
            logger.info("Created fleet %s for org %s", entity_type, org_id)
        except Exception as e:
            logger.warning("Fleet %s creation failed: %s", entity_type, e)
            db.rollback()
            return RedirectResponse(
                url=f"{cfg['list_url']}?error={str(e)[:200]}",
                status_code=303,
            )

        return RedirectResponse(
            url=cfg["list_url"],
            status_code=303,
        )

    async def cancel_reservation_response(
        self,
        request: "Request",
        auth: Any,
        db: Session,
        reservation_id: UUID,
    ) -> Any:
        """Cancel a reservation and redirect."""
        from fastapi import HTTPException
        from fastapi.responses import RedirectResponse

        await request.form()  # consume form body for CSRF validation
        org_id = auth.organization_id
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")

        try:
            service = ReservationService(db, org_id)
            service.cancel(reservation_id)
            db.commit()
        except Exception as e:
            logger.warning("Reservation cancel failed: %s", e)
            db.rollback()
            return RedirectResponse(
                url=f"/fleet/reservations/{reservation_id}?error={str(e)[:200]}",
                status_code=303,
            )

        return RedirectResponse(
            url="/fleet/reservations",
            status_code=303,
        )
