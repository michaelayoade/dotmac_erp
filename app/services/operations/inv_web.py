"""Operations inventory web service helpers."""

from __future__ import annotations

import csv
import json
import logging
from datetime import date, datetime, timedelta, timezone
from datetime import date as date_type
from io import StringIO
from typing import Any, cast

try:
    from datetime import UTC  # type: ignore
except ImportError:  # pragma: no cover
    UTC = timezone.utc

from math import ceil

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session, selectinload
from starlette.datastructures import UploadFile

from app.services.common_filters import build_active_filters
from app.services.finance.common.attachment import AttachmentInput, attachment_service
from app.services.finance.platform.currency_context import get_currency_context
from app.services.finance.platform.org_context import org_context_service
from app.services.inventory.material_request_web import MaterialRequestWebService
from app.services.inventory.return_web import InventoryReturnWebService
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

logger = logging.getLogger(__name__)

_RETURN_IMAGE_CONTENT_TYPES = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/webp",
    }
)


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
    def _extract_uploads(form_data: object, key: str) -> list[UploadFile]:
        """Collect uploaded files from multipart form data."""
        if form_data is None:
            return []
        getter = getattr(form_data, "getlist", None)
        if callable(getter):
            values = getter(key)
        else:
            value = _form_value(form_data, key)
            values = value if isinstance(value, list) else [value]
        return [
            upload
            for upload in values
            if isinstance(upload, UploadFile) and (upload.filename or "").strip()
        ]

    @staticmethod
    def _validate_return_image_uploads(uploads: list[UploadFile]) -> None:
        """Ensure only supported image uploads are accepted."""
        for upload in uploads:
            content_type = (upload.content_type or "").lower().strip()
            if content_type not in _RETURN_IMAGE_CONTENT_TYPES:
                raise ValueError(
                    "Only image files are allowed. Accepted formats: JPG, PNG, GIF, WEBP."
                )

    @staticmethod
    def _save_return_image_uploads(
        db: Session,
        organization_id,
        user_id,
        return_id: str,
        uploads: list[UploadFile],
    ) -> None:
        """Persist uploaded return images as attachments."""
        for upload in uploads:
            attachment_service.save_file(
                db=db,
                organization_id=organization_id,
                input=AttachmentInput(
                    entity_type="INVENTORY_RETURN",
                    entity_id=return_id,
                    file_name=upload.filename or "return-image",
                    content_type=upload.content_type or "application/octet-stream",
                    description="Inventory return image",
                ),
                file_content=upload.file,
                uploaded_by=user_id,
            )

    @staticmethod
    def _org_id_str(auth: WebAuthContext) -> str:
        """Get organization ID as string for view helpers."""
        if auth.organization_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        return str(auth.organization_id)

    def material_request_list_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        status: str | None = None,
        request_type: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        project_id: str | None = None,
        page: int = 1,
        limit: int = 50,
    ) -> HTMLResponse:
        """Material request list page."""
        context = base_context(request, auth, "Material Requests", "material_requests")
        org_id_str = self._org_id_str(auth)
        try:
            context.update(
                MaterialRequestWebService.list_context(
                    db,
                    org_id_str,
                    status=status,
                    request_type=request_type,
                    start_date=start_date,
                    end_date=end_date,
                    project_id=project_id,
                    page=page,
                    per_page=limit,
                )
            )
        except Exception:
            logger.exception(
                "Failed to render material request list",
                extra={"organization_id": org_id_str},
            )
            context.update(
                {
                    "requests": [],
                    "filter_status": status,
                    "filter_request_type": request_type,
                    "filter_start_date": start_date,
                    "filter_end_date": end_date,
                    "filter_project_id": project_id,
                    "status_counts": {},
                    "type_counts": {},
                    "statuses": [],
                    "request_types": [],
                    "active_filters": [],
                    "error": "Unable to load material requests right now.",
                }
            )
        return templates.TemplateResponse(
            request, "inventory/material_requests.html", context
        )

    def inventory_return_list_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        page: int = 1,
        limit: int = 50,
    ) -> HTMLResponse:
        """Inventory return list page."""
        context = base_context(request, auth, "Returned Items", "transactions")
        org_id_str = self._org_id_str(auth)
        try:
            context.update(
                InventoryReturnWebService.list_context(
                    db,
                    org_id_str,
                    page=page,
                    limit=limit,
                )
            )
        except Exception:
            logger.exception(
                "Failed to render inventory returns list",
                extra={"organization_id": org_id_str},
            )
            context.update(
                {
                    "returns": [],
                    "page": page,
                    "limit": limit,
                    "total_count": 0,
                    "total_pages": 1,
                    "error": "Unable to load returned items right now.",
                }
            )
        return templates.TemplateResponse(request, "inventory/returns.html", context)

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
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")

        request_type = _safe_form_text(
            _form_value(form_data, "request_type") or "PURCHASE"
        )
        schedule_date = _safe_form_text(_form_value(form_data, "schedule_date")) or None
        default_warehouse_id = (
            _safe_form_text(_form_value(form_data, "default_warehouse_id")) or None
        )
        transfer_to_warehouse_id = (
            _safe_form_text(_form_value(form_data, "transfer_to_warehouse_id")) or None
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
                transfer_to_warehouse_id=transfer_to_warehouse_id,
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
        start_date: str | None = None,
        end_date: str | None = None,
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
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")

        request_type = _safe_form_text(
            _form_value(form_data, "request_type") or "PURCHASE"
        )
        schedule_date = _safe_form_text(_form_value(form_data, "schedule_date")) or None
        default_warehouse_id = (
            _safe_form_text(_form_value(form_data, "default_warehouse_id")) or None
        )
        transfer_to_warehouse_id = (
            _safe_form_text(_form_value(form_data, "transfer_to_warehouse_id")) or None
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
                transfer_to_warehouse_id=transfer_to_warehouse_id,
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

    def new_inventory_return_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """Render return-to-store form."""
        context = base_context(request, auth, "Return to Store", "transactions")
        context.update(
            InventoryReturnWebService.form_context(db, self._org_id_str(auth))
        )
        return templates.TemplateResponse(
            request, "inventory/return_form.html", context
        )

    def create_inventory_return_response(
        self,
        request: Request,
        form_data: dict,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Create a return-to-store record and post stock."""
        org_id = auth.organization_id
        user_id = auth.user_id
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")

        material_request_id = (
            _safe_form_text(_form_value(form_data, "material_request_id")) or None
        )
        item_id = _safe_form_text(_form_value(form_data, "item_id"))
        source_warehouse_id = _safe_form_text(
            _form_value(form_data, "source_warehouse_id")
        )
        destination_warehouse_id = _safe_form_text(
            _form_value(form_data, "destination_warehouse_id")
        )
        quantity = _safe_form_text(_form_value(form_data, "quantity"))
        return_date = _safe_form_text(_form_value(form_data, "return_date"))
        reason = _safe_form_text(_form_value(form_data, "reason"))
        reference = _safe_form_text(_form_value(form_data, "reference")) or None
        remarks = _safe_form_text(_form_value(form_data, "remarks")) or None
        lot_number = _safe_form_text(_form_value(form_data, "lot_number")) or None
        serial_numbers = (
            _safe_form_text(_form_value(form_data, "serial_numbers")) or None
        )
        image_uploads = self._extract_uploads(form_data, "images")
        try:
            self._validate_return_image_uploads(image_uploads)
            inventory_return = InventoryReturnWebService.create_from_form(
                db=db,
                organization_id=org_id,
                user_id=user_id,
                material_request_id=material_request_id,
                item_id=item_id,
                source_warehouse_id=source_warehouse_id,
                destination_warehouse_id=destination_warehouse_id,
                quantity=quantity,
                return_date=return_date,
                reason=reason,
                reference=reference,
                remarks=remarks,
                lot_number=lot_number,
                serial_numbers_text=serial_numbers,
            )
            db.commit()
            if image_uploads:
                self._save_return_image_uploads(
                    db=db,
                    organization_id=org_id,
                    user_id=user_id,
                    return_id=str(inventory_return.return_id),
                    uploads=image_uploads,
                )
            return RedirectResponse(
                f"/inventory/returns/{inventory_return.return_id}",
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            context = base_context(request, auth, "Return to Store", "transactions")
            context.update(
                InventoryReturnWebService.form_context(db, self._org_id_str(auth))
            )
            context["error"] = str(e)
            return templates.TemplateResponse(
                request, "inventory/return_form.html", context
            )

    def inventory_return_detail_response(
        self,
        request: Request,
        return_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Inventory return detail page."""
        context = base_context(request, auth, "Inventory Return", "transactions")
        context.update(
            InventoryReturnWebService.detail_context(
                db, self._org_id_str(auth), return_id
            )
        )
        if not context.get("inventory_return"):
            return RedirectResponse("/inventory/transactions", status_code=302)
        attachments = context.get("attachments") or []
        for attachment in attachments:
            if attachment.get("uploaded_at") is not None:
                attachment["uploaded_at_display"] = attachment["uploaded_at"].strftime(
                    "%Y-%m-%d %H:%M"
                )
            else:
                attachment["uploaded_at_display"] = "-"
            attachment["file_size_display"] = attachment.get("file_size_display", "-")
        context["error"] = request.query_params.get("error")
        return templates.TemplateResponse(
            request, "inventory/return_detail.html", context
        )

    def download_attachment_response(
        self,
        attachment_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Download an inventory attachment via authenticated file proxy."""
        if auth.organization_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")

        attachment = attachment_service.get(
            db,
            auth.organization_id,
            attachment_id,
        )
        if not attachment or attachment.organization_id != auth.organization_id:
            return RedirectResponse(
                url="/inventory/returns?error=Attachment+not+found",
                status_code=303,
            )
        return RedirectResponse(
            url=f"/files/attachments/{attachment_id}",
            status_code=302,
        )

    def delete_attachment_response(
        self,
        attachment_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Delete an inventory return attachment."""
        if auth.organization_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")

        attachment = attachment_service.get(
            db,
            auth.organization_id,
            attachment_id,
        )
        if not attachment or attachment.organization_id != auth.organization_id:
            return RedirectResponse(
                url="/inventory/returns?error=Attachment+not+found",
                status_code=303,
            )

        entity_type = attachment.entity_type
        entity_id = attachment.entity_id
        attachment_service.delete(db, attachment_id, auth.organization_id)

        redirect_map = {
            "INVENTORY_RETURN": f"/inventory/returns/{entity_id}",
        }
        redirect_url = redirect_map.get(entity_type, "/inventory/returns")
        return RedirectResponse(
            url=f"{redirect_url}?success=Attachment+deleted",
            status_code=303,
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
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
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
        cancel_reason: str,
    ) -> RedirectResponse:
        """Cancel material request."""
        org_id = auth.organization_id
        user_id = auth.user_id
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        try:
            MaterialRequestWebService.cancel_request(
                db,
                org_id,
                user_id=user_id,
                request_id=request_id,
                cancel_reason=cancel_reason,
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
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
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
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
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
        status: str | None = None,
        search: str | None = None,
        warehouse: str | None = None,
        page: int = 1,
        limit: int = 50,
        sort: str | None = "count_date",
        sort_dir: str | None = "desc",
    ) -> HTMLResponse:
        """Stock counts list page."""
        from app.models.inventory.inventory_count import CountStatus, InventoryCount
        from app.models.inventory.inventory_count_line import InventoryCountLine
        from app.models.inventory.warehouse import Warehouse

        context = base_context(request, auth, "Stock Counts", "counts")
        org_id = auth.organization_id
        per_page = limit

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

        variance_subquery = (
            select(
                InventoryCountLine.count_id.label("count_id"),
                func.coalesce(func.sum(InventoryCountLine.variance_value), 0).label(
                    "total_variance_value"
                ),
            )
            .group_by(InventoryCountLine.count_id)
            .subquery()
        )

        warehouse_sort = func.coalesce(Warehouse.warehouse_name, "All Warehouses")
        variance_sort = func.coalesce(variance_subquery.c.total_variance_value, 0)
        type_sort = case(
            (InventoryCount.is_cycle_count.is_(True), 0),
            (InventoryCount.is_full_count.is_(True), 1),
            else_=2,
        )
        sort_columns = {
            "count_number": InventoryCount.count_number,
            "count_date": InventoryCount.count_date,
            "warehouse": warehouse_sort,
            "total_items": InventoryCount.total_items,
            "variance": variance_sort,
            "status": InventoryCount.status,
            "type": type_sort,
        }
        sort_key = sort if sort in sort_columns else "count_date"
        sort_direction = "asc" if sort_dir == "asc" else "desc"
        order_column = sort_columns[sort_key]
        order_by_clause = (
            order_column.asc() if sort_direction == "asc" else order_column.desc()
        )

        # Build filtered query
        stmt = (
            select(
                InventoryCount,
                variance_sort.label("total_variance_value"),
            )
            .outerjoin(
                variance_subquery,
                variance_subquery.c.count_id == InventoryCount.count_id,
            )
            .outerjoin(Warehouse, Warehouse.warehouse_id == InventoryCount.warehouse_id)
            .where(base_filter)
        )
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
        if warehouse:
            from uuid import UUID as UUID_Type

            try:
                stmt = stmt.where(InventoryCount.warehouse_id == UUID_Type(warehouse))
            except ValueError:
                pass

        # Pagination
        filtered_total = (
            db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        )
        total_pages = max(1, ceil(filtered_total / per_page))

        stmt = (
            stmt.options(selectinload(InventoryCount.warehouse))
            .order_by(order_by_clause, InventoryCount.created_at.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
        count_rows = db.execute(stmt).all()
        counts = []
        for count, total_variance_value in count_rows:
            count.total_variance_value = total_variance_value or 0
            counts.append(count)

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

        active_filters = build_active_filters(
            params={
                "search": search or "",
                "status": status or "",
                "warehouse": warehouse or "",
            },
            labels={
                "search": "Search",
                "status": "Status",
                "warehouse": "Warehouse",
            },
            options={
                "warehouse": {
                    str(wh.warehouse_id): wh.warehouse_name for wh in warehouses
                }
            },
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
                "warehouse": warehouse or "",
                "page": page,
                "limit": per_page,
                "filtered_total": filtered_total,
                "total_pages": total_pages,
                "active_filters": active_filters,
                "sort": sort_key,
                "sort_dir": sort_direction,
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
        search: str | None = None,
        bom_type: str | None = None,
        status: str | None = None,
        page: int = 1,
    ) -> HTMLResponse:
        """Bill of Materials list page."""
        from app.models.inventory.bom import BillOfMaterials, BOMType

        context = base_context(request, auth, "Bill of Materials", "boms", db=db)
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
        if hasattr(BillOfMaterials, "bom_type") and bom_type in (
            "ASSEMBLY",
            "DISASSEMBLY",
            "KIT",
            "PHANTOM",
        ):
            try:
                stmt = stmt.where(BillOfMaterials.bom_type == BOMType(bom_type))
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

        active_filters = build_active_filters(
            params={
                "search": search or "",
                "bom_type": bom_type or "",
                "status": status or "",
            },
            labels={
                "search": "Search",
                "bom_type": "BOM Type",
                "status": "Status",
            },
        )

        context.update(
            {
                "total_count": total_count,
                "active_count": active_count,
                "assembly_count": assembly_count,
                "kit_count": kit_count,
                "boms": boms,
                "search": search or "",
                "bom_type": bom_type or "",
                "status": status or "",
                "page": page,
                "total_pages": total_pages,
                "active_filters": active_filters,
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
        search: str | None = None,
        price_list_type: str | None = None,
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
        if price_list_type:
            try:
                stmt = stmt.where(
                    PriceList.price_list_type == PriceListType(price_list_type)
                )
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

        active_filters = build_active_filters(
            params={
                "search": search or "",
                "price_list_type": price_list_type or "",
            },
            labels={
                "search": "Search",
                "price_list_type": "Type",
            },
        )

        context.update(
            {
                "total_count": total_count,
                "sales_count": sales_count,
                "purchase_count": purchase_count,
                "active_count": active_count,
                "price_lists": price_lists,
                "search": search or "",
                "price_list_type": price_list_type or "",
                "page": page,
                "total_pages": total_pages,
                "active_filters": active_filters,
            }
        )
        return templates.TemplateResponse(
            request, "inventory/price_lists.html", context
        )

    # ------------------------------------------------------------------
    # Lots & Serial Numbers
    # ------------------------------------------------------------------

    def list_serials_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        search: str | None = None,
        status: str | None = None,
        warehouse: str | None = None,
        item: str | None = None,
        lot: str | None = None,
        page: int = 1,
    ) -> HTMLResponse:
        """Serial numbers list page."""
        from uuid import UUID as UUID_Type

        from app.models.inventory.inventory_lot import InventoryLot
        from app.models.inventory.inventory_serial import InventorySerial
        from app.models.inventory.item import Item
        from app.models.inventory.warehouse import Warehouse

        context = base_context(request, auth, "Serial Numbers", "serials")
        org_id = auth.organization_id
        per_page = 50

        base_filter = InventorySerial.organization_id == org_id
        total_count = (
            db.scalar(select(func.count(InventorySerial.serial_id)).where(base_filter))
            or 0
        )
        available_count = (
            db.scalar(
                select(func.count(InventorySerial.serial_id)).where(
                    base_filter,
                    InventorySerial.status == "AVAILABLE",
                    InventorySerial.is_active.is_(True),
                )
            )
            or 0
        )
        issued_count = (
            db.scalar(
                select(func.count(InventorySerial.serial_id)).where(
                    base_filter,
                    InventorySerial.status == "ISSUED",
                )
            )
            or 0
        )
        inactive_count = (
            db.scalar(
                select(func.count(InventorySerial.serial_id)).where(
                    base_filter,
                    InventorySerial.is_active.is_(False),
                )
            )
            or 0
        )
        tracked_statuses = ["AVAILABLE", "ISSUED", "RESERVED", "DAMAGED", "TRANSFERRED"]
        status_counts = {
            status_key: (
                db.scalar(
                    select(func.count(InventorySerial.serial_id)).where(
                        base_filter,
                        InventorySerial.status == status_key,
                        InventorySerial.is_active.is_(True),
                    )
                )
                or 0
            )
            for status_key in tracked_statuses
        }
        blocked_count = (
            db.scalar(
                select(func.count(InventorySerial.serial_id)).where(
                    base_filter,
                    or_(
                        InventorySerial.is_active.is_(False),
                        InventorySerial.status != "AVAILABLE",
                        InventorySerial.warehouse_id.is_(None),
                    ),
                )
            )
            or 0
        )

        serial_ids_stmt = (
            select(InventorySerial.serial_id)
            .join(Item, InventorySerial.item_id == Item.item_id)
            .outerjoin(InventoryLot, InventorySerial.lot_id == InventoryLot.lot_id)
            .outerjoin(
                Warehouse, InventorySerial.warehouse_id == Warehouse.warehouse_id
            )
            .where(base_filter)
        )

        normalized_status = (status or "").strip().upper()
        allowed_statuses = {
            "AVAILABLE",
            "ISSUED",
            "RESERVED",
            "DAMAGED",
            "TRANSFERRED",
        }
        if normalized_status in allowed_statuses:
            serial_ids_stmt = serial_ids_stmt.where(
                InventorySerial.status == normalized_status,
                InventorySerial.is_active.is_(True),
            )
        elif normalized_status == "INACTIVE":
            serial_ids_stmt = serial_ids_stmt.where(
                InventorySerial.is_active.is_(False)
            )
        elif normalized_status == "USABLE":
            serial_ids_stmt = serial_ids_stmt.where(
                InventorySerial.status == "AVAILABLE",
                InventorySerial.is_active.is_(True),
                InventorySerial.warehouse_id.is_not(None),
            )
        elif normalized_status == "BLOCKED":
            serial_ids_stmt = serial_ids_stmt.where(
                or_(
                    InventorySerial.is_active.is_(False),
                    InventorySerial.status != "AVAILABLE",
                    InventorySerial.warehouse_id.is_(None),
                )
            )

        if warehouse:
            try:
                wh_id = UUID_Type(warehouse)
                serial_ids_stmt = serial_ids_stmt.where(
                    InventorySerial.warehouse_id == wh_id
                )
            except ValueError:
                pass

        selected_item = None
        if item:
            try:
                item_id = UUID_Type(item)
                serial_ids_stmt = serial_ids_stmt.where(
                    InventorySerial.item_id == item_id
                )
                selected_item = db.get(Item, item_id)
                if selected_item and selected_item.organization_id != org_id:
                    selected_item = None
            except ValueError:
                pass

        selected_lot = None
        if lot:
            try:
                lot_id = UUID_Type(lot)
                serial_ids_stmt = serial_ids_stmt.where(
                    InventorySerial.lot_id == lot_id
                )
                selected_lot = db.get(InventoryLot, lot_id)
                if selected_lot and selected_lot.organization_id != org_id:
                    selected_lot = None
            except ValueError:
                pass

        if search:
            term = f"%{search}%"
            serial_ids_stmt = serial_ids_stmt.where(
                or_(
                    InventorySerial.serial_number.ilike(term),
                    Item.item_code.ilike(term),
                    Item.item_name.ilike(term),
                    InventoryLot.lot_number.ilike(term),
                )
            )

        filtered_total = (
            db.scalar(select(func.count()).select_from(serial_ids_stmt.subquery())) or 0
        )
        total_pages = max(1, ceil(filtered_total / per_page))
        serial_id_rows = list(
            db.execute(
                serial_ids_stmt.order_by(
                    InventorySerial.created_at.desc(),
                    InventorySerial.serial_number,
                )
                .offset((page - 1) * per_page)
                .limit(per_page)
            ).all()
        )
        serial_ids = [row.serial_id for row in serial_id_rows]

        serial_rows = []
        if serial_ids:
            rows = list(
                db.execute(
                    select(InventorySerial, Item, InventoryLot, Warehouse)
                    .join(Item, InventorySerial.item_id == Item.item_id)
                    .outerjoin(
                        InventoryLot, InventorySerial.lot_id == InventoryLot.lot_id
                    )
                    .outerjoin(
                        Warehouse,
                        InventorySerial.warehouse_id == Warehouse.warehouse_id,
                    )
                    .where(InventorySerial.serial_id.in_(serial_ids))
                ).all()
            )
            row_by_id = {row.InventorySerial.serial_id: row for row in rows}
            for serial_id in serial_ids:
                row = row_by_id.get(serial_id)
                if not row:
                    continue
                serial_rows.append(
                    {
                        "serial": row.InventorySerial,
                        "item": row.Item,
                        "lot": row.InventoryLot,
                        "warehouse": row.Warehouse,
                    }
                )

        warehouses = list(
            db.scalars(
                select(Warehouse)
                .where(
                    Warehouse.organization_id == org_id,
                    Warehouse.is_active.is_(True),
                )
                .order_by(Warehouse.warehouse_name)
            ).all()
        )
        items = list(
            db.scalars(
                select(Item)
                .where(
                    Item.organization_id == org_id,
                    Item.is_active.is_(True),
                    Item.track_serial_numbers.is_(True),
                )
                .order_by(Item.item_code)
            ).all()
        )

        active_filters = build_active_filters(
            params={
                "search": search or "",
                "status": (status or "").lower(),
                "warehouse": warehouse or "",
                "item": item or "",
                "lot": lot or "",
            },
            labels={
                "search": "Search",
                "status": "Status",
                "warehouse": "Warehouse",
                "item": "Item",
                "lot": "Lot",
            },
            options={
                "status": {
                    "usable": "Can be used",
                    "available": "Available",
                    "issued": "Issued",
                    "reserved": "Reserved",
                    "damaged": "Damaged",
                    "transferred": "Transferred",
                    "inactive": "Inactive",
                    "blocked": "Not usable",
                },
                "warehouse": {
                    str(wh.warehouse_id): wh.warehouse_name for wh in warehouses
                },
                "item": {
                    str(
                        list_item.item_id
                    ): f"{list_item.item_code} - {list_item.item_name}"
                    for list_item in items
                },
                "lot": {
                    str(selected_lot.lot_id): selected_lot.lot_number
                    for selected_lot in [selected_lot]
                    if selected_lot is not None
                },
            },
        )

        context.update(
            {
                "total_count": total_count,
                "available_count": available_count,
                "issued_count": issued_count,
                "inactive_count": inactive_count,
                "reserved_count": status_counts["RESERVED"],
                "damaged_count": status_counts["DAMAGED"],
                "transferred_count": status_counts["TRANSFERRED"],
                "blocked_count": blocked_count,
                "serial_rows": serial_rows,
                "warehouses": warehouses,
                "items": items,
                "selected_item": selected_item,
                "search": search or "",
                "status": status or "",
                "warehouse": warehouse or "",
                "item": item or "",
                "lot": lot or "",
                "page": page,
                "total_pages": total_pages,
                "filtered_total": filtered_total,
                "active_filters": active_filters,
            }
        )
        return templates.TemplateResponse(request, "inventory/serials.html", context)

    def list_lots_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        search: str | None = None,
        status: str | None = None,
        warehouse: str | None = None,
        page: int = 1,
    ) -> HTMLResponse:
        """Lots and serial numbers list page."""
        from app.models.inventory.inventory_lot import InventoryLot
        from app.models.inventory.inventory_lot_balance import InventoryLotBalance
        from app.models.inventory.item import Item
        from app.models.inventory.warehouse import Warehouse

        context = base_context(request, auth, "Lots & Serial Numbers", "lots")
        org_id = auth.organization_id
        per_page = 50
        today = date_type.today()
        from datetime import timedelta

        expiry_cutoff = today + timedelta(days=30)

        base_lot_filter = InventoryLot.organization_id == org_id

        total_count = (
            db.scalar(
                select(func.count(func.distinct(InventoryLot.lot_id)))
                .select_from(InventoryLot)
                .where(base_lot_filter)
            )
            or 0
        )
        available_count = (
            db.scalar(
                select(func.count(func.distinct(InventoryLot.lot_id)))
                .select_from(InventoryLot)
                .join(
                    InventoryLotBalance,
                    InventoryLotBalance.lot_id == InventoryLot.lot_id,
                )
                .where(
                    base_lot_filter,
                    InventoryLotBalance.quantity_available > 0,
                    InventoryLotBalance.is_quarantined.is_(False),
                )
            )
            or 0
        )
        expiring_count = (
            db.scalar(
                select(func.count(func.distinct(InventoryLot.lot_id)))
                .select_from(InventoryLot)
                .join(
                    InventoryLotBalance,
                    InventoryLotBalance.lot_id == InventoryLot.lot_id,
                )
                .where(
                    base_lot_filter,
                    InventoryLot.expiry_date.isnot(None),
                    InventoryLot.expiry_date > today,
                    InventoryLot.expiry_date <= expiry_cutoff,
                    InventoryLotBalance.quantity_on_hand > 0,
                )
            )
            or 0
        )
        quarantine_count = (
            db.scalar(
                select(func.count(func.distinct(InventoryLot.lot_id)))
                .select_from(InventoryLot)
                .join(
                    InventoryLotBalance,
                    InventoryLotBalance.lot_id == InventoryLot.lot_id,
                )
                .where(base_lot_filter, InventoryLotBalance.is_quarantined.is_(True))
            )
            or 0
        )

        # Build filtered lot-id query first, then load full rows separately.
        lot_ids_stmt = (
            select(
                InventoryLot.lot_id,
                func.max(InventoryLot.created_at).label("sort_created_at"),
            )
            .join(Item, InventoryLot.item_id == Item.item_id)
            .outerjoin(
                InventoryLotBalance, InventoryLotBalance.lot_id == InventoryLot.lot_id
            )
            .where(base_lot_filter)
        )
        if status == "available":
            lot_ids_stmt = lot_ids_stmt.where(
                InventoryLotBalance.quantity_available > 0,
                InventoryLotBalance.is_quarantined.is_(False),
            )
        elif status == "quarantine":
            lot_ids_stmt = lot_ids_stmt.where(
                InventoryLotBalance.is_quarantined.is_(True)
            )
        elif status == "expired":
            lot_ids_stmt = lot_ids_stmt.where(
                InventoryLot.expiry_date.isnot(None),
                InventoryLot.expiry_date < today,
                InventoryLotBalance.quantity_on_hand > 0,
            )
        elif status == "depleted":
            lot_ids_stmt = lot_ids_stmt.where(
                func.coalesce(InventoryLotBalance.quantity_on_hand, 0) <= 0
            )
        if warehouse:
            from uuid import UUID as UUID_Type

            try:
                wh_id = UUID_Type(warehouse)
                lot_ids_stmt = lot_ids_stmt.where(
                    InventoryLotBalance.warehouse_id == wh_id
                )
            except ValueError:
                pass
        if search:
            term = f"%{search}%"
            lot_ids_stmt = lot_ids_stmt.where(
                or_(
                    InventoryLot.lot_number.ilike(term),
                    Item.item_code.ilike(term),
                    Item.item_name.ilike(term),
                )
            )

        lot_ids_stmt = lot_ids_stmt.group_by(InventoryLot.lot_id)

        # Pagination
        filtered_total = (
            db.scalar(select(func.count()).select_from(lot_ids_stmt.subquery())) or 0
        )
        total_pages = max(1, ceil(filtered_total / per_page))

        paged_lot_rows = list(
            db.execute(
                lot_ids_stmt.order_by(func.max(InventoryLot.created_at).desc())
                .offset((page - 1) * per_page)
                .limit(per_page)
            ).all()
        )
        paged_lot_ids = [row.lot_id for row in paged_lot_rows]
        lots = []
        if paged_lot_ids:
            stmt = (
                select(InventoryLot)
                .where(InventoryLot.lot_id.in_(paged_lot_ids))
                .options(
                    selectinload(InventoryLot.item),
                    selectinload(InventoryLot.balances),
                )
            )
            lots = list(db.scalars(stmt).all())
            lot_order = {lot_id: index for index, lot_id in enumerate(paged_lot_ids)}
            lots.sort(key=lambda lot: lot_order.get(lot.lot_id, len(lot_order)))

        warehouse_ids: set = set()
        for lot in lots:
            for balance in getattr(lot, "balances", []) or []:
                if balance.warehouse_id:
                    warehouse_ids.add(balance.warehouse_id)

        warehouse_map = {}
        if warehouse_ids:
            warehouse_map = {
                wh.warehouse_id: wh
                for wh in db.scalars(
                    select(Warehouse).where(Warehouse.warehouse_id.in_(warehouse_ids))
                ).all()
            }

        for lot in lots:
            lot_view = cast(Any, lot)
            balances = list(getattr(lot, "balances", []) or [])
            t_on_hand = sum((balance.quantity_on_hand or 0) for balance in balances)
            t_allocated = sum((balance.quantity_allocated or 0) for balance in balances)
            t_available = sum((balance.quantity_available or 0) for balance in balances)
            lot_view.total_on_hand = t_on_hand
            lot_view.total_allocated = t_allocated
            lot_view.total_available = t_available
            lot_view.remaining_quantity = t_on_hand
            lot.is_quarantined = any(
                bool(balance.is_quarantined) for balance in balances
            )
            active_warehouse_names = [
                warehouse_map[balance.warehouse_id].warehouse_name
                for balance in balances
                if balance.warehouse_id in warehouse_map
                and (
                    (balance.quantity_on_hand or 0) > 0
                    or (balance.quantity_allocated or 0) > 0
                )
            ]
            distinct_names = sorted(set(active_warehouse_names))
            if len(distinct_names) == 1:
                lot_view.display_warehouse = distinct_names[0]
            elif len(distinct_names) > 1:
                lot_view.display_warehouse = f"Multiple ({len(distinct_names)})"
            else:
                lot_view.display_warehouse = "-"

        # Expiring lots (within 30 days)
        expiring_lot_rows = list(
            db.execute(
                select(
                    InventoryLot.lot_id,
                    func.min(InventoryLot.expiry_date).label("sort_expiry_date"),
                )
                .join(
                    InventoryLotBalance,
                    InventoryLotBalance.lot_id == InventoryLot.lot_id,
                )
                .where(
                    base_lot_filter,
                    InventoryLot.expiry_date.isnot(None),
                    InventoryLot.expiry_date > today,
                    InventoryLot.expiry_date <= expiry_cutoff,
                    InventoryLotBalance.quantity_on_hand > 0,
                )
                .group_by(InventoryLot.lot_id)
                .order_by(func.min(InventoryLot.expiry_date))
                .limit(10)
            ).all()
        )
        expiring_lot_ids = [row.lot_id for row in expiring_lot_rows]
        expiring_lots = []
        if expiring_lot_ids:
            expiring_lots = list(
                db.scalars(
                    select(InventoryLot)
                    .where(InventoryLot.lot_id.in_(expiring_lot_ids))
                    .options(
                        selectinload(InventoryLot.item),
                        selectinload(InventoryLot.balances),
                    )
                ).all()
            )
            expiring_order = {
                lot_id: index for index, lot_id in enumerate(expiring_lot_ids)
            }
            expiring_lots.sort(
                key=lambda lot: expiring_order.get(lot.lot_id, len(expiring_order))
            )

        for lot in expiring_lots:
            lot_view = cast(Any, lot)
            balances = list(getattr(lot, "balances", []) or [])
            lot_view.total_on_hand = sum(
                (balance.quantity_on_hand or 0) for balance in balances
            )

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

        active_filters = build_active_filters(
            params={
                "search": search or "",
                "status": status or "",
                "warehouse": warehouse or "",
            },
            labels={
                "search": "Search",
                "status": "Status",
                "warehouse": "Warehouse",
            },
            options={
                "warehouse": {
                    str(wh.warehouse_id): wh.warehouse_name for wh in warehouses
                }
            },
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
                "active_filters": active_filters,
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
                    FiscalPeriod.status.in_(PeriodStatus.accepts_postings()),
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
        from app.services.inventory.count import CountInput, InventoryCountService

        org_id = auth.organization_id
        user_id = auth.user_id
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")

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
            from uuid import UUID as UUID_Type

            count_date = (
                date_cls.fromisoformat(count_date_str)
                if count_date_str
                else date_cls.today()
            )
            count = InventoryCountService.create_count(
                db=db,
                organization_id=org_id,
                input=CountInput(
                    count_number=count_number,
                    count_date=count_date,
                    fiscal_period_id=UUID_Type(fiscal_period_id),
                    warehouse_id=UUID_Type(warehouse_id) if warehouse_id else None,
                    category_id=UUID_Type(category_id) if category_id else None,
                    count_description=count_description,
                    is_full_count=is_full_count,
                    is_cycle_count=is_cycle_count,
                ),
                created_by_user_id=user_id,
            )
            return RedirectResponse(
                f"/inventory/counts/{count.count_id}", status_code=303
            )
        except Exception as e:
            db.rollback()
            logger.warning("Failed to create inventory count: %s", e)
            response = self.new_count_form_response(request, auth, db)
            cast(Any, response).context["error"] = str(e)
            return response

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
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")

        form = await request.form()

        bom_name = _safe_form_text(form.get("bom_name"))
        bom_type_str = _safe_form_text(form.get("bom_type") or "ASSEMBLY")
        finished_item_id = _safe_form_text(form.get("finished_item_id"))
        quantity_str = _safe_form_text(form.get("quantity") or "1")
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

    def serial_stock_report_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        warehouse: str | None = None,
        item: str | None = None,
        search: str | None = None,
        page: int = 1,
    ) -> HTMLResponse:
        """Serial stock by warehouse report."""
        from uuid import UUID as UUID_Type

        from app.models.inventory.inventory_lot import InventoryLot
        from app.models.inventory.inventory_serial import InventorySerial
        from app.models.inventory.item import Item
        from app.models.inventory.warehouse import Warehouse

        context = base_context(request, auth, "Serial Stock by Warehouse", "reports")
        org_id = auth.organization_id
        if org_id is None:
            raise HTTPException(status_code=400, detail="Organization is required")

        per_page = 50
        warehouses = list(
            db.scalars(
                select(Warehouse)
                .where(
                    Warehouse.organization_id == org_id,
                    Warehouse.is_active.is_(True),
                )
                .order_by(Warehouse.warehouse_name)
            ).all()
        )
        items = list(
            db.scalars(
                select(Item)
                .where(
                    Item.organization_id == org_id,
                    Item.is_active.is_(True),
                    Item.track_serial_numbers.is_(True),
                )
                .order_by(Item.item_code)
            ).all()
        )

        serial_ids_stmt = (
            select(
                InventorySerial.serial_id,
                InventorySerial.item_id,
                InventorySerial.warehouse_id,
            )
            .join(Item, InventorySerial.item_id == Item.item_id)
            .join(Warehouse, InventorySerial.warehouse_id == Warehouse.warehouse_id)
            .outerjoin(InventoryLot, InventorySerial.lot_id == InventoryLot.lot_id)
            .where(
                InventorySerial.organization_id == org_id,
                InventorySerial.status == "AVAILABLE",
                InventorySerial.is_active.is_(True),
                InventorySerial.warehouse_id.is_not(None),
            )
        )

        selected_warehouse = None
        if warehouse:
            try:
                warehouse_id = UUID_Type(warehouse)
                serial_ids_stmt = serial_ids_stmt.where(
                    InventorySerial.warehouse_id == warehouse_id
                )
                selected_warehouse = db.get(Warehouse, warehouse_id)
                if selected_warehouse and selected_warehouse.organization_id != org_id:
                    selected_warehouse = None
            except ValueError:
                pass

        selected_item = None
        if item:
            try:
                item_id = UUID_Type(item)
                serial_ids_stmt = serial_ids_stmt.where(
                    InventorySerial.item_id == item_id
                )
                selected_item = db.get(Item, item_id)
                if selected_item and selected_item.organization_id != org_id:
                    selected_item = None
            except ValueError:
                pass

        if search:
            term = f"%{search}%"
            serial_ids_stmt = serial_ids_stmt.where(
                or_(
                    InventorySerial.serial_number.ilike(term),
                    Item.item_code.ilike(term),
                    Item.item_name.ilike(term),
                    Warehouse.warehouse_name.ilike(term),
                    Warehouse.warehouse_code.ilike(term),
                    InventoryLot.lot_number.ilike(term),
                )
            )

        filtered_subquery = serial_ids_stmt.subquery()
        total_count = (
            db.scalar(select(func.count()).select_from(filtered_subquery)) or 0
        )
        warehouse_count = (
            db.scalar(
                select(func.count(func.distinct(filtered_subquery.c.warehouse_id)))
            )
            or 0
        )
        item_count = (
            db.scalar(select(func.count(func.distinct(filtered_subquery.c.item_id))))
            or 0
        )
        total_pages = max(1, ceil(total_count / per_page))
        serial_id_rows = list(
            db.execute(
                serial_ids_stmt.order_by(
                    Warehouse.warehouse_name,
                    Item.item_code,
                    InventorySerial.serial_number,
                )
                .offset((page - 1) * per_page)
                .limit(per_page)
            ).all()
        )
        serial_ids = [row.serial_id for row in serial_id_rows]

        serial_rows = []
        if serial_ids:
            rows = list(
                db.execute(
                    select(InventorySerial, Item, Warehouse, InventoryLot)
                    .join(Item, InventorySerial.item_id == Item.item_id)
                    .join(
                        Warehouse,
                        InventorySerial.warehouse_id == Warehouse.warehouse_id,
                    )
                    .outerjoin(
                        InventoryLot, InventorySerial.lot_id == InventoryLot.lot_id
                    )
                    .where(InventorySerial.serial_id.in_(serial_ids))
                ).all()
            )
            row_by_id = {row.InventorySerial.serial_id: row for row in rows}
            for serial_id in serial_ids:
                row = row_by_id.get(serial_id)
                if not row:
                    continue
                serial_rows.append(
                    {
                        "serial": row.InventorySerial,
                        "item": row.Item,
                        "warehouse": row.Warehouse,
                        "lot": row.InventoryLot,
                    }
                )

        active_filters = build_active_filters(
            params={
                "search": search or "",
                "warehouse": warehouse or "",
                "item": item or "",
            },
            labels={
                "search": "Search",
                "warehouse": "Warehouse",
                "item": "Item",
            },
            options={
                "warehouse": {
                    str(wh.warehouse_id): wh.warehouse_name for wh in warehouses
                },
                "item": {
                    str(
                        list_item.item_id
                    ): f"{list_item.item_code} - {list_item.item_name}"
                    for list_item in items
                },
            },
        )

        context.update(
            {
                "serial_rows": serial_rows,
                "warehouses": warehouses,
                "items": items,
                "selected_warehouse": selected_warehouse,
                "selected_item": selected_item,
                "search": search or "",
                "warehouse": warehouse or "",
                "item": item or "",
                "summary": {
                    "total_serials": total_count,
                    "warehouse_count": warehouse_count,
                    "item_count": item_count,
                },
                "page": page,
                "total_pages": total_pages,
                "total_count": total_count,
                "limit": per_page,
                "active_filters": active_filters,
            }
        )
        return templates.TemplateResponse(
            request, "inventory/report_serial_stock.html", context
        )

    def inventory_valuation_report_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """Inventory valuation report page."""
        from app.services.finance.rpt.inventory_valuation import (
            inventory_valuation_reconciliation_context,
        )

        context = base_context(request, auth, "Inventory Valuation", "reports")
        valuation_context = inventory_valuation_reconciliation_context(
            db,
            str(auth.organization_id),
        )
        context.update(valuation_context)
        self._notify_inventory_valuation_mismatch(db, auth, valuation_context)
        return templates.TemplateResponse(
            request,
            "inventory/report_inventory_valuation.html",
            context,
        )

    def _notify_inventory_valuation_mismatch(
        self,
        db: Session,
        auth: WebAuthContext,
        valuation_context: dict[str, Any],
    ) -> int:
        """Notify admin and inventory-manager users when valuation is mismatched."""
        if not valuation_context.get("has_data") or valuation_context.get(
            "is_balanced"
        ):
            return 0
        org_id = auth.organization_id
        fiscal_period_id = valuation_context.get("fiscal_period_id")
        if org_id is None or not fiscal_period_id:
            return 0

        try:
            from sqlalchemy import func

            from app.models.notification import (
                EntityType,
                NotificationChannel,
                NotificationType,
            )
            from app.models.person import Person
            from app.models.rbac import PersonRole, Role
            from app.services.common import coerce_uuid
            from app.services.notification import NotificationService

            role_names = {"admin", "inventory_manager", "inventory manager"}
            recipients_stmt = (
                select(Person.id)
                .join(PersonRole, PersonRole.person_id == Person.id)
                .join(Role, Role.id == PersonRole.role_id)
                .where(
                    Person.organization_id == org_id,
                    Person.is_active.is_(True),
                    Role.is_active.is_(True),
                    func.lower(Role.name).in_(role_names),
                )
                .distinct()
            )
            recipient_ids = list(db.scalars(recipients_stmt).all())
            if not recipient_ids:
                return 0

            service = NotificationService()
            since = datetime.now(timezone.utc) - timedelta(hours=24)
            sent = 0
            title = "Inventory valuation mismatch detected"
            message = (
                "Inventory valuation does not match the GL control balance. "
                f"Difference: {valuation_context.get('difference', '0')}. "
                f"Mismatched rows: {valuation_context.get('valuation_mismatch_count', 0)}."
            )
            for recipient_id in recipient_ids:
                created = service.create_if_not_sent_since(
                    db,
                    organization_id=org_id,
                    recipient_id=recipient_id,
                    entity_type=EntityType.SYSTEM,
                    entity_id=coerce_uuid(fiscal_period_id),
                    notification_type=NotificationType.ALERT,
                    title=title,
                    message=message,
                    since=since,
                    channel=NotificationChannel.IN_APP,
                    action_url="/inventory/reports/valuation",
                )
                if created is not None:
                    sent += 1

            if sent:
                db.commit()
            return sent
        except Exception:
            logger.exception("Failed to send inventory valuation mismatch notification")
            db.rollback()
            return 0

    def export_inventory_valuation_csv_response(
        self,
        auth: WebAuthContext,
        db: Session,
    ) -> Response:
        """Export inventory valuation summary rows as CSV."""
        valuation_context, filename_stem = self._inventory_valuation_export_context(
            auth, db
        )

        buffer = StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["Inventory Valuation Summary"])
        writer.writerow(["Fiscal Period", valuation_context["fiscal_period_id"]])
        writer.writerow(["Inventory Value", valuation_context["inventory_total"]])
        writer.writerow(["GL Control Balance", valuation_context["gl_total"]])
        writer.writerow(["Difference", valuation_context["difference"]])
        writer.writerow(
            [
                "Status",
                "Matched" if valuation_context["is_balanced"] else "Review",
            ]
        )
        writer.writerow([])
        writer.writerow(
            [
                "Item Code",
                "Item Name",
                "Warehouse",
                "Quantity On Hand",
                "Current WAC",
                "Inventory Value",
                "GL Value",
                "Difference",
                "Status",
            ]
        )
        for row in valuation_context["valuation_rows"]:
            writer.writerow(
                [
                    row["item_code"],
                    row["item_name"],
                    row["warehouse_name"],
                    row["quantity_on_hand"],
                    row["current_wac"],
                    row["inventory_value"],
                    row["gl_value"],
                    row["difference"],
                    "Matched" if row["is_balanced"] else "Review",
                ]
            )

        return Response(
            content=buffer.getvalue(),
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="{filename_stem}.csv"'
            },
        )

    def export_inventory_valuation_pdf_response(
        self,
        auth: WebAuthContext,
        db: Session,
    ) -> Response:
        """Export inventory valuation summary rows as PDF."""
        from app.services.finance.rpt.pdf import ReportPDFService

        valuation_context, filename_stem = self._inventory_valuation_export_context(
            auth, db
        )
        pdf_bytes = ReportPDFService(db).render(
            "inventory_valuation_reconciliation",
            str(auth.organization_id),
            valuation_context,
        )
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{filename_stem}.pdf"'
            },
        )

    def _inventory_valuation_export_context(
        self,
        auth: WebAuthContext,
        db: Session,
    ) -> tuple[dict[str, Any], str]:
        """Build inventory valuation summary export context."""
        from app.services.finance.rpt.inventory_valuation import (
            inventory_valuation_reconciliation_context,
        )

        org_id = auth.organization_id
        if org_id is None:
            raise HTTPException(status_code=400, detail="Organization is required")
        context = inventory_valuation_reconciliation_context(db, str(org_id))
        filename = "inventory_valuation_summary"
        if context.get("fiscal_period_id"):
            filename = f"{filename}_{context['fiscal_period_id']}"
        return context, filename

    def wac_breakdown_report_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        item_id: str,
        warehouse_id: str,
    ) -> HTMLResponse:
        """WAC breakdown report page."""
        from app.services.finance.rpt.inventory_valuation import wac_breakdown_context

        context = base_context(request, auth, "WAC Breakdown", "reports")
        try:
            context.update(
                wac_breakdown_context(
                    db,
                    str(auth.organization_id),
                    item_id=item_id,
                    warehouse_id=warehouse_id,
                )
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return templates.TemplateResponse(
            request,
            "inventory/report_wac_breakdown.html",
            context,
        )

    def export_wac_breakdown_csv_response(
        self,
        auth: WebAuthContext,
        db: Session,
        item_id: str | None = None,
        warehouse_id: str | None = None,
    ) -> Response:
        """Export WAC breakdown rows as CSV for one item/warehouse or all rows."""
        export_rows, filename_stem = self._wac_breakdown_export_rows(
            auth,
            db,
            item_id=item_id,
            warehouse_id=warehouse_id,
        )

        buffer = StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "Item Code",
                "Item Name",
                "Warehouse",
                "Transaction Date",
                "Transaction Type",
                "Reference",
                "Qty In",
                "Qty Out",
                "Unit Cost",
                "Value In",
                "Value Out",
                "Qty After",
                "WAC After",
                "Value After",
            ]
        )
        for row in export_rows:
            writer.writerow(
                [
                    row["item_code"],
                    row["item_name"],
                    row["warehouse_name"],
                    row["transaction_date"],
                    row["transaction_type"],
                    row["reference"],
                    row["quantity_in"],
                    row["quantity_out"],
                    row["unit_cost"],
                    row["value_in"],
                    row["value_out"],
                    row["quantity_after"],
                    row["wac_after"],
                    row["total_value_after"],
                ]
            )

        return Response(
            content=buffer.getvalue(),
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="{filename_stem}.csv"'
            },
        )

    def export_wac_breakdown_pdf_response(
        self,
        auth: WebAuthContext,
        db: Session,
        item_id: str | None = None,
        warehouse_id: str | None = None,
    ) -> Response:
        """Export WAC breakdown rows as PDF for one item/warehouse or all rows."""
        from app.services.finance.rpt.pdf import ReportPDFService

        if not (item_id and warehouse_id):
            raise HTTPException(
                status_code=400,
                detail=(
                    "WAC breakdown PDF export requires a selected item and warehouse. "
                    "Use CSV for all-items transaction exports."
                ),
            )

        export_rows, filename_stem = self._wac_breakdown_export_rows(
            auth,
            db,
            item_id=item_id,
            warehouse_id=warehouse_id,
        )
        pdf_bytes = ReportPDFService(db).render(
            "wac_breakdown",
            str(auth.organization_id),
            {
                "scope_label": "Selected Item"
                if item_id and warehouse_id
                else "All Items",
                "row_count": len(export_rows),
                "rows": export_rows,
            },
        )
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{filename_stem}.pdf"'
            },
        )

    def _wac_breakdown_export_rows(
        self,
        auth: WebAuthContext,
        db: Session,
        item_id: str | None = None,
        warehouse_id: str | None = None,
    ) -> tuple[list[dict[str, str]], str]:
        """Build WAC breakdown export rows for CSV/PDF output."""
        from app.services.common import coerce_uuid
        from app.services.inventory.valuation_reconciliation import (
            ValuationReconciliationService,
        )
        from app.services.inventory.wac_valuation import WACValuationService

        org_id = auth.organization_id
        if org_id is None:
            raise HTTPException(status_code=400, detail="Organization is required")
        if bool(item_id) != bool(warehouse_id):
            raise HTTPException(
                status_code=400,
                detail="Both item_id and warehouse_id are required for one-item export.",
            )

        selected_item_id = coerce_uuid(item_id) if item_id else None
        selected_warehouse_id = coerce_uuid(warehouse_id) if warehouse_id else None
        reconciliation = ValuationReconciliationService(db)
        result = reconciliation.reconcile(org_id)
        detail_rows = reconciliation.detail_rows(
            org_id, result.fiscal_period_id, limit=500
        )
        if selected_item_id and selected_warehouse_id:
            detail_rows = [
                row
                for row in detail_rows
                if row.item_id == selected_item_id
                and row.warehouse_id == selected_warehouse_id
            ]
            if not detail_rows:
                raise HTTPException(
                    status_code=404,
                    detail="No valuation row found for selected item and warehouse.",
                )

        export_rows: list[dict[str, str]] = []
        wac_service = WACValuationService(db)
        for detail_row in detail_rows:
            for row in wac_service.breakdown_rows(
                org_id,
                detail_row.item_id,
                detail_row.warehouse_id,
                limit=1000,
            ):
                transaction_date = row.transaction_date
                transaction_date_text = (
                    transaction_date.isoformat()
                    if isinstance(transaction_date, date)
                    else ""
                )
                export_rows.append(
                    {
                        "item_code": detail_row.item_code,
                        "item_name": detail_row.item_name,
                        "warehouse_name": detail_row.warehouse_name,
                        "transaction_date": transaction_date_text,
                        "transaction_type": row.transaction_type.replace(
                            "_", " "
                        ).title(),
                        "reference": row.reference or "",
                        "quantity_in": f"{row.quantity_in}",
                        "quantity_out": f"{row.quantity_out}",
                        "unit_cost": f"{row.unit_cost}",
                        "value_in": f"{row.value_in}",
                        "value_out": f"{row.value_out}",
                        "quantity_after": f"{row.quantity_after}",
                        "wac_after": f"{row.wac_after}",
                        "total_value_after": f"{row.total_value_after}",
                    }
                )

        filename = (
            f"wac_breakdown_{detail_rows[0].item_code}_{detail_rows[0].warehouse_id}"
            if selected_item_id and selected_warehouse_id
            else "wac_breakdown_all_items"
        )
        return export_rows, filename

    def fifo_layers_report_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        warehouse: str | None = None,
        item: str | None = None,
        search: str | None = None,
        page: int = 1,
    ) -> HTMLResponse:
        """FIFO layers report page."""
        from decimal import Decimal
        from uuid import UUID as UUID_Type

        from app.models.inventory.inventory_lot import InventoryLot
        from app.models.inventory.inventory_lot_balance import InventoryLotBalance
        from app.models.inventory.item import CostingMethod, Item
        from app.models.inventory.warehouse import Warehouse

        context = base_context(request, auth, "FIFO Layers", "reports")
        org_id = auth.organization_id
        if org_id is None:
            raise HTTPException(status_code=400, detail="Organization is required")

        per_page = 50

        warehouses = list(
            db.scalars(
                select(Warehouse)
                .where(
                    Warehouse.organization_id == org_id,
                    Warehouse.is_active.is_(True),
                )
                .order_by(Warehouse.warehouse_name)
            ).all()
        )
        fifo_items = list(
            db.scalars(
                select(Item)
                .where(
                    Item.organization_id == org_id,
                    Item.is_active.is_(True),
                    Item.costing_method == CostingMethod.FIFO,
                )
                .order_by(Item.item_code)
            ).all()
        )

        layers_stmt = (
            select(InventoryLotBalance, InventoryLot, Item, Warehouse)
            .join(InventoryLot, InventoryLotBalance.lot_id == InventoryLot.lot_id)
            .join(Item, InventoryLot.item_id == Item.item_id)
            .outerjoin(
                Warehouse, InventoryLotBalance.warehouse_id == Warehouse.warehouse_id
            )
            .where(
                InventoryLotBalance.organization_id == org_id,
                InventoryLot.organization_id == org_id,
                Item.organization_id == org_id,
                Item.costing_method == CostingMethod.FIFO,
                InventoryLotBalance.is_active.is_(True),
                InventoryLotBalance.quantity_on_hand > 0,
            )
        )

        selected_warehouse = None
        if warehouse:
            try:
                warehouse_id = UUID_Type(warehouse)
                layers_stmt = layers_stmt.where(
                    InventoryLotBalance.warehouse_id == warehouse_id
                )
                selected_warehouse = db.get(Warehouse, warehouse_id)
                if selected_warehouse and selected_warehouse.organization_id != org_id:
                    selected_warehouse = None
            except ValueError:
                pass

        selected_item = None
        if item:
            try:
                item_id = UUID_Type(item)
                layers_stmt = layers_stmt.where(InventoryLot.item_id == item_id)
                selected_item = db.get(Item, item_id)
                if selected_item and selected_item.organization_id != org_id:
                    selected_item = None
            except ValueError:
                pass

        if search:
            term = f"%{search}%"
            layers_stmt = layers_stmt.where(
                or_(
                    InventoryLot.lot_number.ilike(term),
                    Item.item_code.ilike(term),
                    Item.item_name.ilike(term),
                    Warehouse.warehouse_name.ilike(term),
                    Warehouse.warehouse_code.ilike(term),
                    InventoryLot.allocation_reference.ilike(term),
                )
            )

        filtered_subquery = layers_stmt.subquery()
        total_count = (
            db.scalar(select(func.count()).select_from(filtered_subquery)) or 0
        )
        item_count = (
            db.scalar(select(func.count(func.distinct(filtered_subquery.c.item_id))))
            or 0
        )
        total_quantity = Decimal(
            str(
                db.scalar(
                    select(
                        func.coalesce(func.sum(filtered_subquery.c.quantity_on_hand), 0)
                    )
                )
                or 0
            )
        )
        total_value = Decimal(
            str(
                db.scalar(
                    select(
                        func.coalesce(
                            func.sum(
                                filtered_subquery.c.quantity_on_hand
                                * filtered_subquery.c.unit_cost
                            ),
                            0,
                        )
                    )
                )
                or 0
            )
        )

        total_pages = max(1, ceil(total_count / per_page))
        layer_rows = list(
            db.execute(
                layers_stmt.order_by(
                    InventoryLot.received_date.asc(),
                    Item.item_code.asc(),
                    InventoryLot.lot_number.asc(),
                )
                .offset((page - 1) * per_page)
                .limit(per_page)
            ).all()
        )

        layers = []
        for balance, lot, row_item, row_warehouse in layer_rows:
            quantity_on_hand = balance.quantity_on_hand or Decimal("0")
            unit_cost = lot.unit_cost or Decimal("0")
            layers.append(
                {
                    "lot": lot,
                    "balance": balance,
                    "item": row_item,
                    "warehouse": row_warehouse,
                    "quantity_on_hand": quantity_on_hand,
                    "quantity_available": balance.quantity_available or Decimal("0"),
                    "quantity_allocated": balance.quantity_allocated or Decimal("0"),
                    "unit_cost": unit_cost,
                    "total_value": quantity_on_hand * unit_cost,
                }
            )

        active_filters = build_active_filters(
            params={
                "search": search or "",
                "warehouse": warehouse or "",
                "item": item or "",
            },
            labels={
                "search": "Search",
                "warehouse": "Warehouse",
                "item": "Item",
            },
            options={
                "warehouse": {
                    str(list_warehouse.warehouse_id): list_warehouse.warehouse_name
                    for list_warehouse in warehouses
                },
                "item": {
                    str(
                        list_item.item_id
                    ): f"{list_item.item_code} - {list_item.item_name}"
                    for list_item in fifo_items
                },
            },
        )

        context.update(
            {
                "layers": layers,
                "warehouses": warehouses,
                "items": fifo_items,
                "selected_warehouse": selected_warehouse,
                "selected_item": selected_item,
                "search": search or "",
                "warehouse": warehouse or "",
                "item": item or "",
                "summary": {
                    "total_layers": total_count,
                    "item_count": item_count,
                    "total_quantity": total_quantity,
                    "total_value": total_value,
                },
                "page": page,
                "total_pages": total_pages,
                "total_count": total_count,
                "limit": per_page,
                "active_filters": active_filters,
            }
        )
        return templates.TemplateResponse(
            request,
            "inventory/report_fifo_layers.html",
            context,
        )

    def stock_aging_report_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        warehouse: str | None = None,
        item: str | None = None,
        search: str | None = None,
        page: int = 1,
    ) -> HTMLResponse:
        """Stock aging report page."""
        from decimal import Decimal
        from uuid import UUID as UUID_Type

        from app.models.inventory.inventory_lot import InventoryLot
        from app.models.inventory.inventory_lot_balance import InventoryLotBalance
        from app.models.inventory.item import Item
        from app.models.inventory.warehouse import Warehouse

        context = base_context(request, auth, "Stock Aging", "reports")
        org_id = auth.organization_id
        if org_id is None:
            raise HTTPException(status_code=400, detail="Organization is required")

        per_page = 50
        today = date_type.today()

        warehouses = list(
            db.scalars(
                select(Warehouse)
                .where(
                    Warehouse.organization_id == org_id,
                    Warehouse.is_active.is_(True),
                )
                .order_by(Warehouse.warehouse_name)
            ).all()
        )
        items = list(
            db.scalars(
                select(Item)
                .where(
                    Item.organization_id == org_id,
                    Item.is_active.is_(True),
                    Item.track_inventory.is_(True),
                )
                .order_by(Item.item_code)
            ).all()
        )

        aging_stmt = (
            select(InventoryLotBalance, InventoryLot, Item, Warehouse)
            .join(InventoryLot, InventoryLotBalance.lot_id == InventoryLot.lot_id)
            .join(Item, InventoryLot.item_id == Item.item_id)
            .outerjoin(
                Warehouse, InventoryLotBalance.warehouse_id == Warehouse.warehouse_id
            )
            .where(
                InventoryLotBalance.organization_id == org_id,
                InventoryLot.organization_id == org_id,
                Item.organization_id == org_id,
                InventoryLotBalance.is_active.is_(True),
                InventoryLotBalance.quantity_on_hand > 0,
            )
        )

        selected_warehouse = None
        if warehouse:
            try:
                warehouse_id = UUID_Type(warehouse)
                aging_stmt = aging_stmt.where(
                    InventoryLotBalance.warehouse_id == warehouse_id
                )
                selected_warehouse = db.get(Warehouse, warehouse_id)
                if selected_warehouse and selected_warehouse.organization_id != org_id:
                    selected_warehouse = None
            except ValueError:
                pass

        selected_item = None
        if item:
            try:
                item_id = UUID_Type(item)
                aging_stmt = aging_stmt.where(InventoryLot.item_id == item_id)
                selected_item = db.get(Item, item_id)
                if selected_item and selected_item.organization_id != org_id:
                    selected_item = None
            except ValueError:
                pass

        if search:
            term = f"%{search}%"
            aging_stmt = aging_stmt.where(
                or_(
                    InventoryLot.lot_number.ilike(term),
                    Item.item_code.ilike(term),
                    Item.item_name.ilike(term),
                    Warehouse.warehouse_name.ilike(term),
                    Warehouse.warehouse_code.ilike(term),
                    InventoryLot.allocation_reference.ilike(term),
                )
            )

        total_pages = 1
        row_data = list(
            db.execute(
                aging_stmt.order_by(
                    InventoryLot.received_date.asc(),
                    Item.item_code.asc(),
                    InventoryLot.lot_number.asc(),
                )
            ).all()
        )

        rows = []
        total_quantity = Decimal("0")
        total_value = Decimal("0")
        bucket_counts = {
            "0_30": 0,
            "31_60": 0,
            "61_90": 0,
            "90_plus": 0,
        }
        bucket_values = {
            "0_30": Decimal("0"),
            "31_60": Decimal("0"),
            "61_90": Decimal("0"),
            "90_plus": Decimal("0"),
        }

        for balance, lot, row_item, row_warehouse in row_data:
            quantity_on_hand = balance.quantity_on_hand or Decimal("0")
            unit_cost = lot.unit_cost or Decimal("0")
            age_days = max((today - lot.received_date).days, 0)
            total_row_value = quantity_on_hand * unit_cost

            if age_days <= 30:
                bucket = "0_30"
            elif age_days <= 60:
                bucket = "31_60"
            elif age_days <= 90:
                bucket = "61_90"
            else:
                bucket = "90_plus"

            bucket_counts[bucket] += 1
            bucket_values[bucket] += total_row_value
            total_quantity += quantity_on_hand
            total_value += total_row_value

            rows.append(
                {
                    "lot": lot,
                    "balance": balance,
                    "item": row_item,
                    "warehouse": row_warehouse,
                    "quantity_on_hand": quantity_on_hand,
                    "unit_cost": unit_cost,
                    "total_value": total_row_value,
                    "age_days": age_days,
                    "age_bucket": bucket,
                }
            )

        total_count = len(rows)
        total_pages = max(1, ceil(total_count / per_page))
        start_idx = max((page - 1) * per_page, 0)
        end_idx = start_idx + per_page
        paged_rows = rows[start_idx:end_idx]

        active_filters = build_active_filters(
            params={
                "search": search or "",
                "warehouse": warehouse or "",
                "item": item or "",
            },
            labels={
                "search": "Search",
                "warehouse": "Warehouse",
                "item": "Item",
            },
            options={
                "warehouse": {
                    str(list_warehouse.warehouse_id): list_warehouse.warehouse_name
                    for list_warehouse in warehouses
                },
                "item": {
                    str(
                        list_item.item_id
                    ): f"{list_item.item_code} - {list_item.item_name}"
                    for list_item in items
                },
            },
        )

        context.update(
            {
                "aging_rows": paged_rows,
                "warehouses": warehouses,
                "items": items,
                "selected_warehouse": selected_warehouse,
                "selected_item": selected_item,
                "search": search or "",
                "warehouse": warehouse or "",
                "item": item or "",
                "summary": {
                    "total_lots": total_count,
                    "total_quantity": total_quantity,
                    "total_value": total_value,
                    "bucket_counts": bucket_counts,
                    "bucket_values": bucket_values,
                },
                "page": page,
                "total_pages": total_pages,
                "total_count": total_count,
                "limit": per_page,
                "active_filters": active_filters,
            }
        )
        return templates.TemplateResponse(
            request,
            "inventory/report_stock_aging.html",
            context,
        )

    def stock_movement_report_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        warehouse: str | None = None,
        item: str | None = None,
        transaction_type: str | None = None,
        search: str | None = None,
        page: int = 1,
    ) -> HTMLResponse:
        """Stock movement report page."""
        from decimal import Decimal
        from uuid import UUID as UUID_Type

        from sqlalchemy.orm import aliased

        from app.models.inventory.inventory_transaction import (
            InventoryTransaction,
            TransactionType,
        )
        from app.models.inventory.item import Item
        from app.models.inventory.warehouse import Warehouse

        context = base_context(request, auth, "Stock Movement", "reports")
        org_id = auth.organization_id
        if org_id is None:
            raise HTTPException(status_code=400, detail="Organization is required")

        per_page = 50

        warehouses = list(
            db.scalars(
                select(Warehouse)
                .where(
                    Warehouse.organization_id == org_id,
                    Warehouse.is_active.is_(True),
                )
                .order_by(Warehouse.warehouse_name)
            ).all()
        )
        items = list(
            db.scalars(
                select(Item)
                .where(
                    Item.organization_id == org_id,
                    Item.is_active.is_(True),
                    Item.track_inventory.is_(True),
                )
                .order_by(Item.item_code)
            ).all()
        )

        to_warehouse = aliased(Warehouse)
        movement_stmt = (
            select(InventoryTransaction, Item, Warehouse, to_warehouse)
            .join(Item, InventoryTransaction.item_id == Item.item_id)
            .join(
                Warehouse, InventoryTransaction.warehouse_id == Warehouse.warehouse_id
            )
            .outerjoin(
                to_warehouse,
                InventoryTransaction.to_warehouse_id == to_warehouse.warehouse_id,
            )
            .where(InventoryTransaction.organization_id == org_id)
        )

        selected_warehouse = None
        if warehouse:
            try:
                warehouse_id = UUID_Type(warehouse)
                movement_stmt = movement_stmt.where(
                    InventoryTransaction.warehouse_id == warehouse_id
                )
                selected_warehouse = db.get(Warehouse, warehouse_id)
                if selected_warehouse and selected_warehouse.organization_id != org_id:
                    selected_warehouse = None
            except ValueError:
                pass

        selected_item = None
        if item:
            try:
                item_id = UUID_Type(item)
                movement_stmt = movement_stmt.where(
                    InventoryTransaction.item_id == item_id
                )
                selected_item = db.get(Item, item_id)
                if selected_item and selected_item.organization_id != org_id:
                    selected_item = None
            except ValueError:
                pass

        selected_type = None
        if transaction_type:
            parsed_type = next(
                (
                    txn_type
                    for txn_type in TransactionType
                    if txn_type.value == transaction_type
                ),
                None,
            )
            if parsed_type is not None:
                movement_stmt = movement_stmt.where(
                    InventoryTransaction.transaction_type == parsed_type
                )
                selected_type = parsed_type.value

        if search:
            term = f"%{search}%"
            movement_stmt = movement_stmt.where(
                or_(
                    InventoryTransaction.reference.ilike(term),
                    Item.item_code.ilike(term),
                    Item.item_name.ilike(term),
                    Warehouse.warehouse_name.ilike(term),
                    Warehouse.warehouse_code.ilike(term),
                )
            )

        row_data = list(
            db.execute(
                movement_stmt.order_by(InventoryTransaction.transaction_date.desc())
            ).all()
        )

        movement_rows = []
        summary_counts = {
            "RECEIPT": 0,
            "ISSUE": 0,
            "TRANSFER": 0,
            "ADJUSTMENT": 0,
        }
        total_quantity = Decimal("0")
        total_value = Decimal("0")

        for txn, row_item, row_warehouse, to_warehouse_row in row_data:
            movement_type = txn.transaction_type.value
            quantity = txn.quantity or Decimal("0")
            total_cost = txn.total_cost or Decimal("0")

            if movement_type in summary_counts:
                summary_counts[movement_type] += 1
            total_quantity += quantity
            total_value += total_cost

            movement_rows.append(
                {
                    "transaction": txn,
                    "item": row_item,
                    "warehouse": row_warehouse,
                    "to_warehouse_name": to_warehouse_row.warehouse_name
                    if to_warehouse_row is not None
                    else None,
                    "quantity": quantity,
                    "unit_cost": txn.unit_cost or Decimal("0"),
                    "total_cost": total_cost,
                }
            )

        total_count = len(movement_rows)
        total_pages = max(1, ceil(total_count / per_page))
        start_idx = max((page - 1) * per_page, 0)
        end_idx = start_idx + per_page
        paged_rows = movement_rows[start_idx:end_idx]

        active_filters = build_active_filters(
            params={
                "search": search or "",
                "warehouse": warehouse or "",
                "item": item or "",
                "transaction_type": selected_type or "",
            },
            labels={
                "search": "Search",
                "warehouse": "Warehouse",
                "item": "Item",
                "transaction_type": "Type",
            },
            options={
                "warehouse": {
                    str(list_warehouse.warehouse_id): list_warehouse.warehouse_name
                    for list_warehouse in warehouses
                },
                "item": {
                    str(
                        list_item.item_id
                    ): f"{list_item.item_code} - {list_item.item_name}"
                    for list_item in items
                },
                "transaction_type": {
                    txn_type.value: txn_type.value.replace("_", " ").title()
                    for txn_type in TransactionType
                },
            },
        )

        context.update(
            {
                "movement_rows": paged_rows,
                "warehouses": warehouses,
                "items": items,
                "transaction_types": [txn_type.value for txn_type in TransactionType],
                "selected_warehouse": selected_warehouse,
                "selected_item": selected_item,
                "transaction_type": selected_type or "",
                "search": search or "",
                "warehouse": warehouse or "",
                "item": item or "",
                "summary": {
                    "total_rows": total_count,
                    "total_quantity": total_quantity,
                    "total_value": total_value,
                    "counts": summary_counts,
                },
                "page": page,
                "total_pages": total_pages,
                "total_count": total_count,
                "limit": per_page,
                "active_filters": active_filters,
            }
        )
        return templates.TemplateResponse(
            request,
            "inventory/report_stock_movement.html",
            context,
        )

    def yearly_stock_movement_report_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        year: str | None = None,
        month: str | None = None,
        warehouse: str | None = None,
        item: str | None = None,
        search: str | None = None,
        page: int = 1,
    ) -> HTMLResponse:
        """Yearly stock movement summary report page."""
        from decimal import Decimal
        from uuid import UUID as UUID_Type

        from app.models.inventory.inventory_transaction import (
            InventoryTransaction,
            TransactionType,
        )
        from app.models.inventory.item import Item
        from app.models.inventory.warehouse import Warehouse

        context = base_context(request, auth, "Yearly Stock Movement", "reports")
        org_id = auth.organization_id
        if org_id is None:
            raise HTTPException(status_code=400, detail="Organization is required")

        per_page = 50
        selected_year: int | None = None
        if year:
            try:
                parsed_year = int(year)
                if 1900 <= parsed_year <= 2100:
                    selected_year = parsed_year
            except ValueError:
                selected_year = None

        selected_month: int | None = None
        if month:
            try:
                parsed_month = int(month)
                if 1 <= parsed_month <= 12:
                    selected_month = parsed_month
            except ValueError:
                selected_month = None

        warehouses = list(
            db.scalars(
                select(Warehouse)
                .where(
                    Warehouse.organization_id == org_id,
                    Warehouse.is_active.is_(True),
                )
                .order_by(Warehouse.warehouse_name)
            ).all()
        )
        items = list(
            db.scalars(
                select(Item)
                .where(
                    Item.organization_id == org_id,
                    Item.is_active.is_(True),
                    Item.track_inventory.is_(True),
                )
                .order_by(Item.item_code)
            ).all()
        )

        movement_stmt = (
            select(InventoryTransaction, Item, Warehouse)
            .join(Item, InventoryTransaction.item_id == Item.item_id)
            .join(
                Warehouse, InventoryTransaction.warehouse_id == Warehouse.warehouse_id
            )
            .where(InventoryTransaction.organization_id == org_id)
        )

        selected_warehouse = None
        if warehouse:
            try:
                warehouse_id = UUID_Type(warehouse)
                movement_stmt = movement_stmt.where(
                    InventoryTransaction.warehouse_id == warehouse_id
                )
                selected_warehouse = db.get(Warehouse, warehouse_id)
                if selected_warehouse and selected_warehouse.organization_id != org_id:
                    selected_warehouse = None
            except ValueError:
                pass

        selected_item = None
        if item:
            try:
                item_id = UUID_Type(item)
                movement_stmt = movement_stmt.where(
                    InventoryTransaction.item_id == item_id
                )
                selected_item = db.get(Item, item_id)
                if selected_item and selected_item.organization_id != org_id:
                    selected_item = None
            except ValueError:
                pass

        if search:
            term = f"%{search}%"
            movement_stmt = movement_stmt.where(
                or_(
                    InventoryTransaction.reference.ilike(term),
                    Item.item_code.ilike(term),
                    Item.item_name.ilike(term),
                    Warehouse.warehouse_name.ilike(term),
                    Warehouse.warehouse_code.ilike(term),
                )
            )

        row_data = list(
            db.execute(
                movement_stmt.order_by(
                    Item.item_code,
                    Warehouse.warehouse_name,
                    InventoryTransaction.transaction_date,
                    InventoryTransaction.created_at,
                )
            ).all()
        )

        purchase_source_tokens = ("PURCHASE", "GOODS_RECEIPT", "SUPPLIER_INVOICE")
        issued_types = {
            TransactionType.ISSUE.value,
            TransactionType.SALE.value,
            TransactionType.SCRAP.value,
        }

        def _decimal(value: object) -> Decimal:
            if value is None:
                return Decimal("0")
            return Decimal(str(value))

        def _transaction_value(transaction: object, attr: str) -> object:
            value = getattr(transaction, attr, None)
            return getattr(value, "value", value)

        def _net_delta(transaction: object) -> Decimal:
            before = getattr(transaction, "quantity_before", None)
            after = getattr(transaction, "quantity_after", None)
            if before is not None and after is not None:
                return _decimal(after) - _decimal(before)

            quantity = _decimal(getattr(transaction, "quantity", None))
            movement_type = _transaction_value(transaction, "transaction_type")
            if movement_type in issued_types:
                return -abs(quantity)
            return quantity

        def _is_purchase_receipt(transaction: object) -> bool:
            movement_type = _transaction_value(transaction, "transaction_type")
            if movement_type != TransactionType.RECEIPT.value:
                return False
            source_text = " ".join(
                str(part or "")
                for part in (
                    getattr(transaction, "source_document_type", None),
                    getattr(transaction, "reference", None),
                )
            ).upper()
            return any(token in source_text for token in purchase_source_tokens)

        balances: dict[tuple[object, object], Decimal] = {}
        yearly_rows_by_key: dict[tuple[object, object, int], dict[str, object]] = {}
        available_years: set[int] = set()
        month_options = [
            {"value": "1", "label": "January"},
            {"value": "2", "label": "February"},
            {"value": "3", "label": "March"},
            {"value": "4", "label": "April"},
            {"value": "5", "label": "May"},
            {"value": "6", "label": "June"},
            {"value": "7", "label": "July"},
            {"value": "8", "label": "August"},
            {"value": "9", "label": "September"},
            {"value": "10", "label": "October"},
            {"value": "11", "label": "November"},
            {"value": "12", "label": "December"},
        ]

        for txn, row_item, row_warehouse in row_data:
            transaction_date = getattr(txn, "transaction_date", None)
            if transaction_date is None:
                continue

            txn_year = int(transaction_date.year)
            txn_month = int(transaction_date.month)
            available_years.add(txn_year)
            balance_key = (row_item.item_id, row_warehouse.warehouse_id)
            current_balance = balances.get(balance_key, Decimal("0"))
            delta = _net_delta(txn)
            should_show_period = (
                selected_year is None or txn_year == selected_year
            ) and (selected_month is None or txn_month == selected_month)

            if should_show_period:
                row_key = (row_item.item_id, row_warehouse.warehouse_id, txn_year)
                yearly_row = yearly_rows_by_key.get(row_key)
                if yearly_row is None:
                    yearly_row = {
                        "year": txn_year,
                        "item": row_item,
                        "warehouse": row_warehouse,
                        "opening_qty": current_balance,
                        "quantity_in": Decimal("0"),
                        "purchase_qty": Decimal("0"),
                        "issued_qty": Decimal("0"),
                        "quantity_out": Decimal("0"),
                        "closing_qty": current_balance,
                    }
                    yearly_rows_by_key[row_key] = yearly_row

                if delta > 0:
                    yearly_row["quantity_in"] = (
                        cast(Decimal, yearly_row["quantity_in"]) + delta
                    )
                    if _is_purchase_receipt(txn):
                        yearly_row["purchase_qty"] = (
                            cast(Decimal, yearly_row["purchase_qty"]) + delta
                        )
                elif delta < 0:
                    yearly_row["quantity_out"] = cast(
                        Decimal, yearly_row["quantity_out"]
                    ) + abs(delta)

                if _transaction_value(txn, "transaction_type") in issued_types:
                    yearly_row["issued_qty"] = cast(
                        Decimal, yearly_row["issued_qty"]
                    ) + abs(delta)

            current_balance += delta
            balances[balance_key] = current_balance

            if should_show_period and yearly_row is not None:
                yearly_row["closing_qty"] = current_balance

        yearly_rows = sorted(
            yearly_rows_by_key.values(),
            key=lambda row: (
                -cast(int, row["year"]),
                getattr(row["item"], "item_code", ""),
                getattr(row["warehouse"], "warehouse_name", ""),
            ),
        )

        total_count = len(yearly_rows)
        total_pages = max(1, ceil(total_count / per_page))
        start_idx = max((page - 1) * per_page, 0)
        end_idx = start_idx + per_page
        paged_rows = yearly_rows[start_idx:end_idx]

        summary = {
            "total_rows": total_count,
            "opening_qty": sum(
                (cast(Decimal, row["opening_qty"]) for row in yearly_rows), Decimal("0")
            ),
            "quantity_in": sum(
                (cast(Decimal, row["quantity_in"]) for row in yearly_rows), Decimal("0")
            ),
            "purchase_qty": sum(
                (cast(Decimal, row["purchase_qty"]) for row in yearly_rows),
                Decimal("0"),
            ),
            "issued_qty": sum(
                (cast(Decimal, row["issued_qty"]) for row in yearly_rows), Decimal("0")
            ),
            "quantity_out": sum(
                (cast(Decimal, row["quantity_out"]) for row in yearly_rows),
                Decimal("0"),
            ),
            "closing_qty": sum(
                (cast(Decimal, row["closing_qty"]) for row in yearly_rows), Decimal("0")
            ),
        }

        year_options = sorted(available_years, reverse=True)
        active_filters = build_active_filters(
            params={
                "search": search or "",
                "year": str(selected_year) if selected_year else "",
                "month": str(selected_month) if selected_month else "",
                "warehouse": warehouse or "",
                "item": item or "",
            },
            labels={
                "search": "Search",
                "year": "Year",
                "month": "Month",
                "warehouse": "Warehouse",
                "item": "Item",
            },
            options={
                "year": {
                    str(option_year): str(option_year) for option_year in year_options
                },
                "month": {option["value"]: option["label"] for option in month_options},
                "warehouse": {
                    str(list_warehouse.warehouse_id): list_warehouse.warehouse_name
                    for list_warehouse in warehouses
                },
                "item": {
                    str(
                        list_item.item_id
                    ): f"{list_item.item_code} - {list_item.item_name}"
                    for list_item in items
                },
            },
        )

        context.update(
            {
                "yearly_rows": paged_rows,
                "warehouses": warehouses,
                "items": items,
                "year_options": year_options,
                "month_options": month_options,
                "selected_year": selected_year,
                "selected_month": selected_month,
                "selected_warehouse": selected_warehouse,
                "selected_item": selected_item,
                "year": str(selected_year) if selected_year else "",
                "month": str(selected_month) if selected_month else "",
                "warehouse": warehouse or "",
                "item": item or "",
                "search": search or "",
                "summary": summary,
                "page": page,
                "total_pages": total_pages,
                "total_count": total_count,
                "limit": per_page,
                "active_filters": active_filters,
            }
        )
        return templates.TemplateResponse(
            request,
            "inventory/report_yearly_stock_movement.html",
            context,
        )

    def export_yearly_stock_movement_csv_response(
        self,
        auth: WebAuthContext,
        db: Session,
        year: str | None = None,
        month: str | None = None,
        warehouse: str | None = None,
        item: str | None = None,
        search: str | None = None,
    ) -> Response:
        """Export yearly stock movement summary rows as CSV."""
        export_context, filename_stem = self._yearly_stock_movement_export_context(
            auth,
            db,
            year=year,
            month=month,
            warehouse=warehouse,
            item=item,
            search=search,
        )

        buffer = StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "Year",
                "Item Code",
                "Item Name",
                "Warehouse Code",
                "Warehouse Name",
                "Opening Quantity",
                "Quantity In",
                "Purchases",
                "Issued",
                "Quantity Out",
                "Closing Quantity",
            ]
        )
        for row in export_context["yearly_rows"]:
            row_item = row["item"]
            row_warehouse = row["warehouse"]
            writer.writerow(
                [
                    row["year"],
                    row_item.item_code,
                    row_item.item_name,
                    row_warehouse.warehouse_code,
                    row_warehouse.warehouse_name,
                    row["opening_qty"],
                    row["quantity_in"],
                    row["purchase_qty"],
                    row["issued_qty"],
                    row["quantity_out"],
                    row["closing_qty"],
                ]
            )

        return Response(
            content=buffer.getvalue(),
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="{filename_stem}.csv"'
            },
        )

    def export_yearly_stock_movement_pdf_response(
        self,
        auth: WebAuthContext,
        db: Session,
        year: str | None = None,
        month: str | None = None,
        warehouse: str | None = None,
        item: str | None = None,
        search: str | None = None,
    ) -> Response:
        """Export yearly stock movement summary rows as PDF."""
        from app.services.finance.rpt.pdf import ReportPDFService

        export_context, filename_stem = self._yearly_stock_movement_export_context(
            auth,
            db,
            year=year,
            month=month,
            warehouse=warehouse,
            item=item,
            search=search,
        )
        pdf_bytes = ReportPDFService(db).render(
            "yearly_stock_movement",
            str(auth.organization_id),
            export_context,
        )
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{filename_stem}.pdf"'
            },
        )

    def _yearly_stock_movement_export_context(
        self,
        auth: WebAuthContext,
        db: Session,
        year: str | None = None,
        month: str | None = None,
        warehouse: str | None = None,
        item: str | None = None,
        search: str | None = None,
    ) -> tuple[dict[str, Any], str]:
        """Build yearly stock movement data for exports."""
        from decimal import Decimal
        from uuid import UUID as UUID_Type

        from app.models.inventory.inventory_transaction import (
            InventoryTransaction,
            TransactionType,
        )
        from app.models.inventory.item import Item
        from app.models.inventory.warehouse import Warehouse

        org_id = auth.organization_id
        if org_id is None:
            raise HTTPException(status_code=400, detail="Organization is required")

        selected_year: int | None = None
        if year:
            try:
                parsed_year = int(year)
                if 1900 <= parsed_year <= 2100:
                    selected_year = parsed_year
            except ValueError:
                selected_year = None

        selected_month: int | None = None
        if month:
            try:
                parsed_month = int(month)
                if 1 <= parsed_month <= 12:
                    selected_month = parsed_month
            except ValueError:
                selected_month = None

        month_options = [
            {"value": "1", "label": "January"},
            {"value": "2", "label": "February"},
            {"value": "3", "label": "March"},
            {"value": "4", "label": "April"},
            {"value": "5", "label": "May"},
            {"value": "6", "label": "June"},
            {"value": "7", "label": "July"},
            {"value": "8", "label": "August"},
            {"value": "9", "label": "September"},
            {"value": "10", "label": "October"},
            {"value": "11", "label": "November"},
            {"value": "12", "label": "December"},
        ]

        movement_stmt = (
            select(InventoryTransaction, Item, Warehouse)
            .join(Item, InventoryTransaction.item_id == Item.item_id)
            .join(
                Warehouse, InventoryTransaction.warehouse_id == Warehouse.warehouse_id
            )
            .where(InventoryTransaction.organization_id == org_id)
        )

        selected_warehouse = None
        if warehouse:
            try:
                warehouse_id = UUID_Type(warehouse)
                movement_stmt = movement_stmt.where(
                    InventoryTransaction.warehouse_id == warehouse_id
                )
                selected_warehouse = db.get(Warehouse, warehouse_id)
                if selected_warehouse and selected_warehouse.organization_id != org_id:
                    selected_warehouse = None
            except ValueError:
                pass

        selected_item = None
        if item:
            try:
                item_id = UUID_Type(item)
                movement_stmt = movement_stmt.where(
                    InventoryTransaction.item_id == item_id
                )
                selected_item = db.get(Item, item_id)
                if selected_item and selected_item.organization_id != org_id:
                    selected_item = None
            except ValueError:
                pass

        if search:
            term = f"%{search}%"
            movement_stmt = movement_stmt.where(
                or_(
                    InventoryTransaction.reference.ilike(term),
                    Item.item_code.ilike(term),
                    Item.item_name.ilike(term),
                    Warehouse.warehouse_name.ilike(term),
                    Warehouse.warehouse_code.ilike(term),
                )
            )

        row_data = list(
            db.execute(
                movement_stmt.order_by(
                    Item.item_code,
                    Warehouse.warehouse_name,
                    InventoryTransaction.transaction_date,
                    InventoryTransaction.created_at,
                )
            ).all()
        )

        purchase_source_tokens = ("PURCHASE", "GOODS_RECEIPT", "SUPPLIER_INVOICE")
        issued_types = {
            TransactionType.ISSUE.value,
            TransactionType.SALE.value,
            TransactionType.SCRAP.value,
        }

        def _decimal(value: object) -> Decimal:
            if value is None:
                return Decimal("0")
            return Decimal(str(value))

        def _transaction_value(transaction: object, attr: str) -> object:
            value = getattr(transaction, attr, None)
            return getattr(value, "value", value)

        def _net_delta(transaction: object) -> Decimal:
            before = getattr(transaction, "quantity_before", None)
            after = getattr(transaction, "quantity_after", None)
            if before is not None and after is not None:
                return _decimal(after) - _decimal(before)

            quantity = _decimal(getattr(transaction, "quantity", None))
            movement_type = _transaction_value(transaction, "transaction_type")
            if movement_type in issued_types:
                return -abs(quantity)
            return quantity

        def _is_purchase_receipt(transaction: object) -> bool:
            movement_type = _transaction_value(transaction, "transaction_type")
            if movement_type != TransactionType.RECEIPT.value:
                return False
            source_text = " ".join(
                str(part or "")
                for part in (
                    getattr(transaction, "source_document_type", None),
                    getattr(transaction, "reference", None),
                )
            ).upper()
            return any(token in source_text for token in purchase_source_tokens)

        balances: dict[tuple[object, object], Decimal] = {}
        yearly_rows_by_key: dict[tuple[object, object, int], dict[str, object]] = {}
        available_years: set[int] = set()

        for txn, row_item, row_warehouse in row_data:
            transaction_date = getattr(txn, "transaction_date", None)
            if transaction_date is None:
                continue

            txn_year = int(transaction_date.year)
            txn_month = int(transaction_date.month)
            available_years.add(txn_year)
            balance_key = (row_item.item_id, row_warehouse.warehouse_id)
            current_balance = balances.get(balance_key, Decimal("0"))
            delta = _net_delta(txn)
            should_show_period = (
                selected_year is None or txn_year == selected_year
            ) and (selected_month is None or txn_month == selected_month)

            if should_show_period:
                row_key = (row_item.item_id, row_warehouse.warehouse_id, txn_year)
                yearly_row = yearly_rows_by_key.get(row_key)
                if yearly_row is None:
                    yearly_row = {
                        "year": txn_year,
                        "item": row_item,
                        "warehouse": row_warehouse,
                        "opening_qty": current_balance,
                        "quantity_in": Decimal("0"),
                        "purchase_qty": Decimal("0"),
                        "issued_qty": Decimal("0"),
                        "quantity_out": Decimal("0"),
                        "closing_qty": current_balance,
                    }
                    yearly_rows_by_key[row_key] = yearly_row

                if delta > 0:
                    yearly_row["quantity_in"] = (
                        cast(Decimal, yearly_row["quantity_in"]) + delta
                    )
                    if _is_purchase_receipt(txn):
                        yearly_row["purchase_qty"] = (
                            cast(Decimal, yearly_row["purchase_qty"]) + delta
                        )
                elif delta < 0:
                    yearly_row["quantity_out"] = cast(
                        Decimal, yearly_row["quantity_out"]
                    ) + abs(delta)

                if _transaction_value(txn, "transaction_type") in issued_types:
                    yearly_row["issued_qty"] = cast(
                        Decimal, yearly_row["issued_qty"]
                    ) + abs(delta)

            current_balance += delta
            balances[balance_key] = current_balance

            if should_show_period and yearly_row is not None:
                yearly_row["closing_qty"] = current_balance

        yearly_rows = sorted(
            yearly_rows_by_key.values(),
            key=lambda row: (
                -cast(int, row["year"]),
                getattr(row["item"], "item_code", ""),
                getattr(row["warehouse"], "warehouse_name", ""),
            ),
        )
        summary = {
            "total_rows": len(yearly_rows),
            "opening_qty": sum(
                (cast(Decimal, row["opening_qty"]) for row in yearly_rows), Decimal("0")
            ),
            "quantity_in": sum(
                (cast(Decimal, row["quantity_in"]) for row in yearly_rows), Decimal("0")
            ),
            "purchase_qty": sum(
                (cast(Decimal, row["purchase_qty"]) for row in yearly_rows),
                Decimal("0"),
            ),
            "issued_qty": sum(
                (cast(Decimal, row["issued_qty"]) for row in yearly_rows), Decimal("0")
            ),
            "quantity_out": sum(
                (cast(Decimal, row["quantity_out"]) for row in yearly_rows),
                Decimal("0"),
            ),
            "closing_qty": sum(
                (cast(Decimal, row["closing_qty"]) for row in yearly_rows), Decimal("0")
            ),
        }

        selected_month_label = next(
            (
                option["label"]
                for option in month_options
                if option["value"] == str(selected_month)
            ),
            "",
        )
        scope_parts = []
        filename_parts = ["yearly_stock_movement"]
        if selected_year:
            scope_parts.append(f"Year {selected_year}")
            filename_parts.append(str(selected_year))
        if selected_month_label:
            scope_parts.append(selected_month_label)
            filename_parts.append(str(selected_month).zfill(2))
        if selected_warehouse:
            scope_parts.append(f"Warehouse {selected_warehouse.warehouse_name}")
        if selected_item:
            scope_parts.append(f"Item {selected_item.item_code}")
        if search:
            scope_parts.append(f'Search "{search}"')

        return (
            {
                "yearly_rows": yearly_rows,
                "summary": summary,
                "row_count": len(yearly_rows),
                "scope_label": ", ".join(scope_parts) if scope_parts else "All rows",
                "year": str(selected_year) if selected_year else "",
                "month": str(selected_month) if selected_month else "",
                "selected_month_label": selected_month_label,
                "selected_warehouse": selected_warehouse,
                "selected_item": selected_item,
                "search": search or "",
            },
            "_".join(filename_parts),
        )

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
                count.warehouse = wh

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

    def export_count_csv_response(
        self,
        count_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> Response | RedirectResponse:
        """Export a posted stock count as CSV."""
        export_context = self._stock_count_export_context(count_id, auth, db)
        if isinstance(export_context, RedirectResponse):
            return export_context

        count = export_context["count"]
        rows = export_context["rows"]

        buffer = StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "Count Number",
                "Count Date",
                "Status",
                "Item Code",
                "Item Name",
                "Warehouse",
                "System Quantity",
                "Counted Quantity",
                "Final Quantity",
                "Variance Quantity",
                "Variance Value",
                "Reason Code",
                "Notes",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    count.count_number,
                    count.count_date.isoformat() if count.count_date else "",
                    count.status.value,
                    row["item_code"],
                    row["item_name"],
                    row["warehouse_name"],
                    row["system_quantity"],
                    row["counted_quantity"],
                    row["final_quantity"],
                    row["variance_quantity"],
                    row["variance_value"],
                    row["reason_code"],
                    row["notes"],
                ]
            )

        filename = f"stock_count_{count.count_number}.csv"
        return Response(
            content=buffer.getvalue(),
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )

    def export_count_pdf_response(
        self,
        count_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> Response | RedirectResponse:
        """Export a posted stock count as PDF."""
        from app.services.finance.rpt.pdf import ReportPDFService

        export_context = self._stock_count_export_context(count_id, auth, db)
        if isinstance(export_context, RedirectResponse):
            return export_context

        count = export_context["count"]
        filename = f"stock_count_{count.count_number}.pdf"
        pdf_bytes = ReportPDFService(db).render(
            "stock_count",
            str(auth.organization_id),
            export_context,
        )
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )

    def _stock_count_export_context(
        self,
        count_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> dict[str, Any] | RedirectResponse:
        """Build the shared export context for posted stock count downloads."""
        from decimal import Decimal
        from uuid import UUID as UUID_Type

        from app.models.inventory.inventory_count import CountStatus, InventoryCount
        from app.models.inventory.item import Item
        from app.models.inventory.warehouse import Warehouse
        from app.services.inventory.count import InventoryCountService

        try:
            cnt_id = UUID_Type(count_id)
        except ValueError:
            return RedirectResponse("/inventory/counts", status_code=302)

        count = db.get(InventoryCount, cnt_id)
        if not count or count.organization_id != auth.organization_id:
            return RedirectResponse("/inventory/counts", status_code=302)
        if count.status != CountStatus.POSTED:
            return RedirectResponse(f"/inventory/counts/{count_id}", status_code=303)

        lines = InventoryCountService.list_lines(db, count_id, limit=5000)
        item_ids = {line.item_id for line in lines}
        wh_ids = {line.warehouse_id for line in lines}

        items_map: dict[UUID_Type, Item] = {}
        if item_ids:
            items_map = {
                item.item_id: item
                for item in db.scalars(
                    select(Item).where(Item.item_id.in_(item_ids))
                ).all()
            }

        warehouses_map: dict[UUID_Type, Warehouse] = {}
        if wh_ids:
            warehouses_map = {
                warehouse.warehouse_id: warehouse
                for warehouse in db.scalars(
                    select(Warehouse).where(Warehouse.warehouse_id.in_(wh_ids))
                ).all()
            }

        rows = []
        total_system_quantity = Decimal("0")
        total_counted_quantity = Decimal("0")
        total_variance_quantity = Decimal("0")
        total_variance_value = Decimal("0")
        for line in lines:
            item = items_map.get(line.item_id)
            warehouse = warehouses_map.get(line.warehouse_id)
            system_quantity = line.system_quantity or Decimal("0")
            counted_quantity = line.counted_quantity
            final_quantity = line.final_quantity
            variance_quantity = line.variance_quantity
            variance_value = line.variance_value

            total_system_quantity += system_quantity
            if counted_quantity is not None:
                total_counted_quantity += counted_quantity
            if variance_quantity is not None:
                total_variance_quantity += variance_quantity
            if variance_value is not None:
                total_variance_value += variance_value

            rows.append(
                {
                    "item_code": item.item_code if item else "",
                    "item_name": item.item_name if item else "",
                    "warehouse_name": warehouse.warehouse_name if warehouse else "",
                    "system_quantity": f"{system_quantity}",
                    "counted_quantity": f"{counted_quantity or 0}"
                    if counted_quantity is not None
                    else "",
                    "final_quantity": f"{final_quantity or 0}"
                    if final_quantity is not None
                    else "",
                    "variance_quantity": f"{variance_quantity or 0}"
                    if variance_quantity is not None
                    else "",
                    "variance_value": f"{variance_value or 0}"
                    if variance_value is not None
                    else "",
                    "reason_code": line.reason_code or "",
                    "notes": line.notes or "",
                }
            )

        return {
            "count": count,
            "rows": rows,
            "row_count": len(rows),
            "summary": {
                "total_system_quantity": total_system_quantity,
                "total_counted_quantity": total_counted_quantity,
                "total_variance_quantity": total_variance_quantity,
                "total_variance_value": total_variance_value,
            },
        }

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
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
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
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
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
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
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
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")

        form = await request.form()
        counted_qty_str = _safe_form_text(
            form.get("counted_quantity") or form.get(f"counted_quantity_{line_id}")
        )
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

    async def bulk_record_count_lines_response(
        self,
        request: Request,
        count_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Bulk record counted quantities for selected count lines."""
        from decimal import Decimal, InvalidOperation
        from uuid import UUID as UUID_Type

        from app.services.common import coerce_uuid
        from app.services.inventory.count import (
            BulkCountLineInput,
            InventoryCountService,
        )

        org_id = auth.organization_id
        user_id = auth.user_id
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")

        form = await request.form()
        selected_line_ids = []
        getlist = getattr(form, "getlist", None)
        if callable(getlist):
            selected_line_ids = [
                _safe_form_text(value) for value in getlist("selected_line_ids")
            ]

        bulk_inputs: list[BulkCountLineInput] = []
        for raw_line_id in selected_line_ids:
            try:
                line_id = UUID_Type(raw_line_id)
            except ValueError:
                continue

            counted_qty_str = _safe_form_text(
                form.get(f"counted_quantity_{raw_line_id}")
            )
            try:
                counted_qty = (
                    Decimal(counted_qty_str) if counted_qty_str else Decimal("0")
                )
            except (InvalidOperation, ValueError):
                counted_qty = Decimal("0")

            bulk_inputs.append(
                BulkCountLineInput(
                    line_id=line_id,
                    counted_quantity=counted_qty,
                )
            )

        if not bulk_inputs:
            return RedirectResponse(f"/inventory/counts/{count_id}", status_code=303)

        try:
            InventoryCountService.record_count_bulk(
                db=db,
                organization_id=org_id,
                count_id=coerce_uuid(count_id),
                inputs=bulk_inputs,
                counted_by_user_id=user_id,
            )
        except Exception as e:
            logger.warning(
                "Failed bulk count save on count %s for %s lines: %s",
                count_id,
                len(bulk_inputs),
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
            comp.component_item = comp_items_map.get(comp.component_item_id)
            comp.scrap_percentage = comp.scrap_percent  # type: ignore[attr-defined]
            if comp.component_item:
                item_cost = getattr(
                    comp.component_item, "standard_cost", None
                ) or Decimal("0")
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

    def serial_detail_response(
        self,
        request: Request,
        serial_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Serial number detail page."""
        from uuid import UUID as UUID_Type

        from sqlalchemy.orm import aliased

        from app.models.inventory.inventory_lot import InventoryLot
        from app.models.inventory.inventory_serial import (
            InventorySerial,
            InventorySerialMovement,
        )
        from app.models.inventory.inventory_transaction import InventoryTransaction
        from app.models.inventory.item import Item
        from app.models.inventory.warehouse import Warehouse
        from app.models.person import Person

        context = base_context(request, auth, "Serial Detail", "serials")

        try:
            sid = UUID_Type(serial_id)
        except ValueError:
            return RedirectResponse("/inventory/serials", status_code=302)

        row = db.execute(
            select(InventorySerial, Item, InventoryLot, Warehouse)
            .join(Item, InventorySerial.item_id == Item.item_id)
            .outerjoin(InventoryLot, InventorySerial.lot_id == InventoryLot.lot_id)
            .outerjoin(
                Warehouse, InventorySerial.warehouse_id == Warehouse.warehouse_id
            )
            .where(
                InventorySerial.serial_id == sid,
                InventorySerial.organization_id == auth.organization_id,
            )
        ).first()
        if not row:
            return RedirectResponse("/inventory/serials", status_code=302)

        FromWarehouse = aliased(Warehouse)
        ToWarehouse = aliased(Warehouse)
        MovementLot = aliased(InventoryLot)
        movement_labels = {
            "RECEIPT": "Received",
            "RETURN": "Returned",
            "ISSUE": "Issued",
            "TRANSFER_OUT": "Transferred out",
            "TRANSFER_IN": "Transferred in",
            "ADJUSTMENT": "Adjusted",
        }
        movement_rows = []
        for movement_row in db.execute(
            select(
                InventorySerialMovement,
                InventoryTransaction,
                FromWarehouse,
                ToWarehouse,
                MovementLot,
                Person,
            )
            .outerjoin(
                InventoryTransaction,
                InventorySerialMovement.transaction_id
                == InventoryTransaction.transaction_id,
            )
            .outerjoin(
                FromWarehouse,
                InventorySerialMovement.from_warehouse_id == FromWarehouse.warehouse_id,
            )
            .outerjoin(
                ToWarehouse,
                InventorySerialMovement.to_warehouse_id == ToWarehouse.warehouse_id,
            )
            .outerjoin(
                MovementLot, InventorySerialMovement.lot_id == MovementLot.lot_id
            )
            .outerjoin(Person, InventorySerialMovement.created_by_user_id == Person.id)
            .where(
                InventorySerialMovement.organization_id == auth.organization_id,
                InventorySerialMovement.serial_id == sid,
            )
            .order_by(InventorySerialMovement.created_at.desc())
        ).all():
            movement = movement_row.InventorySerialMovement
            transaction = movement_row.InventoryTransaction
            actor = movement_row[5]
            actor_name = None
            if actor:
                actor_name = (
                    actor.display_name
                    or f"{actor.first_name} {actor.last_name}".strip()
                    or actor.email
                )
            elif movement.created_by_user_id:
                actor_name = str(movement.created_by_user_id)

            transaction_type = None
            transaction_reference = None
            if transaction:
                transaction_type = (
                    transaction.transaction_type.value
                    if hasattr(transaction.transaction_type, "value")
                    else str(transaction.transaction_type)
                )
                transaction_reference = (
                    transaction.reference
                    or transaction.source_document_type
                    or str(transaction.transaction_id)
                )

            movement_rows.append(
                {
                    "movement": movement,
                    "movement_label": movement_labels.get(
                        movement.movement_type,
                        movement.movement_type.replace("_", " ").title(),
                    ),
                    "transaction": transaction,
                    "transaction_type": transaction_type,
                    "transaction_reference": transaction_reference,
                    "from_warehouse": movement_row[2],
                    "to_warehouse": movement_row[3],
                    "lot": movement_row[4],
                    "actor_name": actor_name,
                }
            )

        context.update(
            {
                "serial": row.InventorySerial,
                "item": row.Item,
                "lot": row.InventoryLot,
                "warehouse": row.Warehouse,
                "movement_rows": movement_rows,
            }
        )
        return templates.TemplateResponse(
            request, "inventory/serial_detail.html", context
        )

    def lot_detail_response(
        self,
        request: Request,
        lot_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Lot detail page."""
        from datetime import datetime as dt_cls
        from uuid import UUID as UUID_Type

        from app.models.inventory.inventory_lot import InventoryLot
        from app.models.inventory.inventory_transaction import InventoryTransaction
        from app.models.inventory.warehouse import Warehouse

        context = base_context(request, auth, "Lot Detail", "lots")

        try:
            lid = UUID_Type(lot_id)
        except ValueError:
            return RedirectResponse("/inventory/lots", status_code=302)

        lot = db.scalars(
            select(InventoryLot)
            .options(
                selectinload(InventoryLot.item),
                selectinload(InventoryLot.balances),
            )
            .where(InventoryLot.lot_id == lid)
        ).first()
        if not lot or lot.organization_id != auth.organization_id:
            return RedirectResponse("/inventory/lots", status_code=302)

        warehouse_ids = [
            balance.warehouse_id
            for balance in getattr(lot, "balances", []) or []
            if balance.warehouse_id
        ]
        warehouse_map = {}
        if warehouse_ids:
            warehouse_map = {
                wh.warehouse_id: wh
                for wh in db.scalars(
                    select(Warehouse).where(Warehouse.warehouse_id.in_(warehouse_ids))
                ).all()
            }

        balance_rows = []
        total_on_hand = 0
        total_allocated = 0
        total_available = 0
        any_quarantined = False
        for balance in sorted(
            list(getattr(lot, "balances", []) or []),
            key=lambda row: (
                wh.warehouse_name
                if (wh := warehouse_map.get(row.warehouse_id)) is not None
                else ""
            ),
        ):
            warehouse_name = (
                warehouse_map[balance.warehouse_id].warehouse_name
                if balance.warehouse_id in warehouse_map
                else "-"
            )
            balance_rows.append(
                {
                    "warehouse_name": warehouse_name,
                    "quantity_on_hand": balance.quantity_on_hand or 0,
                    "quantity_allocated": balance.quantity_allocated or 0,
                    "quantity_available": balance.quantity_available or 0,
                    "is_quarantined": bool(balance.is_quarantined),
                    "quarantine_reason": balance.quarantine_reason,
                    "qc_status": balance.qc_status,
                }
            )
            total_on_hand += balance.quantity_on_hand or 0
            total_allocated += balance.quantity_allocated or 0
            total_available += balance.quantity_available or 0
            any_quarantined = any_quarantined or bool(balance.is_quarantined)

        lot_view = cast(Any, lot)
        lot_view.total_on_hand = total_on_hand
        lot_view.total_allocated = total_allocated
        lot_view.total_available = total_available
        lot.is_quarantined = any_quarantined

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
                "balance_rows": balance_rows,
                "transactions": transactions,
                "now": dt_cls.now(UTC),
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
        from app.services.inventory.lot_serial import lot_serial_service

        org_id = auth.organization_id
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")

        try:
            lid = UUID_Type(lot_id)
        except ValueError:
            return RedirectResponse("/inventory/lots", status_code=303)

        lot = db.get(InventoryLot, lid)
        if not lot or lot.organization_id != org_id:
            return RedirectResponse("/inventory/lots", status_code=303)

        try:
            if lot.is_quarantined:
                lot_serial_service.release_quarantine(db, org_id, lid, "PASSED")
            else:
                lot_serial_service.quarantine_lot(
                    db,
                    org_id,
                    lid,
                    "Manual quarantine from inventory lot page",
                )
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

        currencies = get_currency_context(db, str(org_id)).get("currencies", [])

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
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")

        form = await request.form()

        pl_name = _safe_form_text(form.get("price_list_name"))
        pl_type_str = _safe_form_text(form.get("price_list_type") or "SALES")
        currency_code = _safe_form_text(form.get("currency_code")) or (
            org_context_service.get_functional_currency(db, org_id)
        )
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
        warehouse: str | None = None,
        category: str | None = None,
        show_zero: str | None = None,
        format: str | None = None,
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
        if org_id is None:
            raise HTTPException(status_code=400, detail="Organization is required")
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
