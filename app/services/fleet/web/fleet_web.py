"""
Fleet Web Service - Context builders for HTML routes.

Provides methods to build template context for fleet management pages.
"""

import logging
from typing import Any
from uuid import UUID

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
            self.db.query(Location)
            .filter(
                Location.organization_id == organization_id,
                Location.is_active == True,  # noqa: E712
            )
            .order_by(Location.location_name.asc())
        )
        return list(stmt.all())

    def _get_employees(self, organization_id: UUID) -> list[Employee]:
        """Get active employees for dropdowns."""
        stmt = (
            self.db.query(Employee)
            .filter(
                Employee.organization_id == organization_id,
                Employee.status == EmployeeStatus.ACTIVE,
                Employee.is_deleted == False,  # noqa: E712
            )
            .order_by(Employee.employee_code.asc())
        )
        return list(stmt.all())

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
            "document_types": [t.value for t in DocumentType],
        }
