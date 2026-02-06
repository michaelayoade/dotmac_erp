"""
AP Purchase Order Web Service - Purchase order-related web view methods.

Provides view-focused data and operations for AP purchase order web routes.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.finance.ap.goods_receipt import GoodsReceipt
from app.models.finance.ap.goods_receipt_line import GoodsReceiptLine
from app.models.finance.ap.purchase_order import POStatus, PurchaseOrder
from app.models.finance.ap.purchase_order_line import PurchaseOrderLine
from app.models.finance.ap.supplier import Supplier
from app.models.finance.common.attachment import AttachmentCategory
from app.models.finance.gl.account_category import IFRSCategory
from app.services.common import coerce_uuid
from app.services.finance.ap.purchase_order import (
    POLineInput,
    PurchaseOrderInput,
    purchase_order_service,
)
from app.services.finance.ap.supplier import supplier_service
from app.services.finance.ap.web.base import (
    format_currency,
    format_date,
    format_file_size,
    get_accounts,
    get_cost_centers,
    get_projects,
    logger,
    parse_date,
    supplier_display_name,
    supplier_form_view,
    supplier_option_view,
)
from app.services.finance.common.attachment import AttachmentInput, attachment_service
from app.services.finance.platform.currency_context import get_currency_context
from app.services.finance.platform.org_context import org_context_service
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

logger = logging.getLogger(__name__)


class PurchaseOrderWebService:
    """Web service methods for AP purchase orders."""

    # =========================================================================
    # Context Methods (Data preparation for views)
    # =========================================================================

    @staticmethod
    def list_purchase_orders_context(
        db: Session,
        organization_id: str,
        search: Optional[str],
        supplier_id: Optional[str],
        status: Optional[str],
        start_date: Optional[str],
        end_date: Optional[str],
        page: int,
        limit: int = 50,
    ) -> dict:
        """Build context for purchase orders list page."""
        logger.debug(
            "list_purchase_orders_context: org=%s search=%r supplier_id=%s status=%s page=%d",
            organization_id,
            search,
            supplier_id,
            status,
            page,
        )
        org_id = coerce_uuid(organization_id)
        offset = (page - 1) * limit

        # Parse status
        status_value = None
        if status:
            try:
                status_value = POStatus(status)
            except ValueError:
                status_value = None

        from_date = parse_date(start_date)
        to_date = parse_date(end_date)

        query = (
            db.query(PurchaseOrder, Supplier)
            .join(Supplier, PurchaseOrder.supplier_id == Supplier.supplier_id)
            .filter(PurchaseOrder.organization_id == org_id)
        )

        if supplier_id:
            query = query.filter(PurchaseOrder.supplier_id == coerce_uuid(supplier_id))
        if status_value:
            query = query.filter(PurchaseOrder.status == status_value)
        if from_date:
            query = query.filter(PurchaseOrder.po_date >= from_date)
        if to_date:
            query = query.filter(PurchaseOrder.po_date <= to_date)
        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                or_(
                    PurchaseOrder.po_number.ilike(search_pattern),
                    Supplier.legal_name.ilike(search_pattern),
                    Supplier.trading_name.ilike(search_pattern),
                )
            )

        total_count = query.with_entities(func.count(PurchaseOrder.po_id)).scalar() or 0
        orders = (
            query.order_by(PurchaseOrder.po_date.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )

        # Build stats
        draft_count = (
            db.query(func.count(PurchaseOrder.po_id))
            .filter(
                PurchaseOrder.organization_id == org_id,
                PurchaseOrder.status == POStatus.DRAFT,
            )
            .scalar()
            or 0
        )
        pending_count = (
            db.query(func.count(PurchaseOrder.po_id))
            .filter(
                PurchaseOrder.organization_id == org_id,
                PurchaseOrder.status == POStatus.PENDING_APPROVAL,
            )
            .scalar()
            or 0
        )
        approved_total = db.query(
            func.coalesce(func.sum(PurchaseOrder.total_amount), 0)
        ).filter(
            PurchaseOrder.organization_id == org_id,
            PurchaseOrder.status == POStatus.APPROVED,
        ).scalar() or Decimal("0")
        open_total = db.query(
            func.coalesce(
                func.sum(PurchaseOrder.total_amount - PurchaseOrder.amount_received), 0
            )
        ).filter(
            PurchaseOrder.organization_id == org_id,
            PurchaseOrder.status.in_([POStatus.APPROVED, POStatus.PARTIALLY_RECEIVED]),
        ).scalar() or Decimal("0")

        orders_view = []
        for po, supplier in orders:
            orders_view.append(
                {
                    "po_id": po.po_id,
                    "po_number": po.po_number,
                    "supplier_name": supplier_display_name(supplier),
                    "po_date": format_date(po.po_date),
                    "expected_delivery_date": format_date(po.expected_delivery_date),
                    "total_amount": format_currency(po.total_amount, po.currency_code),
                    "amount_received": format_currency(
                        po.amount_received, po.currency_code
                    ),
                    "status": po.status.value,
                    "currency_code": po.currency_code,
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

        logger.debug("list_purchase_orders_context: found %d orders", total_count)

        return {
            "orders": orders_view,
            "suppliers_list": suppliers_list,
            "stats": {
                "draft_count": draft_count,
                "pending_count": pending_count,
                "approved_total": format_currency(approved_total) or "$0.00",
                "open_total": format_currency(open_total) or "$0.00",
            },
            "search": search,
            "supplier_id": supplier_id,
            "status": status,
            "start_date": start_date,
            "end_date": end_date,
            "page": page,
            "limit": limit,
            "offset": offset,
            "total_count": total_count,
            "total_pages": total_pages,
            "po_statuses": [s.value for s in POStatus],
        }

    @staticmethod
    def purchase_order_detail_context(
        db: Session,
        organization_id: str,
        po_id: str,
    ) -> dict:
        """Build context for purchase order detail page."""
        logger.debug(
            "purchase_order_detail_context: org=%s po_id=%s", organization_id, po_id
        )
        org_id = coerce_uuid(organization_id)
        po_uuid = coerce_uuid(po_id)

        po = db.get(PurchaseOrder, po_uuid)
        if not po or po.organization_id != org_id:
            return {"order": None, "supplier": None, "lines": []}

        supplier = db.get(Supplier, po.supplier_id)

        lines = (
            db.query(PurchaseOrderLine)
            .filter(PurchaseOrderLine.po_id == po_uuid)
            .order_by(PurchaseOrderLine.line_number)
            .all()
        )

        lines_view = []
        for line in lines:
            lines_view.append(
                {
                    "line_id": line.line_id,
                    "line_number": line.line_number,
                    "description": line.description,
                    "quantity_ordered": line.quantity_ordered,
                    "quantity_received": line.quantity_received,
                    "quantity_invoiced": line.quantity_invoiced,
                    "unit_price": format_currency(line.unit_price, po.currency_code),
                    "line_amount": format_currency(line.line_amount, po.currency_code),
                    "tax_amount": format_currency(line.tax_amount, po.currency_code),
                    "item_id": line.item_id,
                }
            )

        order_view = {
            "po_id": po.po_id,
            "po_number": po.po_number,
            "supplier_id": po.supplier_id,
            "supplier_name": supplier_display_name(supplier) if supplier else "",
            "po_date": format_date(po.po_date),
            "expected_delivery_date": format_date(po.expected_delivery_date),
            "currency_code": po.currency_code,
            "subtotal": format_currency(po.subtotal, po.currency_code),
            "tax_amount": format_currency(po.tax_amount, po.currency_code),
            "total_amount": format_currency(po.total_amount, po.currency_code),
            "amount_received": format_currency(po.amount_received, po.currency_code),
            "amount_invoiced": format_currency(po.amount_invoiced, po.currency_code),
            "status": po.status.value,
            "terms_and_conditions": po.terms_and_conditions,
            "shipping_address": po.shipping_address,
            "created_at": po.created_at,
            "approved_at": po.approved_at,
        }

        # Get related goods receipts
        goods_receipts = (
            db.query(GoodsReceipt)
            .filter(GoodsReceipt.po_id == po_uuid)
            .order_by(GoodsReceipt.receipt_date.desc())
            .all()
        )
        receipts_view = []
        for gr in goods_receipts:
            line_count = (
                db.query(func.count(GoodsReceiptLine.line_id))
                .filter(GoodsReceiptLine.receipt_id == gr.receipt_id)
                .scalar()
                or 0
            )
            receipts_view.append(
                {
                    "receipt_id": gr.receipt_id,
                    "receipt_number": gr.receipt_number,
                    "receipt_date": format_date(gr.receipt_date),
                    "status": gr.status.value,
                    "line_count": line_count,
                    "notes": gr.notes,
                }
            )

        # Get attachments
        attachments = attachment_service.list_for_entity(
            db,
            organization_id=org_id,
            entity_type="PURCHASE_ORDER",
            entity_id=po.po_id,
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

        logger.debug("purchase_order_detail_context: found %d lines", len(lines_view))

        return {
            "order": order_view,
            "supplier": supplier_form_view(supplier) if supplier else None,
            "lines": lines_view,
            "goods_receipts": receipts_view,
            "attachments": attachments_view,
        }

    @staticmethod
    def purchase_order_form_context(
        db: Session,
        organization_id: str,
        po_id: Optional[str] = None,
    ) -> dict:
        """Build context for purchase order form (create/edit)."""
        logger.debug(
            "purchase_order_form_context: org=%s po_id=%s", organization_id, po_id
        )
        org_id = coerce_uuid(organization_id)

        suppliers_list = [
            supplier_option_view(supplier)
            for supplier in supplier_service.list(
                db,
                organization_id=org_id,
                is_active=True,
                limit=200,
            )
        ]

        expense_accounts = get_accounts(db, org_id, IFRSCategory.EXPENSES)

        # Get inventory items for selection
        from app.models.inventory.item import Item

        items = (
            db.query(Item)
            .filter(
                Item.organization_id == org_id,
                Item.is_active.is_(True),
                Item.is_purchaseable.is_(True),
            )
            .order_by(Item.item_code)
            .limit(500)
            .all()
        )
        items_list = [
            {
                "item_id": str(item.item_id),
                "item_code": item.item_code,
                "item_name": item.item_name,
                "standard_cost": float(item.standard_cost)
                if item.standard_cost
                else None,
                "currency_code": item.currency_code,
            }
            for item in items
        ]

        order = None
        lines = []
        if po_id:
            po_uuid = coerce_uuid(po_id)
            po = db.get(PurchaseOrder, po_uuid)
            if po and po.organization_id == org_id:
                order = {
                    "po_id": po.po_id,
                    "po_number": po.po_number,
                    "supplier_id": str(po.supplier_id),
                    "po_date": format_date(po.po_date),
                    "expected_delivery_date": format_date(po.expected_delivery_date),
                    "currency_code": po.currency_code,
                    "terms_and_conditions": po.terms_and_conditions,
                    "status": po.status.value,
                }
                po_lines = (
                    db.query(PurchaseOrderLine)
                    .filter(PurchaseOrderLine.po_id == po_uuid)
                    .order_by(PurchaseOrderLine.line_number)
                    .all()
                )
                for line in po_lines:
                    lines.append(
                        {
                            "line_id": str(line.line_id),
                            "item_id": str(line.item_id) if line.item_id else "",
                            "description": line.description,
                            "quantity": float(line.quantity_ordered),
                            "unit_price": float(line.unit_price),
                            "tax_amount": float(line.tax_amount)
                            if line.tax_amount
                            else 0,
                            "expense_account_id": str(line.expense_account_id)
                            if line.expense_account_id
                            else "",
                        }
                    )

        context = {
            "order": order,
            "lines": lines,
            "suppliers_list": suppliers_list,
            "expense_accounts": expense_accounts,
            "items_list": items_list,
            "cost_centers": get_cost_centers(db, org_id),
            "projects": get_projects(db, org_id),
        }
        context.update(get_currency_context(db, organization_id))
        return context

    # =========================================================================
    # HTTP Response Methods
    # =========================================================================

    def list_purchase_orders_response(
        self,
        request: Request,
        auth: WebAuthContext,
        search: Optional[str],
        supplier_id: Optional[str],
        status: Optional[str],
        start_date: Optional[str],
        end_date: Optional[str],
        page: int,
        db: Session,
    ) -> HTMLResponse:
        """Render purchase orders list page."""
        context = base_context(request, auth, "Purchase Orders", "ap")
        context.update(
            self.list_purchase_orders_context(
                db,
                str(auth.organization_id),
                search=search,
                supplier_id=supplier_id,
                status=status,
                start_date=start_date,
                end_date=end_date,
                page=page,
            )
        )
        return templates.TemplateResponse(
            request, "finance/ap/purchase_orders.html", context
        )

    def purchase_order_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """Render new purchase order form."""
        context = base_context(request, auth, "New Purchase Order", "ap")
        context.update(self.purchase_order_form_context(db, str(auth.organization_id)))
        return templates.TemplateResponse(
            request, "finance/ap/purchase_order_form.html", context
        )

    def purchase_order_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        po_id: str,
    ) -> HTMLResponse:
        """Render purchase order detail page."""
        context = base_context(request, auth, "Purchase Order Details", "ap")
        context.update(
            self.purchase_order_detail_context(
                db,
                str(auth.organization_id),
                po_id,
            )
        )
        return templates.TemplateResponse(
            request, "finance/ap/purchase_order_detail.html", context
        )

    def purchase_order_edit_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        po_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Render purchase order edit form."""
        context = base_context(request, auth, "Edit Purchase Order", "ap")
        context.update(
            self.purchase_order_form_context(db, str(auth.organization_id), po_id)
        )
        if not context.get("order"):
            return RedirectResponse(url="/finance/ap/purchase-orders", status_code=303)
        return templates.TemplateResponse(
            request, "finance/ap/purchase_order_form.html", context
        )

    async def create_purchase_order_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | JSONResponse | RedirectResponse | dict:
        """Handle purchase order creation form submission."""
        content_type = request.headers.get("content-type", "")

        if "application/json" in content_type:
            data = await request.json()
        else:
            form_data = await request.form()
            data = dict(form_data)

        try:
            org_id = auth.organization_id
            user_id = auth.person_id
            assert org_id is not None
            assert user_id is not None
            lines_data = data.get("lines", [])
            if isinstance(lines_data, str):
                lines_data = json.loads(lines_data)

            lines = []
            for line in lines_data:
                if line.get("description"):
                    lines.append(
                        POLineInput(
                            item_id=UUID(line["item_id"])
                            if line.get("item_id")
                            else None,
                            description=line.get("description", ""),
                            quantity_ordered=Decimal(
                                str(
                                    line.get(
                                        "quantity", line.get("quantity_ordered", 1)
                                    )
                                )
                            ),
                            unit_price=Decimal(str(line.get("unit_price", 0))),
                            expense_account_id=UUID(line["expense_account_id"])
                            if line.get("expense_account_id")
                            else None,
                            tax_code_id=UUID(line["tax_code_id"])
                            if line.get("tax_code_id")
                            else None,
                            cost_center_id=UUID(line["cost_center_id"])
                            if line.get("cost_center_id")
                            else None,
                            project_id=UUID(line["project_id"])
                            if line.get("project_id")
                            else None,
                        )
                    )

            po_date_str = data.get("po_date")
            po_date = (
                datetime.strptime(po_date_str, "%Y-%m-%d").date()
                if po_date_str
                else date.today()
            )

            expected_delivery_str = data.get("expected_delivery_date")
            expected_delivery = (
                datetime.strptime(expected_delivery_str, "%Y-%m-%d").date()
                if expected_delivery_str
                else None
            )

            currency_code = data.get(
                "currency_code"
            ) or org_context_service.get_functional_currency(
                db,
                org_id,
            )

            input_data = PurchaseOrderInput(
                supplier_id=UUID(data["supplier_id"]),
                po_date=po_date,
                expected_delivery_date=expected_delivery,
                currency_code=currency_code,
                terms_and_conditions=data.get("terms_and_conditions"),
                lines=lines,
            )

            po = purchase_order_service.create_po(
                db=db,
                organization_id=org_id,
                input=input_data,
                created_by_user_id=user_id,
            )

            logger.info(
                "create_purchase_order_response: created PO %s for org %s",
                po.po_id,
                auth.organization_id,
            )

            if "application/json" in content_type:
                return {"success": True, "po_id": str(po.po_id)}

            return RedirectResponse(
                url=f"/finance/ap/purchase-orders/{po.po_id}",
                status_code=303,
            )

        except Exception as e:
            logger.exception("create_purchase_order_response: failed")
            if "application/json" in content_type:
                return JSONResponse(status_code=400, content={"detail": str(e)})

            context = base_context(request, auth, "New Purchase Order", "ap")
            context.update(
                self.purchase_order_form_context(db, str(auth.organization_id))
            )
            context["error"] = str(e)
            context["form_data"] = data
            return templates.TemplateResponse(
                request, "finance/ap/purchase_order_form.html", context
            )

    def submit_purchase_order_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        po_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Handle purchase order submission for approval."""
        try:
            org_id = auth.organization_id
            user_id = auth.person_id
            assert org_id is not None
            assert user_id is not None
            purchase_order_service.submit_for_approval(
                db=db,
                organization_id=org_id,
                po_id=UUID(po_id),
                submitted_by_user_id=user_id,
            )
            logger.info(
                "submit_purchase_order_response: submitted PO %s for approval", po_id
            )
            return RedirectResponse(
                url=f"/finance/ap/purchase-orders/{po_id}?success=Submitted+for+approval",
                status_code=303,
            )
        except Exception as e:
            logger.exception("submit_purchase_order_response: failed for PO %s", po_id)
            context = base_context(request, auth, "Purchase Order Details", "ap")
            context.update(
                self.purchase_order_detail_context(
                    db,
                    str(auth.organization_id),
                    po_id,
                )
            )
            context["error"] = str(e)
            return templates.TemplateResponse(
                request, "finance/ap/purchase_order_detail.html", context
            )

    def approve_purchase_order_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        po_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Handle purchase order approval."""
        try:
            org_id = auth.organization_id
            user_id = auth.person_id
            assert org_id is not None
            assert user_id is not None
            purchase_order_service.approve_po(
                db=db,
                organization_id=org_id,
                po_id=UUID(po_id),
                approved_by_user_id=user_id,
            )
            logger.info("approve_purchase_order_response: approved PO %s", po_id)
            return RedirectResponse(
                url=f"/finance/ap/purchase-orders/{po_id}?success=Purchase+order+approved",
                status_code=303,
            )
        except Exception as e:
            logger.exception("approve_purchase_order_response: failed for PO %s", po_id)
            context = base_context(request, auth, "Purchase Order Details", "ap")
            context.update(
                self.purchase_order_detail_context(
                    db,
                    str(auth.organization_id),
                    po_id,
                )
            )
            context["error"] = str(e)
            return templates.TemplateResponse(
                request, "finance/ap/purchase_order_detail.html", context
            )

    def cancel_purchase_order_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        po_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Handle purchase order cancellation."""
        try:
            org_id = auth.organization_id
            assert org_id is not None
            purchase_order_service.cancel_po(
                db=db,
                organization_id=org_id,
                po_id=UUID(po_id),
            )
            logger.info("cancel_purchase_order_response: cancelled PO %s", po_id)
            return RedirectResponse(
                url=f"/finance/ap/purchase-orders/{po_id}?success=Purchase+order+cancelled",
                status_code=303,
            )
        except Exception as e:
            logger.exception("cancel_purchase_order_response: failed for PO %s", po_id)
            context = base_context(request, auth, "Purchase Order Details", "ap")
            context.update(
                self.purchase_order_detail_context(
                    db,
                    str(auth.organization_id),
                    po_id,
                )
            )
            context["error"] = str(e)
            return templates.TemplateResponse(
                request, "finance/ap/purchase_order_detail.html", context
            )

    async def upload_po_attachment_response(
        self,
        po_id: str,
        file: UploadFile,
        description: Optional[str],
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Handle purchase order attachment upload."""
        try:
            org_id = auth.organization_id
            user_id = auth.person_id
            assert org_id is not None
            assert user_id is not None
            po = purchase_order_service.get(db, po_id)
            if not po or po.organization_id != auth.organization_id:
                return RedirectResponse(
                    url=f"/finance/ap/purchase-orders/{po_id}?error=Purchase+order+not+found",
                    status_code=303,
                )

            input_data = AttachmentInput(
                entity_type="PURCHASE_ORDER",
                entity_id=po_id,
                file_name=file.filename or "unnamed",
                content_type=file.content_type or "application/octet-stream",
                category=AttachmentCategory.PURCHASE_ORDER,
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
                "upload_po_attachment_response: uploaded attachment for PO %s", po_id
            )

            return RedirectResponse(
                url=f"/finance/ap/purchase-orders/{po_id}?success=Attachment+uploaded",
                status_code=303,
            )

        except ValueError as e:
            logger.warning(
                "upload_po_attachment_response: validation error for PO %s: %s",
                po_id,
                str(e),
            )
            return RedirectResponse(
                url=f"/finance/ap/purchase-orders/{po_id}?error={str(e)}",
                status_code=303,
            )
        except Exception:
            logger.exception("upload_po_attachment_response: failed for PO %s", po_id)
            return RedirectResponse(
                url=f"/finance/ap/purchase-orders/{po_id}?error=Upload+failed",
                status_code=303,
            )
