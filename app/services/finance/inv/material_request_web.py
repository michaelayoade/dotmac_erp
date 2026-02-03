"""
Material Request Web View Service.

Provides view-focused data for material request web routes.
"""

import json
import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models.finance.inv import (
    Item,
    MaterialRequest,
    MaterialRequestItem,
    MaterialRequestStatus,
    MaterialRequestType,
    Warehouse,
)
from app.models.finance.core_org.project import Project, ProjectStatus
from app.models.people.hr import Employee, EmployeeStatus
from app.models.person import Person
from app.services.common import PaginationParams, coerce_uuid, paginate

logger = logging.getLogger(__name__)


# Valid material request status transitions
MATERIAL_REQUEST_STATUS_TRANSITIONS: Dict[MaterialRequestStatus, set] = {
    MaterialRequestStatus.DRAFT: {
        MaterialRequestStatus.SUBMITTED,
        MaterialRequestStatus.CANCELLED,
    },
    MaterialRequestStatus.SUBMITTED: {
        MaterialRequestStatus.PARTIALLY_ORDERED,
        MaterialRequestStatus.ORDERED,
        MaterialRequestStatus.ISSUED,
        MaterialRequestStatus.TRANSFERRED,
        MaterialRequestStatus.CANCELLED,
    },
    MaterialRequestStatus.PARTIALLY_ORDERED: {
        MaterialRequestStatus.ORDERED,
        MaterialRequestStatus.ISSUED,
        MaterialRequestStatus.TRANSFERRED,
        MaterialRequestStatus.CANCELLED,
    },
    MaterialRequestStatus.ORDERED: set(),  # Terminal
    MaterialRequestStatus.ISSUED: set(),  # Terminal
    MaterialRequestStatus.TRANSFERRED: set(),  # Terminal
    MaterialRequestStatus.CANCELLED: set(),  # Terminal
}


def _format_date(value: Optional[date]) -> str:
    return value.strftime("%Y-%m-%d") if value else ""


def _format_datetime(value: Optional[datetime]) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value else ""


def _format_quantity(qty: Optional[Decimal]) -> str:
    """Format a quantity for display."""
    if qty is None:
        return "0.00"
    return f"{Decimal(str(qty)):,.2f}"


