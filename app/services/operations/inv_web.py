"""Operations inventory web service helpers."""

from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.services.finance.inv.material_request_web import MaterialRequestWebService
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


class OperationsInventoryWebService:
    """Service layer for operations inventory web routes."""

    def _mr_service(
        self, db: Session, auth: WebAuthContext
    ) -> MaterialRequestWebService:
        """Instantiate a MaterialRequestWebService for the current request."""
        assert auth.organization_id is not None
        return MaterialRequestWebService(db, auth.organization_id)

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
        service = self._mr_service(db, auth)
        context.update(
            service.list_context(
                status=status,
                request_type=request_type,
                start_date=start_date,
                end_date=end_date,
                project_id=project_id,
                page=page,
                limit=limit,
            )
        )
        return templates.TemplateResponse(
            request, "operations/inv/material_requests.html", context
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
        service = self._mr_service(db, auth)
        context.update(service.form_context())
        return templates.TemplateResponse(
            request, "operations/inv/material_request_form.html", context
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

        request_type = _safe_form_text(form_data.get("request_type", "PURCHASE"))
        schedule_date = _safe_form_text(form_data.get("schedule_date")) or None
        default_warehouse_id = (
            _safe_form_text(form_data.get("default_warehouse_id")) or None
        )
        requested_by_id = _safe_form_text(form_data.get("requested_by_id")) or None
        remarks = _safe_form_text(form_data.get("remarks")) or None

        # Parse items from JSON
        items_json = _safe_form_text(form_data.get("items_json", "[]"))
        try:
            items = json.loads(items_json) if items_json else []
        except json.JSONDecodeError:
            items = []

        service = self._mr_service(db, auth)
        try:
            mr = service.create_from_form(
                user_id=user_id,
                request_type=request_type,
                schedule_date=schedule_date,
                default_warehouse_id=default_warehouse_id,
                requested_by_id=requested_by_id,
                remarks=remarks,
                items=items,
            )
            db.commit()
            return RedirectResponse(
                f"/operations/inv/material-requests/{mr.request_id}", status_code=303
            )
        except Exception as e:
            db.rollback()
            context = base_context(
                request, auth, "New Material Request", "material_requests"
            )
            context.update(service.form_context())
            context["error"] = str(e)
            return templates.TemplateResponse(
                request, "operations/inv/material_request_form.html", context
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
        service = self._mr_service(db, auth)
        context.update(
            service.report_context(
                start_date=start_date,
                end_date=end_date,
                group_by=group_by,
            )
        )
        return templates.TemplateResponse(
            request, "operations/inv/material_request_report.html", context
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
        service = self._mr_service(db, auth)
        context.update(service.detail_context(request_id))
        if not context.get("material_request"):
            return RedirectResponse(
                "/operations/inv/material-requests", status_code=302
            )
        return templates.TemplateResponse(
            request, "operations/inv/material_request_detail.html", context
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
        service = self._mr_service(db, auth)
        context.update(service.form_context(request_id=request_id))
        if not context.get("material_request"):
            return RedirectResponse(
                "/operations/inv/material-requests", status_code=302
            )
        return templates.TemplateResponse(
            request, "operations/inv/material_request_form.html", context
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

        request_type = _safe_form_text(form_data.get("request_type", "PURCHASE"))
        schedule_date = _safe_form_text(form_data.get("schedule_date")) or None
        default_warehouse_id = (
            _safe_form_text(form_data.get("default_warehouse_id")) or None
        )
        requested_by_id = _safe_form_text(form_data.get("requested_by_id")) or None
        remarks = _safe_form_text(form_data.get("remarks")) or None

        # Parse items from JSON
        items_json = _safe_form_text(form_data.get("items_json", "[]"))
        try:
            items = json.loads(items_json) if items_json else []
        except json.JSONDecodeError:
            items = []

        service = self._mr_service(db, auth)
        try:
            mr = service.update_from_form(
                user_id=user_id,
                request_id=request_id,
                request_type=request_type,
                schedule_date=schedule_date,
                default_warehouse_id=default_warehouse_id,
                requested_by_id=requested_by_id,
                remarks=remarks,
                items=items,
            )
            db.commit()
            return RedirectResponse(
                f"/operations/inv/material-requests/{mr.request_id}", status_code=303
            )
        except Exception as e:
            db.rollback()
            context = base_context(
                request, auth, "Edit Material Request", "material_requests"
            )
            context.update(service.form_context(request_id))
            context["error"] = str(e)
            return templates.TemplateResponse(
                request, "operations/inv/material_request_form.html", context
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
        service = MaterialRequestWebService(db, org_id)
        try:
            service.submit_request(
                user_id=user_id,
                request_id=request_id,
            )
            db.commit()
        except Exception as e:
            db.rollback()
            logger.warning("Failed to submit material request %s: %s", request_id, e)
        return RedirectResponse(
            f"/operations/inv/material-requests/{request_id}", status_code=303
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
        service = MaterialRequestWebService(db, org_id)
        try:
            service.cancel_request(
                user_id=user_id,
                request_id=request_id,
            )
            db.commit()
        except Exception as e:
            db.rollback()
            logger.warning("Failed to cancel material request %s: %s", request_id, e)
        return RedirectResponse(
            f"/operations/inv/material-requests/{request_id}", status_code=303
        )

    def transaction_detail_response(
        self,
        request: Request,
        transaction_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Inventory transaction detail page."""
        from uuid import UUID as UUID_Type
        from app.models.finance.inv.inventory_transaction import InventoryTransaction
        from app.models.finance.inv.item import Item
        from app.models.finance.inv.warehouse import Warehouse
        from app.models.finance.inv.inventory_lot import InventoryLot

        context = base_context(request, auth, "Transaction Detail", "inv")

        try:
            txn_id = UUID_Type(transaction_id)
        except ValueError:
            return RedirectResponse("/operations/inv/transactions", status_code=302)

        txn = db.get(InventoryTransaction, txn_id)
        if not txn or txn.organization_id != auth.organization_id:
            return RedirectResponse("/operations/inv/transactions", status_code=302)

        item = db.get(Item, txn.item_id) if txn.item_id else None
        warehouse = db.get(Warehouse, txn.warehouse_id) if txn.warehouse_id else None
        lot = db.get(InventoryLot, txn.lot_id) if txn.lot_id else None

        context["transaction"] = txn
        context["item"] = item
        context["warehouse"] = warehouse
        context["lot"] = lot
        return templates.TemplateResponse(
            request, "operations/inv/transaction_detail.html", context
        )


operations_inv_web_service = OperationsInventoryWebService()
