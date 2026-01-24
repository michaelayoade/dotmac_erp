"""
AR Invoice Web Service - Invoice-related web view methods.

Provides view-focused data and operations for AR invoice web routes.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.config import settings
from app.models.finance.ar.customer import Customer
from app.models.finance.ar.invoice import Invoice, InvoiceStatus, InvoiceType
from app.models.finance.ar.invoice_line import InvoiceLine
from app.models.finance.ar.payment_allocation import PaymentAllocation
from app.models.finance.common.attachment import AttachmentCategory
from app.models.finance.gl.account_category import IFRSCategory
from app.services.common import coerce_uuid
from app.services.finance.ar.customer import customer_service
from app.services.finance.ar.invoice import ARInvoiceInput, ARInvoiceLineInput, ar_invoice_service
from app.services.finance.common.attachment import attachment_service, AttachmentInput
from app.services.finance.platform.currency_context import get_currency_context
from app.services.finance.tax.tax_master import tax_code_service
from app.templates import templates
from app.web.deps import base_context, WebAuthContext
from app.services.finance.ar.web.base import (
    parse_date,
    parse_invoice_status,
    format_date,
    format_currency,
    format_file_size,
    customer_display_name,
    customer_option_view,
    customer_form_view,
    invoice_status_label,
    invoice_line_view,
    invoice_detail_view,
    get_accounts,
    get_cost_centers,
    get_projects,
    InvoiceStats,
)

logger = logging.getLogger(__name__)


class InvoiceWebService:
    """Web service methods for AR invoices."""

    @staticmethod
    def build_invoice_input(data: dict) -> ARInvoiceInput:
        """Build ARInvoiceInput from form data."""
        lines_data = data.get("lines", [])
        if isinstance(lines_data, str):
            lines_data = json.loads(lines_data)

        lines = []
        for line in lines_data:
            if line.get("revenue_account_id") and line.get("description"):
                # Handle both new tax_code_ids array and legacy tax_code_id field
                tax_code_ids = []
                if line.get("tax_code_ids"):
                    tax_code_ids = [UUID(tc_id) for tc_id in line["tax_code_ids"] if tc_id]
                legacy_tax_code_id = UUID(line["tax_code_id"]) if line.get("tax_code_id") else None

                lines.append(
                    ARInvoiceLineInput(
                        description=line.get("description", ""),
                        quantity=Decimal(str(line.get("quantity", 1))),
                        unit_price=Decimal(str(line.get("unit_price", 0))),
                        revenue_account_id=UUID(line["revenue_account_id"])
                        if line.get("revenue_account_id")
                        else None,
                        tax_code_ids=tax_code_ids,
                        tax_code_id=legacy_tax_code_id,
                        cost_center_id=UUID(line["cost_center_id"])
                        if line.get("cost_center_id")
                        else None,
                        project_id=UUID(line["project_id"]) if line.get("project_id") else None,
                    )
                )

        invoice_date = parse_date(data.get("invoice_date")) or date.today()
        due_date = parse_date(data.get("due_date")) or invoice_date

        return ARInvoiceInput(
            customer_id=UUID(data["customer_id"]),
            invoice_type=InvoiceType.STANDARD,
            invoice_date=invoice_date,
            due_date=due_date,
            currency_code=data.get(
                "currency_code",
                settings.default_functional_currency_code,
            ),
            notes=data.get("terms"),
            internal_notes=data.get("notes"),
            lines=lines,
        )

    @staticmethod
    def list_invoices_context(
        db: Session,
        organization_id: str,
        search: Optional[str],
        customer_id: Optional[str],
        status: Optional[str],
        start_date: Optional[str],
        end_date: Optional[str],
        page: int,
        limit: int = 50,
    ) -> dict:
        """Get context for invoice listing page."""
        logger.debug(
            "list_invoices_context: org=%s search=%r customer_id=%s status=%s page=%d",
            organization_id, search, customer_id, status, page
        )
        org_id = coerce_uuid(organization_id)
        offset = (page - 1) * limit
        today = date.today()

        status_value = parse_invoice_status(status)
        from_date = parse_date(start_date)
        to_date = parse_date(end_date)

        query = (
            db.query(Invoice, Customer)
            .join(Customer, Invoice.customer_id == Customer.customer_id)
            .filter(Invoice.organization_id == org_id)
        )

        if customer_id:
            query = query.filter(Invoice.customer_id == coerce_uuid(customer_id))
        if status_value:
            query = query.filter(Invoice.status == status_value)
        if from_date:
            query = query.filter(Invoice.invoice_date >= from_date)
        if to_date:
            query = query.filter(Invoice.invoice_date <= to_date)
        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                or_(
                    Invoice.invoice_number.ilike(search_pattern),
                    Customer.legal_name.ilike(search_pattern),
                    Customer.trading_name.ilike(search_pattern),
                )
            )

        total_count = query.with_entities(func.count(Invoice.invoice_id)).scalar() or 0
        invoices = (
            query.order_by(Invoice.invoice_date.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )

        open_statuses = [
            InvoiceStatus.POSTED,
            InvoiceStatus.PARTIALLY_PAID,
            InvoiceStatus.OVERDUE,
        ]
        stats_base = query.with_entities(Invoice)
        outstanding_filter = stats_base.filter(Invoice.status.in_(open_statuses))

        total_outstanding = (
            outstanding_filter.with_entities(
                func.coalesce(
                    func.sum(Invoice.total_amount - Invoice.amount_paid), 0
                )
            ).scalar()
            or Decimal("0")
        )

        past_due = (
            outstanding_filter.filter(Invoice.due_date < today)
            .with_entities(
                func.coalesce(
                    func.sum(Invoice.total_amount - Invoice.amount_paid), 0
                )
            )
            .scalar()
            or Decimal("0")
        )

        due_this_week_end = today + timedelta(days=7)
        due_this_week = (
            outstanding_filter.filter(
                Invoice.due_date >= today,
                Invoice.due_date <= due_this_week_end,
            )
            .with_entities(
                func.coalesce(
                    func.sum(Invoice.total_amount - Invoice.amount_paid), 0
                )
            )
            .scalar()
            or Decimal("0")
        )

        month_start = date(today.year, today.month, 1)
        if today.month == 12:
            month_end = date(today.year + 1, 1, 1) - timedelta(days=1)
        else:
            month_end = date(today.year, today.month + 1, 1) - timedelta(days=1)

        this_month = (
            outstanding_filter.filter(
                Invoice.due_date >= month_start,
                Invoice.due_date <= month_end,
            )
            .with_entities(
                func.coalesce(
                    func.sum(Invoice.total_amount - Invoice.amount_paid), 0
                )
            )
            .scalar()
            or Decimal("0")
        )

        invoices_view = []
        for invoice, customer in invoices:
            balance = invoice.total_amount - invoice.amount_paid
            invoices_view.append(
                {
                    "invoice_id": invoice.invoice_id,
                    "invoice_number": invoice.invoice_number,
                    "customer_name": customer_display_name(customer),
                    "invoice_date": format_date(invoice.invoice_date),
                    "due_date": format_date(invoice.due_date),
                    "total_amount": format_currency(
                        invoice.total_amount, invoice.currency_code
                    ),
                    "balance": format_currency(balance, invoice.currency_code),
                    "status": invoice_status_label(invoice.status),
                    "is_overdue": (
                        invoice.due_date < today
                        and invoice.status not in {InvoiceStatus.PAID, InvoiceStatus.VOID}
                    ),
                }
            )

        customers_list = [
            customer_option_view(customer)
            for customer in customer_service.list(
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
            this_month=format_currency(this_month) or "$0.00",
        )

        logger.debug("list_invoices_context: found %d invoices", total_count)

        return {
            "invoices": invoices_view,
            "customers_list": customers_list,
            "stats": stats.__dict__,
            "search": search,
            "customer_id": customer_id,
            "status": status,
            "start_date": start_date,
            "end_date": end_date,
            "page": page,
            "limit": limit,
            "offset": offset,
            "total_count": total_count,
            "total_pages": total_pages,
        }

    @staticmethod
    def invoice_form_context(
        db: Session,
        organization_id: str,
    ) -> dict:
        """Get context for invoice create form."""
        logger.debug("invoice_form_context: org=%s", organization_id)
        org_id = coerce_uuid(organization_id)
        customers_list = [
            customer_option_view(customer)
            for customer in customer_service.list(
                db,
                organization_id=org_id,
                is_active=True,
                limit=200,
            )
        ]

        revenue_accounts = get_accounts(db, org_id, IFRSCategory.REVENUE)

        tax_codes = [
            {
                "tax_code_id": str(tax.tax_code_id),
                "tax_code": tax.tax_code,
                "tax_name": tax.tax_name,
                "tax_rate": tax.tax_rate,
                "rate": (tax.tax_rate * 100).quantize(Decimal("0.01")) if tax.tax_rate < 1 else tax.tax_rate,
                "is_inclusive": tax.is_inclusive,
                "is_compound": tax.is_compound,
            }
            for tax in tax_code_service.list(
                db,
                organization_id=org_id,
                is_active=True,
                applies_to_sales=True,
                limit=200,
            )
        ]

        context = {
            "customers_list": customers_list,
            "revenue_accounts": revenue_accounts,
            "tax_codes": tax_codes,
            "cost_centers": get_cost_centers(db, org_id),
            "projects": get_projects(db, org_id),
            "organization_id": str(organization_id),
            "user_id": "00000000-0000-0000-0000-000000000001",
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
            "invoice_detail_context: org=%s invoice_id=%s",
            organization_id, invoice_id
        )
        org_id = coerce_uuid(organization_id)
        invoice = None
        try:
            invoice = ar_invoice_service.get(db, org_id, invoice_id)
        except Exception:
            invoice = None

        if not invoice or invoice.organization_id != org_id:
            return {"invoice": None, "customer": None, "lines": []}

        customer = None
        try:
            customer = customer_service.get(db, org_id, str(invoice.customer_id))
        except Exception:
            customer = None

        lines = ar_invoice_service.get_invoice_lines(
            db,
            organization_id=org_id,
            invoice_id=invoice.invoice_id,
        )
        lines_view = [
            invoice_line_view(line, invoice.currency_code) for line in lines
        ]

        # Get attachments
        attachments = attachment_service.list_for_entity(
            db,
            organization_id=org_id,
            entity_type="CUSTOMER_INVOICE",
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
                "download_url": f"/ar/attachments/{att.attachment_id}/download",
            }
            for att in attachments
        ]

        logger.debug("invoice_detail_context: found %d lines", len(lines_view))

        return {
            "invoice": invoice_detail_view(invoice, customer),
            "customer": customer_form_view(customer) if customer else None,
            "lines": lines_view,
            "attachments": attachments_view,
        }

    @staticmethod
    def delete_invoice(
        db: Session,
        organization_id: str,
        invoice_id: str,
    ) -> Optional[str]:
        """Delete an invoice. Returns error message or None on success."""
        logger.debug(
            "delete_invoice: org=%s invoice_id=%s",
            organization_id, invoice_id
        )
        org_id = coerce_uuid(organization_id)
        inv_id = coerce_uuid(invoice_id)

        invoice = db.get(Invoice, inv_id)
        if not invoice or invoice.organization_id != org_id:
            return "Invoice not found"

        # Only DRAFT invoices can be deleted
        if invoice.status != InvoiceStatus.DRAFT:
            return f"Cannot delete invoice with status '{invoice.status.value}'. Only DRAFT invoices can be deleted."

        # Check for existing payment allocations
        allocation_count = (
            db.query(func.count(PaymentAllocation.allocation_id))
            .filter(PaymentAllocation.invoice_id == inv_id)
            .scalar()
            or 0
        )

        if allocation_count > 0:
            return f"Cannot delete invoice with {allocation_count} payment allocation(s)."

        try:
            # Delete invoice lines first
            db.query(InvoiceLine).filter(
                InvoiceLine.invoice_id == inv_id
            ).delete()
            db.delete(invoice)
            db.commit()
            logger.info("delete_invoice: deleted invoice %s for org %s", inv_id, org_id)
            return None
        except Exception as e:
            db.rollback()
            logger.exception("delete_invoice: failed for org %s", org_id)
            return f"Failed to delete invoice: {str(e)}"

    # =====================================================================
    # HTTP Response Methods
    # =====================================================================

    def list_invoices_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        search: Optional[str],
        customer_id: Optional[str],
        status: Optional[str],
        start_date: Optional[str],
        end_date: Optional[str],
        page: int,
    ) -> HTMLResponse:
        """Render invoice list page."""
        context = base_context(request, auth, "AR Invoices", "ar")
        context.update(
            self.list_invoices_context(
                db,
                str(auth.organization_id),
                search=search,
                customer_id=customer_id,
                status=status,
                start_date=start_date,
                end_date=end_date,
                page=page,
            )
        )
        return templates.TemplateResponse(request, "finance/ar/invoices.html", context)

    def invoice_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """Render new invoice form."""
        context = base_context(request, auth, "New AR Invoice", "ar")
        context.update(self.invoice_form_context(db, str(auth.organization_id)))
        return templates.TemplateResponse(request, "finance/ar/invoice_form.html", context)

    async def create_invoice_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | JSONResponse | RedirectResponse | dict:
        """Handle invoice creation form submission."""
        content_type = request.headers.get("content-type", "")

        if "application/json" in content_type:
            data = await request.json()
        else:
            form_data = await request.form()
            data = dict(form_data)

        try:
            input_data = self.build_invoice_input(data)

            invoice = ar_invoice_service.create_invoice(
                db=db,
                organization_id=auth.organization_id,
                input=input_data,
                created_by_user_id=auth.user_id,
            )

            if "application/json" in content_type:
                return {"success": True, "invoice_id": str(invoice.invoice_id)}

            return RedirectResponse(
                url="/finance/ar/invoices?success=Invoice+created+successfully",
                status_code=303,
            )

        except Exception as e:
            logger.exception("create_invoice_response: failed")
            if "application/json" in content_type:
                return JSONResponse(
                    status_code=400,
                    content={"detail": str(e)},
                )

            context = base_context(request, auth, "New AR Invoice", "ar")
            context.update(self.invoice_form_context(db, str(auth.organization_id)))
            context["error"] = str(e)
            context["form_data"] = data
            return templates.TemplateResponse(request, "finance/ar/invoice_form.html", context)

    def invoice_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        invoice_id: str,
    ) -> HTMLResponse:
        """Render invoice detail page."""
        context = base_context(request, auth, "AR Invoice Details", "ar")
        context.update(
            self.invoice_detail_context(
                db,
                str(auth.organization_id),
                invoice_id,
            )
        )
        return templates.TemplateResponse(request, "finance/ar/invoice_detail.html", context)

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
            context = base_context(request, auth, "AR Invoice Details", "ar")
            context.update(
                self.invoice_detail_context(
                    db,
                    str(auth.organization_id),
                    invoice_id,
                )
            )
            context["error"] = error
            return templates.TemplateResponse(request, "finance/ar/invoice_detail.html", context)

        return RedirectResponse(url="/finance/ar/invoices", status_code=303)

    async def upload_invoice_attachment_response(
        self,
        invoice_id: str,
        file: UploadFile,
        description: Optional[str],
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Handle invoice attachment upload."""
        try:
            invoice = ar_invoice_service.get(db, auth.organization_id, invoice_id)
            if not invoice or invoice.organization_id != auth.organization_id:
                return RedirectResponse(
                    url=f"/ar/invoices/{invoice_id}?error=Invoice+not+found",
                    status_code=303,
                )

            input_data = AttachmentInput(
                entity_type="CUSTOMER_INVOICE",
                entity_id=invoice_id,
                file_name=file.filename or "unnamed",
                content_type=file.content_type or "application/octet-stream",
                category=AttachmentCategory.CUSTOMER_INVOICE,
                description=description,
            )

            attachment_service.save_file(
                db=db,
                organization_id=auth.organization_id,
                input=input_data,
                file_content=file.file,
                uploaded_by=auth.person_id,
            )

            return RedirectResponse(
                url=f"/ar/invoices/{invoice_id}?success=Attachment+uploaded",
                status_code=303,
            )

        except ValueError as e:
            return RedirectResponse(
                url=f"/ar/invoices/{invoice_id}?error={str(e)}",
                status_code=303,
            )
        except Exception:
            logger.exception("upload_invoice_attachment_response: failed")
            return RedirectResponse(
                url=f"/ar/invoices/{invoice_id}?error=Upload+failed",
                status_code=303,
            )
