"""Operations inventory web service helpers."""

from __future__ import annotations

import json
import logging
from datetime import date as date_type
from math import ceil
from typing import Optional

from fastapi import Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.services.inventory.material_request_web import MaterialRequestWebService
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

logger = logging.getLogger(__name__)


def _safe_form_text(value: object) -> str:
    """Normalize form values to text for safe parsing."""
    if value is None:
        return ""
    if isinstance(value, UploadFile):
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _form_value(form_data: object, key: str) -> object:
    """Get a form value that may be a list or multimap entry."""
    if form_data is None:
        return None
    getter = getattr(form_data, "get", None)
    if callable(getter):
        value = getter(key)
    elif isinstance(form_data, dict):
        value = form_data.get(key)
    else:
        return None
    if isinstance(value, (list, tuple)):
        return value[-1] if value else None
    return value


class OperationsInventoryWebService:
    """Service layer for operations inventory web routes."""

    @staticmethod
    def _org_id_str(auth: WebAuthContext) -> str:
        """Get organization ID as string for view helpers."""
        assert auth.organization_id is not None
        return str(auth.organization_id)

    def material_request_list_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        status: Optional[str] = None,
        request_type: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        project_id: Optional[str] = None,
        page: int = 1,
        limit: int = 50,
    ) -> HTMLResponse:
        """Material request list page."""
        context = base_context(request, auth, "Material Requests", "material_requests")
        org_id_str = self._org_id_str(auth)
        context.update(
            MaterialRequestWebService.list_context(
                db,
                org_id_str,
                status=status,
                request_type=request_type,
                start_date=start_date,
                end_date=end_date,
                project_id=project_id,
            )
        )
        return templates.TemplateResponse(
            request, "inventory/material_requests.html", context
        )

    def new_material_request_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """New material request form."""
        context = base_context(
            request, auth, "New Material Request", "material_requests"
        )
        org_id_str = self._org_id_str(auth)
        context.update(MaterialRequestWebService.form_context(db, org_id_str))
        return templates.TemplateResponse(
            request, "inventory/material_request_form.html", context
        )

    def create_material_request_response(
        self,
        request: Request,
        form_data: dict,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Create new material request from parsed form data."""
        org_id = auth.organization_id
        user_id = auth.user_id
        assert org_id is not None
        assert user_id is not None

        request_type = _safe_form_text(
            _form_value(form_data, "request_type") or "PURCHASE"
        )
        schedule_date = _safe_form_text(_form_value(form_data, "schedule_date")) or None
        default_warehouse_id = (
            _safe_form_text(_form_value(form_data, "default_warehouse_id")) or None
        )
        project_id = _safe_form_text(_form_value(form_data, "project_id")) or None
        ticket_id = _safe_form_text(_form_value(form_data, "ticket_id")) or None
        requested_by_id = (
            _safe_form_text(_form_value(form_data, "requested_by_id")) or None
        )
        remarks = _safe_form_text(_form_value(form_data, "remarks")) or None

        # Parse items from JSON
        items_json = _safe_form_text(_form_value(form_data, "items_json") or "[]")
        try:
            items = json.loads(items_json) if items_json else []
        except json.JSONDecodeError:
            items = []

        org_id_str = str(org_id)
        try:
            mr = MaterialRequestWebService.create_from_form(
                db,
                org_id,
                user_id=user_id,
                request_type=request_type,
                schedule_date=schedule_date,
                default_warehouse_id=default_warehouse_id,
                project_id=project_id,
                ticket_id=ticket_id,
                requested_by_id=requested_by_id,
                remarks=remarks,
                items=items,
            )
            db.commit()
            return RedirectResponse(
                f"/inventory/material-requests/{mr.request_id}", status_code=303
            )
        except Exception as e:
            db.rollback()
            context = base_context(
                request, auth, "New Material Request", "material_requests"
            )
            context.update(MaterialRequestWebService.form_context(db, org_id_str))
            context["error"] = str(e)
            return templates.TemplateResponse(
                request, "inventory/material_request_form.html", context
            )

    def material_request_report_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        group_by: str = "status",
    ) -> HTMLResponse:
        """Material request summary report page."""
        context = base_context(
            request, auth, "Material Request Report", "material_requests"
        )
        org_id_str = self._org_id_str(auth)
        context.update(
            MaterialRequestWebService.report_context(
                db,
                org_id_str,
                start_date=start_date,
                end_date=end_date,
                group_by=group_by,
            )
        )
        return templates.TemplateResponse(
            request, "inventory/material_request_report.html", context
        )

    def material_request_detail_response(
        self,
        request: Request,
        request_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Material request detail page."""
        context = base_context(request, auth, "Material Request", "material_requests")
        org_id_str = self._org_id_str(auth)
        context.update(
            MaterialRequestWebService.detail_context(db, org_id_str, request_id)
        )
        if not context.get("material_request"):
            return RedirectResponse("/inventory/material-requests", status_code=302)
        return templates.TemplateResponse(
            request, "inventory/material_request_detail.html", context
        )

    def edit_material_request_form_response(
        self,
        request: Request,
        request_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Edit material request form."""
        context = base_context(
            request, auth, "Edit Material Request", "material_requests"
        )
        org_id_str = self._org_id_str(auth)
        context.update(
            MaterialRequestWebService.form_context(
                db,
                org_id_str,
                request_id=request_id,
            )
        )
        if not context.get("material_request"):
            return RedirectResponse("/inventory/material-requests", status_code=302)
        return templates.TemplateResponse(
            request, "inventory/material_request_form.html", context
        )

    def update_material_request_response(
        self,
        request: Request,
        request_id: str,
        form_data: dict,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Update material request from parsed form data."""
        org_id = auth.organization_id
        user_id = auth.user_id
        assert org_id is not None
        assert user_id is not None

        request_type = _safe_form_text(
            _form_value(form_data, "request_type") or "PURCHASE"
        )
        schedule_date = _safe_form_text(_form_value(form_data, "schedule_date")) or None
        default_warehouse_id = (
            _safe_form_text(_form_value(form_data, "default_warehouse_id")) or None
        )
        project_id = _safe_form_text(_form_value(form_data, "project_id")) or None
        ticket_id = _safe_form_text(_form_value(form_data, "ticket_id")) or None
        requested_by_id = (
            _safe_form_text(_form_value(form_data, "requested_by_id")) or None
        )
        remarks = _safe_form_text(_form_value(form_data, "remarks")) or None

        # Parse items from JSON
        items_json = _safe_form_text(_form_value(form_data, "items_json") or "[]")
        try:
            items = json.loads(items_json) if items_json else []
        except json.JSONDecodeError:
            items = []

        org_id_str = str(org_id)
        try:
            mr = MaterialRequestWebService.update_from_form(
                db,
                org_id,
                user_id=user_id,
                request_id=request_id,
                request_type=request_type,
                schedule_date=schedule_date,
                default_warehouse_id=default_warehouse_id,
                project_id=project_id,
                ticket_id=ticket_id,
                requested_by_id=requested_by_id,
                remarks=remarks,
                items=items,
            )
            db.commit()
            return RedirectResponse(
                f"/inventory/material-requests/{mr.request_id}", status_code=303
            )
        except Exception as e:
            db.rollback()
            context = base_context(
                request, auth, "Edit Material Request", "material_requests"
            )
            context.update(
                MaterialRequestWebService.form_context(
                    db,
                    org_id_str,
                    request_id=request_id,
                )
            )
            context["error"] = str(e)
            return templates.TemplateResponse(
                request, "inventory/material_request_form.html", context
            )

    def submit_material_request_response(
        self,
        request_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Submit material request."""
        org_id = auth.organization_id
        user_id = auth.user_id
        assert org_id is not None
        assert user_id is not None
        try:
            MaterialRequestWebService.submit_request(
                db,
                org_id,
                user_id=user_id,
                request_id=request_id,
            )
            db.commit()
        except Exception as e:
            db.rollback()
            logger.warning("Failed to submit material request %s: %s", request_id, e)
        return RedirectResponse(
            f"/inventory/material-requests/{request_id}", status_code=303
        )

    def cancel_material_request_response(
        self,
        request_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Cancel material request."""
        org_id = auth.organization_id
        user_id = auth.user_id
        assert org_id is not None
        assert user_id is not None
        try:
            MaterialRequestWebService.cancel_request(
                db,
                org_id,
                user_id=user_id,
                request_id=request_id,
            )
            db.commit()
        except Exception as e:
            db.rollback()
            logger.warning("Failed to cancel material request %s: %s", request_id, e)
        return RedirectResponse(
            f"/inventory/material-requests/{request_id}", status_code=303
        )

    def approve_material_request_response(
        self,
        request_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Approve material request and auto-deduct stock."""
        org_id = auth.organization_id
        user_id = auth.user_id
        assert org_id is not None
        assert user_id is not None
        try:
            MaterialRequestWebService.approve_request(
                db,
                org_id,
                user_id=user_id,
                request_id=request_id,
            )
            db.commit()
        except Exception as e:
            db.rollback()
            logger.warning("Failed to approve material request %s: %s", request_id, e)
        return RedirectResponse(
            f"/inventory/material-requests/{request_id}", status_code=303
        )

    def delete_material_request_response(
        self,
        request_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Delete a material request."""
        org_id = auth.organization_id
        assert org_id is not None
        try:
            MaterialRequestWebService.delete_request(db, org_id, request_id)
            db.commit()
        except Exception as e:
            db.rollback()
            logger.warning("Failed to delete material request %s: %s", request_id, e)
            return RedirectResponse(
                f"/inventory/material-requests/{request_id}", status_code=303
            )
        return RedirectResponse("/inventory/material-requests", status_code=303)

    def transaction_detail_response(
        self,
        request: Request,
        transaction_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Inventory transaction detail page."""
        from uuid import UUID as UUID_Type

        from app.models.inventory.inventory_lot import InventoryLot
        from app.models.inventory.inventory_transaction import InventoryTransaction
        from app.models.inventory.item import Item
        from app.models.inventory.warehouse import Warehouse

        context = base_context(request, auth, "Transaction Detail", "transactions")

        try:
            txn_id = UUID_Type(transaction_id)
        except ValueError:
            return RedirectResponse("/inventory/transactions", status_code=302)

        txn = db.get(InventoryTransaction, txn_id)
        if not txn or txn.organization_id != auth.organization_id:
            return RedirectResponse("/inventory/transactions", status_code=302)

        item = db.get(Item, txn.item_id) if txn.item_id else None
        warehouse = db.get(Warehouse, txn.warehouse_id) if txn.warehouse_id else None
        lot = db.get(InventoryLot, txn.lot_id) if txn.lot_id else None

        context["transaction"] = txn
        context["item"] = item
        context["warehouse"] = warehouse
        context["lot"] = lot
        return templates.TemplateResponse(
            request, "inventory/transaction_detail.html", context
        )

    # ------------------------------------------------------------------
    # Stock Counts
    # ------------------------------------------------------------------

    def list_counts_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        status: Optional[str] = None,
        search: Optional[str] = None,
        page: int = 1,
    ) -> HTMLResponse:
        """Stock counts list page."""
        from app.models.inventory.inventory_count import CountStatus, InventoryCount
        from app.models.inventory.warehouse import Warehouse

        context = base_context(request, auth, "Stock Counts", "counts")
        org_id = auth.organization_id
        per_page = 50

        # Summary stats (unfiltered)
        base_filter = InventoryCount.organization_id == org_id
        total_count = (
            db.scalar(
                select(func.count()).select_from(InventoryCount).where(base_filter)
            )
            or 0
        )
        in_progress_count = (
            db.scalar(
                select(func.count())
                .select_from(InventoryCount)
                .where(base_filter, InventoryCount.status == CountStatus.IN_PROGRESS)
            )
            or 0
        )
        completed_count = (
            db.scalar(
                select(func.count())
                .select_from(InventoryCount)
                .where(base_filter, InventoryCount.status == CountStatus.COMPLETED)
            )
            or 0
        )
        variance_count = (
            db.scalar(
                select(func.count())
                .select_from(InventoryCount)
                .where(base_filter, InventoryCount.items_with_variance > 0)
            )
            or 0
        )

        # Build filtered query
        stmt = select(InventoryCount).where(base_filter)
        if status:
            try:
                stmt = stmt.where(InventoryCount.status == CountStatus(status))
            except ValueError:
                pass
        if search:
            term = f"%{search}%"
            stmt = stmt.where(
                or_(
                    InventoryCount.count_number.ilike(term),
                    InventoryCount.count_description.ilike(term),
                )
            )

        # Pagination
        filtered_total = (
            db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        )
        total_pages = max(1, ceil(filtered_total / per_page))

        stmt = (
            stmt.options(selectinload(InventoryCount.warehouse))
            .order_by(InventoryCount.created_at.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
        counts = list(db.scalars(stmt).all())

        # Warehouses for filter dropdown
        warehouses = list(
            db.scalars(
                select(Warehouse)
                .where(
                    Warehouse.organization_id == org_id, Warehouse.is_active.is_(True)
                )
                .order_by(Warehouse.warehouse_name)
            ).all()
        )

        context.update(
            {
                "total_count": total_count,
                "in_progress_count": in_progress_count,
                "completed_count": completed_count,
                "variance_count": variance_count,
                "counts": counts,
                "warehouses": warehouses,
                "search": search or "",
                "status": status or "",
                "warehouse": "",
                "page": page,
                "total_pages": total_pages,
            }
        )
        return templates.TemplateResponse(request, "inventory/counts.html", context)

    # ------------------------------------------------------------------
    # Bill of Materials
    # ------------------------------------------------------------------

    def list_boms_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        search: Optional[str] = None,
        status: Optional[str] = None,
        page: int = 1,
    ) -> HTMLResponse:
        """Bill of Materials list page."""
        from app.models.inventory.bom import BillOfMaterials, BOMType

        context = base_context(request, auth, "Bill of Materials", "boms")
        org_id = auth.organization_id
        per_page = 50

        # Summary stats (unfiltered)
        base_filter = BillOfMaterials.organization_id == org_id
        total_count = (
            db.scalar(
                select(func.count()).select_from(BillOfMaterials).where(base_filter)
            )
            or 0
        )
        active_count = (
            db.scalar(
                select(func.count())
                .select_from(BillOfMaterials)
                .where(base_filter, BillOfMaterials.is_active.is_(True))
            )
            or 0
        )
        assembly_count = (
            db.scalar(
                select(func.count())
                .select_from(BillOfMaterials)
                .where(base_filter, BillOfMaterials.bom_type == BOMType.ASSEMBLY)
            )
            or 0
        )
        kit_count = (
            db.scalar(
                select(func.count())
                .select_from(BillOfMaterials)
                .where(base_filter, BillOfMaterials.bom_type == BOMType.KIT)
            )
            or 0
        )

        # Build filtered query
        stmt = select(BillOfMaterials).where(base_filter)
        if status == "active":
            stmt = stmt.where(BillOfMaterials.is_active.is_(True))
        elif status == "inactive":
            stmt = stmt.where(BillOfMaterials.is_active.is_(False))
        if search:
            term = f"%{search}%"
            stmt = stmt.where(
                or_(
                    BillOfMaterials.bom_name.ilike(term),
                    BillOfMaterials.bom_code.ilike(term),
                )
            )
        if hasattr(BillOfMaterials, "bom_type") and status in (
            "ASSEMBLY",
            "DISASSEMBLY",
            "KIT",
            "PHANTOM",
        ):
            try:
                stmt = stmt.where(BillOfMaterials.bom_type == BOMType(status))
            except ValueError:
                pass

        # Pagination
        filtered_total = (
            db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        )
        total_pages = max(1, ceil(filtered_total / per_page))

        stmt = (
            stmt.options(
                selectinload(BillOfMaterials.item),
                selectinload(BillOfMaterials.components),
            )
            .order_by(BillOfMaterials.created_at.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
        boms = list(db.scalars(stmt).all())

        context.update(
            {
                "total_count": total_count,
                "active_count": active_count,
                "assembly_count": assembly_count,
                "kit_count": kit_count,
                "boms": boms,
                "search": search or "",
                "bom_type": "",
                "status": status or "",
                "page": page,
                "total_pages": total_pages,
            }
        )
        return templates.TemplateResponse(request, "inventory/boms.html", context)

    # ------------------------------------------------------------------
    # Price Lists
    # ------------------------------------------------------------------

    def list_price_lists_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        search: Optional[str] = None,
        list_type: Optional[str] = None,
        page: int = 1,
    ) -> HTMLResponse:
        """Price lists page."""
        from app.models.inventory.price_list import PriceList, PriceListType

        context = base_context(request, auth, "Price Lists", "price_lists")
        org_id = auth.organization_id
        per_page = 50

        # Summary stats (unfiltered)
        base_filter = PriceList.organization_id == org_id
        total_count = (
            db.scalar(select(func.count()).select_from(PriceList).where(base_filter))
            or 0
        )
        sales_count = (
            db.scalar(
                select(func.count())
                .select_from(PriceList)
                .where(base_filter, PriceList.price_list_type == PriceListType.SALES)
            )
            or 0
        )
        purchase_count = (
            db.scalar(
                select(func.count())
                .select_from(PriceList)
                .where(base_filter, PriceList.price_list_type == PriceListType.PURCHASE)
            )
            or 0
        )
        active_count = (
            db.scalar(
                select(func.count())
                .select_from(PriceList)
                .where(base_filter, PriceList.is_active.is_(True))
            )
            or 0
        )

        # Build filtered query
        stmt = select(PriceList).where(base_filter)
        if list_type:
            try:
                stmt = stmt.where(PriceList.price_list_type == PriceListType(list_type))
            except ValueError:
                pass
        if search:
            term = f"%{search}%"
            stmt = stmt.where(
                or_(
                    PriceList.price_list_name.ilike(term),
                    PriceList.price_list_code.ilike(term),
                )
            )

        # Pagination
        filtered_total = (
            db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        )
        total_pages = max(1, ceil(filtered_total / per_page))

        stmt = (
            stmt.options(selectinload(PriceList.items))
            .order_by(PriceList.created_at.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
        price_lists = list(db.scalars(stmt).all())

        context.update(
            {
                "total_count": total_count,
                "sales_count": sales_count,
                "purchase_count": purchase_count,
                "active_count": active_count,
                "price_lists": price_lists,
                "search": search or "",
                "price_list_type": list_type or "",
                "page": page,
                "total_pages": total_pages,
            }
        )
        return templates.TemplateResponse(
            request, "inventory/price_lists.html", context
        )

    # ------------------------------------------------------------------
    # Lots & Serial Numbers
    # ------------------------------------------------------------------

    def list_lots_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        search: Optional[str] = None,
        status: Optional[str] = None,
        warehouse: Optional[str] = None,
        page: int = 1,
    ) -> HTMLResponse:
        """Lots and serial numbers list page."""
        from app.models.inventory.inventory_lot import InventoryLot
        from app.models.inventory.warehouse import Warehouse

        context = base_context(request, auth, "Lots & Serial Numbers", "lots")
        org_id = auth.organization_id
        per_page = 50
        today = date_type.today()

        # Summary stats (unfiltered)
        base_filter = InventoryLot.organization_id == org_id
        total_count = (
            db.scalar(select(func.count()).select_from(InventoryLot).where(base_filter))
            or 0
        )
        available_count = (
            db.scalar(
                select(func.count())
                .select_from(InventoryLot)
                .where(
                    base_filter,
                    InventoryLot.quantity_available > 0,
                    InventoryLot.is_quarantined.is_(False),
                )
            )
            or 0
        )
        expiring_count = (
            db.scalar(
                select(func.count())
                .select_from(InventoryLot)
                .where(
                    base_filter,
                    InventoryLot.expiry_date.isnot(None),
                    InventoryLot.expiry_date <= today,
                )
            )
            or 0
        )
        quarantine_count = (
            db.scalar(
                select(func.count())
                .select_from(InventoryLot)
                .where(base_filter, InventoryLot.is_quarantined.is_(True))
            )
            or 0
        )

        # Build filtered query
        stmt = select(InventoryLot).where(base_filter)
        if status == "available":
            stmt = stmt.where(
                InventoryLot.quantity_available > 0,
                InventoryLot.is_quarantined.is_(False),
            )
        elif status == "quarantine":
            stmt = stmt.where(InventoryLot.is_quarantined.is_(True))
        elif status == "expired":
            stmt = stmt.where(
                InventoryLot.expiry_date.isnot(None),
                InventoryLot.expiry_date < today,
            )
        elif status == "depleted":
            stmt = stmt.where(InventoryLot.quantity_on_hand <= 0)
        if warehouse:
            from uuid import UUID as UUID_Type

            try:
                wh_id = UUID_Type(warehouse)
                stmt = stmt.where(InventoryLot.warehouse_id == wh_id)
            except ValueError:
                pass
        if search:
            term = f"%{search}%"
            stmt = stmt.where(InventoryLot.lot_number.ilike(term))

        # Pagination
        filtered_total = (
            db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        )
        total_pages = max(1, ceil(filtered_total / per_page))

        stmt = (
            stmt.options(
                selectinload(InventoryLot.item),
                selectinload(InventoryLot.warehouse),
            )
            .order_by(InventoryLot.created_at.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
        lots = list(db.scalars(stmt).all())

        # Expiring lots (within 30 days)
        from datetime import timedelta

        expiry_cutoff = today + timedelta(days=30)
        expiring_lots_stmt = (
            select(InventoryLot)
            .where(
                base_filter,
                InventoryLot.expiry_date.isnot(None),
                InventoryLot.expiry_date > today,
                InventoryLot.expiry_date <= expiry_cutoff,
                InventoryLot.quantity_on_hand > 0,
            )
            .options(
                selectinload(InventoryLot.item),
                selectinload(InventoryLot.warehouse),
            )
            .order_by(InventoryLot.expiry_date)
            .limit(10)
        )
        expiring_lots = list(db.scalars(expiring_lots_stmt).all())

        # Warehouses for filter dropdown
        warehouses = list(
            db.scalars(
                select(Warehouse)
                .where(
                    Warehouse.organization_id == org_id, Warehouse.is_active.is_(True)
                )
                .order_by(Warehouse.warehouse_name)
            ).all()
        )

        context.update(
            {
                "total_count": total_count,
                "available_count": available_count,
                "expiring_count": expiring_count,
                "quarantine_count": quarantine_count,
                "lots": lots,
                "expiring_lots": expiring_lots,
                "warehouses": warehouses,
                "now": today,
                "search": search or "",
                "status": status or "",
                "warehouse": warehouse or "",
                "page": page,
                "total_pages": total_pages,
            }
        )
        return templates.TemplateResponse(request, "inventory/lots.html", context)

    # ------------------------------------------------------------------
    # Inventory Counts — Form / Create
    # ------------------------------------------------------------------

    def new_count_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """New inventory count form."""
        from app.models.finance.gl.fiscal_period import FiscalPeriod, PeriodStatus
        from app.models.inventory.item_category import ItemCategory
        from app.models.inventory.warehouse import Warehouse

        context = base_context(request, auth, "New Stock Count", "counts")
        org_id = auth.organization_id

        warehouses = list(
            db.scalars(
                select(Warehouse)
                .where(
                    Warehouse.organization_id == org_id, Warehouse.is_active.is_(True)
                )
                .order_by(Warehouse.warehouse_name)
            ).all()
        )
        categories = list(
            db.scalars(
                select(ItemCategory)
                .where(
                    ItemCategory.organization_id == org_id,
                    ItemCategory.is_active.is_(True),
                )
                .order_by(ItemCategory.category_name)
            ).all()
        )
        periods = list(
            db.scalars(
                select(FiscalPeriod)
                .where(
                    FiscalPeriod.organization_id == org_id,
                    FiscalPeriod.status.in_([PeriodStatus.OPEN, PeriodStatus.REOPENED]),
                )
                .order_by(FiscalPeriod.start_date.desc())
            ).all()
        )

        # Generate next count number
        from app.models.inventory.inventory_count import InventoryCount

        last_num = (
            db.scalar(
                select(func.count())
                .select_from(InventoryCount)
                .where(
                    InventoryCount.organization_id == org_id,
                )
            )
            or 0
        )
        next_count_number = f"CNT-{last_num + 1:05d}"

        context.update(
            {
                "warehouses": warehouses,
                "categories": categories,
                "fiscal_periods": periods,
                "next_count_number": next_count_number,
                "today": date_type.today().strftime("%Y-%m-%d"),
            }
        )
        return templates.TemplateResponse(request, "inventory/count_form.html", context)

    async def create_count_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Create new inventory count from form data."""
        from uuid import UUID as UUID_Type

        from app.models.inventory.inventory_count import CountStatus, InventoryCount

        org_id = auth.organization_id
        user_id = auth.user_id
        assert org_id is not None
        assert user_id is not None

        form = await request.form()

        count_number = _safe_form_text(form.get("count_number"))
        count_date_str = _safe_form_text(form.get("count_date"))
        warehouse_id = _safe_form_text(form.get("warehouse_id")) or None
        category_id = _safe_form_text(form.get("category_id")) or None
        fiscal_period_id = _safe_form_text(form.get("fiscal_period_id"))
        count_description = _safe_form_text(form.get("count_description")) or None
        is_full_count = bool(form.get("is_full_count"))
        is_cycle_count = bool(form.get("is_cycle_count"))

        try:
            from datetime import date as date_cls

            count_date = (
                date_cls.fromisoformat(count_date_str)
                if count_date_str
                else date_cls.today()
            )
            count = InventoryCount(
                organization_id=org_id,
                count_number=count_number,
                count_date=count_date,
                fiscal_period_id=UUID_Type(fiscal_period_id),
                warehouse_id=UUID_Type(warehouse_id) if warehouse_id else None,
                category_id=UUID_Type(category_id) if category_id else None,
                count_description=count_description,
                is_full_count=is_full_count,
                is_cycle_count=is_cycle_count,
                status=CountStatus.DRAFT,
                created_by_user_id=user_id,
            )
            db.add(count)
            db.commit()
            return RedirectResponse(
                f"/inventory/counts/{count.count_id}", status_code=303
            )
        except Exception as e:
            db.rollback()
            logger.warning("Failed to create inventory count: %s", e)
            context = base_context(request, auth, "New Stock Count", "counts")
            context["error"] = str(e)
            # Re-populate form context
            return self.new_count_form_response(request, auth, db)

    # ------------------------------------------------------------------
    # Bill of Materials — Form / Create
    # ------------------------------------------------------------------

    def new_bom_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """New BOM form."""
        from app.models.inventory.item import Item
        from app.models.inventory.warehouse import Warehouse

        context = base_context(request, auth, "New Bill of Materials", "boms")
        org_id = auth.organization_id

        items = list(
            db.scalars(
                select(Item)
                .where(Item.organization_id == org_id, Item.is_active.is_(True))
                .order_by(Item.item_name)
            ).all()
        )
        warehouses = list(
            db.scalars(
                select(Warehouse)
                .where(
                    Warehouse.organization_id == org_id, Warehouse.is_active.is_(True)
                )
                .order_by(Warehouse.warehouse_name)
            ).all()
        )

        context.update({"items": items, "warehouses": warehouses})
        return templates.TemplateResponse(request, "inventory/bom_form.html", context)

    async def create_bom_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Create new BOM from form data."""
        from decimal import Decimal, InvalidOperation
        from uuid import UUID as UUID_Type

        from app.models.inventory.bom import BillOfMaterials, BOMComponent, BOMType

        org_id = auth.organization_id
        assert org_id is not None

        form = await request.form()

        bom_name = _safe_form_text(form.get("bom_name"))
        bom_type_str = _safe_form_text(form.get("bom_type") or "ASSEMBLY")
        finished_item_id = _safe_form_text(form.get("finished_item_id"))
        quantity_str = _safe_form_text(form.get("quantity") or "1")
        warehouse_id = _safe_form_text(form.get("warehouse_id")) or None
        description = _safe_form_text(form.get("description")) or None
        components_json = _safe_form_text(form.get("components_json") or "[]")

        try:
            bom_type = (
                BOMType(bom_type_str)
                if bom_type_str in {t.value for t in BOMType}
                else BOMType.ASSEMBLY
            )
            output_qty = Decimal(quantity_str) if quantity_str else Decimal("1")

            # Generate BOM code
            from app.models.inventory.bom import BillOfMaterials as BOM_Model

            last_num = (
                db.scalar(
                    select(func.count())
                    .select_from(BOM_Model)
                    .where(
                        BOM_Model.organization_id == org_id,
                    )
                )
                or 0
            )
            bom_code = f"BOM-{last_num + 1:05d}"

            bom = BillOfMaterials(
                organization_id=org_id,
                bom_code=bom_code,
                bom_name=bom_name,
                item_id=UUID_Type(finished_item_id),
                bom_type=bom_type,
                output_quantity=output_qty,
                output_uom="EACH",
                description=description,
            )
            db.add(bom)
            db.flush()

            # Parse and add components
            try:
                components = json.loads(components_json) if components_json else []
            except json.JSONDecodeError:
                components = []

            for idx, comp in enumerate(components, start=1):
                comp_item_id = comp.get("item_id")
                if not comp_item_id:
                    continue
                try:
                    comp_qty = Decimal(str(comp.get("quantity", "1")))
                except (InvalidOperation, ValueError):
                    comp_qty = Decimal("1")
                try:
                    scrap = Decimal(str(comp.get("scrap_percentage", "0")))
                except (InvalidOperation, ValueError):
                    scrap = Decimal("0")

                db.add(
                    BOMComponent(
                        bom_id=bom.bom_id,
                        component_item_id=UUID_Type(comp_item_id),
                        quantity=comp_qty,
                        uom=comp.get("uom") or "EACH",
                        scrap_percent=scrap,
                        line_number=idx,
                    )
                )

            db.commit()
            return RedirectResponse(f"/inventory/boms/{bom.bom_id}", status_code=303)
        except Exception as e:
            db.rollback()
            logger.warning("Failed to create BOM: %s", e)
            context = base_context(request, auth, "New Bill of Materials", "boms")
            context["error"] = str(e)
            return self.new_bom_form_response(request, auth, db)

    # ------------------------------------------------------------------
    # Inventory Reports Hub
    # ------------------------------------------------------------------

    def inventory_reports_hub_response(
        self,
        request: Request,
        auth: WebAuthContext,
    ) -> HTMLResponse:
        """Inventory reports hub page (navigation only)."""
        context = base_context(request, auth, "Inventory Reports", "reports")
        return templates.TemplateResponse(request, "inventory/reports.html", context)

    # ------------------------------------------------------------------
    # Stock Count Detail & Workflow
    # ------------------------------------------------------------------

    def count_detail_response(
        self,
        request: Request,
        count_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Stock count detail page."""
        from uuid import UUID as UUID_Type

        from app.models.inventory.inventory_count import InventoryCount
        from app.models.inventory.item import Item
        from app.models.inventory.warehouse import Warehouse
        from app.services.inventory.count import InventoryCountService

        context = base_context(request, auth, "Stock Count", "counts")

        try:
            cnt_id = UUID_Type(count_id)
        except ValueError:
            return RedirectResponse("/inventory/counts", status_code=302)

        count = db.get(InventoryCount, cnt_id)
        if not count or count.organization_id != auth.organization_id:
            return RedirectResponse("/inventory/counts", status_code=302)

        # Eager-load warehouse for the count header
        if count.warehouse_id:
            wh = db.get(Warehouse, count.warehouse_id)
            if wh:
                count.warehouse = wh  # type: ignore[assignment]

        # Load count lines with related item and warehouse
        lines_raw = InventoryCountService.list_lines(
            db,
            count_id,
            limit=500,
        )

        # Batch-load items and warehouses for the lines
        item_ids = {l.item_id for l in lines_raw}
        wh_ids = {l.warehouse_id for l in lines_raw}

        items_map: dict[UUID_Type, Item] = {}
        if item_ids:
            items_map = {
                it.item_id: it
                for it in db.scalars(
                    select(Item).where(Item.item_id.in_(item_ids))
                ).all()
            }
        wh_map: dict[UUID_Type, Warehouse] = {}
        if wh_ids:
            wh_map = {
                w.warehouse_id: w
                for w in db.scalars(
                    select(Warehouse).where(Warehouse.warehouse_id.in_(wh_ids))
                ).all()
            }

        # Attach item/warehouse to each line for template access
        for line in lines_raw:
            line.item = items_map.get(line.item_id)  # type: ignore[attr-defined]
            line.warehouse = wh_map.get(line.warehouse_id)  # type: ignore[attr-defined]

        # Get summary stats
        try:
            summary = InventoryCountService.get_count_summary(
                db,
                auth.organization_id,
                cnt_id,
            )
            summary_dict = {
                "total_items": summary.total_items,
                "items_counted": summary.items_counted,
                "items_with_variance": summary.items_with_variance,
                "total_variance_value": summary.total_variance_value,
            }
        except Exception:
            summary_dict = {
                "total_items": count.total_items,
                "items_counted": count.items_counted,
                "items_with_variance": count.items_with_variance,
                "total_variance_value": 0,
            }

        context.update(
            {
                "count": count,
                "lines": lines_raw,
                "summary": summary_dict,
            }
        )
        return templates.TemplateResponse(
            request, "inventory/count_detail.html", context
        )

    def start_count_response(
        self,
        count_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Start an inventory count (DRAFT → IN_PROGRESS)."""
        from app.services.common import coerce_uuid
        from app.services.inventory.count import InventoryCountService

        org_id = auth.organization_id
        user_id = auth.user_id
        assert org_id is not None
        assert user_id is not None
        try:
            InventoryCountService.start_count(
                db,
                org_id,
                coerce_uuid(count_id),
                user_id,
            )
        except Exception as e:
            logger.warning("Failed to start count %s: %s", count_id, e)
        return RedirectResponse(f"/inventory/counts/{count_id}", status_code=303)

    def complete_count_response(
        self,
        count_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Complete an inventory count (IN_PROGRESS → COMPLETED)."""
        from app.services.common import coerce_uuid
        from app.services.inventory.count import InventoryCountService

        org_id = auth.organization_id
        assert org_id is not None
        try:
            InventoryCountService.complete_count(
                db,
                org_id,
                coerce_uuid(count_id),
            )
        except Exception as e:
            logger.warning("Failed to complete count %s: %s", count_id, e)
        return RedirectResponse(f"/inventory/counts/{count_id}", status_code=303)

    def post_count_response(
        self,
        count_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Post inventory count adjustments (COMPLETED → POSTED)."""
        from app.services.common import coerce_uuid
        from app.services.inventory.count import InventoryCountService

        org_id = auth.organization_id
        user_id = auth.user_id
        assert org_id is not None
        assert user_id is not None
        try:
            InventoryCountService.post_count(
                db,
                org_id,
                coerce_uuid(count_id),
                user_id,
            )
        except Exception as e:
            logger.warning("Failed to post count %s: %s", count_id, e)
        return RedirectResponse(f"/inventory/counts/{count_id}", status_code=303)

    async def record_count_line_response(
        self,
        request: Request,
        count_id: str,
        line_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Record counted quantity for a count line."""
        from decimal import Decimal, InvalidOperation
        from uuid import UUID as UUID_Type

        from app.models.inventory.inventory_count_line import InventoryCountLine
        from app.services.common import coerce_uuid
        from app.services.inventory.count import CountLineInput, InventoryCountService

        org_id = auth.organization_id
        user_id = auth.user_id
        assert org_id is not None
        assert user_id is not None

        form = await request.form()
        counted_qty_str = _safe_form_text(form.get("counted_quantity"))
        reason_code = _safe_form_text(form.get("reason_code")) or None
        notes = _safe_form_text(form.get("notes")) or None

        try:
            counted_qty = Decimal(counted_qty_str) if counted_qty_str else Decimal("0")
        except (InvalidOperation, ValueError):
            counted_qty = Decimal("0")

        # Get the existing line to extract item_id, warehouse_id, lot_id
        try:
            lid = UUID_Type(line_id)
        except ValueError:
            return RedirectResponse(f"/inventory/counts/{count_id}", status_code=303)

        line = db.get(InventoryCountLine, lid)
        if not line or str(line.count_id) != count_id:
            return RedirectResponse(f"/inventory/counts/{count_id}", status_code=303)

        try:
            InventoryCountService.record_count(
                db,
                org_id,
                coerce_uuid(count_id),
                CountLineInput(
                    item_id=line.item_id,
                    warehouse_id=line.warehouse_id,
                    counted_quantity=counted_qty,
                    lot_id=line.lot_id,
                    reason_code=reason_code,
                    notes=notes,
                ),
                user_id,
            )
        except Exception as e:
            logger.warning(
                "Failed to record count line %s on count %s: %s",
                line_id,
                count_id,
                e,
            )
        return RedirectResponse(f"/inventory/counts/{count_id}", status_code=303)

    # ------------------------------------------------------------------
    # BOM Detail
    # ------------------------------------------------------------------

    def bom_detail_response(
        self,
        request: Request,
        bom_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Bill of Materials detail page."""
        from decimal import Decimal
        from uuid import UUID as UUID_Type

        from app.models.inventory.bom import BillOfMaterials
        from app.models.inventory.inventory_transaction import (
            InventoryTransaction,
            TransactionType,
        )
        from app.models.inventory.item import Item

        context = base_context(request, auth, "Bill of Materials", "boms")

        try:
            bid = UUID_Type(bom_id)
        except ValueError:
            return RedirectResponse("/inventory/boms", status_code=302)

        bom = db.scalars(
            select(BillOfMaterials)
            .options(selectinload(BillOfMaterials.components))
            .where(BillOfMaterials.bom_id == bid)
        ).first()

        if not bom or bom.organization_id != auth.organization_id:
            return RedirectResponse("/inventory/boms", status_code=302)

        # Load the finished item
        finished_item = db.get(Item, bom.item_id) if bom.item_id else None
        bom.finished_item = finished_item  # type: ignore[attr-defined]

        # Provide template-expected aliases
        bom.quantity = bom.output_quantity  # type: ignore[attr-defined]

        # Load component items in batch
        comp_item_ids = {c.component_item_id for c in bom.components}
        comp_items_map: dict[UUID_Type, Item] = {}
        if comp_item_ids:
            comp_items_map = {
                it.item_id: it
                for it in db.scalars(
                    select(Item).where(Item.item_id.in_(comp_item_ids))
                ).all()
            }

        # Attach component_item + scrap_percentage alias to each component
        estimated_cost = Decimal("0")
        for comp in bom.components:
            comp.component_item = comp_items_map.get(comp.component_item_id)  # type: ignore[attr-defined]
            comp.scrap_percentage = comp.scrap_percent  # type: ignore[attr-defined]
            if comp.component_item:  # type: ignore[attr-defined]
                item_cost = getattr(
                    comp.component_item, "standard_cost", None
                ) or Decimal("0")  # type: ignore[attr-defined]
                estimated_cost += (comp.quantity or Decimal("0")) * item_cost

        bom.estimated_cost = estimated_cost  # type: ignore[attr-defined]
        bom.scrap_percentage = Decimal("0")  # type: ignore[attr-defined]

        # Recent transactions for the finished item
        recent_transactions: list = []
        if bom.item_id:
            recent_transactions = list(
                db.scalars(
                    select(InventoryTransaction)
                    .where(
                        InventoryTransaction.organization_id == auth.organization_id,
                        InventoryTransaction.item_id == bom.item_id,
                        InventoryTransaction.transaction_type.in_(
                            [
                                TransactionType.ASSEMBLY,
                                TransactionType.DISASSEMBLY,
                            ]
                        ),
                    )
                    .order_by(InventoryTransaction.transaction_date.desc())
                    .limit(10)
                ).all()
            )

        context.update(
            {
                "bom": bom,
                "recent_transactions": recent_transactions,
            }
        )
        return templates.TemplateResponse(request, "inventory/bom_detail.html", context)

    # ------------------------------------------------------------------
    # Lot Detail & Quarantine
    # ------------------------------------------------------------------

    def lot_detail_response(
        self,
        request: Request,
        lot_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Lot detail page."""
        from datetime import datetime as dt_cls
        from datetime import timezone
        from uuid import UUID as UUID_Type

        from app.models.inventory.inventory_lot import InventoryLot
        from app.models.inventory.inventory_transaction import InventoryTransaction
        from app.models.inventory.item import Item
        from app.models.inventory.warehouse import Warehouse

        context = base_context(request, auth, "Lot Detail", "lots")

        try:
            lid = UUID_Type(lot_id)
        except ValueError:
            return RedirectResponse("/inventory/lots", status_code=302)

        lot = db.get(InventoryLot, lid)
        if not lot or lot.organization_id != auth.organization_id:
            return RedirectResponse("/inventory/lots", status_code=302)

        # Load related item and warehouse
        if lot.item_id:
            lot.item = db.get(Item, lot.item_id)  # type: ignore[assignment]
        if lot.warehouse_id:
            lot.warehouse = db.get(Warehouse, lot.warehouse_id)  # type: ignore[assignment]

        # Recent transactions for this lot
        transactions = list(
            db.scalars(
                select(InventoryTransaction)
                .where(
                    InventoryTransaction.organization_id == auth.organization_id,
                    InventoryTransaction.lot_id == lid,
                )
                .order_by(InventoryTransaction.transaction_date.desc())
                .limit(20)
            ).all()
        )

        context.update(
            {
                "lot": lot,
                "transactions": transactions,
                "now": dt_cls.now(timezone.utc),
            }
        )
        return templates.TemplateResponse(request, "inventory/lot_detail.html", context)

    def toggle_lot_quarantine_response(
        self,
        lot_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Toggle lot quarantine status."""
        from uuid import UUID as UUID_Type

        from app.models.inventory.inventory_lot import InventoryLot

        org_id = auth.organization_id
        assert org_id is not None

        try:
            lid = UUID_Type(lot_id)
        except ValueError:
            return RedirectResponse("/inventory/lots", status_code=303)

        lot = db.get(InventoryLot, lid)
        if not lot or lot.organization_id != org_id:
            return RedirectResponse("/inventory/lots", status_code=303)

        lot.is_quarantined = not lot.is_quarantined
        if not lot.is_quarantined:
            lot.quarantine_reason = None
        try:
            db.commit()
        except Exception as e:
            db.rollback()
            logger.warning("Failed to toggle quarantine for lot %s: %s", lot_id, e)

        return RedirectResponse(f"/inventory/lots/{lot_id}", status_code=303)

    # ------------------------------------------------------------------
    # Price List Form & Create
    # ------------------------------------------------------------------

    def new_price_list_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """New price list form."""
        from app.models.inventory.item import Item

        context = base_context(request, auth, "New Price List", "price_lists")
        org_id = auth.organization_id

        # Common currencies
        currencies = [
            {"currency_code": "NGN", "currency_name": "Nigerian Naira"},
            {"currency_code": "USD", "currency_name": "US Dollar"},
            {"currency_code": "EUR", "currency_name": "Euro"},
            {"currency_code": "GBP", "currency_name": "British Pound"},
        ]

        items = list(
            db.scalars(
                select(Item)
                .where(Item.organization_id == org_id, Item.is_active.is_(True))
                .order_by(Item.item_name)
            ).all()
        )

        context.update(
            {
                "price_list": None,
                "currencies": currencies,
                "inventory_items": items,
            }
        )
        return templates.TemplateResponse(
            request, "inventory/price_list_form.html", context
        )

    async def create_price_list_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Create new price list from form data."""
        from decimal import Decimal, InvalidOperation
        from uuid import UUID as UUID_Type

        from app.models.inventory.price_list import (
            PriceList,
            PriceListItem,
            PriceListType,
        )

        org_id = auth.organization_id
        assert org_id is not None

        form = await request.form()

        pl_name = _safe_form_text(form.get("price_list_name"))
        pl_type_str = _safe_form_text(form.get("price_list_type") or "SALES")
        currency_code = _safe_form_text(form.get("currency_code") or "NGN")
        effective_from = _safe_form_text(form.get("effective_from")) or None
        effective_to = _safe_form_text(form.get("effective_to")) or None
        markup_str = _safe_form_text(form.get("markup_percent")) or None
        description = _safe_form_text(form.get("description")) or None
        is_default = bool(form.get("is_default"))
        items_json = _safe_form_text(form.get("items_json") or "[]")

        try:
            pl_type = (
                PriceListType(pl_type_str)
                if pl_type_str in {t.value for t in PriceListType}
                else PriceListType.SALES
            )

            # Generate price list code
            last_num = (
                db.scalar(
                    select(func.count())
                    .select_from(PriceList)
                    .where(
                        PriceList.organization_id == org_id,
                    )
                )
                or 0
            )
            pl_code = f"PL-{last_num + 1:05d}"

            # Parse dates
            from datetime import date as date_cls

            eff_from = (
                date_cls.fromisoformat(effective_from) if effective_from else None
            )
            eff_to = date_cls.fromisoformat(effective_to) if effective_to else None

            markup = None
            if markup_str:
                try:
                    markup = Decimal(markup_str)
                except (InvalidOperation, ValueError):
                    markup = None

            price_list = PriceList(
                organization_id=org_id,
                price_list_code=pl_code,
                price_list_name=pl_name,
                description=description,
                price_list_type=pl_type,
                currency_code=currency_code,
                effective_from=eff_from,
                effective_to=eff_to,
                markup_percent=markup,
                is_default=is_default,
            )
            db.add(price_list)
            db.flush()

            # Parse and add items
            try:
                items = json.loads(items_json) if items_json else []
            except json.JSONDecodeError:
                items = []

            for item_data in items:
                item_id_str = item_data.get("item_id")
                if not item_id_str:
                    continue
                try:
                    unit_price = Decimal(str(item_data.get("price", "0")))
                except (InvalidOperation, ValueError):
                    unit_price = Decimal("0")
                try:
                    min_qty = Decimal(str(item_data.get("min_quantity", "1")))
                except (InvalidOperation, ValueError):
                    min_qty = Decimal("1")
                try:
                    disc_pct = Decimal(str(item_data.get("discount_percent", "0")))
                except (InvalidOperation, ValueError):
                    disc_pct = None

                db.add(
                    PriceListItem(
                        price_list_id=price_list.price_list_id,
                        item_id=UUID_Type(item_id_str),
                        unit_price=unit_price,
                        currency_code=currency_code,
                        min_quantity=min_qty,
                        discount_percent=disc_pct if disc_pct else None,
                    )
                )

            db.commit()
            return RedirectResponse("/inventory/price-lists", status_code=303)
        except Exception as e:
            db.rollback()
            logger.warning("Failed to create price list: %s", e)
            context = base_context(request, auth, "New Price List", "price_lists")
            context["error"] = str(e)
            return self.new_price_list_form_response(request, auth, db)

    # ------------------------------------------------------------------
    # Stock on Hand Report
    # ------------------------------------------------------------------

    def stock_on_hand_report_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        warehouse: Optional[str] = None,
        category: Optional[str] = None,
        show_zero: Optional[str] = None,
        format: Optional[str] = None,
        page: int = 1,
    ) -> HTMLResponse:
        """Stock on hand report page."""
        from decimal import Decimal
        from uuid import UUID as UUID_Type

        from app.models.inventory.item import Item
        from app.models.inventory.item_category import ItemCategory
        from app.models.inventory.warehouse import Warehouse

        context = base_context(request, auth, "Stock on Hand", "reports")
        org_id = auth.organization_id
        per_page = 50
        include_zero = show_zero in ("true", "1", "on")

        # Get filter options
        warehouses = list(
            db.scalars(
                select(Warehouse)
                .where(
                    Warehouse.organization_id == org_id, Warehouse.is_active.is_(True)
                )
                .order_by(Warehouse.warehouse_name)
            ).all()
        )
        categories = list(
            db.scalars(
                select(ItemCategory)
                .where(
                    ItemCategory.organization_id == org_id,
                    ItemCategory.is_active.is_(True),
                )
                .order_by(ItemCategory.category_name)
            ).all()
        )

        # Build items query
        items_stmt = select(Item).where(
            Item.organization_id == org_id,
            Item.is_active.is_(True),
            Item.track_inventory.is_(True),
        )
        if category:
            try:
                cat_id = UUID_Type(category)
                items_stmt = items_stmt.where(Item.category_id == cat_id)
            except ValueError:
                pass

        items = list(db.scalars(items_stmt.order_by(Item.item_code)).all())

        # Batch load categories for the items
        cat_ids = {item.category_id for item in items if item.category_id}
        cat_map: dict[UUID_Type, ItemCategory] = {}
        if cat_ids:
            cat_map = {
                c.category_id: c
                for c in db.scalars(
                    select(ItemCategory).where(ItemCategory.category_id.in_(cat_ids))
                ).all()
            }

        # Batch load stock quantities
        from app.services.inventory.web import _get_batch_stock_quantities

        item_ids = [item.item_id for item in items]
        stock_quantities = (
            _get_batch_stock_quantities(db, org_id, item_ids) if item_ids else {}
        )

        # Build stock data rows
        all_stock_data = []
        total_quantity = Decimal("0")
        total_value = Decimal("0")
        total_reserved = Decimal("0")
        total_available = Decimal("0")
        below_reorder = 0

        for item in items:
            stock = stock_quantities.get(item.item_id, {})
            on_hand = stock.get("on_hand", Decimal("0"))
            reserved = stock.get("reserved", Decimal("0"))
            available = stock.get("available", Decimal("0"))
            unit_cost = item.average_cost or item.standard_cost or Decimal("0")
            item_value = on_hand * unit_cost
            reorder_pt = (
                Decimal(str(item.reorder_point)) if item.reorder_point else None
            )
            is_low = bool(reorder_pt and on_hand < reorder_pt)

            if not include_zero and on_hand == 0:
                continue

            cat = cat_map.get(item.category_id)
            all_stock_data.append(
                {
                    "item_code": item.item_code,
                    "item_name": item.item_name,
                    "category_name": cat.category_name if cat else "-",
                    "warehouse_name": "All",
                    "on_hand": on_hand,
                    "reserved": reserved,
                    "available": available,
                    "unit_cost": unit_cost,
                    "total_value": item_value,
                    "is_low_stock": is_low,
                }
            )

            total_quantity += on_hand
            total_value += item_value
            total_reserved += reserved
            total_available += available
            if is_low:
                below_reorder += 1

        # Paginate
        total_items_count = len(all_stock_data)
        total_pages = max(1, ceil(total_items_count / per_page))
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        stock_data_page = all_stock_data[start_idx:end_idx]

        summary = {
            "total_items": total_items_count,
            "total_quantity": total_quantity,
            "total_value": total_value,
            "total_reserved": total_reserved,
            "total_available": total_available,
            "below_reorder": below_reorder,
        }

        context.update(
            {
                "summary": summary,
                "stock_data": stock_data_page,
                "warehouses": warehouses,
                "categories": categories,
                "warehouse": warehouse or "",
                "category": category or "",
                "show_zero": include_zero,
                "page": page,
                "total_pages": total_pages,
                "total_count": total_items_count,
                "limit": per_page,
            }
        )
        return templates.TemplateResponse(
            request, "inventory/report_stock_on_hand.html", context
        )


operations_inv_web_service = OperationsInventoryWebService()