class MaterialRequestWebService:
    """View service for material request web routes."""

    def __init__(self, db: Session, organization_id: UUID):
        self.db = db
        self.organization_id = organization_id

    def _validate_transition(
        self, current: MaterialRequestStatus, target: MaterialRequestStatus
    ) -> None:
        """Validate a status transition against the transition map."""
        allowed = MATERIAL_REQUEST_STATUS_TRANSITIONS.get(current, set())
        if target not in allowed:
            raise ValueError(
                f"Cannot transition from {current.value} to {target.value}"
            )

    def _batch_load_warehouses(
        self,
        warehouse_ids: list[UUID],
    ) -> dict[UUID, Any]:
        """Batch load warehouses by IDs."""
        if not warehouse_ids:
            return {}
        unique_ids = list(set(warehouse_ids))
        stmt = select(Warehouse).where(
            Warehouse.warehouse_id.in_(unique_ids),
            Warehouse.organization_id == self.organization_id,
        )
        warehouses = list(self.db.scalars(stmt).all())
        return {w.warehouse_id: w for w in warehouses}

    def _batch_load_employee_names(
        self,
        employee_ids: list[UUID],
    ) -> dict[UUID, str]:
        """Batch load employee display names by IDs."""
        if not employee_ids:
            return {}
        unique_ids = list(set(employee_ids))
        stmt = (
            select(Employee)
            .options(selectinload(Employee.person))
            .where(
                Employee.employee_id.in_(unique_ids),
                Employee.organization_id == self.organization_id,
            )
        )
        employees = list(self.db.scalars(stmt).all())
        return {
            emp.employee_id: (emp.person.name if emp.person else "")
            for emp in employees
        }

    def list_context(
        self,
        *,
        status: Optional[str] = None,
        request_type: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        project_id: Optional[str] = None,
        page: int = 1,
        limit: int = 50,
    ) -> dict:
        """Get context for material request list page."""
        stmt = (
            select(MaterialRequest)
            .options(selectinload(MaterialRequest.items))
            .where(MaterialRequest.organization_id == self.organization_id)
        )

        if status:
            try:
                stmt = stmt.where(
                    MaterialRequest.status == MaterialRequestStatus(status)
                )
            except ValueError:
                pass

        if request_type:
            try:
                stmt = stmt.where(
                    MaterialRequest.request_type == MaterialRequestType(request_type)
                )
            except ValueError:
                pass

        if start_date:
            stmt = stmt.where(MaterialRequest.schedule_date >= start_date)

        if end_date:
            stmt = stmt.where(MaterialRequest.schedule_date <= end_date)

        if project_id:
            stmt = stmt.where(
                MaterialRequest.request_id.in_(
                    select(MaterialRequestItem.request_id).where(
                        MaterialRequestItem.project_id == coerce_uuid(project_id)
                    )
                )
            )

        stmt = stmt.order_by(MaterialRequest.created_at.desc())
        pagination_params = PaginationParams.from_page(page, limit)
        result = paginate(self.db, stmt, pagination_params)
        requests = result.items

        # Batch load related data to avoid N+1 queries
        warehouse_ids = [
            r.default_warehouse_id for r in requests if r.default_warehouse_id
        ]
        employee_ids = [r.requested_by_id for r in requests if r.requested_by_id]
        warehouses_map = self._batch_load_warehouses(warehouse_ids)
        employees_map = self._batch_load_employee_names(employee_ids)

        items = []
        for req in requests:
            total_qty = (
                sum((item.requested_qty for item in req.items), Decimal("0"))
                if req.items
                else Decimal("0")
            )
            total_ordered = (
                sum((item.ordered_qty for item in req.items), Decimal("0"))
                if req.items
                else Decimal("0")
            )
            wh = (
                warehouses_map.get(req.default_warehouse_id)
                if req.default_warehouse_id
                else None
            )
            requested_by_name = (
                employees_map.get(req.requested_by_id) if req.requested_by_id else None
            )

            items.append(
                {
                    "request_id": str(req.request_id),
                    "request_number": req.request_number,
                    "request_type": req.request_type.value,
                    "status": req.status.value,
                    "schedule_date": _format_date(req.schedule_date),
                    "remarks": (
                        (req.remarks or "")[:100] + "..."
                        if req.remarks and len(req.remarks) > 100
                        else (req.remarks or "-")
                    ),
                    "item_count": len(req.items) if req.items else 0,
                    "total_qty": _format_quantity(total_qty),
                    "total_ordered": _format_quantity(total_ordered),
                    "created_at": _format_datetime(req.created_at),
                    "warehouse_name": wh.warehouse_name if wh else None,
                    "requested_by_name": requested_by_name,
                }
            )

        # Status counts via SQL
        status_stmt = (
            select(MaterialRequest.status, func.count())
            .where(MaterialRequest.organization_id == self.organization_id)
            .group_by(MaterialRequest.status)
        )
        status_counts = self.db.execute(status_stmt).all()
        counts = {s.value: c for s, c in status_counts}

        # Type counts via SQL
        type_stmt = (
            select(MaterialRequest.request_type, func.count())
            .where(MaterialRequest.organization_id == self.organization_id)
            .group_by(MaterialRequest.request_type)
        )
        type_counts = self.db.execute(type_stmt).all()
        type_count_dict = {t.value: c for t, c in type_counts}

        return {
            "requests": items,
            "filter_status": status,
            "filter_request_type": request_type,
            "filter_start_date": start_date,
            "filter_end_date": end_date,
            "filter_project_id": project_id,
            "status_counts": counts,
            "type_counts": type_count_dict,
            "statuses": [s.value for s in MaterialRequestStatus],
            "request_types": [t.value for t in MaterialRequestType],
            "page": result.page,
            "total_pages": result.total_pages,
            "total_count": result.total,
            "limit": limit,
            "has_next": result.has_next,
            "has_prev": result.has_prev,
        }

    def form_context(
        self,
        request_id: Optional[str] = None,
    ) -> dict:
        """Get context for material request form (new/edit)."""
        # Get items for selection
        item_stmt = (
            select(Item)
            .where(
                Item.organization_id == self.organization_id,
                Item.is_active.is_(True),
            )
            .order_by(Item.item_code)
        )
        items = list(self.db.scalars(item_stmt).all())

        item_options = [
            {
                "item_id": str(i.item_id),
                "item_code": i.item_code,
                "item_name": i.item_name,
                "base_uom": i.base_uom,
            }
            for i in items
        ]

        # Get warehouses
        wh_stmt = (
            select(Warehouse)
            .where(
                Warehouse.organization_id == self.organization_id,
                Warehouse.is_active.is_(True),
            )
            .order_by(Warehouse.warehouse_code)
        )
        warehouses = list(self.db.scalars(wh_stmt).all())

        warehouse_options = [
            {
                "warehouse_id": str(w.warehouse_id),
                "warehouse_code": w.warehouse_code,
                "warehouse_name": w.warehouse_name,
            }
            for w in warehouses
        ]

        # Get active projects
        proj_stmt = (
            select(Project)
            .where(
                Project.organization_id == self.organization_id,
                Project.status == ProjectStatus.ACTIVE,
            )
            .order_by(Project.project_code)
        )
        projects = list(self.db.scalars(proj_stmt).all())

        project_options = [
            {
                "project_id": str(p.project_id),
                "project_code": p.project_code,
                "project_name": p.project_name,
            }
            for p in projects
        ]

        context: dict[str, Any] = {
            "inventory_items": item_options,
            "warehouses": warehouse_options,
            "projects": project_options,
            "request_types": [t.value for t in MaterialRequestType],
            "today": _format_date(date.today()),
            "material_request": None,
            "items_json": "[]",
        }

        # If editing, load request data
        if request_id:
            mr_stmt = (
                select(MaterialRequest)
                .options(joinedload(MaterialRequest.items))
                .where(
                    MaterialRequest.request_id == coerce_uuid(request_id),
                    MaterialRequest.organization_id == self.organization_id,
                )
            )
            material_request = self.db.scalar(mr_stmt)
            if material_request:
                requested_by_name = ""
                if material_request.requested_by_id:
                    names = self._batch_load_employee_names(
                        [material_request.requested_by_id]
                    )
                    requested_by_name = names.get(material_request.requested_by_id, "")
                context["material_request"] = {
                    "request_id": str(material_request.request_id),
                    "request_number": material_request.request_number,
                    "request_type": material_request.request_type.value,
                    "status": material_request.status.value,
                    "schedule_date": _format_date(material_request.schedule_date),
                    "default_warehouse_id": (
                        str(material_request.default_warehouse_id)
                        if material_request.default_warehouse_id
                        else ""
                    ),
                    "requested_by_id": (
                        str(material_request.requested_by_id)
                        if material_request.requested_by_id
                        else ""
                    ),
                    "requested_by_name": requested_by_name,
                    "remarks": material_request.remarks or "",
                    "can_edit": material_request.status == MaterialRequestStatus.DRAFT,
                }
                request_items = [
                    {
                        "item_id": str(item.inventory_item_id),
                        "warehouse_id": (
                            str(item.warehouse_id) if item.warehouse_id else ""
                        ),
                        "qty": float(item.requested_qty),
                        "uom": item.uom or "Nos",
                        "schedule_date": _format_date(item.schedule_date),
                        "project_id": (str(item.project_id) if item.project_id else ""),
                    }
                    for item in sorted(material_request.items, key=lambda x: x.sequence)
                ]
                context["items_json"] = json.dumps(request_items)

        return context

    def requested_by_typeahead(
        self,
        query: str,
        limit: int = 8,
    ) -> dict:
        """Search active employees for requested-by typeahead."""
        search_term = f"%{query.strip()}%"
        stmt = (
            select(Employee)
            .join(Person, Person.id == Employee.person_id)
            .options(selectinload(Employee.person))
            .where(
                Employee.organization_id == self.organization_id,
                Employee.status == EmployeeStatus.ACTIVE,
            )
            .where(
                (Person.first_name.ilike(search_term))
                | (Person.last_name.ilike(search_term))
                | (Person.email.ilike(search_term))
                | (Employee.employee_code.ilike(search_term))
            )
            .order_by(Person.first_name.asc(), Person.last_name.asc())
            .limit(limit)
        )
        employees = list(self.db.scalars(stmt).all())
        items = []
        for employee in employees:
            name = employee.person.name if employee.person else ""
            label = name
            if employee.employee_code:
                label = (
                    f"{name} ({employee.employee_code})"
                    if name
                    else employee.employee_code
                )
            items.append(
                {
                    "ref": str(employee.employee_id),
                    "label": label,
                    "name": name,
                    "employee_code": employee.employee_code or "",
                }
            )
        return {"items": items}

    def detail_context(
        self,
        request_id: str,
    ) -> dict:
        """Get context for material request detail page."""
        mr_stmt = (
            select(MaterialRequest)
            .options(joinedload(MaterialRequest.items))
            .where(
                MaterialRequest.request_id == coerce_uuid(request_id),
                MaterialRequest.organization_id == self.organization_id,
            )
        )
        request = self.db.scalar(mr_stmt)

        if not request:
            return {"material_request": None}

        # Batch load related data for items
        item_ids = [item.inventory_item_id for item in request.items]
        warehouse_ids = [
            item.warehouse_id for item in request.items if item.warehouse_id
        ]
        project_ids = [item.project_id for item in request.items if item.project_id]

        items_map: dict[UUID, Any] = {}
        if item_ids:
            inv_stmt = select(Item).where(
                Item.item_id.in_(item_ids),
                Item.organization_id == self.organization_id,
            )
            inv_items = list(self.db.scalars(inv_stmt).all())
            items_map = {i.item_id: i for i in inv_items}

        warehouses_map: dict[UUID, Any] = {}
        if warehouse_ids:
            wh_stmt = select(Warehouse).where(
                Warehouse.warehouse_id.in_(warehouse_ids),
                Warehouse.organization_id == self.organization_id,
            )
            wh_list = list(self.db.scalars(wh_stmt).all())
            warehouses_map = {w.warehouse_id: w for w in wh_list}

        projects_map: dict[UUID, Any] = {}
        if project_ids:
            proj_stmt = select(Project).where(
                Project.project_id.in_(project_ids),
                Project.organization_id == self.organization_id,
            )
            proj_list = list(self.db.scalars(proj_stmt).all())
            projects_map = {p.project_id: p for p in proj_list}

        # Get default warehouse and requested by names
        default_warehouse_name = None
        if request.default_warehouse_id:
            wh_map = self._batch_load_warehouses([request.default_warehouse_id])
            wh = wh_map.get(request.default_warehouse_id)
            if wh:
                default_warehouse_name = f"{wh.warehouse_code} - {wh.warehouse_name}"

        requested_by_name = None
        if request.requested_by_id:
            emp_map = self._batch_load_employee_names([request.requested_by_id])
            requested_by_name = emp_map.get(request.requested_by_id)

        total_qty = (
            sum((item.requested_qty for item in request.items), Decimal("0"))
            if request.items
            else Decimal("0")
        )
        total_ordered = (
            sum((item.ordered_qty for item in request.items), Decimal("0"))
            if request.items
            else Decimal("0")
        )

        detail_items = []
        for item in sorted(request.items, key=lambda x: x.sequence):
            inv_item = items_map.get(item.inventory_item_id)
            wh = warehouses_map.get(item.warehouse_id) if item.warehouse_id else None
            proj = projects_map.get(item.project_id) if item.project_id else None

            detail_items.append(
                {
                    "item_id": str(item.item_id),
                    "item_code": inv_item.item_code if inv_item else "Unknown",
                    "item_name": (inv_item.item_name if inv_item else "Unknown Item"),
                    "warehouse_code": wh.warehouse_code if wh else None,
                    "warehouse_name": wh.warehouse_name if wh else None,
                    "requested_qty": _format_quantity(item.requested_qty),
                    "ordered_qty": _format_quantity(item.ordered_qty),
                    "ordered_qty_value": float(item.ordered_qty or 0),
                    "pending_qty": _format_quantity(
                        item.requested_qty - item.ordered_qty
                    ),
                    "uom": item.uom or (inv_item.base_uom if inv_item else ""),
                    "schedule_date": _format_date(item.schedule_date),
                    "project_code": proj.project_code if proj else None,
                    "project_name": proj.project_name if proj else None,
                    "sequence": item.sequence,
                }
            )

        return {
            "material_request": {
                "request_id": str(request.request_id),
                "request_number": request.request_number,
                "request_type": request.request_type.value,
                "status": request.status.value,
                "schedule_date": _format_date(request.schedule_date),
                "warehouse_name": default_warehouse_name,
                "requested_by_name": requested_by_name,
                "remarks": request.remarks or "-",
                "total_requested_qty": _format_quantity(total_qty),
                "total_ordered_qty": _format_quantity(total_ordered),
                "total_pending": _format_quantity(total_qty - total_ordered),
                "total_items": len(request.items),
                "created_at": _format_datetime(request.created_at),
                "updated_at": (
                    _format_datetime(request.updated_at) if request.updated_at else None
                ),
                "last_synced_at": (
                    _format_datetime(request.last_synced_at)
                    if request.last_synced_at
                    else None
                ),
                "erpnext_id": request.erpnext_id,
                "can_edit": request.status == MaterialRequestStatus.DRAFT,
                "can_submit": request.status == MaterialRequestStatus.DRAFT,
                "can_cancel": request.status
                in [
                    MaterialRequestStatus.DRAFT,
                    MaterialRequestStatus.SUBMITTED,
                ],
                "can_delete": (
                    request.status == MaterialRequestStatus.DRAFT
                    and not request.erpnext_id
                ),
                "items": detail_items,
            },
            "material_request_items": detail_items,
        }

    def report_context(
        self,
        *,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        group_by: str = "status",
    ) -> dict:
        """Get context for material request report page using SQL aggregation."""
        base_filter = [MaterialRequest.organization_id == self.organization_id]
        if start_date:
            base_filter.append(MaterialRequest.schedule_date >= start_date)
        if end_date:
            base_filter.append(MaterialRequest.schedule_date <= end_date)

        # Summary stats via SQL aggregation
        pending_statuses = [
            MaterialRequestStatus.DRAFT,
            MaterialRequestStatus.SUBMITTED,
            MaterialRequestStatus.PARTIALLY_ORDERED,
        ]
        completed_statuses = [
            MaterialRequestStatus.ORDERED,
            MaterialRequestStatus.ISSUED,
            MaterialRequestStatus.TRANSFERRED,
        ]

        summary_stmt = (
            select(
                func.count(func.distinct(MaterialRequest.request_id)).label(
                    "total_requests"
                ),
                func.count(MaterialRequestItem.item_id).label("total_items"),
                func.coalesce(
                    func.sum(MaterialRequestItem.requested_qty), Decimal("0")
                ).label("total_qty"),
                func.coalesce(
                    func.sum(MaterialRequestItem.ordered_qty), Decimal("0")
                ).label("total_ordered"),
            )
            .select_from(MaterialRequest)
            .outerjoin(
                MaterialRequestItem,
                MaterialRequestItem.request_id == MaterialRequest.request_id,
            )
            .where(*base_filter)
        )
        summary_row = self.db.execute(summary_stmt).one()

        # Pending/completed counts via status counts
        status_count_stmt = (
            select(MaterialRequest.status, func.count())
            .where(*base_filter)
            .group_by(MaterialRequest.status)
        )
        status_rows = self.db.execute(status_count_stmt).all()
        status_counts = {s: c for s, c in status_rows}

        pending_requests = sum(status_counts.get(s, 0) for s in pending_statuses)
        completed_requests = sum(status_counts.get(s, 0) for s in completed_statuses)

        # Group data via SQL aggregation
        group_col = (
            MaterialRequest.request_type
            if group_by == "type"
            else MaterialRequest.status
        )

        group_stmt = (
            select(
                group_col.label("group_key"),
                func.count(func.distinct(MaterialRequest.request_id)).label(
                    "request_count"
                ),
                func.count(MaterialRequestItem.item_id).label("item_count"),
                func.coalesce(
                    func.sum(MaterialRequestItem.requested_qty), Decimal("0")
                ).label("requested_qty"),
                func.coalesce(
                    func.sum(MaterialRequestItem.ordered_qty), Decimal("0")
                ).label("ordered_qty"),
            )
            .select_from(MaterialRequest)
            .outerjoin(
                MaterialRequestItem,
                MaterialRequestItem.request_id == MaterialRequest.request_id,
            )
            .where(*base_filter)
            .group_by(group_col)
        )
        group_rows = self.db.execute(group_stmt).all()

        formatted_groups = [
            {
                "group_key": row.group_key.value,
                "request_count": row.request_count,
                "item_count": row.item_count,
                "requested_qty": float(row.requested_qty),
                "ordered_qty": float(row.ordered_qty),
            }
            for row in group_rows
        ]

        # Recent requests with batch employee loading
        recent_stmt = (
            select(MaterialRequest)
            .options(selectinload(MaterialRequest.items))
            .where(*base_filter)
            .order_by(MaterialRequest.created_at.desc())
            .limit(10)
        )
        recent_requests_raw = list(self.db.scalars(recent_stmt).all())

        employee_ids = [
            r.requested_by_id for r in recent_requests_raw if r.requested_by_id
        ]
        emp_names = self._batch_load_employee_names(employee_ids)

        recent_requests = [
            {
                "request_id": str(req.request_id),
                "request_number": req.request_number,
                "request_type": req.request_type.value,
                "status": req.status.value,
                "schedule_date": _format_date(req.schedule_date),
                "item_count": len(req.items) if req.items else 0,
                "requested_by_name": (
                    emp_names.get(req.requested_by_id) if req.requested_by_id else None
                ),
            }
            for req in recent_requests_raw
        ]

        return {
            "summary": {
                "total_requests": summary_row.total_requests,
                "pending_requests": pending_requests,
                "completed_requests": completed_requests,
                "total_items": summary_row.total_items,
                "total_requested_qty": float(summary_row.total_qty),
                "total_ordered_qty": float(summary_row.total_ordered),
            },
            "grouped_data": formatted_groups,
            "recent_requests": recent_requests,
            "filter_group_by": group_by,
            "filter_start_date": start_date or "",
            "filter_end_date": end_date or "",
        }

    def create_from_form(
        self,
        user_id: UUID,
        request_type: str,
        schedule_date: Optional[str] = None,
        default_warehouse_id: Optional[str] = None,
        requested_by_id: Optional[str] = None,
        remarks: Optional[str] = None,
        items: Optional[list[dict]] = None,
    ) -> MaterialRequest:
        """Create a material request from form data."""
        from app.services.finance.inv.material_request_numbering import (
            material_request_numbering_service,
        )

        # Generate request number
        request_number = material_request_numbering_service.get_next_number(
            self.db,
            self.organization_id,
        )

        # Parse date
        parsed_date = None
        if schedule_date:
            parsed_date = datetime.strptime(schedule_date, "%Y-%m-%d").date()

        # Create request
        request = MaterialRequest(
            organization_id=self.organization_id,
            request_number=request_number,
            request_type=MaterialRequestType(request_type),
            status=MaterialRequestStatus.DRAFT,
            schedule_date=parsed_date,
            default_warehouse_id=(
                coerce_uuid(default_warehouse_id) if default_warehouse_id else None
            ),
            requested_by_id=(coerce_uuid(requested_by_id) if requested_by_id else None),
            remarks=remarks,
            created_by_id=user_id,
        )

        self.db.add(request)
        self.db.flush()

        # Add items
        self._add_items_from_form(request.request_id, items)

        return request

    def update_from_form(
        self,
        user_id: UUID,
        request_id: str,
        request_type: str,
        schedule_date: Optional[str] = None,
        default_warehouse_id: Optional[str] = None,
        requested_by_id: Optional[str] = None,
        remarks: Optional[str] = None,
        items: Optional[list[dict]] = None,
    ) -> MaterialRequest:
        """Update a material request from form data."""
        request = self.db.get(MaterialRequest, coerce_uuid(request_id))
        if not request or request.organization_id != self.organization_id:
            raise ValueError("Material request not found")

        if request.status != MaterialRequestStatus.DRAFT:
            raise ValueError("Only draft requests can be edited")

        # Parse date
        parsed_date = None
        if schedule_date:
            parsed_date = datetime.strptime(schedule_date, "%Y-%m-%d").date()

        # Update request
        request.request_type = MaterialRequestType(request_type)
        request.schedule_date = parsed_date
        request.default_warehouse_id = (
            coerce_uuid(default_warehouse_id) if default_warehouse_id else None
        )
        request.requested_by_id = (
            coerce_uuid(requested_by_id) if requested_by_id else None
        )
        request.remarks = remarks
        request.updated_by_id = user_id

        # Delete existing items and recreate
        for item in request.items:
            self.db.delete(item)
        self.db.flush()

        self._add_items_from_form(request.request_id, items)

        return request

    def _add_items_from_form(
        self,
        request_id: UUID,
        items: Optional[list[dict]],
    ) -> None:
        """Parse and add line items from form data with validation."""
        if not items:
            return

        for seq, item_data in enumerate(items, 1):
            item_schedule = None
            if item_data.get("schedule_date"):
                item_schedule = datetime.strptime(
                    item_data["schedule_date"], "%Y-%m-%d"
                ).date()

            inv_item_id = item_data.get("item_id") or item_data.get("inventory_item_id")
            if not inv_item_id:
                continue

            # Parse and validate quantity
            raw_qty = item_data.get("qty") or item_data.get("requested_qty") or "0"
            try:
                qty_value = Decimal(str(raw_qty))
            except (InvalidOperation, ValueError) as e:
                raise ValueError(f"Invalid quantity value: {raw_qty}") from e
            if qty_value <= 0:
                raise ValueError(f"Item quantity must be positive, got {qty_value}")

            item = MaterialRequestItem(
                organization_id=self.organization_id,
                request_id=request_id,
                inventory_item_id=coerce_uuid(inv_item_id),
                warehouse_id=(
                    coerce_uuid(item_data.get("warehouse_id"))
                    if item_data.get("warehouse_id")
                    else None
                ),
                requested_qty=qty_value,
                ordered_qty=Decimal("0"),
                uom=item_data.get("uom"),
                schedule_date=item_schedule,
                project_id=(
                    coerce_uuid(item_data.get("project_id"))
                    if item_data.get("project_id")
                    else None
                ),
                sequence=seq,
            )
            self.db.add(item)

    def submit_request(
        self,
        user_id: UUID,
        request_id: str,
    ) -> MaterialRequest:
        """Submit a material request."""
        request = self.db.get(MaterialRequest, coerce_uuid(request_id))
        if not request or request.organization_id != self.organization_id:
            raise ValueError("Material request not found")

        self._validate_transition(request.status, MaterialRequestStatus.SUBMITTED)

        if not request.items:
            raise ValueError("Cannot submit request without items")

        request.status = MaterialRequestStatus.SUBMITTED
        request.updated_by_id = user_id

        return request

    def cancel_request(
        self,
        user_id: UUID,
        request_id: str,
    ) -> MaterialRequest:
        """Cancel a material request."""
        request = self.db.get(MaterialRequest, coerce_uuid(request_id))
        if not request or request.organization_id != self.organization_id:
            raise ValueError("Material request not found")

        self._validate_transition(request.status, MaterialRequestStatus.CANCELLED)

        request.status = MaterialRequestStatus.CANCELLED
        request.updated_by_id = user_id

        return request

    def delete_request(
        self,
        request_id: str,
    ) -> None:
        """Delete a material request (draft, non-synced only)."""
        request = self.db.get(MaterialRequest, coerce_uuid(request_id))
        if not request or request.organization_id != self.organization_id:
            raise ValueError("Material request not found")

        if request.status != MaterialRequestStatus.DRAFT:
            raise ValueError("Only draft requests can be deleted")

        if request.erpnext_id:
            raise ValueError("Synced requests cannot be deleted")

        self.db.delete(request)

    def dashboard_context(self) -> dict:
        """Get material request metrics for dashboard widget."""
        # Status counts via SQL
        status_stmt = (
            select(MaterialRequest.status, func.count())
            .where(MaterialRequest.organization_id == self.organization_id)
            .group_by(MaterialRequest.status)
        )
        status_counts = self.db.execute(status_stmt).all()
        counts = {s.value: c for s, c in status_counts}

        total_requests = sum(counts.values())
        draft_count = counts.get("DRAFT", 0)
        submitted_count = counts.get("SUBMITTED", 0)
        pending_count = (
            draft_count + submitted_count + counts.get("PARTIALLY_ORDERED", 0)
        )
        completed_count = (
            counts.get("ORDERED", 0)
            + counts.get("ISSUED", 0)
            + counts.get("TRANSFERRED", 0)
        )

        # Recent pending requests with item counts via single query
        pending_stmt = (
            select(
                MaterialRequest,
                func.count(MaterialRequestItem.item_id).label("item_count"),
            )
            .outerjoin(
                MaterialRequestItem,
                MaterialRequestItem.request_id == MaterialRequest.request_id,
            )
            .where(
                MaterialRequest.organization_id == self.organization_id,
                MaterialRequest.status.in_(
                    [
                        MaterialRequestStatus.DRAFT,
                        MaterialRequestStatus.SUBMITTED,
                        MaterialRequestStatus.PARTIALLY_ORDERED,
                    ]
                ),
            )
            .group_by(MaterialRequest.request_id)
            .order_by(MaterialRequest.created_at.desc())
            .limit(5)
        )
        pending_rows = self.db.execute(pending_stmt).all()

        recent_pending_list = [
            {
                "request_id": str(req.request_id),
                "request_number": req.request_number,
                "request_type": req.request_type.value,
                "status": req.status.value,
                "status_label": req.status.value.replace("_", " ").title(),
                "schedule_date": _format_date(req.schedule_date),
                "item_count": item_count,
            }
            for req, item_count in pending_rows
        ]

        return {
            "material_request_stats": {
                "total": total_requests,
                "draft": draft_count,
                "submitted": submitted_count,
                "pending": pending_count,
                "completed": completed_count,
            },
            "recent_pending_requests": recent_pending_list,
        }


class _MaterialRequestWebFacade:
    """Facade to match dashboard usage patterns in other web services."""

    @staticmethod
    def dashboard_context(db: Session, organization_id: str) -> dict:
        service = MaterialRequestWebService(db, coerce_uuid(organization_id))
        return service.dashboard_context()


material_request_web_service = _MaterialRequestWebFacade()
