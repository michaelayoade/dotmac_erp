"""
AP Goods Receipt Web Service - Goods receipt-related web view methods.

Provides view-focused data and operations for AP goods receipt web routes.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.finance.ap.goods_receipt import GoodsReceipt, ReceiptStatus
from app.models.finance.ap.goods_receipt_line import GoodsReceiptLine
from app.models.finance.ap.purchase_order import POStatus, PurchaseOrder
from app.models.finance.ap.purchase_order_line import PurchaseOrderLine
from app.models.finance.ap.supplier import Supplier
from app.models.finance.common.attachment import AttachmentCategory
from app.services.common import coerce_uuid
from app.services.finance.ap.goods_receipt import goods_receipt_service
from app.services.finance.ap.supplier import supplier_service
from app.services.finance.ap.web.base import (
    format_currency,
    format_date,
    format_file_size,
    logger,
    parse_date,
    supplier_display_name,
    supplier_form_view,
    supplier_option_view,
)
from app.services.finance.common.attachment import AttachmentInput, attachment_service
from app.templates import templates
from app.web.deps import WebAuthContext, base_context


class GoodsReceiptWebService:
    """Web service methods for AP goods receipts."""

    # =========================================================================
    # Context Methods
    # =========================================================================

    @staticmethod
    def list_goods_receipts_context(
        db: Session,
        organization_id: str,
        search: str | None,
        supplier_id: str | None,
        po_id: str | None,
        status: str | None,
        start_date: str | None,
        end_date: str | None,
        page: int,
        limit: int = 50,
    ) -> dict:
        """Build context for goods receipts list page."""
        logger.debug(
            "list_goods_receipts_context: org=%s search=%r supplier_id=%s po_id=%s status=%s page=%d",
            organization_id,
            search,
            supplier_id,
            po_id,
            status,
            page,
        )
        org_id = coerce_uuid(organization_id)
        offset = (page - 1) * limit

        # Parse status
        status_value = None
        if status:
            try:
                status_value = ReceiptStatus(status)
            except ValueError:
                status_value = None

        from_date = parse_date(start_date)
        to_date = parse_date(end_date)

        query = (
            select(GoodsReceipt, Supplier, PurchaseOrder)
            .join(Supplier, GoodsReceipt.supplier_id == Supplier.supplier_id)
            .join(PurchaseOrder, GoodsReceipt.po_id == PurchaseOrder.po_id)
            .where(GoodsReceipt.organization_id == org_id)
        )

        if supplier_id:
            query = query.where(GoodsReceipt.supplier_id == coerce_uuid(supplier_id))
        if po_id:
            query = query.where(GoodsReceipt.po_id == coerce_uuid(po_id))
        if status_value:
            query = query.where(GoodsReceipt.status == status_value)
        if from_date:
            query = query.where(GoodsReceipt.receipt_date >= from_date)
        if to_date:
            query = query.where(GoodsReceipt.receipt_date <= to_date)
        if search:
            search_pattern = f"%{search}%"
            query = query.where(
                or_(
                    GoodsReceipt.receipt_number.ilike(search_pattern),
                    PurchaseOrder.po_number.ilike(search_pattern),
                    Supplier.legal_name.ilike(search_pattern),
                    Supplier.trading_name.ilike(search_pattern),
                )
            )

        total_count = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        receipts = db.execute(
            query.order_by(GoodsReceipt.receipt_date.desc()).limit(limit).offset(offset)
        ).all()

        # Build stats
        received_count = (
            db.scalar(
                select(func.count(GoodsReceipt.receipt_id)).where(
                    GoodsReceipt.organization_id == org_id,
                    GoodsReceipt.status == ReceiptStatus.RECEIVED,
                )
            )
            or 0
        )
        inspecting_count = (
            db.scalar(
                select(func.count(GoodsReceipt.receipt_id)).where(
                    GoodsReceipt.organization_id == org_id,
                    GoodsReceipt.status == ReceiptStatus.INSPECTING,
                )
            )
            or 0
        )
        accepted_count = (
            db.scalar(
                select(func.count(GoodsReceipt.receipt_id)).where(
                    GoodsReceipt.organization_id == org_id,
                    GoodsReceipt.status == ReceiptStatus.ACCEPTED,
                )
            )
            or 0
        )

        receipts_view = []
        for gr, supplier, po in receipts:
            # Count lines
            line_count = (
                db.scalar(
                    select(func.count(GoodsReceiptLine.line_id)).where(
                        GoodsReceiptLine.receipt_id == gr.receipt_id
                    )
                )
                or 0
            )
            receipts_view.append(
                {
                    "receipt_id": gr.receipt_id,
                    "receipt_number": gr.receipt_number,
                    "supplier_name": supplier_display_name(supplier),
                    "po_number": po.po_number,
                    "po_id": po.po_id,
                    "receipt_date": format_date(gr.receipt_date),
                    "status": gr.status.value,
                    "line_count": line_count,
                    "notes": gr.notes,
                }
            )

        suppliers_list = [
            supplier_option_view(supplier)
            for supplier in supplier_service.list(
                db,
                organization_id=org_id,
                is_active=True,
                limit=200,
            )
        ]

        total_pages = max(1, (total_count + limit - 1) // limit)

        logger.debug("list_goods_receipts_context: found %d receipts", total_count)

        return {
            "receipts": receipts_view,
            "suppliers_list": suppliers_list,
            "stats": {
                "received_count": received_count,
                "inspecting_count": inspecting_count,
                "accepted_count": accepted_count,
            },
            "search": search,
            "supplier_id": supplier_id,
            "po_id": po_id,
            "status": status,
            "start_date": start_date,
            "end_date": end_date,
            "page": page,
            "limit": limit,
            "offset": offset,
            "total_count": total_count,
            "total_pages": total_pages,
            "gr_statuses": [s.value for s in ReceiptStatus],
        }

    @staticmethod
    def goods_receipt_detail_context(
        db: Session,
        organization_id: str,
        receipt_id: str,
    ) -> dict:
        """Build context for goods receipt detail page."""
        logger.debug(
            "goods_receipt_detail_context: org=%s receipt_id=%s",
            organization_id,
            receipt_id,
        )
        org_id = coerce_uuid(organization_id)
        receipt_uuid = coerce_uuid(receipt_id)

        gr = db.get(GoodsReceipt, receipt_uuid)
        if not gr or gr.organization_id != org_id:
            logger.warning(
                "goods_receipt_detail_context: receipt not found org=%s receipt_id=%s",
                organization_id,
                receipt_id,
            )
            return {"receipt": None, "supplier": None, "order": None, "lines": []}

        supplier = db.get(Supplier, gr.supplier_id)
        po = db.get(PurchaseOrder, gr.po_id)

        lines = db.execute(
            select(GoodsReceiptLine, PurchaseOrderLine)
            .join(
                PurchaseOrderLine,
                GoodsReceiptLine.po_line_id == PurchaseOrderLine.line_id,
            )
            .where(GoodsReceiptLine.receipt_id == receipt_uuid)
            .order_by(GoodsReceiptLine.line_number)
        ).all()

        lines_view = []
        for gr_line, po_line in lines:
            lines_view.append(
                {
                    "line_id": gr_line.line_id,
                    "line_number": gr_line.line_number,
                    "description": po_line.description,
                    "quantity_ordered": po_line.quantity_ordered,
                    "quantity_received": gr_line.quantity_received,
                    "quantity_accepted": gr_line.quantity_accepted,
                    "quantity_rejected": gr_line.quantity_rejected,
                    "rejection_reason": gr_line.rejection_reason,
                    "lot_number": gr_line.lot_number,
                    "unit_price": format_currency(po_line.unit_price, po.currency_code)
                    if po
                    else None,
                }
            )

        receipt_view = {
            "receipt_id": gr.receipt_id,
            "receipt_number": gr.receipt_number,
            "supplier_id": gr.supplier_id,
            "supplier_name": supplier_display_name(supplier) if supplier else "",
            "po_id": gr.po_id,
            "po_number": po.po_number if po else "",
            "receipt_date": format_date(gr.receipt_date),
            "status": gr.status.value,
            "notes": gr.notes,
            "warehouse_id": gr.warehouse_id,
            "created_at": gr.created_at,
        }

        # Get attachments
        attachments = attachment_service.list_for_entity(
            db,
            organization_id=org_id,
            entity_type="GOODS_RECEIPT",
            entity_id=gr.receipt_id,
        )
        attachments_view = [
            {
                "attachment_id": str(att.attachment_id),
                "file_name": att.file_name,
                "file_size": att.file_size,
                "file_size_display": format_file_size(att.file_size),
                "content_type": att.content_type,
                "category": att.category.value,
                "description": att.description,
                "uploaded_at": att.uploaded_at,
                "download_url": f"/finance/ap/attachments/{att.attachment_id}/download",
            }
            for att in attachments
        ]

        logger.debug(
            "goods_receipt_detail_context: found receipt %s with %d lines",
            gr.receipt_number,
            len(lines_view),
        )

        return {
            "receipt": receipt_view,
            "supplier": supplier_form_view(supplier) if supplier else None,
            "order": {
                "po_id": po.po_id,
                "po_number": po.po_number,
                "po_date": format_date(po.po_date),
                "status": po.status.value,
                "total_amount": format_currency(po.total_amount, po.currency_code),
            }
            if po
            else None,
            "lines": lines_view,
            "attachments": attachments_view,
        }

    @staticmethod
    def goods_receipt_form_context(
        db: Session,
        organization_id: str,
        po_id: str | None = None,
    ) -> dict:
        """Build context for goods receipt form (create)."""
        logger.debug(
            "goods_receipt_form_context: org=%s po_id=%s", organization_id, po_id
        )
        org_id = coerce_uuid(organization_id)

        # Get POs that can receive goods (APPROVED or PARTIALLY_RECEIVED)
        receivable_statuses = [POStatus.APPROVED, POStatus.PARTIALLY_RECEIVED]
        pos = db.execute(
            select(PurchaseOrder, Supplier)
            .join(Supplier, PurchaseOrder.supplier_id == Supplier.supplier_id)
            .where(
                PurchaseOrder.organization_id == org_id,
                PurchaseOrder.status.in_(receivable_statuses),
            )
            .order_by(PurchaseOrder.po_date.desc())
            .limit(100)
        ).all()

        po_list = []
        for po, supplier in pos:
            po_list.append(
                {
                    "po_id": str(po.po_id),
                    "po_number": po.po_number,
                    "supplier_id": str(po.supplier_id),
                    "supplier_name": supplier_display_name(supplier),
                    "po_date": format_date(po.po_date),
                    "total_amount": format_currency(po.total_amount, po.currency_code),
                    "currency_code": po.currency_code,
                }
            )

        # If a specific PO is selected, get its lines
        selected_po = None
        po_lines = []
        if po_id:
            po_uuid = coerce_uuid(po_id)
            po = db.get(PurchaseOrder, po_uuid)
            if po and po.organization_id == org_id:
                supplier = db.get(Supplier, po.supplier_id)
                selected_po = {
                    "po_id": str(po.po_id),
                    "po_number": po.po_number,
                    "supplier_id": str(po.supplier_id),
                    "supplier_name": supplier_display_name(supplier)
                    if supplier
                    else "",
                    "po_date": format_date(po.po_date),
                    "currency_code": po.currency_code,
                }

                lines = db.scalars(
                    select(PurchaseOrderLine)
                    .where(PurchaseOrderLine.po_id == po_uuid)
                    .order_by(PurchaseOrderLine.line_number)
                ).all()

                for line in lines:
                    remaining = line.quantity_ordered - line.quantity_received
                    if remaining > 0:
                        po_lines.append(
                            {
                                "line_id": str(line.line_id),
                                "line_number": line.line_number,
                                "description": line.description,
                                "quantity_ordered": float(line.quantity_ordered),
                                "quantity_received": float(line.quantity_received),
                                "quantity_remaining": float(remaining),
                                "unit_price": float(line.unit_price),
                            }
                        )

        # Get warehouses for selection
        from app.models.inventory.warehouse import Warehouse

        warehouses = db.scalars(
            select(Warehouse)
            .where(
                Warehouse.organization_id == org_id,
                Warehouse.is_active.is_(True),
            )
            .order_by(Warehouse.warehouse_code)
        ).all()
        warehouse_list = [
            {
                "warehouse_id": str(w.warehouse_id),
                "warehouse_code": w.warehouse_code,
                "warehouse_name": w.warehouse_name,
            }
            for w in warehouses
        ]

        logger.debug(
            "goods_receipt_form_context: found %d POs, %d lines for selected PO",
            len(po_list),
            len(po_lines),
        )

        return {
            "po_list": po_list,
            "selected_po": selected_po,
            "po_lines": po_lines,
            "warehouse_list": warehouse_list,
        }

    # =========================================================================
    # HTTP Response Methods
    # =========================================================================

    def list_goods_receipts_response(
        self,
        request: Request,
        auth: WebAuthContext,
        search: str | None,
        supplier_id: str | None,
        po_id: str | None,
        status: str | None,
        start_date: str | None,
        end_date: str | None,
        page: int,
        db: Session,
    ) -> HTMLResponse:
        """Render goods receipts list page."""
        context = base_context(request, auth, "Goods Receipts", "ap")
        context.update(
            self.list_goods_receipts_context(
                db,
                str(auth.organization_id),
                search=search,
                supplier_id=supplier_id,
                po_id=po_id,
                status=status,
                start_date=start_date,
                end_date=end_date,
                page=page,
            )
        )
        return templates.TemplateResponse(
            request, "finance/ap/goods_receipts.html", context
        )

    def goods_receipt_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        po_id: str | None,
        db: Session,
    ) -> HTMLResponse:
        """Render new goods receipt form."""
        context = base_context(request, auth, "New Goods Receipt", "ap")
        context.update(
            self.goods_receipt_form_context(db, str(auth.organization_id), po_id)
        )
        return templates.TemplateResponse(
            request, "finance/ap/goods_receipt_form.html", context
        )

    def goods_receipt_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        receipt_id: str,
    ) -> HTMLResponse:
        """Render goods receipt detail page."""
        context = base_context(request, auth, "Goods Receipt Details", "ap")
        context.update(
            self.goods_receipt_detail_context(
                db,
                str(auth.organization_id),
                receipt_id,
            )
        )
        return templates.TemplateResponse(
            request, "finance/ap/goods_receipt_detail.html", context
        )

    async def create_goods_receipt_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | JSONResponse | RedirectResponse | dict:
        """Handle goods receipt creation form submission."""
        content_type = request.headers.get("content-type", "")

        if "application/json" in content_type:
            data = await request.json()
        else:
            form_data = await request.form()
            data = dict(form_data)

        try:
            org_id = auth.organization_id
            user_id = auth.person_id
            if org_id is None:
                raise HTTPException(status_code=401, detail="Authentication required")
            if user_id is None:
                raise HTTPException(status_code=401, detail="Authentication required")
            payload = dict(data)
            input_data = goods_receipt_service.build_input_from_payload(
                db=db,
                organization_id=org_id,
                payload=payload,
            )

            receipt = goods_receipt_service.create_receipt(
                db=db,
                organization_id=org_id,
                input=input_data,
                received_by_user_id=user_id,
            )

            logger.info(
                "create_goods_receipt_response: created receipt %s for org %s",
                receipt.receipt_id,
                auth.organization_id,
            )

            if "application/json" in content_type:
                return {"success": True, "receipt_id": str(receipt.receipt_id)}

            return RedirectResponse(
                url=f"/finance/ap/goods-receipts/{receipt.receipt_id}?saved=1",
                status_code=303,
            )

        except Exception as e:
            logger.exception("create_goods_receipt_response: failed")
            if "application/json" in content_type:
                return JSONResponse(status_code=400, content={"detail": str(e)})

            context = base_context(request, auth, "New Goods Receipt", "ap")
            context.update(
                self.goods_receipt_form_context(
                    db,
                    str(auth.organization_id),
                    data.get("po_id"),
                )
            )
            context["error"] = str(e)
            context["form_data"] = data
            return templates.TemplateResponse(
                request, "finance/ap/goods_receipt_form.html", context
            )

    def start_inspection_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        receipt_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Handle starting inspection for a goods receipt."""
        try:
            org_id = auth.organization_id
            if org_id is None:
                raise HTTPException(status_code=401, detail="Authentication required")
            goods_receipt_service.start_inspection(
                db=db,
                organization_id=org_id,
                receipt_id=UUID(receipt_id),
            )
            logger.info(
                "start_inspection_response: started inspection for receipt %s org %s",
                receipt_id,
                auth.organization_id,
            )
            return RedirectResponse(
                url=f"/finance/ap/goods-receipts/{receipt_id}?success=Inspection+started",
                status_code=303,
            )
        except Exception as e:
            logger.exception(
                "start_inspection_response: failed for receipt %s", receipt_id
            )
            context = base_context(request, auth, "Goods Receipt Details", "ap")
            context.update(
                self.goods_receipt_detail_context(
                    db,
                    str(auth.organization_id),
                    receipt_id,
                )
            )
            context["error"] = str(e)
            return templates.TemplateResponse(
                request, "finance/ap/goods_receipt_detail.html", context
            )

    def accept_all_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        receipt_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Handle accepting all items in a goods receipt."""
        try:
            org_id = auth.organization_id
            if org_id is None:
                raise HTTPException(status_code=401, detail="Authentication required")
            goods_receipt_service.accept_all(
                db=db,
                organization_id=org_id,
                receipt_id=UUID(receipt_id),
            )
            logger.info(
                "accept_all_response: accepted all items for receipt %s org %s",
                receipt_id,
                auth.organization_id,
            )
            return RedirectResponse(
                url=f"/finance/ap/goods-receipts/{receipt_id}?success=All+items+accepted",
                status_code=303,
            )
        except Exception as e:
            logger.exception("accept_all_response: failed for receipt %s", receipt_id)
            context = base_context(request, auth, "Goods Receipt Details", "ap")
            context.update(
                self.goods_receipt_detail_context(
                    db,
                    str(auth.organization_id),
                    receipt_id,
                )
            )
            context["error"] = str(e)
            return templates.TemplateResponse(
                request, "finance/ap/goods_receipt_detail.html", context
            )

    async def upload_goods_receipt_attachment_response(
        self,
        receipt_id: str,
        file: UploadFile,
        description: str | None,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Handle goods receipt attachment upload."""
        try:
            org_id = auth.organization_id
            user_id = auth.person_id
            if org_id is None:
                raise HTTPException(status_code=401, detail="Authentication required")
            if user_id is None:
                raise HTTPException(status_code=401, detail="Authentication required")
            receipt = goods_receipt_service.get(db, receipt_id)
            if not receipt or receipt.organization_id != auth.organization_id:
                return RedirectResponse(
                    url=f"/finance/ap/goods-receipts/{receipt_id}?error=Goods+receipt+not+found",
                    status_code=303,
                )

            input_data = AttachmentInput(
                entity_type="GOODS_RECEIPT",
                entity_id=receipt_id,
                file_name=file.filename or "unnamed",
                content_type=file.content_type or "application/octet-stream",
                category=AttachmentCategory.GOODS_RECEIPT,
                description=description,
            )

            attachment_service.save_file(
                db=db,
                organization_id=org_id,
                input=input_data,
                file_content=file.file,
                uploaded_by=user_id,
            )

            logger.info(
                "upload_goods_receipt_attachment_response: uploaded attachment for receipt %s",
                receipt_id,
            )

            return RedirectResponse(
                url=f"/finance/ap/goods-receipts/{receipt_id}?success=Attachment+uploaded",
                status_code=303,
            )

        except ValueError as e:
            return RedirectResponse(
                url=f"/finance/ap/goods-receipts/{receipt_id}?error={str(e)}",
                status_code=303,
            )
        except Exception:
            logger.exception(
                "upload_goods_receipt_attachment_response: failed for receipt %s",
                receipt_id,
            )
            return RedirectResponse(
                url=f"/finance/ap/goods-receipts/{receipt_id}?error=Upload+failed",
                status_code=303,
            )
