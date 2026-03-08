"""
AR Invoice Web Service - Invoice-related web view methods.

Provides view-focused data and operations for AR invoice web routes.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.finance.ar.customer import Customer
from app.models.finance.ar.invoice import Invoice, InvoiceStatus
from app.models.finance.ar.invoice_line_tax import InvoiceLineTax
from app.models.finance.common.attachment import AttachmentCategory
from app.models.finance.gl.account_category import IFRSCategory
from app.models.finance.tax.tax_code import TaxCode, TaxType
from app.models.inventory.item import Item
from app.services.common import coerce_uuid
from app.services.common_filters import build_active_filters
from app.services.finance.ar.customer import customer_service
from app.services.finance.ar.invoice import ARInvoiceInput, ar_invoice_service
from app.services.finance.ar.web.base import (
    InvoiceStats,
    customer_display_name,
    customer_form_view,
    customer_option_view,
    format_currency,
    format_date,
    format_file_size,
    get_accounts,
    get_cost_centers,
    get_projects,
    invoice_detail_view,
    invoice_line_view,
    invoice_status_label,
    normalize_date_range_filters,
)
from app.services.finance.common.attachment import AttachmentInput, attachment_service
from app.services.finance.platform.currency_context import get_currency_context
from app.services.finance.tax.tax_master import tax_code_service
from app.services.recent_activity import get_recent_activity
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

logger = logging.getLogger(__name__)


class InvoiceWebService:
    """Web service methods for AR invoices."""

    @staticmethod
    def build_invoice_input(
        db: Session, data: dict, organization_id: UUID
    ) -> ARInvoiceInput:
        """Build ARInvoiceInput from form data via service helper."""
        payload = dict(data)
        return ar_invoice_service.build_input_from_payload(
            db=db,
            organization_id=organization_id,
            payload=payload,
        )

    @staticmethod
    def list_invoices_context(
        db: Session,
        organization_id: str,
        search: str | None,
        customer_id: str | None,
        status: str | None,
        start_date: str | None,
        end_date: str | None,
        page: int,
        sort: str | None = None,
        sort_dir: str | None = None,
        limit: int = 50,
    ) -> dict:
        """Get context for invoice listing page."""
        logger.debug(
            "list_invoices_context: org=%s search=%r customer_id=%s status=%s page=%d",
            organization_id,
            search,
            customer_id,
            status,
            page,
        )
        offset = (page - 1) * limit
        today = date.today()
        org_id = coerce_uuid(organization_id)

        from app.services.finance.ar.invoice_query import build_invoice_query

        query = build_invoice_query(
            db=db,
            organization_id=organization_id,
            search=search,
            customer_id=customer_id,
            status=status,
            start_date=start_date,
            end_date=end_date,
        )

        total_count = query.with_entities(func.count(Invoice.invoice_id)).scalar() or 0

        sort_dir_norm = (sort_dir or "desc").lower()
        if sort_dir_norm not in {"asc", "desc"}:
            sort_dir_norm = "desc"

        order_map = {
            "invoice_date": Invoice.invoice_date,
            "invoice_number": Invoice.invoice_number,
            "customer_name": Customer.legal_name,
            "due_date": Invoice.due_date,
            "total_amount": Invoice.total_amount,
            "status": Invoice.status,
        }
        order_col = order_map.get(sort or "", Invoice.invoice_date)
        order_expr = order_col.asc() if sort_dir_norm == "asc" else order_col.desc()

        invoices = (
            query.with_entities(Invoice, Customer)
            .order_by(order_expr, Invoice.invoice_date.desc())
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

        total_outstanding = outstanding_filter.with_entities(
            func.coalesce(func.sum(Invoice.total_amount - Invoice.amount_paid), 0)
        ).scalar() or Decimal("0")

        past_due = outstanding_filter.filter(Invoice.due_date < today).with_entities(
            func.coalesce(func.sum(Invoice.total_amount - Invoice.amount_paid), 0)
        ).scalar() or Decimal("0")

        due_this_week_end = today + timedelta(days=7)
        due_this_week = outstanding_filter.filter(
            Invoice.due_date >= today,
            Invoice.due_date <= due_this_week_end,
        ).with_entities(
            func.coalesce(func.sum(Invoice.total_amount - Invoice.amount_paid), 0)
        ).scalar() or Decimal("0")

        month_start = date(today.year, today.month, 1)
        if today.month == 12:
            month_end = date(today.year + 1, 1, 1) - timedelta(days=1)
        else:
            month_end = date(today.year, today.month + 1, 1) - timedelta(days=1)

        this_month = outstanding_filter.filter(
            Invoice.due_date >= month_start,
            Invoice.due_date <= month_end,
        ).with_entities(
            func.coalesce(func.sum(Invoice.total_amount - Invoice.amount_paid), 0)
        ).scalar() or Decimal("0")

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
                    "tax_amount": format_currency(
                        invoice.tax_amount, invoice.currency_code
                    ),
                    "balance": format_currency(balance, invoice.currency_code),
                    "status": invoice_status_label(invoice.status),
                    "is_overdue": (
                        invoice.due_date < today
                        and invoice.status
                        not in {InvoiceStatus.PAID, InvoiceStatus.VOID}
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

        active_filters = build_active_filters(
            params={
                "status": status,
                "customer_id": customer_id,
                "start_date": start_date,
                "end_date": end_date,
            },
            labels={"start_date": "From", "end_date": "To"},
        )
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
            "active_filters": active_filters,
            "sort": sort or "",
            "sort_dir": sort_dir_norm,
        }

    @staticmethod
    def invoice_form_context(
        db: Session,
        organization_id: str,
        customer_id: str | None = None,
        user_id: str | None = None,
    ) -> dict:
        """Get context for invoice create form."""
        logger.debug(
            "invoice_form_context: org=%s customer_id=%s",
            organization_id,
            customer_id,
        )
        org_id = coerce_uuid(organization_id)
        customers = list(
            customer_service.list(
                db,
                organization_id=org_id,
                is_active=True,
                limit=10000,
            )
        )

        selected_customer = None
        if customer_id:
            try:
                selected_customer = customer_service.get(db, org_id, customer_id)
            except Exception:
                selected_customer = None

        if selected_customer and all(
            existing.customer_id != selected_customer.customer_id
            for existing in customers
        ):
            customers.append(selected_customer)

        customers_list = [customer_option_view(customer) for customer in customers]

        revenue_accounts = get_accounts(db, org_id, IFRSCategory.REVENUE)

        tax_codes = [
            {
                "tax_code_id": str(tax.tax_code_id),
                "tax_code": tax.tax_code,
                "tax_name": tax.tax_name,
                "tax_rate": tax.tax_rate,
                "rate": (tax.tax_rate * 100).quantize(Decimal("0.01"))
                if tax.tax_rate < 1
                else tax.tax_rate,
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

        items = list(
            db.scalars(
                select(Item)
                .where(
                    Item.organization_id == org_id,
                    Item.is_active.is_(True),
                    Item.is_saleable.is_(True),
                )
                .order_by(Item.item_code)
            ).all()
        )
        item_options = [
            {
                "item_id": str(i.item_id),
                "item_code": i.item_code,
                "item_name": i.item_name,
                "list_price": float(i.list_price) if i.list_price is not None else None,
                "revenue_account_id": str(i.revenue_account_id)
                if i.revenue_account_id
                else None,
                "tax_code_id": str(i.tax_code_id) if i.tax_code_id else None,
            }
            for i in items
        ]

        context: dict = {
            "customers_list": customers_list,
            "revenue_accounts": revenue_accounts,
            "tax_codes": tax_codes,
            "items": item_options,
            "cost_centers": get_cost_centers(db, org_id),
            "projects": get_projects(db, org_id),
            "organization_id": str(organization_id),
            "user_id": user_id or "00000000-0000-0000-0000-000000000001",
            "selected_customer_id": "",
            "locked_customer": False,
        }
        context.update(get_currency_context(db, organization_id))

        # Pre-select customer from query param
        if customer_id:
            context["selected_customer_id"] = customer_id
            context["locked_customer"] = True

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

        # Resolve payment terms name
        payment_terms_name = None
        if invoice.payment_terms_id:
            from app.models.finance.ar.payment_terms import PaymentTerms

            pt = db.get(PaymentTerms, invoice.payment_terms_id)
            if pt:
                payment_terms_name = pt.terms_name

        lines = ar_invoice_service.get_invoice_lines(
            db,
            organization_id=org_id,
            invoice_id=invoice.invoice_id,
        )
        lines_view = [invoice_line_view(line, invoice.currency_code) for line in lines]

        # Enrich lines with tax metadata and VAT-per-line display values.
        primary_tax_ids = {line.tax_code_id for line in lines if line.tax_code_id}
        tax_map: dict[UUID, TaxCode] = {}
        if primary_tax_ids:
            tax_codes = list(
                db.scalars(
                    select(TaxCode).where(
                        TaxCode.organization_id == org_id,
                        TaxCode.tax_code_id.in_(primary_tax_ids),
                    )
                ).all()
            )
            tax_map = {tax.tax_code_id: tax for tax in tax_codes}

        vat_by_line: dict[UUID, Decimal] = {}
        vat_labels_by_line: dict[UUID, set[str]] = {}
        line_ids = [line.line_id for line in lines]
        if line_ids:
            vat_taxes = db.execute(
                select(InvoiceLineTax, TaxCode)
                .join(TaxCode, TaxCode.tax_code_id == InvoiceLineTax.tax_code_id)
                .where(
                    InvoiceLineTax.line_id.in_(line_ids),
                    TaxCode.organization_id == org_id,
                    TaxCode.tax_type.in_([TaxType.VAT, TaxType.GST]),
                )
            ).all()
            for line_tax, tax_code in vat_taxes:
                vat_by_line[line_tax.line_id] = (
                    vat_by_line.get(line_tax.line_id, Decimal("0"))
                    + line_tax.tax_amount
                )
                rate_label = (
                    f"{(line_tax.tax_rate * 100).quantize(Decimal('0.01'))}%"
                    if line_tax.tax_rate < 1
                    else f"{line_tax.tax_rate}%"
                )
                incl_suffix = " Incl." if line_tax.is_inclusive else ""
                vat_labels_by_line.setdefault(line_tax.line_id, set()).add(
                    f"{tax_code.tax_code} {rate_label}{incl_suffix}"
                )

        for idx, line in enumerate(lines):
            line_view = lines_view[idx]
            tax = tax_map.get(line.tax_code_id) if line.tax_code_id else None
            if tax:
                line_view["tax_code"] = tax.tax_code
                line_view["tax_name"] = tax.tax_name
                line_view["tax_type"] = tax.tax_type.value

            vat_amount = vat_by_line.get(line.line_id, Decimal("0"))
            if vat_amount == 0 and tax and tax.tax_type in {TaxType.VAT, TaxType.GST}:
                vat_amount = line.tax_amount
                rate_label = (
                    f"{(tax.tax_rate * 100).quantize(Decimal('0.01'))}%"
                    if tax.tax_rate < 1
                    else f"{tax.tax_rate}%"
                )
                incl_suffix = " Incl." if tax.is_inclusive else ""
                vat_labels_by_line.setdefault(line.line_id, set()).add(
                    f"{tax.tax_code} {rate_label}{incl_suffix}"
                )

            if vat_amount > 0:
                line_view["vat_amount_raw"] = float(vat_amount)
                line_view["vat_amount"] = format_currency(
                    vat_amount, invoice.currency_code
                )
                line_view["vat_label"] = ", ".join(
                    sorted(vat_labels_by_line[line.line_id])
                )
            else:
                line_view["vat_amount_raw"] = 0.0
                line_view["vat_amount"] = None
                line_view["vat_label"] = None

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
                "download_url": f"/finance/ar/attachments/{att.attachment_id}/download",
            }
            for att in attachments
        ]

        logger.debug("invoice_detail_context: found %d lines", len(lines_view))

        invoice_view = invoice_detail_view(invoice, customer)
        if payment_terms_name:
            invoice_view["payment_terms"] = payment_terms_name

        # Fetch org TIN for display on invoice document
        org_tin: str | None = None
        try:
            from app.models.finance.core_org.organization import Organization

            org = db.get(Organization, org_id)
            if org:
                org_tin = org.tax_identification_number
        except (AttributeError, TypeError):
            pass  # org TIN lookup is best-effort

        return {
            "invoice": invoice_view,
            "customer": customer_form_view(customer) if customer else None,
            "lines": lines_view,
            "attachments": attachments_view,
            "org_tin": org_tin,
            "recent_activity": get_recent_activity(
                db,
                org_id,
                table_schema="ar",
                table_name="invoice",
                record_id=str(invoice.invoice_id),
                limit=10,
            ),
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
            ar_invoice_service.delete_invoice(db, org_id, inv_id)
            logger.info("delete_invoice: deleted invoice %s for org %s", inv_id, org_id)
            return None
        except HTTPException as exc:
            return str(exc.detail)
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
        db: Session,
        search: str | None,
        customer_id: str | None,
        status: str | None,
        start_date: str | None,
        end_date: str | None,
        page: int,
        sort: str | None = None,
        sort_dir: str | None = None,
    ) -> HTMLResponse:
        """Render invoice list page."""
        start_date, end_date = normalize_date_range_filters(
            start_date,
            end_date,
            request.query_params,
        )
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
                sort=sort,
                sort_dir=sort_dir,
            )
        )
        return templates.TemplateResponse(request, "finance/ar/invoices.html", context)

    def invoice_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        customer_id: str | None = None,
        duplicate_from: str | None = None,
    ) -> HTMLResponse:
        """Render new invoice form, optionally pre-filled from a source invoice."""
        context = base_context(request, auth, "New AR Invoice", "ar")
        context.update(
            self.invoice_form_context(
                db, str(auth.organization_id), customer_id=customer_id,
                user_id=str(auth.user_id) if auth.user_id else None,
            )
        )

        if duplicate_from:
            dup_ctx = self._duplicate_invoice_context(
                db, str(auth.organization_id), duplicate_from
            )
            if dup_ctx:
                context["duplicate_source"] = dup_ctx
                # Pre-select + lock the customer from the source invoice
                context["selected_customer_id"] = dup_ctx["customer_id"]
                context["locked_customer"] = True

        return templates.TemplateResponse(
            request, "finance/ar/invoice_form.html", context
        )

    @staticmethod
    def _duplicate_invoice_context(
        db: Session,
        organization_id: str,
        invoice_id: str,
    ) -> dict | None:
        """Build pre-fill context from an existing invoice for duplication.

        Returns a dict with customer, line items, and metadata from the
        source invoice — but no invoice_id/invoice_number so the form
        creates a new invoice.
        """
        from app.models.finance.ar.invoice_line import InvoiceLine

        org_id = coerce_uuid(organization_id)
        inv_id = coerce_uuid(invoice_id)

        invoice = db.get(Invoice, inv_id)
        if not invoice or invoice.organization_id != org_id:
            return None

        lines = db.scalars(
            select(InvoiceLine)
            .where(InvoiceLine.invoice_id == inv_id)
            .order_by(InvoiceLine.line_number)
        ).all()

        return {
            "source_number": invoice.invoice_number,
            "customer_id": str(invoice.customer_id),
            "currency_code": invoice.currency_code or "",
            "po_number": getattr(invoice, "po_number", "") or "",
            "notes": invoice.notes or "",
            "terms": getattr(invoice, "payment_terms", "") or "",
            "exchange_rate": str(invoice.exchange_rate)
            if invoice.exchange_rate
            else "1",
            "lines": [
                {
                    "item_id": str(line.item_id) if line.item_id else "",
                    "revenue_account_id": str(line.revenue_account_id)
                    if line.revenue_account_id
                    else "",
                    "description": (line.description or "").replace("'", "\\'"),
                    "quantity": line.quantity,
                    "unit_price": line.unit_price,
                    "tax_amount": line.tax_amount or 0,
                    "line_taxes": list(
                        db.scalars(
                            select(InvoiceLineTax).where(
                                InvoiceLineTax.line_id == line.line_id
                            )
                        ).all()
                    ),
                }
                for line in lines
            ],
        }

    async def create_invoice_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | JSONResponse | RedirectResponse | dict:
        """Handle invoice creation form submission."""
        content_type = request.headers.get("content-type", "")
        org_id = auth.organization_id
        user_id = auth.user_id
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
            input_data = self.build_invoice_input(
                db=db,
                data=data,
                organization_id=org_id,
            )

            invoice = ar_invoice_service.create_invoice(
                db=db,
                organization_id=org_id,
                input=input_data,
                created_by_user_id=user_id,
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
            context.update(self.invoice_form_context(
                db, str(auth.organization_id),
                user_id=str(auth.user_id) if auth.user_id else None,
            ))
            context["error"] = str(e)
            context["form_data"] = data
            return templates.TemplateResponse(
                request, "finance/ar/invoice_form.html", context
            )

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
        return templates.TemplateResponse(
            request, "finance/ar/invoice_detail.html", context
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
            context = base_context(request, auth, "AR Invoice Details", "ar")
            context.update(
                self.invoice_detail_context(
                    db,
                    str(auth.organization_id),
                    invoice_id,
                )
            )
            context["error"] = error
            return templates.TemplateResponse(
                request, "finance/ar/invoice_detail.html", context
            )

        return RedirectResponse(
            url="/finance/ar/invoices?success=Record+deleted+successfully",
            status_code=303,
        )

    # =====================================================================
    # Invoice Status Transitions
    # =====================================================================

    def submit_invoice_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        invoice_id: str,
    ) -> RedirectResponse:
        """Submit invoice for approval."""
        org_id = auth.organization_id
        user_id = auth.user_id
        if org_id is None or user_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        try:
            ar_invoice_service.submit_invoice(
                db=db,
                organization_id=org_id,
                invoice_id=coerce_uuid(invoice_id),
                submitted_by_user_id=user_id,
            )
            return RedirectResponse(
                url=f"/finance/ar/invoices/{invoice_id}?success=Invoice+submitted+for+approval",
                status_code=303,
            )
        except Exception as e:
            logger.exception("Failed to submit invoice %s", invoice_id)
            return RedirectResponse(
                url=f"/finance/ar/invoices/{invoice_id}?error={str(e)}",
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
        org_id = auth.organization_id
        user_id = auth.user_id
        if org_id is None or user_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        try:
            ar_invoice_service.approve_invoice(
                db=db,
                organization_id=org_id,
                invoice_id=coerce_uuid(invoice_id),
                approved_by_user_id=user_id,
            )
            return RedirectResponse(
                url=f"/finance/ar/invoices/{invoice_id}?success=Invoice+approved",
                status_code=303,
            )
        except Exception as e:
            logger.exception("Failed to approve invoice %s", invoice_id)
            return RedirectResponse(
                url=f"/finance/ar/invoices/{invoice_id}?error={str(e)}",
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
        org_id = auth.organization_id
        user_id = auth.user_id
        if org_id is None or user_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        try:
            ar_invoice_service.post_invoice(
                db=db,
                organization_id=org_id,
                invoice_id=coerce_uuid(invoice_id),
                posted_by_user_id=user_id,
            )
            return RedirectResponse(
                url=f"/finance/ar/invoices/{invoice_id}?success=Invoice+posted+to+ledger",
                status_code=303,
            )
        except Exception as e:
            logger.exception("Failed to post invoice %s", invoice_id)
            return RedirectResponse(
                url=f"/finance/ar/invoices/{invoice_id}?error={str(e)}",
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
        org_id = auth.organization_id
        user_id = auth.user_id
        if org_id is None or user_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        try:
            ar_invoice_service.void_invoice(
                db=db,
                organization_id=org_id,
                invoice_id=coerce_uuid(invoice_id),
                voided_by_user_id=user_id,
                reason="Voided via web interface",
            )
            return RedirectResponse(
                url=f"/finance/ar/invoices/{invoice_id}?success=Invoice+voided",
                status_code=303,
            )
        except Exception as e:
            logger.exception("Failed to void invoice %s", invoice_id)
            return RedirectResponse(
                url=f"/finance/ar/invoices/{invoice_id}?error={str(e)}",
                status_code=303,
            )

    def invoice_edit_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        invoice_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Return the edit invoice form with existing invoice data."""
        from app.models.finance.ar.invoice_line import InvoiceLine

        org_id = coerce_uuid(auth.organization_id)
        inv_id = coerce_uuid(invoice_id)

        invoice = db.get(Invoice, inv_id)
        if not invoice or invoice.organization_id != org_id:
            return RedirectResponse(
                url="/finance/ar/invoices?error=Invoice+not+found",
                status_code=303,
            )

        if invoice.status != InvoiceStatus.DRAFT:
            return RedirectResponse(
                url=f"/finance/ar/invoices/{invoice_id}?error=Only+draft+invoices+can+be+edited",
                status_code=303,
            )

        context = base_context(request, auth, "Edit AR Invoice", "ar")
        context.update(
            self.invoice_form_context(
                db,
                str(auth.organization_id),
                customer_id=str(invoice.customer_id),
                user_id=str(auth.user_id) if auth.user_id else None,
            )
        )

        lines = db.scalars(
            select(InvoiceLine)
            .where(InvoiceLine.invoice_id == inv_id)
            .order_by(InvoiceLine.line_number)
        ).all()

        context["invoice"] = {
            "invoice_id": str(invoice.invoice_id),
            "invoice_number": invoice.invoice_number,
            "customer_id": str(invoice.customer_id),
            "invoice_date": invoice.invoice_date,
            "due_date": invoice.due_date,
            "currency_code": invoice.currency_code,
            "po_number": "",
            "description": invoice.notes or "",
            "notes": invoice.notes or "",
            "internal_notes": invoice.internal_notes or "",
            "terms": "",
            "cost_center_id": None,
            "project_id": None,
            "lines": [
                {
                    "line_id": str(line.line_id),
                    "item_id": str(line.item_id) if line.item_id else "",
                    "revenue_account_id": str(line.revenue_account_id)
                    if line.revenue_account_id
                    else "",
                    "description": line.description or "",
                    "quantity": line.quantity,
                    "unit_price": line.unit_price,
                    "tax_amount": line.tax_amount or 0,
                    "line_taxes": list(
                        db.scalars(
                            select(InvoiceLineTax).where(
                                InvoiceLineTax.line_id == line.line_id
                            )
                        ).all()
                    ),
                }
                for line in lines
            ],
        }

        return templates.TemplateResponse(
            request, "finance/ar/invoice_form.html", context
        )

    async def update_invoice_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        invoice_id: str,
    ) -> HTMLResponse | JSONResponse | RedirectResponse:
        """Handle invoice update form submission."""
        content_type = request.headers.get("content-type", "")

        if "application/json" in content_type:
            data = await request.json()
        else:
            form_data = await request.form()
            data = dict(form_data)

        org_id = coerce_uuid(auth.organization_id)
        user_id = coerce_uuid(auth.user_id)

        try:
            input_data = self.build_invoice_input(db, data, org_id)

            invoice = ar_invoice_service.update_invoice(
                db=db,
                organization_id=org_id,
                invoice_id=coerce_uuid(invoice_id),
                input=input_data,
                updated_by_user_id=user_id,
            )

            if "application/json" in content_type:
                return JSONResponse(
                    content={
                        "success": True,
                        "invoice_id": str(invoice.invoice_id),
                    }
                )

            return RedirectResponse(
                url=f"/finance/ar/invoices/{invoice.invoice_id}?success=Invoice+updated+successfully",
                status_code=303,
            )

        except (ValueError, RuntimeError) as e:
            if "application/json" in content_type:
                return JSONResponse(
                    status_code=400,
                    content={"detail": str(e)},
                )

            context = base_context(request, auth, "Edit AR Invoice", "ar")
            context.update(self.invoice_form_context(
                db, str(auth.organization_id),
                user_id=str(auth.user_id) if auth.user_id else None,
            ))
            context["error"] = str(e)
            context["form_data"] = data
            return templates.TemplateResponse(
                request, "finance/ar/invoice_form.html", context
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
            invoice = ar_invoice_service.get(db, org_id, invoice_id)
            if not invoice or invoice.organization_id != auth.organization_id:
                return RedirectResponse(
                    url=f"/finance/ar/invoices/{invoice_id}?error=Invoice+not+found",
                    status_code=303,
                )

            input_data = AttachmentInput(
                entity_type="CUSTOMER_INVOICE",
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
                url=f"/finance/ar/invoices/{invoice_id}?success=Attachment+uploaded",
                status_code=303,
            )

        except ValueError as e:
            return RedirectResponse(
                url=f"/finance/ar/invoices/{invoice_id}?error={str(e)}",
                status_code=303,
            )
        except Exception:
            logger.exception("upload_invoice_attachment_response: failed")
            return RedirectResponse(
                url=f"/finance/ar/invoices/{invoice_id}?error=Upload+failed",
                status_code=303,
            )
