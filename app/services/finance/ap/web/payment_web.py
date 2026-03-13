"""
AP Payment Web Service - Payment-related web view methods.

Provides view-focused data and operations for AP payment and aging web routes.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.finance.ap.payment_batch import APBatchStatus
from app.models.finance.ap.supplier import Supplier
from app.models.finance.ap.supplier_invoice import (
    SupplierInvoice,
    SupplierInvoiceStatus,
)
from app.models.finance.ap.supplier_payment import (
    APPaymentMethod,
    APPaymentStatus,
    SupplierPayment,
)
from app.models.finance.banking.bank_account import BankAccountStatus
from app.models.finance.common.attachment import AttachmentCategory
from app.services.common import coerce_uuid
from app.services.common_filters import build_active_filters
from app.services.finance.ap.ap_aging import ap_aging_service
from app.services.finance.ap.payment_batch import payment_batch_service
from app.services.finance.ap.supplier import supplier_service
from app.services.finance.ap.supplier_payment import (
    SupplierPaymentInput,
    supplier_payment_service,
)
from app.services.finance.ap.web.base import (
    allocation_view,
    format_currency,
    format_date,
    format_file_size,
    logger,
    parse_date,
    payment_detail_view,
    payment_status_label,
    recent_activity_view,
    supplier_display_name,
    supplier_form_view,
    supplier_option_view,
)
from app.services.finance.banking.bank_account import bank_account_service
from app.services.finance.common.attachment import AttachmentInput, attachment_service
from app.services.finance.platform.currency_context import get_currency_context
from app.templates import templates
from app.web.deps import WebAuthContext, base_context


class PaymentWebService:
    """Web service methods for AP payments/supplier payments and aging reports."""

    @staticmethod
    def build_payment_input(
        db: Session, data: dict, organization_id: UUID
    ) -> SupplierPaymentInput:
        """Build SupplierPaymentInput from form data."""
        logger.debug("build_payment_input: building input from form data")
        payload = dict(data)
        return supplier_payment_service.build_input_from_payload(
            db=db,
            organization_id=organization_id,
            payload=payload,
        )

    @staticmethod
    def list_payments_context(
        db: Session,
        organization_id: str,
        search: str | None,
        supplier_id: str | None,
        status: str | None,
        start_date: str | None,
        end_date: str | None,
        page: int,
        sort: str | None = None,
        sort_dir: str | None = None,
        limit: int = 50,
    ) -> dict:
        """Get context for payment listing page."""
        logger.debug(
            "list_payments_context: org=%s search=%r supplier_id=%s status=%s page=%d",
            organization_id,
            search,
            supplier_id,
            status,
            page,
        )
        offset = (page - 1) * limit
        org_id = coerce_uuid(organization_id)
        from app.services.finance.ap.payment_query import build_payment_query

        base_stmt = build_payment_query(
            db=db,
            organization_id=organization_id,
            search=search,
            supplier_id=supplier_id,
            status=status,
            start_date=start_date,
            end_date=end_date,
        )

        total_count = (
            db.scalar(select(func.count()).select_from(base_stmt.subquery())) or 0
        )

        sort_dir_norm = (sort_dir or "desc").lower()
        if sort_dir_norm not in {"asc", "desc"}:
            sort_dir_norm = "desc"

        order_map = {
            "payment_date": SupplierPayment.payment_date,
            "payment_number": SupplierPayment.payment_number,
            "amount": SupplierPayment.amount,
            "status": SupplierPayment.status,
        }
        order_col = order_map.get(sort or "", SupplierPayment.payment_date)
        order_expr = order_col.asc() if sort_dir_norm == "asc" else order_col.desc()

        # Add Supplier to result columns so we get (SupplierPayment, Supplier) tuples
        payments = db.execute(
            base_stmt.add_columns(Supplier)
            .order_by(order_expr, SupplierPayment.payment_date.desc())
            .limit(limit)
            .offset(offset)
        ).all()

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

        active_filters = build_active_filters(
            params={
                "status": status,
                "supplier_id": supplier_id,
                "start_date": start_date,
                "end_date": end_date,
            },
            labels={"start_date": "From", "end_date": "To"},
        )
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
            "active_filters": active_filters,
            "sort": sort or "",
            "sort_dir": sort_dir_norm,
        }

    @staticmethod
    def payment_form_context(
        db: Session,
        organization_id: str,
        invoice_id: str | None = None,
    ) -> dict:
        """Get context for payment create/edit form."""
        logger.debug(
            "payment_form_context: org=%s invoice_id=%s", organization_id, invoice_id
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
            SupplierInvoiceStatus.APPROVED,
            SupplierInvoiceStatus.POSTED,
            SupplierInvoiceStatus.PARTIALLY_PAID,
        ]

        stmt = (
            select(SupplierInvoice, Supplier)
            .join(Supplier, SupplierInvoice.supplier_id == Supplier.supplier_id)
            .where(
                SupplierInvoice.organization_id == org_id,
                SupplierInvoice.status.in_(open_statuses),
            )
        )

        if invoice_id:
            stmt = stmt.where(SupplierInvoice.invoice_id == coerce_uuid(invoice_id))

        rows = db.execute(stmt.order_by(SupplierInvoice.due_date)).all()

        open_invoices = []
        selected_invoice = None
        for invoice, supplier in rows:
            balance = invoice.total_amount - invoice.amount_paid
            view = {
                "invoice_id": str(invoice.invoice_id),
                "invoice_number": invoice.invoice_number,
                "supplier_id": str(invoice.supplier_id),
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
        wht_codes = db.scalars(
            select(TaxCode)
            .where(
                TaxCode.organization_id == org_id,
                TaxCode.tax_type == TaxType.WITHHOLDING,
                TaxCode.is_active == True,
                TaxCode.applies_to_purchases == True,
            )
            .order_by(TaxCode.tax_code)
        )
        wht_codes = wht_codes.all()
        wht_codes_list = [
            {
                "id": str(code.tax_code_id),
                "code": code.tax_code,
                "name": code.tax_name,
                "rate": float(code.tax_rate)
                * 100,  # Convert decimal to percentage for display
            }
            for code in wht_codes
        ]

        # Get bank accounts — filter to actual bank/cash accounts, not all assets
        from app.models.finance.banking.bank_account import BankAccount
        from app.models.finance.gl.account import Account

        bank_accounts_query = db.execute(
            select(BankAccount, Account)
            .join(Account, BankAccount.gl_account_id == Account.account_id)
            .where(
                BankAccount.organization_id == org_id,
                BankAccount.status == BankAccountStatus.active,
            )
            .order_by(Account.account_code)
        )
        bank_accounts_query = bank_accounts_query.all()
        bank_accounts_list = [
            {
                "id": str(ba.gl_account_id),
                "code": acct.account_code,
                "name": f"{ba.bank_name} - {ba.account_name} ({acct.account_code})",
            }
            for ba, acct in bank_accounts_query
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
            "payment_detail_context: org=%s payment_id=%s", organization_id, payment_id
        )
        org_id = coerce_uuid(organization_id)
        payment = None
        try:
            payment = supplier_payment_service.get(db, payment_id)
        except (ValueError, LookupError) as e:
            logger.warning("Failed to load entity: %s", e)
            payment = None

        if not payment or payment.organization_id != org_id:
            return {"payment": None, "supplier": None, "allocations": []}

        supplier = None
        try:
            supplier = supplier_service.get(db, org_id, str(payment.supplier_id))
        except (ValueError, LookupError) as e:
            logger.warning("Failed to load entity: %s", e)
            supplier = None

        allocations = supplier_payment_service.get_payment_allocations(
            db,
            organization_id=org_id,
            payment_id=payment.payment_id,
        )

        invoice_map: dict[UUID, SupplierInvoice] = {}
        if allocations:
            invoice_ids = [allocation.invoice_id for allocation in allocations]
            invoices = db.scalars(
                select(SupplierInvoice).where(
                    SupplierInvoice.invoice_id.in_(invoice_ids)
                )
            )
            invoices = invoices.all()
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

        logger.debug(
            "payment_detail_context: found %d allocations", len(allocations_view)
        )

        # Resolve bank account name
        bank_account_name = ""
        if payment.bank_account_id:
            from app.models.finance.gl.account import Account

            acct = db.get(Account, payment.bank_account_id)
            if acct:
                bank_account_name = f"{acct.account_code} - {acct.account_name}"

        # Resolve WHT code name
        wht_code_name = ""
        if payment.withholding_tax_code_id:
            from app.models.finance.tax.tax_code import TaxCode

            wht_code = db.get(TaxCode, payment.withholding_tax_code_id)
            if wht_code:
                rate_pct = float(wht_code.tax_rate * 100)
                wht_code_name = f"{wht_code.tax_name} ({rate_pct:g}%)"

        return {
            "payment": payment_detail_view(
                payment, supplier, bank_account_name, wht_code_name
            ),
            "supplier": supplier_form_view(supplier) if supplier else None,
            "allocations": allocations_view,
            "attachments": attachments_view,
            "recent_activity": recent_activity_view(
                db,
                org_id,
                table_schema="ap",
                table_name="supplier_payment",
                record_id=str(payment.payment_id),
                limit=10,
            ),
        }

    @staticmethod
    def delete_payment(
        db: Session,
        organization_id: str,
        payment_id: str,
    ) -> str | None:
        """Delete a payment. Returns error message or None on success."""
        logger.debug(
            "delete_payment: org=%s payment_id=%s", organization_id, payment_id
        )
        org_id = coerce_uuid(organization_id)
        pay_id = coerce_uuid(payment_id)

        try:
            supplier_payment_service.delete_payment(db, org_id, pay_id)
            logger.info("delete_payment: deleted payment %s for org %s", pay_id, org_id)
            return None
        except HTTPException as exc:
            return exc.detail
        except Exception as e:
            logger.exception("delete_payment: failed for org %s", org_id)
            return f"Failed to delete payment: {str(e)}"

    @staticmethod
    def aging_context(
        db: Session,
        organization_id: str,
        as_of_date: str | None,
        supplier_id: str | None,
    ) -> dict:
        """Get context for AP aging report."""
        logger.debug(
            "aging_context: org=%s as_of_date=%s supplier_id=%s",
            organization_id,
            as_of_date,
            supplier_id,
        )
        org_id = coerce_uuid(organization_id)
        ref_date = parse_date(as_of_date)

        if supplier_id:
            summary = ap_aging_service.calculate_supplier_aging(
                db, org_id, coerce_uuid(supplier_id), ref_date
            )
            aging_data = [summary]
        else:
            aging_data = ap_aging_service.get_aging_by_supplier(db, org_id, ref_date)

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

        supplier_aging: list[dict] = []
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

            supplier_aging.append(
                {
                    "supplier_id": row.supplier_id,
                    "supplier_code": row.supplier_code,
                    "supplier_name": row.supplier_name,
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
            "dpo": 0,
        }

        suppliers = [
            {
                "supplier_id": option["supplier_id"],
                "legal_name": option["supplier_name"],
            }
            for option in suppliers_list
        ]

        return {
            "aging_summary": aging_summary,
            "supplier_aging": supplier_aging,
            "suppliers": suppliers,
            "selected_supplier_id": supplier_id,
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
                sort=sort,
                sort_dir=sort_dir,
            )
        )
        return templates.TemplateResponse(request, "finance/ap/payments.html", context)

    def payment_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        invoice_id: str | None = None,
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
        return templates.TemplateResponse(
            request, "finance/ap/payment_form.html", context
        )

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
        return templates.TemplateResponse(
            request, "finance/ap/payment_detail.html", context
        )

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
            input_data = self.build_payment_input(db, data, org_id)

            payment = supplier_payment_service.create_payment(
                db=db,
                organization_id=org_id,
                input=input_data,
                created_by_user_id=user_id,
            )

            if "application/json" in content_type:
                return {"success": True, "payment_id": str(payment.payment_id)}

            redirect_url = "/finance/ap/payments?success=Payment+created+successfully"
            if request.headers.get("HX-Request"):
                return HTMLResponse(
                    content="",
                    headers={"HX-Redirect": redirect_url},
                )
            return RedirectResponse(url=redirect_url, status_code=303)

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
            return templates.TemplateResponse(
                request, "finance/ap/payment_form.html", context
            )

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
            return templates.TemplateResponse(
                request, "finance/ap/payment_detail.html", context
            )

        return RedirectResponse(
            url="/finance/ap/payments?success=Record+deleted+successfully",
            status_code=303,
        )

    def payment_edit_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        payment_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Render the edit form for draft payments."""
        org_id = coerce_uuid(auth.organization_id)
        pay_id = coerce_uuid(payment_id)

        payment = db.get(SupplierPayment, pay_id)
        if not payment or payment.organization_id != org_id:
            return RedirectResponse(
                url="/finance/ap/payments?success=Record+updated+successfully",
                status_code=303,
            )

        if payment.status != APPaymentStatus.DRAFT:
            return RedirectResponse(
                url=f"/finance/ap/payments/{payment_id}?error=Only+draft+payments+can+be+edited",
                status_code=303,
            )

        context = base_context(request, auth, "Edit AP Payment", "ap")
        context.update(self.payment_form_context(db, str(auth.organization_id)))
        context["payment"] = {
            "payment_id": payment.payment_id,
            "payment_number": payment.payment_number,
            "supplier_id": payment.supplier_id,
            "payment_date": payment.payment_date,
            "payment_method": payment.payment_method.value
            if payment.payment_method
            else "",
            "currency_code": payment.currency_code,
            "amount": payment.amount,
            "reference": payment.reference,
            "description": getattr(payment, "description", None),
            "bank_account_id": payment.bank_account_id,
        }
        return templates.TemplateResponse(
            request, "finance/ap/payment_form.html", context
        )

    async def update_payment_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        payment_id: str,
    ) -> HTMLResponse | JSONResponse | RedirectResponse:
        """Handle payment update form submission."""
        content_type = request.headers.get("content-type", "")

        if "application/json" in content_type:
            data = await request.json()
        else:
            form_data = await request.form()
            data = dict(form_data)

        try:
            return RedirectResponse(
                url=f"/finance/ap/payments/{payment_id}?error=Payment+update+not+yet+implemented",
                status_code=303,
            )
        except Exception as e:
            if "application/json" in content_type:
                return JSONResponse(
                    status_code=400,
                    content={"detail": str(e)},
                )

            context = base_context(request, auth, "Edit AP Payment", "ap")
            context.update(self.payment_form_context(db, str(auth.organization_id)))
            context["error"] = str(e)
            context["form_data"] = data
            return templates.TemplateResponse(
                request, "finance/ap/payment_form.html", context
            )

    def aging_report_response(
        self,
        request: Request,
        auth: WebAuthContext,
        as_of_date: str | None,
        supplier_id: str | None,
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
        status: str | None,
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
        context.update(
            {
                "batches": batches,
                "status": status or "",
                "page": page,
            }
        )
        return templates.TemplateResponse(
            request, "finance/ap/payment_batches.html", context
        )

    def payment_batch_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """Render new payment batch form."""
        org_id = auth.organization_id
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        bank_accounts = bank_account_service.list(
            db=db,
            organization_id=org_id,
            status=BankAccountStatus.active,
            limit=200,
        )
        invoices = db.execute(
            select(SupplierInvoice, Supplier)
            .join(Supplier, SupplierInvoice.supplier_id == Supplier.supplier_id)
            .where(SupplierInvoice.organization_id == org_id)
            .order_by(SupplierInvoice.invoice_date.desc())
            .limit(50)
        )
        invoices = invoices.all()
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
        context.update(
            {
                "bank_accounts": bank_accounts,
                "invoices": invoices_view,
                "payment_methods": [method.value for method in APPaymentMethod],
                "form_data": {},
            }
        )
        context.update(get_currency_context(db, str(auth.organization_id)))
        return templates.TemplateResponse(
            request, "finance/ap/payment_batch_form.html", context
        )

    async def upload_payment_attachment_response(
        self,
        payment_id: str,
        file: UploadFile,
        description: str | None,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Handle payment attachment upload."""
        try:
            org_id = auth.organization_id
            user_id = auth.person_id
            if org_id is None:
                raise HTTPException(status_code=401, detail="Authentication required")
            if user_id is None:
                raise HTTPException(status_code=401, detail="Authentication required")
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
