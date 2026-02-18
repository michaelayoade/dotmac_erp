"""
AP Invoice Web Service - Invoice-related web view methods.

Provides view-focused data and operations for AP invoice web routes.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.finance.ap.purchase_order import PurchaseOrder
from app.models.finance.ap.purchase_order_line import PurchaseOrderLine
from app.models.finance.ap.supplier import Supplier
from app.models.finance.ap.supplier_invoice import (
    SupplierInvoice,
    SupplierInvoiceStatus,
)
from app.models.finance.ap.supplier_invoice_line import SupplierInvoiceLine
from app.models.finance.common.attachment import AttachmentCategory
from app.models.finance.gl.account_category import IFRSCategory
from app.services.common import coerce_uuid
from app.services.common_filters import build_active_filters
from app.services.finance.ap.supplier import supplier_service
from app.services.finance.ap.supplier_invoice import (
    SupplierInvoiceInput,
    supplier_invoice_service,
)
from app.services.finance.ap.web.base import (
    InvoiceStats,
    format_currency,
    format_date,
    format_file_size,
    get_accounts,
    invoice_detail_view,
    invoice_line_view,
    invoice_status_label,
    logger,
    supplier_display_name,
    supplier_form_view,
    supplier_option_view,
)
from app.services.finance.common.attachment import AttachmentInput, attachment_service
from app.services.finance.common.sorting import apply_sort
from app.services.finance.platform.currency_context import get_currency_context
from app.templates import templates
from app.web.deps import WebAuthContext, base_context


class InvoiceWebService:
    """Web service methods for AP invoices."""

    @staticmethod
    def build_invoice_input(
        db: Session, data: dict, organization_id: UUID
    ) -> SupplierInvoiceInput:
        """Build SupplierInvoiceInput from form data."""
        logger.debug("build_invoice_input: building input from form data")
        payload = dict(data)
        return supplier_invoice_service.build_input_from_payload(
            db=db,
            organization_id=organization_id,
            payload=payload,
        )

    @staticmethod
    def list_invoices_context(
        db: Session,
        organization_id: str,
        search: str | None,
        supplier_id: str | None,
        status: str | None,
        start_date: str | None,
        end_date: str | None,
        page: int,
        limit: int = 50,
        sort: str | None = None,
        sort_dir: str | None = None,
    ) -> dict:
        """Get context for invoice listing page."""
        logger.debug(
            "list_invoices_context: org=%s search=%r supplier_id=%s status=%s page=%d",
            organization_id,
            search,
            supplier_id,
            status,
            page,
        )
        offset = (page - 1) * limit
        today = date.today()
        org_id = coerce_uuid(organization_id)

        from app.services.finance.ap.invoice_query import build_invoice_query

        base_query = build_invoice_query(
            db=db,
            organization_id=organization_id,
            search=search,
            supplier_id=supplier_id,
            status=status,
            start_date=start_date,
            end_date=end_date,
        )

        total_count = (
            base_query.with_entities(func.count(SupplierInvoice.invoice_id)).scalar()
            or 0
        )

        invoice_sort_map = {
            "invoice_date": SupplierInvoice.invoice_date,
            "invoice_number": SupplierInvoice.invoice_number,
            "supplier_name": Supplier.legal_name,
            "total_amount": SupplierInvoice.total_amount,
            "due_date": SupplierInvoice.due_date,
            "status": SupplierInvoice.status,
        }
        sorted_query = apply_sort(
            base_query,
            sort,
            sort_dir,
            invoice_sort_map,
            default=SupplierInvoice.invoice_date.desc(),
        )

        invoices = (
            sorted_query.with_entities(SupplierInvoice, Supplier)
            .limit(limit)
            .offset(offset)
            .all()
        )

        open_statuses = [
            SupplierInvoiceStatus.POSTED,
            SupplierInvoiceStatus.PARTIALLY_PAID,
        ]
        stats_base = base_query.with_entities(SupplierInvoice)
        outstanding_filter = stats_base.filter(
            SupplierInvoice.status.in_(open_statuses)
        )

        total_outstanding = outstanding_filter.with_entities(
            func.coalesce(
                func.sum(SupplierInvoice.total_amount - SupplierInvoice.amount_paid), 0
            )
        ).scalar() or Decimal("0")

        past_due = outstanding_filter.filter(
            SupplierInvoice.due_date < today
        ).with_entities(
            func.coalesce(
                func.sum(SupplierInvoice.total_amount - SupplierInvoice.amount_paid), 0
            )
        ).scalar() or Decimal("0")

        due_this_week_end = today + timedelta(days=7)
        due_this_week = outstanding_filter.filter(
            SupplierInvoice.due_date >= today,
            SupplierInvoice.due_date <= due_this_week_end,
        ).with_entities(
            func.coalesce(
                func.sum(SupplierInvoice.total_amount - SupplierInvoice.amount_paid), 0
            )
        ).scalar() or Decimal("0")

        pending_count = (
            stats_base.filter(
                SupplierInvoice.status == SupplierInvoiceStatus.PENDING_APPROVAL
            )
            .with_entities(func.count(SupplierInvoice.invoice_id))
            .scalar()
            or 0
        )

        invoices_view = []
        for invoice, supplier in invoices:
            balance = invoice.total_amount - invoice.amount_paid
            invoices_view.append(
                {
                    "invoice_id": invoice.invoice_id,
                    "invoice_number": invoice.invoice_number,
                    "supplier_name": supplier_display_name(supplier),
                    "invoice_date": format_date(invoice.invoice_date),
                    "due_date": format_date(invoice.due_date),
                    "total_amount": format_currency(
                        invoice.total_amount, invoice.currency_code
                    ),
                    "balance": format_currency(balance, invoice.currency_code),
                    "status": invoice_status_label(invoice.status),
                    "is_overdue": (
                        invoice.due_date < today
                        and invoice.status
                        not in {SupplierInvoiceStatus.PAID, SupplierInvoiceStatus.VOID}
                    ),
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

        stats = InvoiceStats(
            total_outstanding=format_currency(total_outstanding) or "$0.00",
            past_due=format_currency(past_due) or "$0.00",
            due_this_week=format_currency(due_this_week) or "$0.00",
            pending_count=pending_count,
        )

        logger.debug("list_invoices_context: found %d invoices", total_count)

        active_filters = build_active_filters(
            params={
                "status": status,
                "supplier_id": supplier_id,
                "start_date": start_date,
                "end_date": end_date,
            },
            labels={"start_date": "From", "end_date": "To"},
            options={
                "supplier_id": {
                    str(s["supplier_id"]): s["supplier_name"] for s in suppliers_list
                }
            },
        )

        return {
            "invoices": invoices_view,
            "suppliers_list": suppliers_list,
            "stats": stats.__dict__,
            "search": search,
            "supplier_id": supplier_id,
            "status": status,
            "start_date": start_date,
            "end_date": end_date,
            "sort": sort,
            "sort_dir": sort_dir,
            "page": page,
            "limit": limit,
            "offset": offset,
            "total_count": total_count,
            "total_pages": total_pages,
            "active_filters": active_filters,
        }

    @staticmethod
    def invoice_form_context(
        db: Session,
        organization_id: str,
        supplier_id: str | None = None,
        po_id: str | None = None,
    ) -> dict:
        """Get context for invoice create form."""
        from app.models.finance.tax.tax_code import TaxCode
        from app.models.fixed_assets.asset_category import AssetCategory
        from app.models.inventory.item import Item

        logger.debug(
            "invoice_form_context: org=%s supplier_id=%s po_id=%s",
            organization_id,
            supplier_id,
            po_id,
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

        # Get tax codes that apply to purchases
        tax_codes = [
            {
                "tax_code_id": str(tax.tax_code_id),
                "tax_code": tax.tax_code,
                "tax_name": tax.tax_name,
                "tax_rate": float(tax.tax_rate),
                "rate_display": float((tax.tax_rate * 100).quantize(Decimal("0.01")))
                if tax.tax_rate < 1
                else float(tax.tax_rate),
                "is_inclusive": tax.is_inclusive,
                "is_compound": tax.is_compound,
                "is_recoverable": getattr(tax, "is_recoverable", True),
            }
            for tax in db.query(TaxCode)
            .filter(
                TaxCode.organization_id == org_id,
                TaxCode.is_active == True,
                TaxCode.applies_to_purchases == True,
            )
            .all()
        ]

        # Pre-populated data from PO
        selected_supplier = None
        selected_po = None
        po_lines = []

        if supplier_id:
            supplier_uuid = coerce_uuid(supplier_id)
            supplier = db.get(Supplier, supplier_uuid)
            if supplier and supplier.organization_id == org_id:
                selected_supplier = supplier_option_view(supplier)

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
                    "currency_code": po.currency_code,
                    "total_amount": float(po.total_amount) if po.total_amount else 0,
                }
                # If supplier not already set, use PO's supplier
                if not selected_supplier and supplier:
                    selected_supplier = supplier_option_view(supplier)

                # Get PO lines for pre-populating invoice lines
                lines = (
                    db.query(PurchaseOrderLine)
                    .filter(PurchaseOrderLine.po_id == po_uuid)
                    .order_by(PurchaseOrderLine.line_number)
                    .all()
                )
                for line in lines:
                    po_lines.append(
                        {
                            "line_id": str(line.line_id),
                            "line_number": line.line_number,
                            "description": line.description,
                            "quantity": float(line.quantity_ordered),
                            "unit_price": float(line.unit_price),
                            "amount": float(line.quantity_ordered * line.unit_price),
                            "expense_account_id": str(line.expense_account_id)
                            if line.expense_account_id
                            else "",
                        }
                    )

        # Get inventory items for AP -> INV integration
        items_list = [
            {
                "item_id": str(item.item_id),
                "item_code": item.item_code,
                "item_name": item.item_name,
                "unit_price": float(item.last_purchase_cost)
                if item.last_purchase_cost
                else 0,
                "uom": item.base_uom,
            }
            for item in db.query(Item)
            .filter(
                Item.organization_id == org_id,
                Item.is_active == True,
                Item.is_purchaseable == True,
            )
            .order_by(Item.item_code)
            .limit(200)
            .all()
        ]

        # Get asset accounts for capitalization (AP -> FA integration)
        asset_accounts = get_accounts(db, org_id, IFRSCategory.ASSETS)

        # Get asset categories for capitalization
        asset_categories = [
            {
                "category_id": str(cat.category_id),
                "category_code": cat.category_code,
                "category_name": cat.category_name,
                "threshold": float(cat.capitalization_threshold),
            }
            for cat in db.query(AssetCategory)
            .filter(
                AssetCategory.organization_id == org_id,
                AssetCategory.is_active == True,
            )
            .order_by(AssetCategory.category_code)
            .all()
        ]

        context = {
            "suppliers_list": suppliers_list,
            "expense_accounts": expense_accounts,
            "asset_accounts": asset_accounts,
            "items_list": items_list,
            "tax_codes": tax_codes,
            "asset_categories": asset_categories,
            "organization_id": organization_id,
            "user_id": "00000000-0000-0000-0000-000000000001",
            "selected_supplier": selected_supplier,
            "selected_po": selected_po,
            "po_lines": po_lines,
        }
        context.update(get_currency_context(db, organization_id))
        return context

    @staticmethod
    def invoice_detail_context(
        db: Session,
        organization_id: str,
        invoice_id: str,
    ) -> dict:
        """Get context for invoice detail page."""
        logger.debug(
            "invoice_detail_context: org=%s invoice_id=%s", organization_id, invoice_id
        )
        org_id = coerce_uuid(organization_id)
        invoice = None
        try:
            invoice = supplier_invoice_service.get(db, invoice_id)
        except Exception:
            invoice = None

        if not invoice or invoice.organization_id != org_id:
            return {"invoice": None, "supplier": None, "lines": []}

        supplier = None
        try:
            supplier = supplier_service.get(db, org_id, str(invoice.supplier_id))
        except Exception:
            supplier = None

        lines = supplier_invoice_service.get_invoice_lines(
            db,
            organization_id=org_id,
            invoice_id=invoice.invoice_id,
        )
        lines_view = [invoice_line_view(line, invoice.currency_code) for line in lines]

        # Get attachments
        attachments = attachment_service.list_for_entity(
            db,
            organization_id=org_id,
            entity_type="SUPPLIER_INVOICE",
            entity_id=invoice.invoice_id,
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

        logger.debug("invoice_detail_context: found %d lines", len(lines_view))

        return {
            "invoice": invoice_detail_view(invoice, supplier),
            "supplier": supplier_form_view(supplier) if supplier else None,
            "lines": lines_view,
            "attachments": attachments_view,
        }

    @staticmethod
    def delete_invoice(
        db: Session,
        organization_id: str,
        invoice_id: str,
    ) -> str | None:
        """Delete an invoice. Returns error message or None on success."""
        logger.debug(
            "delete_invoice: org=%s invoice_id=%s", organization_id, invoice_id
        )
        org_id = coerce_uuid(organization_id)
        inv_id = coerce_uuid(invoice_id)

        try:
            supplier_invoice_service.delete_invoice(db, org_id, inv_id)
            logger.info("delete_invoice: deleted invoice %s for org %s", inv_id, org_id)
            return None
        except HTTPException as exc:
            return exc.detail
        except Exception as e:
            logger.exception("delete_invoice: failed for org %s", org_id)
            return f"Failed to delete invoice: {str(e)}"

    # =====================================================================
    # HTTP Response Methods
    # =====================================================================

    def list_invoices_response(
        self,
        request: Request,
        auth: WebAuthContext,
        search: str | None,
        supplier_id: str | None,
        status: str | None,
        start_date: str | None,
        end_date: str | None,
        page: int,
        db: Session,
        sort: str | None = None,
        sort_dir: str | None = None,
    ) -> HTMLResponse:
        """Render invoice list page."""
        context = base_context(request, auth, "AP Invoices", "ap")
        context.update(
            self.list_invoices_context(
                db,
                str(auth.organization_id),
                search=search,
                supplier_id=supplier_id,
                status=status,
                start_date=start_date,
                end_date=end_date,
                page=page,
                sort=sort,
                sort_dir=sort_dir,
            )
        )
        return templates.TemplateResponse(request, "finance/ap/invoices.html", context)

    def invoice_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        supplier_id: str | None,
        po_id: str | None,
        db: Session,
    ) -> HTMLResponse:
        """Render new invoice form."""
        context = base_context(request, auth, "New AP Invoice", "ap")
        context.update(
            self.invoice_form_context(
                db,
                str(auth.organization_id),
                supplier_id=supplier_id,
                po_id=po_id,
            )
        )
        return templates.TemplateResponse(
            request, "finance/ap/invoice_form.html", context
        )

    def invoice_edit_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        invoice_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Render edit invoice form with existing invoice data."""
        org_id = coerce_uuid(auth.organization_id)
        inv_id = coerce_uuid(invoice_id)

        invoice = db.get(SupplierInvoice, inv_id)
        if not invoice or invoice.organization_id != org_id:
            return RedirectResponse(
                url="/finance/ap/invoices?error=Invoice+not+found",
                status_code=303,
            )

        if invoice.status != SupplierInvoiceStatus.DRAFT:
            return RedirectResponse(
                url=f"/finance/ap/invoices/{invoice_id}?error=Only+draft+invoices+can+be+edited",
                status_code=303,
            )

        context = base_context(request, auth, "Edit AP Invoice", "ap")
        context.update(
            self.invoice_form_context(
                db,
                str(auth.organization_id),
                supplier_id=str(invoice.supplier_id),
            )
        )

        lines = (
            db.query(SupplierInvoiceLine)
            .filter(SupplierInvoiceLine.invoice_id == inv_id)
            .order_by(SupplierInvoiceLine.line_number)
            .all()
        )

        context["invoice"] = {
            "invoice_id": invoice.invoice_id,
            "invoice_number": invoice.invoice_number,
            "supplier_id": invoice.supplier_id,
            "invoice_date": invoice.invoice_date,
            "received_date": invoice.received_date,
            "due_date": invoice.due_date,
            "currency_code": invoice.currency_code,
            "exchange_rate": str(invoice.exchange_rate)
            if invoice.exchange_rate
            else "",
            "lines": [
                {
                    "line_id": line.line_id,
                    "expense_account_id": line.expense_account_id,
                    "description": line.description,
                    "quantity": line.quantity,
                    "unit_price": line.unit_price,
                    "tax_code_id": line.tax_code_id,
                    "tax_amount": line.tax_amount,
                    "cost_center_id": line.cost_center_id,
                    "project_id": line.project_id,
                }
                for line in lines
            ],
        }

        return templates.TemplateResponse(
            request, "finance/ap/invoice_form.html", context
        )

    async def create_invoice_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | JSONResponse | RedirectResponse | dict:
        """Handle invoice creation form submission."""
        content_type = request.headers.get("content-type", "")
        org_id = auth.organization_id
        user_id = auth.person_id
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")

        if "application/json" in content_type:
            data = await request.json()
        else:
            form_data = await request.form()
            data = dict(form_data)

        try:
            input_data = self.build_invoice_input(db, data, org_id)

            invoice = supplier_invoice_service.create_invoice(
                db=db,
                organization_id=org_id,
                input=input_data,
                created_by_user_id=user_id,
            )

            if "application/json" in content_type:
                return {"success": True, "invoice_id": str(invoice.invoice_id)}

            return RedirectResponse(
                url="/finance/ap/invoices?success=Invoice+created+successfully",
                status_code=303,
            )

        except Exception as e:
            logger.exception("create_invoice_response: failed")
            if "application/json" in content_type:
                return JSONResponse(
                    status_code=400,
                    content={"detail": str(e)},
                )

            context = base_context(request, auth, "New AP Invoice", "ap")
            context.update(self.invoice_form_context(db, str(auth.organization_id)))
            context["error"] = str(e)
            context["form_data"] = data
            return templates.TemplateResponse(
                request, "finance/ap/invoice_form.html", context
            )

    async def update_invoice_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        invoice_id: str,
    ) -> HTMLResponse | JSONResponse | RedirectResponse | dict:
        """Handle invoice update form submission."""
        content_type = request.headers.get("content-type", "")
        org_id = auth.organization_id
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")

        if "application/json" in content_type:
            data = await request.json()
        else:
            form_data = await request.form()
            data = dict(form_data)

        try:
            input_data = self.build_invoice_input(db, data, org_id)

            invoice = supplier_invoice_service.update_invoice(
                db=db,
                organization_id=org_id,
                invoice_id=coerce_uuid(invoice_id),
                input=input_data,
            )

            if "application/json" in content_type:
                return JSONResponse(
                    content={"success": True, "invoice_id": str(invoice.invoice_id)}
                )

            return RedirectResponse(
                url=f"/finance/ap/invoices/{invoice.invoice_id}?success=Invoice+updated+successfully",
                status_code=303,
            )
        except Exception as e:
            logger.exception("update_invoice_response: failed")
            if "application/json" in content_type:
                return JSONResponse(status_code=400, content={"detail": str(e)})

            context = base_context(request, auth, "Edit AP Invoice", "ap")
            context.update(self.invoice_form_context(db, str(auth.organization_id)))
            context["error"] = str(e)
            context["form_data"] = data
            return templates.TemplateResponse(
                request, "finance/ap/invoice_form.html", context
            )

    def invoice_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        invoice_id: str,
    ) -> HTMLResponse:
        """Render invoice detail page."""
        context = base_context(request, auth, "AP Invoice Details", "ap")
        context.update(
            self.invoice_detail_context(
                db,
                str(auth.organization_id),
                invoice_id,
            )
        )
        return templates.TemplateResponse(
            request, "finance/ap/invoice_detail.html", context
        )

    def delete_invoice_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        invoice_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Handle invoice deletion."""
        error = self.delete_invoice(db, str(auth.organization_id), invoice_id)

        if error:
            context = base_context(request, auth, "AP Invoice Details", "ap")
            context.update(
                self.invoice_detail_context(
                    db,
                    str(auth.organization_id),
                    invoice_id,
                )
            )
            context["error"] = error
            return templates.TemplateResponse(
                request, "finance/ap/invoice_detail.html", context
            )

        return RedirectResponse(
            url="/finance/ap/invoices?success=Record+deleted+successfully",
            status_code=303,
        )

    def submit_invoice_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        invoice_id: str,
    ) -> RedirectResponse:
        """Submit invoice for approval."""
        try:
            supplier_invoice_service.submit_invoice(
                db=db,
                organization_id=auth.organization_id,
                invoice_id=coerce_uuid(invoice_id),
                submitted_by_user_id=auth.user_id,
            )
            return RedirectResponse(
                url=f"/finance/ap/invoices/{invoice_id}?success=Invoice+submitted+for+approval",
                status_code=303,
            )
        except Exception as e:
            return RedirectResponse(
                url=f"/finance/ap/invoices/{invoice_id}?error={str(e)}",
                status_code=303,
            )

    def approve_invoice_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        invoice_id: str,
    ) -> RedirectResponse:
        """Approve a submitted invoice."""
        try:
            supplier_invoice_service.approve_invoice(
                db=db,
                organization_id=auth.organization_id,
                invoice_id=coerce_uuid(invoice_id),
                approved_by_user_id=auth.user_id,
            )
            return RedirectResponse(
                url=f"/finance/ap/invoices/{invoice_id}?success=Invoice+approved",
                status_code=303,
            )
        except Exception as e:
            return RedirectResponse(
                url=f"/finance/ap/invoices/{invoice_id}?error={str(e)}",
                status_code=303,
            )

    def post_invoice_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        invoice_id: str,
    ) -> RedirectResponse:
        """Post invoice to general ledger."""
        try:
            supplier_invoice_service.post_invoice(
                db=db,
                organization_id=auth.organization_id,
                invoice_id=coerce_uuid(invoice_id),
                posted_by_user_id=auth.user_id,
            )
            return RedirectResponse(
                url=f"/finance/ap/invoices/{invoice_id}?success=Invoice+posted+to+ledger",
                status_code=303,
            )
        except Exception as e:
            return RedirectResponse(
                url=f"/finance/ap/invoices/{invoice_id}?error={str(e)}",
                status_code=303,
            )

    def void_invoice_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        invoice_id: str,
    ) -> RedirectResponse:
        """Void an invoice."""
        try:
            supplier_invoice_service.void_invoice(
                db=db,
                organization_id=auth.organization_id,
                invoice_id=coerce_uuid(invoice_id),
                voided_by_user_id=auth.user_id,
                reason="Voided via web interface",
            )
            return RedirectResponse(
                url=f"/finance/ap/invoices/{invoice_id}?success=Invoice+voided",
                status_code=303,
            )
        except Exception as e:
            return RedirectResponse(
                url=f"/finance/ap/invoices/{invoice_id}?error={str(e)}",
                status_code=303,
            )

    async def upload_invoice_attachment_response(
        self,
        invoice_id: str,
        file: UploadFile,
        description: str | None,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Handle invoice attachment upload."""
        try:
            org_id = auth.organization_id
            user_id = auth.person_id
            if org_id is None:
                raise HTTPException(status_code=401, detail="Authentication required")
            if user_id is None:
                raise HTTPException(status_code=401, detail="Authentication required")
            invoice = supplier_invoice_service.get(db, invoice_id)
            if not invoice or invoice.organization_id != auth.organization_id:
                return RedirectResponse(
                    url=f"/finance/ap/invoices/{invoice_id}?error=Invoice+not+found",
                    status_code=303,
                )

            input_data = AttachmentInput(
                entity_type="SUPPLIER_INVOICE",
                entity_id=invoice_id,
                file_name=file.filename or "unnamed",
                content_type=file.content_type or "application/octet-stream",
                category=AttachmentCategory.INVOICE,
                description=description,
            )

            attachment_service.save_file(
                db=db,
                organization_id=org_id,
                input=input_data,
                file_content=file.file,
                uploaded_by=user_id,
            )

            return RedirectResponse(
                url=f"/finance/ap/invoices/{invoice_id}?success=Attachment+uploaded",
                status_code=303,
            )

        except ValueError as e:
            return RedirectResponse(
                url=f"/finance/ap/invoices/{invoice_id}?error={str(e)}",
                status_code=303,
            )
        except Exception:
            logger.exception("upload_invoice_attachment_response: failed")
            return RedirectResponse(
                url=f"/finance/ap/invoices/{invoice_id}?error=Upload+failed",
                status_code=303,
            )


# Singleton instance
invoice_web_service = InvoiceWebService()
