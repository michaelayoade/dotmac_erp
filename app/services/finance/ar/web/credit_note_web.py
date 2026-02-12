"""
AR Credit Note Web Service - Credit note-related web view methods.

Provides view-focused data and operations for AR credit note web routes.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.finance.ar.customer import Customer
from app.models.finance.ar.invoice import Invoice, InvoiceStatus, InvoiceType
from app.models.finance.common.attachment import AttachmentCategory
from app.models.finance.gl.account_category import IFRSCategory
from app.services.common import coerce_uuid
from app.services.finance.ar.customer import customer_service
from app.services.finance.ar.invoice import ARInvoiceInput, ar_invoice_service
from app.services.finance.ar.web.base import (
    customer_display_name,
    customer_form_view,
    customer_option_view,
    format_currency,
    format_date,
    format_file_size,
    get_accounts,
    get_cost_centers,
    get_projects,
    invoice_line_view,
    invoice_status_label,
    parse_date,
    parse_invoice_status,
)
from app.services.finance.common.attachment import AttachmentInput, attachment_service
from app.services.finance.platform.currency_context import get_currency_context
from app.services.finance.tax.tax_master import tax_code_service
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

logger = logging.getLogger(__name__)


class CreditNoteWebService:
    """Web service methods for AR credit notes."""

    @staticmethod
    def build_credit_note_input(
        db: Session, data: dict, organization_id: UUID
    ) -> ARInvoiceInput:
        """Build ARInvoiceInput from form data for credit note."""
        payload = dict(data)
        return ar_invoice_service.build_credit_note_input_from_payload(
            db=db,
            organization_id=organization_id,
            payload=payload,
        )

    @staticmethod
    def list_credit_notes_context(
        db: Session,
        organization_id: str,
        search: str | None,
        customer_id: str | None,
        status: str | None,
        start_date: str | None,
        end_date: str | None,
        page: int,
        limit: int = 50,
    ) -> dict:
        """Get context for credit note listing page."""
        logger.debug(
            "list_credit_notes_context: org=%s search=%r customer_id=%s status=%s page=%d",
            organization_id,
            search,
            customer_id,
            status,
            page,
        )
        org_id = coerce_uuid(organization_id)
        offset = (page - 1) * limit

        status_value = parse_invoice_status(status)
        from_date = parse_date(start_date)
        to_date = parse_date(end_date)

        query = (
            db.query(Invoice, Customer)
            .join(Customer, Invoice.customer_id == Customer.customer_id)
            .filter(
                Invoice.organization_id == org_id,
                Invoice.invoice_type == InvoiceType.CREDIT_NOTE,
            )
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
        credit_notes = (
            query.order_by(Invoice.invoice_date.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )

        # Calculate stats
        stats_query = db.query(Invoice).filter(
            Invoice.organization_id == org_id,
            Invoice.invoice_type == InvoiceType.CREDIT_NOTE,
        )

        total_credit_notes = stats_query.with_entities(
            func.coalesce(func.sum(Invoice.total_amount), 0)
        ).scalar() or Decimal("0")

        draft_total = stats_query.filter(
            Invoice.status == InvoiceStatus.DRAFT
        ).with_entities(
            func.coalesce(func.sum(Invoice.total_amount), 0)
        ).scalar() or Decimal("0")

        posted_total = stats_query.filter(
            Invoice.status == InvoiceStatus.POSTED
        ).with_entities(
            func.coalesce(func.sum(Invoice.total_amount), 0)
        ).scalar() or Decimal("0")

        applied_total = stats_query.filter(
            Invoice.status == InvoiceStatus.PAID
        ).with_entities(
            func.coalesce(func.sum(Invoice.total_amount), 0)
        ).scalar() or Decimal("0")

        credit_notes_view = []
        for credit_note, customer in credit_notes:
            credit_notes_view.append(
                {
                    "credit_note_id": credit_note.invoice_id,
                    "credit_note_number": credit_note.invoice_number,
                    "customer_name": customer_display_name(customer),
                    "credit_note_date": format_date(credit_note.invoice_date),
                    "total_amount": format_currency(
                        credit_note.total_amount, credit_note.currency_code
                    ),
                    "amount_applied": format_currency(
                        credit_note.amount_paid, credit_note.currency_code
                    ),
                    "balance": format_currency(
                        credit_note.total_amount - credit_note.amount_paid,
                        credit_note.currency_code,
                    ),
                    "status": invoice_status_label(credit_note.status),
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

        logger.debug("list_credit_notes_context: found %d credit notes", total_count)

        return {
            "credit_notes": credit_notes_view,
            "customers_list": customers_list,
            "stats": {
                "total_credit_notes": format_currency(total_credit_notes) or "$0.00",
                "draft_total": format_currency(draft_total) or "$0.00",
                "posted_total": format_currency(posted_total) or "$0.00",
                "applied_total": format_currency(applied_total) or "$0.00",
            },
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
    def credit_note_form_context(
        db: Session,
        organization_id: str,
        invoice_id: str | None = None,
    ) -> dict:
        """Get context for credit note create form."""
        logger.debug(
            "credit_note_form_context: org=%s invoice_id=%s",
            organization_id,
            invoice_id,
        )
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
                "tax_code_id": tax.tax_code_id,
                "tax_code": tax.tax_code,
                "rate": (tax.tax_rate * 100).quantize(Decimal("0.01")),
            }
            for tax in tax_code_service.list(
                db,
                organization_id=org_id,
                is_active=True,
                applies_to_sales=True,
                limit=200,
            )
        ]

        # Get open invoices for reference
        open_statuses = [
            InvoiceStatus.POSTED,
            InvoiceStatus.PARTIALLY_PAID,
            InvoiceStatus.OVERDUE,
        ]

        open_invoices = []
        selected_invoice = None
        invoices_query = (
            db.query(Invoice, Customer)
            .join(Customer, Invoice.customer_id == Customer.customer_id)
            .filter(
                Invoice.organization_id == org_id,
                Invoice.invoice_type == InvoiceType.STANDARD,
                Invoice.status.in_(open_statuses),
            )
            .order_by(Invoice.due_date)
        )

        for invoice, customer in invoices_query.all():
            balance = invoice.total_amount - invoice.amount_paid
            view = {
                "invoice_id": invoice.invoice_id,
                "invoice_number": invoice.invoice_number,
                "customer_id": invoice.customer_id,
                "customer_name": customer_display_name(customer),
                "invoice_date": format_date(invoice.invoice_date),
                "total_amount": format_currency(
                    invoice.total_amount, invoice.currency_code
                ),
                "balance": format_currency(balance, invoice.currency_code),
                "currency_code": invoice.currency_code,
            }
            open_invoices.append(view)
            if invoice_id and str(invoice.invoice_id) == invoice_id:
                selected_invoice = view

        context = {
            "customers_list": customers_list,
            "revenue_accounts": revenue_accounts,
            "tax_codes": tax_codes,
            "cost_centers": get_cost_centers(db, org_id),
            "projects": get_projects(db, org_id),
            "open_invoices": open_invoices,
            "invoice_id": invoice_id,
            "selected_invoice": selected_invoice,
            "organization_id": organization_id,
        }
        context.update(get_currency_context(db, organization_id))
        return context

    @staticmethod
    def credit_note_detail_context(
        db: Session,
        organization_id: str,
        credit_note_id: str,
    ) -> dict:
        """Get context for credit note detail page."""
        logger.debug(
            "credit_note_detail_context: org=%s credit_note_id=%s",
            organization_id,
            credit_note_id,
        )
        org_id = coerce_uuid(organization_id)
        credit_note = None
        try:
            credit_note = ar_invoice_service.get(db, org_id, credit_note_id)
        except Exception:
            credit_note = None

        if not credit_note or credit_note.organization_id != org_id:
            return {"credit_note": None, "customer": None, "lines": []}

        if credit_note.invoice_type != InvoiceType.CREDIT_NOTE:
            return {"credit_note": None, "customer": None, "lines": []}

        customer = None
        try:
            customer = customer_service.get(db, org_id, str(credit_note.customer_id))
        except Exception:
            customer = None

        lines = ar_invoice_service.get_invoice_lines(
            db,
            organization_id=org_id,
            invoice_id=credit_note.invoice_id,
        )
        lines_view = [
            invoice_line_view(line, credit_note.currency_code) for line in lines
        ]

        balance = credit_note.total_amount - credit_note.amount_paid
        credit_note_view = {
            "credit_note_id": credit_note.invoice_id,
            "credit_note_number": credit_note.invoice_number,
            "customer_id": credit_note.customer_id,
            "customer_name": customer_display_name(customer) if customer else "",
            "credit_note_date": format_date(credit_note.invoice_date),
            "currency_code": credit_note.currency_code,
            "subtotal": format_currency(
                credit_note.subtotal, credit_note.currency_code
            ),
            "tax_amount": format_currency(
                credit_note.tax_amount, credit_note.currency_code
            ),
            "total_amount": format_currency(
                credit_note.total_amount, credit_note.currency_code
            ),
            "amount_applied": format_currency(
                credit_note.amount_paid, credit_note.currency_code
            ),
            "balance": format_currency(balance, credit_note.currency_code),
            "status": invoice_status_label(credit_note.status),
            "notes": credit_note.notes,
            "internal_notes": credit_note.internal_notes,
        }

        # Get attachments
        attachments = attachment_service.list_for_entity(
            db,
            organization_id=org_id,
            entity_type="CREDIT_NOTE",
            entity_id=credit_note.invoice_id,
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

        logger.debug("credit_note_detail_context: found %d lines", len(lines_view))

        return {
            "credit_note": credit_note_view,
            "customer": customer_form_view(customer) if customer else None,
            "lines": lines_view,
            "attachments": attachments_view,
        }

    @staticmethod
    def delete_credit_note(
        db: Session,
        organization_id: str,
        credit_note_id: str,
    ) -> str | None:
        """Delete a credit note. Returns error message or None on success."""
        logger.debug(
            "delete_credit_note: org=%s credit_note_id=%s",
            organization_id,
            credit_note_id,
        )
        org_id = coerce_uuid(organization_id)
        cn_id = coerce_uuid(credit_note_id)

        try:
            ar_invoice_service.delete_credit_note(db, org_id, cn_id)
            logger.info(
                "delete_credit_note: deleted credit note %s for org %s", cn_id, org_id
            )
            return None
        except HTTPException as exc:
            return exc.detail
        except Exception as e:
            logger.exception("delete_credit_note: failed for org %s", org_id)
            return f"Failed to delete credit note: {str(e)}"

    # =====================================================================
    # HTTP Response Methods
    # =====================================================================

    def list_credit_notes_response(
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
    ) -> HTMLResponse:
        """Render credit note list page."""
        context = base_context(request, auth, "AR Credit Notes", "ar")
        context.update(
            self.list_credit_notes_context(
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
        return templates.TemplateResponse(
            request, "finance/ar/credit_notes.html", context
        )

    def credit_note_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        invoice_id: str | None,
    ) -> HTMLResponse:
        """Render new credit note form."""
        context = base_context(request, auth, "New Credit Note", "ar")
        context.update(
            self.credit_note_form_context(
                db,
                str(auth.organization_id),
                invoice_id=invoice_id,
            )
        )
        return templates.TemplateResponse(
            request, "finance/ar/credit_note_form.html", context
        )

    async def create_credit_note_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | JSONResponse | RedirectResponse | dict:
        """Handle credit note creation form submission."""
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
            input_data = self.build_credit_note_input(db, data, org_id)

            credit_note = ar_invoice_service.create_invoice(
                db=db,
                organization_id=org_id,
                input=input_data,
                created_by_user_id=user_id,
            )

            if "application/json" in content_type:
                return {"success": True, "credit_note_id": str(credit_note.invoice_id)}

            return RedirectResponse(
                url="/finance/ar/credit-notes?success=Credit+note+created+successfully",
                status_code=303,
            )

        except Exception as e:
            logger.exception("create_credit_note_response: failed")
            if "application/json" in content_type:
                return JSONResponse(
                    status_code=400,
                    content={"detail": str(e)},
                )

            context = base_context(request, auth, "New Credit Note", "ar")
            context.update(self.credit_note_form_context(db, str(auth.organization_id)))
            context["error"] = str(e)
            context["form_data"] = data
            return templates.TemplateResponse(
                request, "finance/ar/credit_note_form.html", context
            )

    def credit_note_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        credit_note_id: str,
    ) -> HTMLResponse:
        """Render credit note detail page."""
        context = base_context(request, auth, "Credit Note Details", "ar")
        context.update(
            self.credit_note_detail_context(
                db,
                str(auth.organization_id),
                credit_note_id,
            )
        )
        return templates.TemplateResponse(
            request, "finance/ar/credit_note_detail.html", context
        )

    def delete_credit_note_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        credit_note_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Handle credit note deletion."""
        error = self.delete_credit_note(db, str(auth.organization_id), credit_note_id)

        if error:
            context = base_context(request, auth, "Credit Note Details", "ar")
            context.update(
                self.credit_note_detail_context(
                    db,
                    str(auth.organization_id),
                    credit_note_id,
                )
            )
            context["error"] = error
            return templates.TemplateResponse(
                request, "finance/ar/credit_note_detail.html", context
            )

        return RedirectResponse(
            url="/finance/ar/credit-notes?success=Record+deleted+successfully",
            status_code=303,
        )

    async def upload_credit_note_attachment_response(
        self,
        credit_note_id: str,
        file: UploadFile,
        description: str | None,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Handle credit note attachment upload."""
        try:
            org_id = auth.organization_id
            user_id = auth.person_id
            if org_id is None:
                raise HTTPException(status_code=401, detail="Authentication required")
            if user_id is None:
                raise HTTPException(status_code=401, detail="Authentication required")
            credit_note = ar_invoice_service.get(db, org_id, credit_note_id)
            if not credit_note or credit_note.organization_id != auth.organization_id:
                return RedirectResponse(
                    url=f"/ar/credit-notes/{credit_note_id}?error=Credit+note+not+found",
                    status_code=303,
                )

            input_data = AttachmentInput(
                entity_type="CREDIT_NOTE",
                entity_id=credit_note_id,
                file_name=file.filename or "unnamed",
                content_type=file.content_type or "application/octet-stream",
                category=AttachmentCategory.CREDIT_NOTE,
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
                url=f"/ar/credit-notes/{credit_note_id}?success=Attachment+uploaded",
                status_code=303,
            )

        except ValueError as e:
            return RedirectResponse(
                url=f"/ar/credit-notes/{credit_note_id}?error={str(e)}",
                status_code=303,
            )
        except Exception:
            logger.exception("upload_credit_note_attachment_response: failed")
            return RedirectResponse(
                url=f"/ar/credit-notes/{credit_note_id}?error=Upload+failed",
                status_code=303,
            )
