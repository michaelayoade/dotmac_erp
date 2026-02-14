"""
AR Receipt Web Service - Receipt/payment-related web view methods.

Provides view-focused data and operations for AR receipt and aging web routes.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.finance.ar.customer import Customer
from app.models.finance.ar.customer_payment import (
    CustomerPayment,
)
from app.models.finance.ar.invoice import Invoice, InvoiceStatus
from app.models.finance.common.attachment import AttachmentCategory
from app.models.finance.gl.account_category import IFRSCategory
from app.services.common import coerce_uuid
from app.services.finance.ar.ar_aging import ar_aging_service
from app.services.finance.ar.customer import customer_service
from app.services.finance.ar.customer_payment import (
    CustomerPaymentInput,
    customer_payment_service,
)
from app.services.finance.ar.web.base import (
    allocation_view,
    customer_display_name,
    customer_form_view,
    customer_option_view,
    format_currency,
    format_date,
    format_file_size,
    get_accounts,
    normalize_date_range_filters,
    parse_date,
    receipt_detail_view,
    receipt_status_label,
)
from app.services.finance.common.attachment import AttachmentInput, attachment_service
from app.services.finance.platform.currency_context import get_currency_context
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

logger = logging.getLogger(__name__)


class ReceiptWebService:
    """Web service methods for AR receipts/payments and aging reports."""

    @staticmethod
    def build_receipt_input(
        db: Session, data: dict, organization_id: UUID
    ) -> CustomerPaymentInput:
        """Build CustomerPaymentInput from form data."""
        payload = dict(data)
        return customer_payment_service.build_input_from_payload(
            db=db,
            organization_id=organization_id,
            payload=payload,
        )

    @staticmethod
    def list_receipts_context(
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
        """Get context for receipt listing page."""
        logger.debug(
            "list_receipts_context: org=%s search=%r customer_id=%s status=%s page=%d",
            organization_id,
            search,
            customer_id,
            status,
            page,
        )
        offset = (page - 1) * limit
        org_id = coerce_uuid(organization_id)
        from app.services.finance.ar.receipt_query import build_receipt_query

        query = build_receipt_query(
            db=db,
            organization_id=organization_id,
            search=search,
            customer_id=customer_id,
            status=status,
            start_date=start_date,
            end_date=end_date,
        )

        total_count = (
            query.with_entities(func.count(CustomerPayment.payment_id)).scalar() or 0
        )
        receipts = (
            query.with_entities(CustomerPayment, Customer)
            .order_by(CustomerPayment.payment_date.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )

        receipts_view = []
        for payment, customer in receipts:
            receipts_view.append(
                {
                    "receipt_id": payment.payment_id,
                    "receipt_number": payment.payment_number,
                    "customer_name": customer_display_name(customer),
                    "receipt_date": format_date(payment.payment_date),
                    "payment_method": payment.payment_method.value,
                    "reference_number": payment.reference,
                    "amount": format_currency(payment.amount, payment.currency_code),
                    "status": receipt_status_label(payment.status),
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

        logger.debug("list_receipts_context: found %d receipts", total_count)

        return {
            "receipts": receipts_view,
            "customers_list": customers_list,
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
    def receipt_form_context(
        db: Session,
        organization_id: str,
        invoice_id: str | None = None,
        receipt_id: str | None = None,
        customer_id: str | None = None,
    ) -> dict:
        """Get context for receipt create/edit form."""
        logger.debug(
            "receipt_form_context: org=%s invoice_id=%s receipt_id=%s",
            organization_id,
            invoice_id,
            receipt_id,
        )
        from app.models.finance.tax.tax_code import TaxCode, TaxType

        org_id = coerce_uuid(organization_id)

        # Get existing receipt if editing
        receipt = None
        receipt_view = None
        existing_allocations = []
        if receipt_id:
            try:
                receipt = customer_payment_service.get(db, receipt_id)
                if receipt and receipt.organization_id == org_id:
                    receipt_view = {
                        "payment_id": str(receipt.payment_id),
                        "payment_number": receipt.payment_number,
                        "customer_id": str(receipt.customer_id),
                        "payment_date": receipt.payment_date.isoformat()
                        if receipt.payment_date
                        else None,
                        "payment_method": receipt.payment_method.value
                        if receipt.payment_method
                        else None,
                        "bank_account_id": str(receipt.bank_account_id)
                        if receipt.bank_account_id
                        else None,
                        "currency_code": receipt.currency_code,
                        "amount": float(receipt.amount),
                        "gross_amount": float(receipt.gross_amount)
                        if receipt.gross_amount
                        else None,
                        "wht_amount": float(receipt.wht_amount)
                        if receipt.wht_amount
                        else 0,
                        "wht_code_id": str(receipt.wht_code_id)
                        if receipt.wht_code_id
                        else None,
                        "wht_certificate_number": receipt.wht_certificate_number,
                        "reference": receipt.reference,
                        "description": receipt.description,
                        "status": receipt.status.value if receipt.status else None,
                        "has_wht": receipt.wht_amount and receipt.wht_amount > 0,
                    }
                    # Get existing allocations
                    allocations = customer_payment_service.get_payment_allocations(
                        db, org_id, receipt.payment_id
                    )
                    for alloc in allocations:
                        inv = db.get(Invoice, alloc.invoice_id)
                        existing_allocations.append(
                            {
                                "invoice_id": str(alloc.invoice_id),
                                "invoice_number": inv.invoice_number
                                if inv
                                else "Unknown",
                                "amount": float(alloc.allocated_amount),
                            }
                        )
            except Exception:
                logger.exception("Ignored exception")

        # Determine selected customer (if provided)
        selected_customer_id = None
        if customer_id:
            try:
                selected_customer = customer_service.get(db, org_id, customer_id)
                if selected_customer and selected_customer.organization_id == org_id:
                    selected_customer_id = str(selected_customer.customer_id)
            except Exception:
                selected_customer_id = None

        # Get customers with WHT info
        customers_list = []
        for customer in customer_service.list(
            db,
            organization_id=org_id,
            is_active=True,
            limit=200,
        ):
            cust_view = customer_option_view(customer)
            cust_view["is_wht_applicable"] = getattr(
                customer, "is_wht_applicable", False
            )
            cust_view["default_wht_code_id"] = (
                str(customer.default_wht_code_id)
                if getattr(customer, "default_wht_code_id", None)
                else None
            )
            customers_list.append(cust_view)

        # Get WHT tax codes
        wht_codes = [
            {
                "tax_code_id": str(tc.tax_code_id),
                "tax_code": tc.tax_code,
                "tax_name": tc.tax_name,
                "tax_rate": tc.tax_rate,
            }
            for tc in db.query(TaxCode)
            .filter(
                TaxCode.organization_id == org_id,
                TaxCode.is_active == True,
                TaxCode.tax_type == TaxType.WITHHOLDING,
            )
            .all()
        ]

        # Get bank accounts
        bank_accounts = get_accounts(db, org_id, IFRSCategory.ASSETS)

        open_statuses = [
            InvoiceStatus.POSTED,
            InvoiceStatus.PARTIALLY_PAID,
            InvoiceStatus.OVERDUE,
        ]

        query = (
            db.query(Invoice, Customer)
            .join(Customer, Invoice.customer_id == Customer.customer_id)
            .filter(
                Invoice.organization_id == org_id,
                Invoice.status.in_(open_statuses),
            )
        )

        if invoice_id:
            query = query.filter(Invoice.invoice_id == coerce_uuid(invoice_id))
        elif selected_customer_id:
            query = query.filter(
                Invoice.customer_id == coerce_uuid(selected_customer_id)
            )

        rows = query.order_by(Invoice.due_date).all()

        open_invoices = []
        selected_invoice = None
        for invoice, customer in rows:
            balance = invoice.total_amount - invoice.amount_paid
            view = {
                "invoice_id": invoice.invoice_id,
                "invoice_number": invoice.invoice_number,
                "customer_id": invoice.customer_id,
                "customer_name": customer_display_name(customer),
                "invoice_date": format_date(invoice.invoice_date),
                "due_date": format_date(invoice.due_date),
                "total_amount": format_currency(
                    invoice.total_amount,
                    invoice.currency_code,
                ),
                "balance": format_currency(balance, invoice.currency_code),
                "balance_raw": float(balance),
                "currency_code": invoice.currency_code,
            }
            open_invoices.append(view)
            if invoice_id and invoice.invoice_id == coerce_uuid(invoice_id):
                selected_invoice = view

        context = {
            "customers_list": customers_list,
            "wht_codes": wht_codes,
            "bank_accounts": bank_accounts,
            "invoice_id": invoice_id,
            "invoice": selected_invoice,
            "open_invoices": open_invoices,
            "receipt": receipt_view,
            "existing_allocations": existing_allocations,
            "selected_customer_id": selected_customer_id,
        }
        context.update(get_currency_context(db, organization_id))
        return context

    @staticmethod
    def receipt_detail_context(
        db: Session,
        organization_id: str,
        receipt_id: str,
    ) -> dict:
        """Get context for receipt detail page."""
        logger.debug(
            "receipt_detail_context: org=%s receipt_id=%s", organization_id, receipt_id
        )
        org_id = coerce_uuid(organization_id)
        receipt = None
        try:
            receipt = customer_payment_service.get(db, receipt_id)
        except Exception:
            receipt = None

        if not receipt or receipt.organization_id != org_id:
            return {"receipt": None, "customer": None, "allocations": []}

        customer = None
        try:
            customer = customer_service.get(db, org_id, str(receipt.customer_id))
        except Exception:
            customer = None

        allocations = customer_payment_service.get_payment_allocations(
            db,
            organization_id=org_id,
            payment_id=receipt.payment_id,
        )

        invoice_map: dict[UUID, Invoice] = {}
        if allocations:
            invoice_ids = [allocation.invoice_id for allocation in allocations]
            invoices = (
                db.query(Invoice).filter(Invoice.invoice_id.in_(invoice_ids)).all()
            )
            invoice_map = {invoice.invoice_id: invoice for invoice in invoices}

        allocations_view = [
            allocation_view(
                alloc,
                invoice_map.get(alloc.invoice_id),
                receipt.currency_code,
            )
            for alloc in allocations
        ]

        # Get attachments
        attachments = attachment_service.list_for_entity(
            db,
            organization_id=org_id,
            entity_type="CUSTOMER_PAYMENT",
            entity_id=receipt.payment_id,
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

        logger.debug(
            "receipt_detail_context: found %d allocations", len(allocations_view)
        )

        return {
            "receipt": receipt_detail_view(receipt, customer),
            "customer": customer_form_view(customer) if customer else None,
            "allocations": allocations_view,
            "attachments": attachments_view,
        }

    @staticmethod
    def delete_receipt(
        db: Session,
        organization_id: str,
        receipt_id: str,
    ) -> str | None:
        """Delete a receipt. Returns error message or None on success."""
        logger.debug(
            "delete_receipt: org=%s receipt_id=%s", organization_id, receipt_id
        )
        org_id = coerce_uuid(organization_id)
        pay_id = coerce_uuid(receipt_id)

        try:
            customer_payment_service.delete_receipt(db, org_id, pay_id)
            logger.info("delete_receipt: deleted receipt %s for org %s", pay_id, org_id)
            return None
        except HTTPException as exc:
            return exc.detail
        except Exception as e:
            logger.exception("delete_receipt: failed for org %s", org_id)
            return f"Failed to delete receipt: {str(e)}"

    @staticmethod
    def aging_context(
        db: Session,
        organization_id: str,
        as_of_date: str | None,
        customer_id: str | None,
    ) -> dict:
        """Get context for AR aging report."""
        logger.debug(
            "aging_context: org=%s as_of_date=%s customer_id=%s",
            organization_id,
            as_of_date,
            customer_id,
        )
        org_id = coerce_uuid(organization_id)
        ref_date = parse_date(as_of_date)

        if customer_id:
            summary = ar_aging_service.calculate_customer_aging(
                db, org_id, coerce_uuid(customer_id), ref_date
            )
            aging_data = [summary]
        else:
            aging_data = ar_aging_service.get_aging_by_customer(db, org_id, ref_date)

        customers_list = [
            customer_option_view(customer)
            for customer in customer_service.list(
                db,
                organization_id=org_id,
                is_active=True,
                limit=200,
            )
        ]

        logger.debug("aging_context: found %d aging records", len(aging_data))

        customer_aging: list[dict] = []
        total_current = Decimal("0")
        total_31_60 = Decimal("0")
        total_61_90 = Decimal("0")
        total_over_90 = Decimal("0")
        total_outstanding = Decimal("0")
        total_invoices = 0

        for row in aging_data:
            row_total = row.total_outstanding or Decimal("0")
            row_current = row.current or Decimal("0")
            row_31_60 = row.days_31_60 or Decimal("0")
            row_61_90 = row.days_61_90 or Decimal("0")
            row_over_90 = row.over_90 or Decimal("0")

            total_current += row_current
            total_31_60 += row_31_60
            total_61_90 += row_61_90
            total_over_90 += row_over_90
            total_outstanding += row_total
            total_invoices += row.invoice_count or 0

            if row_total > 0:
                current_pct = float((row_current / row_total) * 100)
                days_30_pct = float((row_31_60 / row_total) * 100)
                days_60_pct = float((row_61_90 / row_total) * 100)
                days_90_pct = float((row_over_90 / row_total) * 100)
            else:
                current_pct = days_30_pct = days_60_pct = days_90_pct = 0.0

            customer_aging.append(
                {
                    "customer_id": row.customer_id,
                    "customer_code": row.customer_code,
                    "customer_name": row.customer_name,
                    "current": format_currency(row_current, row.currency_code),
                    "days_30": format_currency(row_31_60, row.currency_code),
                    "days_60": format_currency(row_61_90, row.currency_code),
                    "days_90": format_currency(row_over_90, row.currency_code),
                    "total": format_currency(row_total, row.currency_code),
                    "current_raw": row_current,
                    "days_30_raw": row_31_60,
                    "days_60_raw": row_61_90,
                    "days_90_raw": row_over_90,
                    "current_pct": round(current_pct, 2),
                    "days_30_pct": round(days_30_pct, 2),
                    "days_60_pct": round(days_60_pct, 2),
                    "days_90_pct": round(days_90_pct, 2),
                }
            )

        if total_outstanding > 0:
            total_current_pct = float((total_current / total_outstanding) * 100)
            total_30_pct = float((total_31_60 / total_outstanding) * 100)
            total_60_pct = float((total_61_90 / total_outstanding) * 100)
            total_90_pct = float((total_over_90 / total_outstanding) * 100)
        else:
            total_current_pct = total_30_pct = total_60_pct = total_90_pct = 0.0

        currency_code = aging_data[0].currency_code if aging_data else None
        aging_summary = {
            "total": format_currency(total_outstanding, currency_code),
            "current": format_currency(total_current, currency_code),
            "days_30": format_currency(total_31_60, currency_code),
            "days_60": format_currency(total_61_90, currency_code),
            "days_90": format_currency(total_over_90, currency_code),
            "current_raw": total_current,
            "days_30_raw": total_31_60,
            "days_60_raw": total_61_90,
            "days_90_raw": total_over_90,
            "current_pct": round(total_current_pct, 2),
            "days_30_pct": round(total_30_pct, 2),
            "days_60_pct": round(total_60_pct, 2),
            "days_90_pct": round(total_90_pct, 2),
            "invoice_count": total_invoices,
            "dso": 0,
        }

        customers = [
            {
                "customer_id": option["customer_id"],
                "legal_name": option["customer_name"],
            }
            for option in customers_list
        ]

        return {
            "aging_summary": aging_summary,
            "customer_aging": customer_aging,
            "customers": customers,
            "selected_customer_id": customer_id,
            "aging_data": aging_data,
            "customers_list": customers_list,
            "as_of_date": as_of_date,
            "customer_id": customer_id,
        }

    # =====================================================================
    # HTTP Response Methods
    # =====================================================================

    def list_receipts_response(
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
        """Render receipt list page."""
        start_date, end_date = normalize_date_range_filters(
            start_date,
            end_date,
            request.query_params,
        )
        context = base_context(request, auth, "AR Receipts", "ar")
        context.update(
            self.list_receipts_context(
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
        return templates.TemplateResponse(request, "finance/ar/receipts.html", context)

    def receipt_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        invoice_id: str | None,
        customer_id: str | None = None,
    ) -> HTMLResponse:
        """Render new receipt form."""
        context = base_context(request, auth, "New AR Receipt", "ar")
        context.update(
            self.receipt_form_context(
                db,
                str(auth.organization_id),
                invoice_id=invoice_id,
                customer_id=customer_id,
            )
        )
        return templates.TemplateResponse(
            request, "finance/ar/receipt_form.html", context
        )

    def receipt_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        receipt_id: str,
    ) -> HTMLResponse:
        """Render receipt detail page."""
        context = base_context(request, auth, "AR Receipt Details", "ar")
        context.update(
            self.receipt_detail_context(
                db,
                str(auth.organization_id),
                receipt_id,
            )
        )
        return templates.TemplateResponse(
            request, "finance/ar/receipt_detail.html", context
        )

    async def create_receipt_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | JSONResponse | RedirectResponse | dict:
        """Handle receipt creation form submission."""
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
            input_data = self.build_receipt_input(db, data, org_id)

            receipt = customer_payment_service.create_payment(
                db=db,
                organization_id=org_id,
                input=input_data,
                created_by_user_id=user_id,
            )

            if "application/json" in content_type:
                return {"success": True, "receipt_id": str(receipt.payment_id)}

            return RedirectResponse(
                url="/finance/ar/receipts?success=Receipt+created+successfully",
                status_code=303,
            )

        except Exception as e:
            logger.exception("create_receipt_response: failed")
            if "application/json" in content_type:
                return JSONResponse(
                    status_code=400,
                    content={"detail": str(e)},
                )

            context = base_context(request, auth, "New AR Receipt", "ar")
            context.update(self.receipt_form_context(db, str(auth.organization_id)))
            context["error"] = str(e)
            context["form_data"] = data
            return templates.TemplateResponse(
                request, "finance/ar/receipt_form.html", context
            )

    def delete_receipt_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        receipt_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Handle receipt deletion."""
        error = self.delete_receipt(db, str(auth.organization_id), receipt_id)

        if error:
            context = base_context(request, auth, "AR Receipt Details", "ar")
            context.update(
                self.receipt_detail_context(
                    db,
                    str(auth.organization_id),
                    receipt_id,
                )
            )
            context["error"] = error
            return templates.TemplateResponse(
                request, "finance/ar/receipt_detail.html", context
            )

        return RedirectResponse(
            url="/finance/ar/receipts?success=Record+deleted+successfully",
            status_code=303,
        )

    def receipt_edit_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        receipt_id: str,
    ) -> HTMLResponse:
        """Render receipt edit form."""
        context = base_context(request, auth, "Edit AR Receipt", "ar")
        context.update(
            self.receipt_form_context(
                db,
                str(auth.organization_id),
                receipt_id=receipt_id,
            )
        )
        return templates.TemplateResponse(
            request, "finance/ar/receipt_form.html", context
        )

    async def update_receipt_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        receipt_id: str,
    ) -> HTMLResponse | JSONResponse | RedirectResponse | dict:
        """Handle receipt update form submission."""
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
            input_data = self.build_receipt_input(db, data, org_id)

            customer_payment_service.update_payment(
                db=db,
                organization_id=org_id,
                payment_id=UUID(receipt_id),
                input=input_data,
                updated_by_user_id=user_id,
            )

            if "application/json" in content_type:
                return {"success": True, "receipt_id": receipt_id}

            return RedirectResponse(
                url=f"/ar/receipts/{receipt_id}?success=Receipt+updated+successfully",
                status_code=303,
            )

        except Exception as e:
            logger.exception("update_receipt_response: failed")
            if "application/json" in content_type:
                return JSONResponse(
                    status_code=400,
                    content={"detail": str(e)},
                )

            context = base_context(request, auth, "Edit AR Receipt", "ar")
            context.update(
                self.receipt_form_context(
                    db,
                    str(auth.organization_id),
                    receipt_id=receipt_id,
                )
            )
            context["error"] = str(e)
            context["form_data"] = data
            return templates.TemplateResponse(
                request, "finance/ar/receipt_form.html", context
            )

    def aging_report_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        as_of_date: str | None,
        customer_id: str | None,
    ) -> HTMLResponse:
        """Render AR aging report page."""
        context = base_context(request, auth, "AR Aging Report", "ar")
        context.update(
            self.aging_context(
                db,
                str(auth.organization_id),
                as_of_date=as_of_date,
                customer_id=customer_id,
            )
        )
        return templates.TemplateResponse(request, "finance/ar/aging.html", context)

    async def upload_receipt_attachment_response(
        self,
        receipt_id: str,
        file: UploadFile,
        description: str | None,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Handle receipt attachment upload."""
        try:
            org_id = auth.organization_id
            user_id = auth.person_id
            if org_id is None:
                raise HTTPException(status_code=401, detail="Authentication required")
            if user_id is None:
                raise HTTPException(status_code=401, detail="Authentication required")
            receipt = customer_payment_service.get(db, receipt_id)
            if not receipt or receipt.organization_id != auth.organization_id:
                return RedirectResponse(
                    url=f"/ar/receipts/{receipt_id}?error=Receipt+not+found",
                    status_code=303,
                )

            input_data = AttachmentInput(
                entity_type="CUSTOMER_PAYMENT",
                entity_id=receipt_id,
                file_name=file.filename or "unnamed",
                content_type=file.content_type or "application/octet-stream",
                category=AttachmentCategory.RECEIPT,
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
                url=f"/ar/receipts/{receipt_id}?success=Attachment+uploaded",
                status_code=303,
            )

        except ValueError as e:
            return RedirectResponse(
                url=f"/ar/receipts/{receipt_id}?error={str(e)}",
                status_code=303,
            )
        except Exception:
            logger.exception("upload_receipt_attachment_response: failed")
            return RedirectResponse(
                url=f"/ar/receipts/{receipt_id}?error=Upload+failed",
                status_code=303,
            )
