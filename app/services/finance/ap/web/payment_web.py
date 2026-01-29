"""
AP Payment Web Service - Payment-related web view methods.

Provides view-focused data and operations for AP payment and aging web routes.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.config import settings
from app.models.finance.ap.payment_batch import APBatchStatus
from app.models.finance.ap.supplier import Supplier
from app.models.finance.ap.supplier_invoice import SupplierInvoice, SupplierInvoiceStatus
from app.models.finance.ap.supplier_payment import SupplierPayment, APPaymentMethod, APPaymentStatus
from app.models.finance.ap.ap_payment_allocation import APPaymentAllocation
from app.models.finance.banking.bank_account import BankAccountStatus
from app.models.finance.common.attachment import AttachmentCategory
from app.models.finance.gl.account_category import IFRSCategory
from app.services.common import coerce_uuid
from app.services.finance.ap.ap_aging import ap_aging_service
from app.services.finance.ap.payment_batch import payment_batch_service
from app.services.finance.ap.supplier import supplier_service
from app.services.finance.ap.supplier_payment import (
    supplier_payment_service,
    SupplierPaymentInput,
    PaymentAllocationInput,
)
from app.services.finance.banking.bank_account import bank_account_service
from app.services.finance.common.attachment import attachment_service, AttachmentInput
from app.services.finance.platform.currency_context import get_currency_context
from app.templates import templates
from app.web.deps import base_context, WebAuthContext
from app.services.finance.ap.web.base import (
    logger,
    parse_date,
    parse_payment_status,
    format_date,
    format_currency,
    format_file_size,
    supplier_display_name,
    supplier_option_view,
    supplier_form_view,
    payment_status_label,
    payment_detail_view,
    allocation_view,
    get_accounts,
)


class PaymentWebService:
    """Web service methods for AP payments/supplier payments and aging reports."""

    @staticmethod
    def build_payment_input(data: dict) -> SupplierPaymentInput:
        """Build SupplierPaymentInput from form data."""
        logger.debug("build_payment_input: building input from form data")
        payment_date = parse_date(data.get("payment_date")) or date.today()

        # Parse payment method
        method_str = data.get("payment_method", "BANK_TRANSFER")
        try:
            payment_method = APPaymentMethod(method_str)
        except ValueError:
            payment_method = APPaymentMethod.BANK_TRANSFER

        # Parse allocations if provided
        allocations = []
        allocations_data = data.get("allocations", [])
        if isinstance(allocations_data, str):
            try:
                allocations_data = json.loads(allocations_data)
            except json.JSONDecodeError:
                allocations_data = []

        for alloc in allocations_data:
            if alloc.get("invoice_id") and alloc.get("amount"):
                allocations.append(
                    PaymentAllocationInput(
                        invoice_id=UUID(alloc["invoice_id"]),
                        amount=Decimal(str(alloc["amount"])),
                    )
                )

        # Parse WHT fields
        wht_code_id = None
        wht_amount = Decimal("0")
        gross_amount = None

        # Check if WHT is applied (has_wht checkbox or wht_amount > 0)
        has_wht = data.get("has_wht") in ("true", "1", True, "on")
        if has_wht:
            if data.get("wht_code_id"):
                wht_code_id = UUID(data["wht_code_id"])
            if data.get("wht_amount"):
                wht_amount = Decimal(str(data["wht_amount"]))
            if data.get("gross_amount"):
                gross_amount = Decimal(str(data["gross_amount"]))

        if not data.get("bank_account_id"):
            raise ValueError("Bank account is required for supplier payments")

        return SupplierPaymentInput(
            supplier_id=UUID(data["supplier_id"]),
            payment_date=payment_date,
            payment_method=payment_method,
            currency_code=data.get(
                "currency_code",
                settings.default_functional_currency_code,
            ),
            amount=Decimal(str(data.get("amount", 0))),
            bank_account_id=UUID(data["bank_account_id"]),
            reference=data.get("reference"),
            description=data.get("description"),
            allocations=allocations,
            # WHT fields
            gross_amount=gross_amount,
            wht_code_id=wht_code_id,
            wht_amount=wht_amount,
        )

    @staticmethod
    def list_payments_context(
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
        """Get context for payment listing page."""
        logger.debug(
            "list_payments_context: org=%s search=%r supplier_id=%s status=%s page=%d",
            organization_id, search, supplier_id, status, page
        )
        org_id = coerce_uuid(organization_id)
        offset = (page - 1) * limit

        status_value = parse_payment_status(status)
        from_date = parse_date(start_date)
        to_date = parse_date(end_date)

        query = (
            db.query(SupplierPayment, Supplier)
            .join(Supplier, SupplierPayment.supplier_id == Supplier.supplier_id)
            .filter(SupplierPayment.organization_id == org_id)
        )

        if supplier_id:
            query = query.filter(SupplierPayment.supplier_id == coerce_uuid(supplier_id))
        if status_value:
            query = query.filter(SupplierPayment.status == status_value)
        if from_date:
            query = query.filter(SupplierPayment.payment_date >= from_date)
        if to_date:
            query = query.filter(SupplierPayment.payment_date <= to_date)
        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                or_(
                    SupplierPayment.payment_number.ilike(search_pattern),
                    SupplierPayment.reference.ilike(search_pattern),
                )
            )

        total_count = query.with_entities(func.count(SupplierPayment.payment_id)).scalar() or 0
        payments = (
            query.order_by(SupplierPayment.payment_date.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )

        payments_view = []
        for payment, supplier in payments:
            payments_view.append(
                {
                    "payment_id": payment.payment_id,
                    "payment_number": payment.payment_number,
                    "supplier_name": supplier_display_name(supplier),
                    "payment_date": format_date(payment.payment_date),
                    "payment_method": payment.payment_method.value,
                    "reference_number": payment.reference,
                    "amount": format_currency(payment.amount, payment.currency_code),
                    "status": payment_status_label(payment.status),
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

        logger.debug("list_payments_context: found %d payments", total_count)

        return {
            "payments": payments_view,
            "suppliers_list": suppliers_list,
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
        }

    @staticmethod
    def payment_form_context(
        db: Session,
        organization_id: str,
        invoice_id: Optional[str] = None,
    ) -> dict:
        """Get context for payment create/edit form."""
        logger.debug(
            "payment_form_context: org=%s invoice_id=%s",
            organization_id, invoice_id
        )
        from app.models.finance.tax.tax_code import TaxCode, TaxType

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

        open_statuses = [
            SupplierInvoiceStatus.POSTED,
            SupplierInvoiceStatus.PARTIALLY_PAID,
        ]

        query = (
            db.query(SupplierInvoice, Supplier)
            .join(Supplier, SupplierInvoice.supplier_id == Supplier.supplier_id)
            .filter(
                SupplierInvoice.organization_id == org_id,
                SupplierInvoice.status.in_(open_statuses),
            )
        )

        if invoice_id:
            query = query.filter(SupplierInvoice.invoice_id == coerce_uuid(invoice_id))

        rows = query.order_by(SupplierInvoice.due_date).all()

        open_invoices = []
        selected_invoice = None
        for invoice, supplier in rows:
            balance = invoice.total_amount - invoice.amount_paid
            view = {
                "invoice_id": invoice.invoice_id,
                "invoice_number": invoice.invoice_number,
                "supplier_id": invoice.supplier_id,
                "supplier_name": supplier_display_name(supplier),
                "invoice_date": format_date(invoice.invoice_date),
                "due_date": format_date(invoice.due_date),
                "total_amount": format_currency(
                    invoice.total_amount,
                    invoice.currency_code,
                ),
                "balance": format_currency(balance, invoice.currency_code),
                "balance_raw": float(balance),  # For JS calculations
                "currency_code": invoice.currency_code,
            }
            open_invoices.append(view)
            if invoice_id and invoice.invoice_id == coerce_uuid(invoice_id):
                selected_invoice = view

        # Get WHT codes for payments
        wht_codes = (
            db.query(TaxCode)
            .filter(
                TaxCode.organization_id == org_id,
                TaxCode.tax_type == TaxType.WITHHOLDING,
                TaxCode.is_active == True,
                TaxCode.applies_to_purchases == True,
            )
            .order_by(TaxCode.tax_code)
            .all()
        )
        wht_codes_list = [
            {
                "id": str(code.tax_code_id),
                "code": code.tax_code,
                "name": code.tax_name,
                "rate": float(code.tax_rate) * 100,  # Convert decimal to percentage for display
            }
            for code in wht_codes
        ]

        # Get bank accounts
        bank_accounts = get_accounts(db, org_id, IFRSCategory.ASSETS)
        bank_accounts_list = [
            {
                "id": str(acct.account_id),
                "code": acct.account_code,
                "name": acct.account_name,
            }
            for acct in bank_accounts
        ]

        context = {
            "suppliers_list": suppliers_list,
            "invoice_id": invoice_id,
            "invoice": selected_invoice,
            "open_invoices": open_invoices,
            "wht_codes": wht_codes_list,
            "bank_accounts": bank_accounts_list,
        }
        context.update(get_currency_context(db, organization_id))
        return context

    @staticmethod
    def payment_detail_context(
        db: Session,
        organization_id: str,
        payment_id: str,
    ) -> dict:
        """Get context for payment detail page."""
        logger.debug(
            "payment_detail_context: org=%s payment_id=%s",
            organization_id, payment_id
        )
        org_id = coerce_uuid(organization_id)
        payment = None
        try:
            payment = supplier_payment_service.get(db, payment_id)
        except Exception:
            payment = None

        if not payment or payment.organization_id != org_id:
            return {"payment": None, "supplier": None, "allocations": []}

        supplier = None
        try:
            supplier = supplier_service.get(db, org_id, str(payment.supplier_id))
        except Exception:
            supplier = None

        allocations = supplier_payment_service.get_payment_allocations(
            db,
            organization_id=org_id,
            payment_id=payment.payment_id,
        )

        invoice_map: dict[UUID, SupplierInvoice] = {}
        if allocations:
            invoice_ids = [allocation.invoice_id for allocation in allocations]
            invoices = (
                db.query(SupplierInvoice)
                .filter(SupplierInvoice.invoice_id.in_(invoice_ids))
                .all()
            )
            invoice_map = {invoice.invoice_id: invoice for invoice in invoices}

        allocations_view = [
            allocation_view(
                allocation,
                invoice_map.get(allocation.invoice_id),
                payment.currency_code,
            )
            for allocation in allocations
        ]

        # Get attachments
        attachments = attachment_service.list_for_entity(
            db,
            organization_id=org_id,
            entity_type="SUPPLIER_PAYMENT",
            entity_id=payment.payment_id,
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

        logger.debug("payment_detail_context: found %d allocations", len(allocations_view))

        return {
            "payment": payment_detail_view(payment, supplier),
            "supplier": supplier_form_view(supplier) if supplier else None,
            "allocations": allocations_view,
            "attachments": attachments_view,
        }

    @staticmethod
    def delete_payment(
        db: Session,
        organization_id: str,
        payment_id: str,
    ) -> Optional[str]:
        """Delete a payment. Returns error message or None on success."""
        logger.debug(
            "delete_payment: org=%s payment_id=%s",
            organization_id, payment_id
        )
        org_id = coerce_uuid(organization_id)
        pay_id = coerce_uuid(payment_id)

        payment = db.get(SupplierPayment, pay_id)
        if not payment or payment.organization_id != org_id:
            return "Payment not found"

        # Only DRAFT payments can be deleted
        if payment.status != APPaymentStatus.DRAFT:
            return f"Cannot delete payment with status '{payment.status.value}'. Only draft payments can be deleted."

        try:
            # Delete allocations first
            db.query(APPaymentAllocation).filter(
                APPaymentAllocation.payment_id == pay_id
            ).delete()
            db.delete(payment)
            db.commit()
            logger.info("delete_payment: deleted payment %s for org %s", pay_id, org_id)
            return None
        except Exception as e:
            db.rollback()
            logger.exception("delete_payment: failed for org %s", org_id)
            return f"Failed to delete payment: {str(e)}"

    @staticmethod
    def aging_context(
        db: Session,
        organization_id: str,
        as_of_date: Optional[str],
        supplier_id: Optional[str],
    ) -> dict:
        """Get context for AP aging report."""
        logger.debug(
            "aging_context: org=%s as_of_date=%s supplier_id=%s",
            organization_id, as_of_date, supplier_id
        )
        org_id = coerce_uuid(organization_id)
        ref_date = parse_date(as_of_date)

        if supplier_id:
            summary = ap_aging_service.calculate_supplier_aging(
                db, org_id, coerce_uuid(supplier_id), ref_date
            )
            aging_data = [summary]
        else:
            aging_data = ap_aging_service.get_aging_by_supplier(
                db, org_id, ref_date
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

        logger.debug("aging_context: found %d aging records", len(aging_data))

        return {
            "aging_data": aging_data,
            "suppliers_list": suppliers_list,
            "as_of_date": as_of_date,
            "supplier_id": supplier_id,
        }

    # =====================================================================
    # HTTP Response Methods
    # =====================================================================

    def list_payments_response(
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
        """Render payment list page."""
        context = base_context(request, auth, "AP Payments", "ap")
        context.update(
            self.list_payments_context(
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
        return templates.TemplateResponse(request, "finance/ap/payments.html", context)

    def payment_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        invoice_id: Optional[str] = None,
    ) -> HTMLResponse:
        """Render new payment form."""
        context = base_context(request, auth, "New AP Payment", "ap")
        context.update(
            self.payment_form_context(
                db,
                str(auth.organization_id),
                invoice_id=invoice_id,
            )
        )
        return templates.TemplateResponse(request, "finance/ap/payment_form.html", context)

    def payment_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        payment_id: str,
    ) -> HTMLResponse:
        """Render payment detail page."""
        context = base_context(request, auth, "AP Payment Details", "ap")
        context.update(
            self.payment_detail_context(
                db,
                str(auth.organization_id),
                payment_id,
            )
        )
        return templates.TemplateResponse(request, "finance/ap/payment_detail.html", context)

    async def create_payment_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | JSONResponse | RedirectResponse | dict:
        """Handle payment creation form submission."""
        content_type = request.headers.get("content-type", "")
        org_id = auth.organization_id
        user_id = auth.person_id
        assert org_id is not None
        assert user_id is not None

        if "application/json" in content_type:
            data = await request.json()
        else:
            form_data = await request.form()
            data = dict(form_data)

        try:
            input_data = self.build_payment_input(data)

            payment = supplier_payment_service.create_payment(
                db=db,
                organization_id=org_id,
                input=input_data,
                created_by_user_id=user_id,
            )

            if "application/json" in content_type:
                return {"success": True, "payment_id": str(payment.payment_id)}

            return RedirectResponse(
                url="/finance/ap/payments?success=Payment+created+successfully",
                status_code=303,
            )

        except Exception as e:
            logger.exception("create_payment_response: failed")
            if "application/json" in content_type:
                return JSONResponse(
                    status_code=400,
                    content={"detail": str(e)},
                )

            context = base_context(request, auth, "New AP Payment", "ap")
            context.update(self.payment_form_context(db, str(auth.organization_id)))
            context["error"] = str(e)
            context["form_data"] = data
            return templates.TemplateResponse(request, "finance/ap/payment_form.html", context)

    def delete_payment_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        payment_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Handle payment deletion."""
        error = self.delete_payment(db, str(auth.organization_id), payment_id)

        if error:
            context = base_context(request, auth, "AP Payment Details", "ap")
            context.update(
                self.payment_detail_context(
                    db,
                    str(auth.organization_id),
                    payment_id,
                )
            )
            context["error"] = error
            return templates.TemplateResponse(request, "finance/ap/payment_detail.html", context)

        return RedirectResponse(url="/finance/ap/payments", status_code=303)

    def aging_report_response(
        self,
        request: Request,
        auth: WebAuthContext,
        as_of_date: Optional[str],
        supplier_id: Optional[str],
        db: Session,
    ) -> HTMLResponse:
        """Render AP aging report page."""
        context = base_context(request, auth, "AP Aging Report", "ap")
        context.update(
            self.aging_context(
                db,
                str(auth.organization_id),
                as_of_date=as_of_date,
                supplier_id=supplier_id,
            )
        )
        return templates.TemplateResponse(request, "finance/ap/aging.html", context)

    def list_payment_batches_response(
        self,
        request: Request,
        auth: WebAuthContext,
        status: Optional[str],
        page: int,
        db: Session,
    ) -> HTMLResponse:
        """Render payment batches list page."""
        status_value = None
        if status:
            try:
                status_value = APBatchStatus(status)
            except ValueError:
                status_value = None

        limit = 50
        offset = (page - 1) * limit
        batches = payment_batch_service.list(
            db=db,
            organization_id=str(auth.organization_id),
            status=status_value,
            limit=limit,
            offset=offset,
        )

        context = base_context(request, auth, "Payment Batches", "ap")
        context.update({
            "batches": batches,
            "status": status or "",
            "page": page,
        })
        return templates.TemplateResponse(request, "finance/ap/payment_batches.html", context)

    def payment_batch_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """Render new payment batch form."""
        org_id = auth.organization_id
        assert org_id is not None
        bank_accounts = bank_account_service.list(
            db=db,
            organization_id=org_id,
            status=BankAccountStatus.active,
            limit=200,
        )
        invoices = (
            db.query(SupplierInvoice, Supplier)
            .join(Supplier, SupplierInvoice.supplier_id == Supplier.supplier_id)
            .filter(SupplierInvoice.organization_id == org_id)
            .order_by(SupplierInvoice.invoice_date.desc())
            .limit(50)
            .all()
        )
        invoices_view = [
            {
                "invoice_id": invoice.invoice_id,
                "invoice_number": invoice.invoice_number,
                "supplier_name": supplier.trading_name or supplier.legal_name,
                "due_date": invoice.due_date,
                "amount": invoice.total_amount,
                "currency_code": invoice.currency_code,
            }
            for invoice, supplier in invoices
        ]

        context = base_context(request, auth, "New Payment Batch", "ap")
        context.update({
            "bank_accounts": bank_accounts,
            "invoices": invoices_view,
            "payment_methods": [method.value for method in APPaymentMethod],
        })
        context.update(get_currency_context(db, str(auth.organization_id)))
        return templates.TemplateResponse(request, "finance/ap/payment_batch_form.html", context)

    async def upload_payment_attachment_response(
        self,
        payment_id: str,
        file: UploadFile,
        description: Optional[str],
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Handle payment attachment upload."""
        try:
            org_id = auth.organization_id
            user_id = auth.person_id
            assert org_id is not None
            assert user_id is not None
            payment = supplier_payment_service.get(db, payment_id)
            if not payment or payment.organization_id != auth.organization_id:
                return RedirectResponse(
                    url=f"/finance/ap/payments/{payment_id}?error=Payment+not+found",
                    status_code=303,
                )

            input_data = AttachmentInput(
                entity_type="SUPPLIER_PAYMENT",
                entity_id=payment_id,
                file_name=file.filename or "unnamed",
                content_type=file.content_type or "application/octet-stream",
                category=AttachmentCategory.PAYMENT,
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
                url=f"/finance/ap/payments/{payment_id}?success=Attachment+uploaded",
                status_code=303,
            )

        except ValueError as e:
            return RedirectResponse(
                url=f"/finance/ap/payments/{payment_id}?error={str(e)}",
                status_code=303,
            )
        except Exception:
            logger.exception("upload_payment_attachment_response: failed")
            return RedirectResponse(
                url=f"/finance/ap/payments/{payment_id}?error=Upload+failed",
                status_code=303,
            )
