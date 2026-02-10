"""
Material Request Web View Service.

Provides view-focused data for material request web routes.
"""

import logging
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, TypedDict
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.models.finance.core_org.project import Project, ProjectStatus
from app.models.inventory import (
    Item,
    MaterialRequest,
    MaterialRequestItem,
    MaterialRequestStatus,
    MaterialRequestType,
    Warehouse,
)
from app.models.people.hr import Employee, EmployeeStatus
from app.models.person import Person
from app.models.support.ticket import Ticket
from app.services.common import coerce_uuid
from app.services.formatters import format_currency as _format_currency
from app.services.formatters import format_date as _format_date
from app.services.formatters import format_datetime as _format_datetime

logger = logging.getLogger(__name__)


class _GroupTotals(TypedDict):
    count: int
    items: int
    qty: Decimal
    ordered: Decimal


class MaterialRequestWebService:
    """View service for material request web routes."""

    @staticmethod
    def list_context(
        db: Session,
        organization_id: str,
        status: str | None = None,
        request_type: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        project_id: str | None = None,
    ) -> dict:
        """Get context for material request list page."""
        org_id = coerce_uuid(organization_id)

        query = (
            db.query(MaterialRequest)
            .options(
                joinedload(MaterialRequest.items),
            )
            .filter(MaterialRequest.organization_id == org_id)
        )

        if status:
            try:
                query = query.filter(
                    MaterialRequest.status == MaterialRequestStatus(status)
                )
            except ValueError:
                pass

        if request_type:
            try:
                query = query.filter(
                    MaterialRequest.request_type == MaterialRequestType(request_type)
                )
            except ValueError:
                pass

        if start_date:
            query = query.filter(MaterialRequest.schedule_date >= start_date)

        if end_date:
            query = query.filter(MaterialRequest.schedule_date <= end_date)

        if project_id:
            query = query.filter(MaterialRequest.project_id == coerce_uuid(project_id))

        requests = query.order_by(MaterialRequest.created_at.desc()).limit(100).all()

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
            # Get warehouse name
            warehouse_name = None
            if req.default_warehouse_id:
                wh = db.get(Warehouse, req.default_warehouse_id)
                if wh:
                    warehouse_name = wh.warehouse_name

            # Get requested by name
            requested_by_name = None
            if req.requested_by_id:
                emp = (
                    db.query(Employee)
                    .join(Person, Person.id == Employee.person_id)
                    .filter(Employee.employee_id == req.requested_by_id)
                    .first()
                )
                if emp and emp.person:
                    requested_by_name = emp.person.name

            items.append(
                {
                    "request_id": str(req.request_id),
                    "request_number": req.request_number,
                    "request_type": req.request_type.value,
                    "status": req.status.value,
                    "schedule_date": _format_date(req.schedule_date),
                    "remarks": (req.remarks or "")[:100] + "..."
                    if req.remarks and len(req.remarks) > 100
                    else (req.remarks or "-"),
                    "item_count": len(req.items) if req.items else 0,
                    "total_qty": _format_currency(total_qty),
                    "total_ordered": _format_currency(total_ordered),
                    "created_at": _format_datetime(req.created_at),
                    "warehouse_name": warehouse_name,
                    "requested_by_name": requested_by_name,
                }
            )

        # Status counts
        status_counts = (
            db.query(MaterialRequest.status, func.count())
            .filter(MaterialRequest.organization_id == org_id)
            .group_by(MaterialRequest.status)
            .all()
        )
        counts = {s.value: c for s, c in status_counts}

        # Type counts
        type_counts = (
            db.query(MaterialRequest.request_type, func.count())
            .filter(MaterialRequest.organization_id == org_id)
            .group_by(MaterialRequest.request_type)
            .all()
        )
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
        }

    @staticmethod
    def form_context(
        db: Session,
        organization_id: str,
        request_id: str | None = None,
    ) -> dict:
        """Get context for material request form (new/edit)."""
        org_id = coerce_uuid(organization_id)

        # Get items for selection
        items = (
            db.query(Item)
            .filter(
                Item.organization_id == org_id,
                Item.is_active.is_(True),
            )
            .order_by(Item.item_code)
            .all()
        )

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
        warehouses = (
            db.query(Warehouse)
            .filter(
                Warehouse.organization_id == org_id,
                Warehouse.is_active.is_(True),
            )
            .order_by(Warehouse.warehouse_code)
            .all()
        )

        warehouse_options = [
            {
                "warehouse_id": str(w.warehouse_id),
                "warehouse_code": w.warehouse_code,
                "warehouse_name": w.warehouse_name,
            }
            for w in warehouses
        ]

        # Get active projects
        projects = (
            db.query(Project)
            .filter(
                Project.organization_id == org_id,
                Project.status == ProjectStatus.ACTIVE,
            )
            .order_by(Project.project_code)
            .all()
        )

        project_options = [
            {
                "project_id": str(p.project_id),
                "project_code": p.project_code,
                "project_name": p.project_name,
            }
            for p in projects
        ]

        import json

        context: dict[str, Any] = {
            "inventory_items": item_options,
            "warehouses": warehouse_options,
            "projects": project_options,
            "request_types": [t.value for t in MaterialRequestType],
            "today": _format_date(date.today()),
            "material_request": {},
            "items_json": "[]",
            "requested_by_name": "",
            "tickets": [],
        }

        # If editing, load request data
        if request_id:
            material_request = (
                db.query(MaterialRequest)
                .options(
                    joinedload(MaterialRequest.items).joinedload(
                        MaterialRequestItem.request
                    ),
                )
                .filter(
                    MaterialRequest.request_id == coerce_uuid(request_id),
                    MaterialRequest.organization_id == org_id,
                )
                .first()
            )
            if material_request:
                context["material_request"] = {
                    "request_id": str(material_request.request_id),
                    "request_number": material_request.request_number,
                    "request_type": material_request.request_type.value,
                    "status": material_request.status.value,
                    "schedule_date": _format_date(material_request.schedule_date),
                    "default_warehouse_id": str(material_request.default_warehouse_id)
                    if material_request.default_warehouse_id
                    else "",
                    "project_id": str(material_request.project_id)
                    if material_request.project_id
                    else "",
                    "ticket_id": str(material_request.ticket_id)
                    if material_request.ticket_id
                    else "",
                    "requested_by_id": str(material_request.requested_by_id)
                    if material_request.requested_by_id
                    else "",
                    "remarks": material_request.remarks or "",
                    "can_edit": material_request.status == MaterialRequestStatus.DRAFT,
                }
                if material_request.requested_by_id:
                    emp = (
                        db.query(Employee)
                        .join(Person, Person.id == Employee.person_id)
                        .filter(
                            Employee.employee_id == material_request.requested_by_id
                        )
                        .first()
                    )
                    if emp and emp.person:
                        context["requested_by_name"] = emp.person.name
                if material_request.ticket_id:
                    pass
                request_items = [
                    {
                        "item_id": str(item.inventory_item_id),
                        "warehouse_id": str(item.warehouse_id)
                        if item.warehouse_id
                        else "",
                        "qty": float(item.requested_qty),
                        "uom": item.uom or "Nos",
                        "schedule_date": _format_date(item.schedule_date),
                    }
                    for item in sorted(material_request.items, key=lambda x: x.sequence)
                ]
                context["items_json"] = json.dumps(request_items)

        # Load ticket options (active + recent, non-deleted)
        tickets = (
            db.query(Ticket)
            .filter(
                Ticket.organization_id == org_id,
                Ticket.is_deleted.is_(False),
            )
            .order_by(Ticket.opening_date.desc())
            .limit(200)
            .all()
        )
        ticket_options = [
            {
                "ticket_id": str(t.ticket_id),
                "ticket_number": t.ticket_number,
                "subject": t.subject or "",
            }
            for t in tickets
        ]
        if context.get("material_request", {}).get("ticket_id"):
            current_id = context["material_request"]["ticket_id"]
            if current_id and all(t["ticket_id"] != current_id for t in ticket_options):
                current_ticket = (
                    db.query(Ticket)
                    .filter(
                        Ticket.ticket_id == coerce_uuid(current_id),
                        Ticket.organization_id == org_id,
                    )
                    .first()
                )
                if current_ticket:
                    ticket_options.append(
                        {
                            "ticket_id": str(current_ticket.ticket_id),
                            "ticket_number": current_ticket.ticket_number,
                            "subject": current_ticket.subject or "",
                        }
                    )
        context["tickets"] = ticket_options

        return context

    @staticmethod
    def requested_by_typeahead(
        db: Session,
        organization_id: str,
        query: str,
        limit: int = 8,
    ) -> dict:
        """Search active employees for material request requested-by typeahead."""
        from sqlalchemy import select as sa_select
        from sqlalchemy.orm import joinedload as jl

        org_id = coerce_uuid(organization_id)
        search_term = f"%{query.strip()}%"
        stmt = (
            sa_select(Employee)
            .join(Person, Person.id == Employee.person_id)
            .options(jl(Employee.person))
            .where(
                Employee.organization_id == org_id,
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
        employees = list(db.scalars(stmt).unique().all())
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

    @staticmethod
    def detail_context(
        db: Session,
        organization_id: str,
        request_id: str,
    ) -> dict:
        """Get context for material request detail page."""
        org_id = coerce_uuid(organization_id)
        request = (
            db.query(MaterialRequest)
            .options(
                joinedload(MaterialRequest.items),
            )
            .filter(
                MaterialRequest.request_id == coerce_uuid(request_id),
                MaterialRequest.organization_id == org_id,
            )
            .first()
        )

        if not request:
            return {"material_request": None}

        # Get related data for items
        item_ids = [item.inventory_item_id for item in request.items]
        warehouse_ids = [
            item.warehouse_id for item in request.items if item.warehouse_id
        ]
        items_map = {}
        if item_ids:
            inv_items = (
                db.query(Item)
                .filter(
                    Item.item_id.in_(item_ids),
                    Item.organization_id == org_id,
                )
                .all()
            )
            items_map = {i.item_id: i for i in inv_items}

        warehouses_map = {}
        if warehouse_ids:
            wh_list = (
                db.query(Warehouse)
                .filter(
                    Warehouse.warehouse_id.in_(warehouse_ids),
                    Warehouse.organization_id == org_id,
                )
                .all()
            )
            warehouses_map = {w.warehouse_id: w for w in wh_list}

        # Get default warehouse and requested by names
        default_warehouse_name = None
        if request.default_warehouse_id:
            wh = (
                db.query(Warehouse)
                .filter(
                    Warehouse.warehouse_id == request.default_warehouse_id,
                    Warehouse.organization_id == org_id,
                )
                .first()
            )
            if wh:
                default_warehouse_name = f"{wh.warehouse_code} - {wh.warehouse_name}"

        requested_by_name = None
        if request.requested_by_id:
            emp = (
                db.query(Employee)
                .join(Person, Person.id == Employee.person_id)
                .filter(
                    Employee.employee_id == request.requested_by_id,
                    Employee.organization_id == org_id,
                )
                .first()
            )
            if emp and emp.person:
                requested_by_name = emp.person.name

        project_code = None
        project_name = None
        if request.project_id:
            proj = (
                db.query(Project)
                .filter(
                    Project.project_id == request.project_id,
                    Project.organization_id == org_id,
                )
                .first()
            )
            if proj:
                project_code = proj.project_code
                project_name = proj.project_name

        ticket_number = None
        ticket_subject = None
        if request.ticket_id:
            ticket = (
                db.query(Ticket)
                .filter(
                    Ticket.ticket_id == request.ticket_id,
                    Ticket.organization_id == org_id,
                )
                .first()
            )
            if ticket:
                ticket_number = ticket.ticket_number
                ticket_subject = ticket.subject

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

            detail_items.append(
                {
                    "item_id": str(item.item_id),
                    "item_code": inv_item.item_code if inv_item else "Unknown",
                    "item_name": inv_item.item_name if inv_item else "Unknown Item",
                    "warehouse_code": wh.warehouse_code if wh else None,
                    "warehouse_name": wh.warehouse_name if wh else None,
                    "requested_qty": _format_currency(item.requested_qty),
                    "ordered_qty": _format_currency(item.ordered_qty),
                    "ordered_qty_value": float(item.ordered_qty or Decimal("0")),
                    "pending_qty": _format_currency(
                        item.requested_qty - item.ordered_qty
                    ),
                    "uom": item.uom or (inv_item.base_uom if inv_item else ""),
                    "schedule_date": _format_date(item.schedule_date),
                    "sequence": item.sequence,
                }
            )

        return {
            "material_request_items": detail_items,
            "material_request": {
                "request_id": str(request.request_id),
                "request_number": request.request_number,
                "request_type": request.request_type.value,
                "status": request.status.value,
                "schedule_date": _format_date(request.schedule_date),
                "warehouse_name": default_warehouse_name,
                "project_code": project_code,
                "project_name": project_name,
                "ticket_number": ticket_number,
                "ticket_subject": ticket_subject,
                "requested_by_name": requested_by_name,
                "remarks": request.remarks or "-",
                "cancel_reason": request.cancel_reason or "",
                "total_requested_qty": _format_currency(total_qty),
                "total_ordered_qty": _format_currency(total_ordered),
                "total_pending": _format_currency(total_qty - total_ordered),
                "total_items": len(request.items),
                "created_at": _format_datetime(request.created_at),
                "updated_at": _format_datetime(request.updated_at)
                if request.updated_at
                else None,
                "last_synced_at": _format_datetime(request.last_synced_at)
                if request.last_synced_at
                else None,
                "erpnext_id": request.erpnext_id,
                "can_edit": request.status == MaterialRequestStatus.DRAFT,
                "can_submit": request.status == MaterialRequestStatus.DRAFT,
                "can_approve": request.status == MaterialRequestStatus.SUBMITTED,
                "can_cancel": request.status
                in [MaterialRequestStatus.DRAFT, MaterialRequestStatus.SUBMITTED],
                "can_delete": request.status == MaterialRequestStatus.DRAFT,
                "items": detail_items,
            },
        }

    @staticmethod
    def report_context(
        db: Session,
        organization_id: str,
        start_date: str | None = None,
        end_date: str | None = None,
        group_by: str = "status",
    ) -> dict:
        """Get context for material request report page."""
        org_id = coerce_uuid(organization_id)

        query = db.query(MaterialRequest).filter(
            MaterialRequest.organization_id == org_id
        )

        if start_date:
            query = query.filter(MaterialRequest.schedule_date >= start_date)
        if end_date:
            query = query.filter(MaterialRequest.schedule_date <= end_date)

        requests = (
            query.options(joinedload(MaterialRequest.items))
            .order_by(MaterialRequest.created_at.desc())
            .all()
        )

        # Calculate totals
        total_requests = len(requests)
        total_items = sum(len(r.items) for r in requests)
        total_qty = sum(
            (
                sum((item.requested_qty for item in r.items), Decimal("0"))
                for r in requests
            ),
            Decimal("0"),
        )
        total_ordered = sum(
            (
                sum((item.ordered_qty for item in r.items), Decimal("0"))
                for r in requests
            ),
            Decimal("0"),
        )

        # Calculate pending and completed
        pending_requests = sum(
            1
            for r in requests
            if r.status
            in [
                MaterialRequestStatus.DRAFT,
                MaterialRequestStatus.SUBMITTED,
                MaterialRequestStatus.PARTIALLY_ORDERED,
            ]
        )
        completed_requests = sum(
            1
            for r in requests
            if r.status
            in [
                MaterialRequestStatus.ORDERED,
                MaterialRequestStatus.ISSUED,
                MaterialRequestStatus.TRANSFERRED,
            ]
        )

        # Group data based on group_by parameter
        grouped_data: dict[str, _GroupTotals] = {}
        if group_by == "status":
            for req in requests:
                key = req.status.value
                if key not in grouped_data:
                    grouped_data[key] = {
                        "count": 0,
                        "items": 0,
                        "qty": Decimal("0"),
                        "ordered": Decimal("0"),
                    }
                grouped_data[key]["count"] += 1
                grouped_data[key]["items"] += len(req.items)
                grouped_data[key]["qty"] += sum(
                    item.requested_qty for item in req.items
                )
                grouped_data[key]["ordered"] += sum(
                    item.ordered_qty for item in req.items
                )
        elif group_by == "type":
            for req in requests:
                key = req.request_type.value
                if key not in grouped_data:
                    grouped_data[key] = {
                        "count": 0,
                        "items": 0,
                        "qty": Decimal("0"),
                        "ordered": Decimal("0"),
                    }
                grouped_data[key]["count"] += 1
                grouped_data[key]["items"] += len(req.items)
                grouped_data[key]["qty"] += sum(
                    item.requested_qty for item in req.items
                )
                grouped_data[key]["ordered"] += sum(
                    item.ordered_qty for item in req.items
                )

        # Format grouped data for template
        formatted_groups = []
        for key, data in grouped_data.items():
            formatted_groups.append(
                {
                    "group_key": key,
                    "request_count": data["count"],
                    "item_count": data["items"],
                    "requested_qty": float(data["qty"]),
                    "ordered_qty": float(data["ordered"]),
                }
            )

        # Build recent requests for display
        recent_requests = []
        for req in requests[:10]:
            # Get requested by name
            requested_by_name = None
            if req.requested_by_id:
                emp = (
                    db.query(Employee)
                    .join(Person, Person.id == Employee.person_id)
                    .filter(Employee.employee_id == req.requested_by_id)
                    .first()
                )
                if emp and emp.person:
                    requested_by_name = emp.person.name

            recent_requests.append(
                {
                    "request_id": str(req.request_id),
                    "request_number": req.request_number,
                    "request_type": req.request_type.value,
                    "status": req.status.value,
                    "schedule_date": _format_date(req.schedule_date),
                    "item_count": len(req.items) if req.items else 0,
                    "requested_by_name": requested_by_name,
                }
            )

        return {
            "summary": {
                "total_requests": total_requests,
                "pending_requests": pending_requests,
                "completed_requests": completed_requests,
                "total_items": total_items,
                "total_requested_qty": float(total_qty),
                "total_ordered_qty": float(total_ordered),
            },
            "grouped_data": formatted_groups,
            "recent_requests": recent_requests,
            "filter_group_by": group_by,
            "filter_start_date": start_date or "",
            "filter_end_date": end_date or "",
        }

    @staticmethod
    def create_from_form(
        db: Session,
        organization_id: UUID,
        user_id: UUID,
        request_type: str,
        schedule_date: str | None = None,
        default_warehouse_id: str | None = None,
        project_id: str | None = None,
        ticket_id: str | None = None,
        requested_by_id: str | None = None,
        remarks: str | None = None,
        items: list[dict] | None = None,
    ) -> MaterialRequest:
        """Create a material request from form data."""
        from app.models.finance.core_config.numbering_sequence import SequenceType
        from app.services.finance.common.numbering import SyncNumberingService

        # Generate request number via unified numbering system
        request_number = SyncNumberingService(db).generate_next_number(
            organization_id, SequenceType.MATERIAL_REQUEST
        )

        # Parse date
        parsed_date = None
        if schedule_date:
            parsed_date = datetime.strptime(schedule_date, "%Y-%m-%d").date()

        # Create request
        request = MaterialRequest(
            organization_id=organization_id,
            request_number=request_number,
            request_type=MaterialRequestType(request_type),
            status=MaterialRequestStatus.DRAFT,
            schedule_date=parsed_date,
            default_warehouse_id=coerce_uuid(default_warehouse_id)
            if default_warehouse_id
            else None,
            project_id=coerce_uuid(project_id) if project_id else None,
            ticket_id=coerce_uuid(ticket_id) if ticket_id else None,
            requested_by_id=coerce_uuid(requested_by_id) if requested_by_id else None,
            remarks=remarks,
            created_by_id=user_id,
        )

        db.add(request)
        db.flush()

        # Add items
        if items:
            for seq, item_data in enumerate(items, 1):
                item_schedule = None
                if item_data.get("schedule_date"):
                    item_schedule = datetime.strptime(
                        item_data["schedule_date"], "%Y-%m-%d"
                    ).date()

                # Support both item_id and inventory_item_id for flexibility
                inv_item_id = item_data.get("item_id") or item_data.get(
                    "inventory_item_id"
                )
                if not inv_item_id:
                    continue

                # Support both qty and requested_qty
                qty = item_data.get("qty") or item_data.get("requested_qty") or "0"

                item = MaterialRequestItem(
                    organization_id=organization_id,
                    request_id=request.request_id,
                    inventory_item_id=coerce_uuid(inv_item_id),
                    warehouse_id=coerce_uuid(item_data.get("warehouse_id"))
                    if item_data.get("warehouse_id")
                    else None,
                    requested_qty=Decimal(str(qty)),
                    ordered_qty=Decimal("0"),
                    uom=item_data.get("uom"),
                    schedule_date=item_schedule,
                    project_id=None,
                    sequence=seq,
                )
                db.add(item)

        return request

    @staticmethod
    def update_from_form(
        db: Session,
        organization_id: UUID,
        user_id: UUID,
        request_id: str,
        request_type: str,
        schedule_date: str | None = None,
        default_warehouse_id: str | None = None,
        project_id: str | None = None,
        ticket_id: str | None = None,
        requested_by_id: str | None = None,
        remarks: str | None = None,
        items: list[dict] | None = None,
    ) -> MaterialRequest:
        """Update a material request from form data."""
        request = db.get(MaterialRequest, coerce_uuid(request_id))
        if not request or request.organization_id != organization_id:
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
        request.project_id = coerce_uuid(project_id) if project_id else None
        request.ticket_id = coerce_uuid(ticket_id) if ticket_id else None
        request.requested_by_id = (
            coerce_uuid(requested_by_id) if requested_by_id else None
        )
        request.remarks = remarks
        request.updated_by_id = user_id

        # Delete existing items
        for item in request.items:
            db.delete(item)
        db.flush()

        # Add new items
        if items:
            for seq, item_data in enumerate(items, 1):
                item_schedule = None
                if item_data.get("schedule_date"):
                    item_schedule = datetime.strptime(
                        item_data["schedule_date"], "%Y-%m-%d"
                    ).date()

                # Support both item_id and inventory_item_id for flexibility
                inv_item_id = item_data.get("item_id") or item_data.get(
                    "inventory_item_id"
                )
                if not inv_item_id:
                    continue

                # Support both qty and requested_qty
                qty = item_data.get("qty") or item_data.get("requested_qty") or "0"

                item = MaterialRequestItem(
                    organization_id=organization_id,
                    request_id=request.request_id,
                    inventory_item_id=coerce_uuid(inv_item_id),
                    warehouse_id=coerce_uuid(item_data.get("warehouse_id"))
                    if item_data.get("warehouse_id")
                    else None,
                    requested_qty=Decimal(str(qty)),
                    ordered_qty=Decimal("0"),
                    uom=item_data.get("uom"),
                    schedule_date=item_schedule,
                    project_id=None,
                    sequence=seq,
                )
                db.add(item)

        return request

    @staticmethod
    def submit_request(
        db: Session,
        organization_id: UUID,
        user_id: UUID,
        request_id: str,
    ) -> MaterialRequest:
        """Submit a material request."""
        request = db.get(MaterialRequest, coerce_uuid(request_id))
        if not request or request.organization_id != organization_id:
            raise ValueError("Material request not found")

        if request.status != MaterialRequestStatus.DRAFT:
            raise ValueError("Only draft requests can be submitted")

        if not request.items:
            raise ValueError("Cannot submit request without items")

        old_status = request.status.value
        request.status = MaterialRequestStatus.SUBMITTED
        request.updated_by_id = user_id

        # Fire workflow automation event
        try:
            from app.services.finance.automation.event_dispatcher import (
                fire_workflow_event,
            )

            fire_workflow_event(
                db=db,
                organization_id=organization_id,
                entity_type="MATERIAL_REQUEST",
                entity_id=request.request_id,
                event="ON_STATUS_CHANGE",
                old_values={"status": old_status},
                new_values={
                    "status": MaterialRequestStatus.SUBMITTED.value,
                    "request_number": request.request_number,
                    "request_type": request.request_type.value
                    if request.request_type
                    else None,
                    "ticket_id": str(request.ticket_id) if request.ticket_id else None,
                    "project_id": str(request.project_id)
                    if request.project_id
                    else None,
                },
                user_id=user_id,
            )
        except Exception:
            logger.exception(
                "Ignored exception"
            )  # Side effect — never breaks the main operation

        return request

    @staticmethod
    def cancel_request(
        db: Session,
        organization_id: UUID,
        user_id: UUID,
        request_id: str,
        cancel_reason: str,
    ) -> MaterialRequest:
        """Cancel a material request."""
        request = db.get(MaterialRequest, coerce_uuid(request_id))
        if not request or request.organization_id != organization_id:
            raise ValueError("Material request not found")

        if request.status not in [
            MaterialRequestStatus.DRAFT,
            MaterialRequestStatus.SUBMITTED,
        ]:
            raise ValueError("Only draft or submitted requests can be cancelled")

        reason = (cancel_reason or "").strip()
        if not reason:
            raise ValueError("Cancellation reason is required")

        request.status = MaterialRequestStatus.CANCELLED
        request.cancel_reason = reason
        request.updated_by_id = user_id

        return request

    @staticmethod
    def delete_request(
        db: Session,
        organization_id: UUID,
        request_id: str,
    ) -> None:
        """Delete a draft material request."""
        mr = db.get(MaterialRequest, coerce_uuid(request_id))
        if not mr or mr.organization_id != organization_id:
            raise ValueError("Material request not found")

        if mr.status != MaterialRequestStatus.DRAFT:
            raise ValueError("Only draft requests can be deleted")

        # Delete items first
        for item in list(mr.items):
            db.delete(item)
        db.delete(mr)

    @staticmethod
    def approve_request(
        db: Session,
        organization_id: UUID,
        user_id: UUID,
        request_id: str,
    ) -> MaterialRequest:
        """
        Approve a submitted material request and auto-deduct stock.

        For ISSUE requests: creates ISSUE transactions for each line item,
        deducting stock from the specified warehouse. Status → ISSUED.

        For TRANSFER requests: creates TRANSFER transactions for each line
        item. Status → TRANSFERRED.

        For PURCHASE requests: no stock movement; status → ORDERED.
        """
        import logging
        from datetime import datetime

        from app.models.finance.gl.fiscal_period import FiscalPeriod
        from app.models.inventory.inventory_transaction import TransactionType
        from app.services.inventory.transaction import (
            InventoryTransactionService,
            TransactionInput,
        )

        logger = logging.getLogger(__name__)

        request = (
            db.query(MaterialRequest)
            .options(joinedload(MaterialRequest.items))
            .filter(
                MaterialRequest.request_id == coerce_uuid(request_id),
                MaterialRequest.organization_id == organization_id,
            )
            .first()
        )
        if not request:
            raise ValueError("Material request not found")

        if request.status != MaterialRequestStatus.SUBMITTED:
            raise ValueError("Only submitted requests can be approved")

        if not request.items:
            raise ValueError("Cannot approve request without items")

        # For PURCHASE type: just mark as ordered, no stock movement
        if request.request_type == MaterialRequestType.PURCHASE:
            request.status = MaterialRequestStatus.ORDERED
            request.updated_by_id = user_id
            return request

        # For ISSUE and TRANSFER types: create inventory transactions
        now = datetime.now(UTC)
        txn_date = now

        # Find fiscal period for today
        fiscal_period = (
            db.query(FiscalPeriod)
            .filter(
                FiscalPeriod.organization_id == organization_id,
                FiscalPeriod.start_date <= now.date(),
                FiscalPeriod.end_date >= now.date(),
            )
            .first()
        )
        if not fiscal_period:
            raise ValueError(
                "No fiscal period found for today. "
                "Please ensure an open fiscal period exists before approving."
            )

        errors: list[str] = []

        for line in request.items:
            # Resolve warehouse: line-level or header default
            wh_id = line.warehouse_id or request.default_warehouse_id
            if not wh_id:
                errors.append(f"Item #{line.sequence}: no warehouse specified")
                continue

            # Fetch item to get its UOM and currency
            item = db.get(Item, line.inventory_item_id)
            if not item:
                errors.append(f"Item #{line.sequence}: inventory item not found")
                continue

            try:
                if request.request_type == MaterialRequestType.ISSUE:
                    txn_input = TransactionInput(
                        transaction_type=TransactionType.ISSUE,
                        transaction_date=txn_date,
                        fiscal_period_id=fiscal_period.fiscal_period_id,
                        item_id=line.inventory_item_id,
                        warehouse_id=wh_id,
                        quantity=line.requested_qty,
                        unit_cost=item.average_cost or Decimal("0"),
                        uom=line.uom or item.base_uom or "",
                        currency_code=item.currency_code
                        or settings.default_presentation_currency_code,
                        source_document_type="MATERIAL_REQUEST",
                        source_document_id=request.request_id,
                        source_document_line_id=line.item_id,
                        reference=request.request_number,
                    )
                    InventoryTransactionService.create_issue(
                        db, organization_id, txn_input, user_id
                    )
                elif request.request_type == MaterialRequestType.TRANSFER:
                    # For transfers: issue from source warehouse
                    # (full transfer support would need to_warehouse on lines)
                    txn_input = TransactionInput(
                        transaction_type=TransactionType.ISSUE,
                        transaction_date=txn_date,
                        fiscal_period_id=fiscal_period.fiscal_period_id,
                        item_id=line.inventory_item_id,
                        warehouse_id=wh_id,
                        quantity=line.requested_qty,
                        unit_cost=item.average_cost or Decimal("0"),
                        uom=line.uom or item.base_uom or "",
                        currency_code=item.currency_code
                        or settings.default_presentation_currency_code,
                        source_document_type="MATERIAL_REQUEST",
                        source_document_id=request.request_id,
                        source_document_line_id=line.item_id,
                        reference=request.request_number,
                    )
                    InventoryTransactionService.create_issue(
                        db, organization_id, txn_input, user_id
                    )
                elif request.request_type == MaterialRequestType.MANUFACTURE:
                    # Manufacture requests issue raw materials
                    txn_input = TransactionInput(
                        transaction_type=TransactionType.ISSUE,
                        transaction_date=txn_date,
                        fiscal_period_id=fiscal_period.fiscal_period_id,
                        item_id=line.inventory_item_id,
                        warehouse_id=wh_id,
                        quantity=line.requested_qty,
                        unit_cost=item.average_cost or Decimal("0"),
                        uom=line.uom or item.base_uom or "",
                        currency_code=item.currency_code
                        or settings.default_presentation_currency_code,
                        source_document_type="MATERIAL_REQUEST",
                        source_document_id=request.request_id,
                        source_document_line_id=line.item_id,
                        reference=request.request_number,
                    )
                    InventoryTransactionService.create_issue(
                        db, organization_id, txn_input, user_id
                    )

                # Mark line as fulfilled
                line.ordered_qty = line.requested_qty

            except Exception as e:
                errors.append(f"Item #{line.sequence}: {e}")
                logger.warning(
                    "Failed to create transaction for MR %s item #%s: %s",
                    request.request_number,
                    line.sequence,
                    e,
                )

        if errors and len(errors) == len(request.items):
            raise ValueError("All items failed to process: " + "; ".join(errors))

        # Set final status based on type
        if request.request_type == MaterialRequestType.TRANSFER:
            request.status = MaterialRequestStatus.TRANSFERRED
        else:
            request.status = MaterialRequestStatus.ISSUED

        request.updated_by_id = user_id

        if errors:
            logger.warning(
                "Material request %s approved with %d errors: %s",
                request.request_number,
                len(errors),
                "; ".join(errors),
            )

        return request

    @staticmethod
    def dashboard_context(db: Session, organization_id: str) -> dict:
        """Get material request metrics for dashboard widget."""
        org_id = coerce_uuid(organization_id)

        # Status counts
        status_counts = (
            db.query(MaterialRequest.status, func.count())
            .filter(MaterialRequest.organization_id == org_id)
            .group_by(MaterialRequest.status)
            .all()
        )
        counts = {s.value: c for s, c in status_counts}

        # Calculate totals
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

        # Get recent pending requests (draft or submitted)
        recent_pending = (
            db.query(MaterialRequest)
            .filter(
                MaterialRequest.organization_id == org_id,
                MaterialRequest.status.in_(
                    [
                        MaterialRequestStatus.DRAFT,
                        MaterialRequestStatus.SUBMITTED,
                        MaterialRequestStatus.PARTIALLY_ORDERED,
                    ]
                ),
            )
            .order_by(MaterialRequest.created_at.desc())
            .limit(5)
            .all()
        )

        recent_pending_list = []
        for req in recent_pending:
            item_count = (
                db.query(func.count())
                .filter(MaterialRequestItem.request_id == req.request_id)
                .scalar()
            )
            recent_pending_list.append(
                {
                    "request_id": str(req.request_id),
                    "request_number": req.request_number,
                    "request_type": req.request_type.value,
                    "status": req.status.value,
                    "status_label": req.status.value.replace("_", " ").title(),
                    "schedule_date": _format_date(req.schedule_date),
                    "item_count": item_count,
                }
            )

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


material_request_web_service = MaterialRequestWebService()
