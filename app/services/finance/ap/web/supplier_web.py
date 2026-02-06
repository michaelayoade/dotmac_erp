"""
AP Supplier Web Service - Supplier-related web view methods.

Provides view-focused data and operations for AP supplier web routes.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models.finance.ap.supplier import Supplier
from app.models.finance.ap.supplier_invoice import (
    SupplierInvoice,
    SupplierInvoiceStatus,
)
from app.models.finance.ap.supplier_payment import SupplierPayment
from app.models.finance.common.attachment import AttachmentCategory
from app.models.finance.gl.account_category import IFRSCategory
from app.services.audit_info import get_audit_service
from app.services.common import coerce_uuid
from app.services.finance.ap.supplier import SupplierInput, supplier_service
from app.services.finance.ap.web.base import (
    calculate_supplier_balance_trends,
    format_currency,
    format_date,
    format_file_size,
    get_accounts,
    invoice_status_label,
    logger,
    parse_supplier_type,
    supplier_detail_view,
    supplier_form_view,
    supplier_list_view,
)
from app.services.finance.common.attachment import AttachmentInput, attachment_service
from app.services.finance.platform.currency_context import get_currency_context
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

logger = logging.getLogger(__name__)


class SupplierWebService:
    """Web service methods for AP suppliers."""

    @staticmethod
    def build_supplier_input(form_data: dict) -> SupplierInput:
        """Build SupplierInput from form data."""
        return SupplierInput(
            supplier_code=form_data.get("supplier_code", ""),
            supplier_type=parse_supplier_type(form_data.get("supplier_type")),
            supplier_name=form_data.get("supplier_name", ""),
            trading_name=form_data.get("supplier_name"),
            tax_id=form_data.get("tax_id"),
            currency_code=form_data.get(
                "currency_code",
                settings.default_functional_currency_code,
            ),
            payment_terms_days=int(form_data.get("payment_terms_days", 30)),
            default_payable_account_id=(
                UUID(form_data["default_payable_account_id"])
                if form_data.get("default_payable_account_id")
                else UUID("00000000-0000-0000-0000-000000000001")
            ),
            default_expense_account_id=(
                UUID(form_data["default_expense_account_id"])
                if form_data.get("default_expense_account_id")
                else None
            ),
            billing_address={
                "address": form_data.get("billing_address", ""),
            }
            if form_data.get("billing_address")
            else None,
            primary_contact={
                "email": form_data.get("email", ""),
                "phone": form_data.get("phone", ""),
            }
            if form_data.get("email") or form_data.get("phone")
            else None,
        )

    @staticmethod
    def list_suppliers_context(
        db: Session,
        organization_id: str,
        search: Optional[str],
        status: Optional[str],
        page: int,
        limit: int = 50,
    ) -> dict:
        """Get context for supplier listing page."""
        logger.debug(
            "list_suppliers_context: org=%s search=%r status=%s page=%d",
            organization_id,
            search,
            status,
            page,
        )
        org_id = coerce_uuid(organization_id)
        offset = (page - 1) * limit

        is_active = None
        if status == "active":
            is_active = True
        elif status == "inactive":
            is_active = False

        query = db.query(Supplier).filter(Supplier.organization_id == org_id)
        if is_active is not None:
            query = query.filter(Supplier.is_active == is_active)
        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                (Supplier.supplier_code.ilike(search_pattern))
                | (Supplier.legal_name.ilike(search_pattern))
                | (Supplier.trading_name.ilike(search_pattern))
                | (Supplier.tax_identification_number.ilike(search_pattern))
            )

        total_count = (
            query.with_entities(func.count(Supplier.supplier_id)).scalar() or 0
        )
        suppliers = (
            query.order_by(Supplier.legal_name).limit(limit).offset(offset).all()
        )

        open_statuses = [
            SupplierInvoiceStatus.POSTED,
            SupplierInvoiceStatus.PARTIALLY_PAID,
        ]
        balances = (
            db.query(
                SupplierInvoice.supplier_id,
                func.coalesce(
                    func.sum(
                        SupplierInvoice.total_amount - SupplierInvoice.amount_paid
                    ),
                    0,
                ).label("balance"),
            )
            .filter(
                SupplierInvoice.organization_id == org_id,
                SupplierInvoice.status.in_(open_statuses),
            )
            .group_by(SupplierInvoice.supplier_id)
            .all()
        )
        balance_map = {row.supplier_id: row.balance for row in balances}

        # Use shared audit service for user names
        audit_service = get_audit_service(db)
        creator_ids = [
            supplier.created_by_user_id
            for supplier in suppliers
            if supplier.created_by_user_id
        ]
        creator_names = audit_service.get_user_names_batch(creator_ids)

        # Calculate balance trends for sparkline charts
        supplier_ids = [s.supplier_id for s in suppliers]
        balance_trends = calculate_supplier_balance_trends(db, org_id, supplier_ids)

        suppliers_view = [
            supplier_list_view(
                supplier,
                balance_map.get(supplier.supplier_id, Decimal("0")),
                creator_names.get(supplier.created_by_user_id)
                if supplier.created_by_user_id
                else None,
                balance_trends.get(supplier.supplier_id),
            )
            for supplier in suppliers
        ]

        total_pages = max(1, (total_count + limit - 1) // limit)

        # Calculate stats for template header cards
        total_suppliers = (
            db.query(func.count(Supplier.supplier_id))
            .filter(Supplier.organization_id == org_id)
            .scalar()
            or 0
        )
        active_count = (
            db.query(func.count(Supplier.supplier_id))
            .filter(Supplier.organization_id == org_id, Supplier.is_active == True)
            .scalar()
            or 0
        )
        total_payables_raw = db.query(
            func.coalesce(
                func.sum(SupplierInvoice.total_amount - SupplierInvoice.amount_paid), 0
            )
        ).filter(
            SupplierInvoice.organization_id == org_id,
            SupplierInvoice.status.in_(open_statuses),
        ).scalar() or Decimal("0")
        overdue_count = (
            db.query(func.count(SupplierInvoice.invoice_id))
            .filter(
                SupplierInvoice.organization_id == org_id,
                SupplierInvoice.status.in_(open_statuses),
                SupplierInvoice.due_date < date.today(),
            )
            .scalar()
            or 0
        )

        logger.debug("list_suppliers_context: found %d suppliers", total_count)

        return {
            "suppliers": suppliers_view,
            "search": search,
            "status": status,
            "page": page,
            "limit": limit,
            "per_page": limit,
            "offset": offset,
            "total_count": total_count,
            "total_pages": total_pages,
            # Stats for header cards
            "total_suppliers": total_suppliers,
            "active_count": active_count,
            "total_payables": format_currency(total_payables_raw),
            "overdue_count": overdue_count,
        }

    @staticmethod
    def supplier_form_context(
        db: Session,
        organization_id: str,
        supplier_id: Optional[str] = None,
    ) -> dict:
        """Get context for supplier create/edit form."""
        logger.debug(
            "supplier_form_context: org=%s supplier_id=%s", organization_id, supplier_id
        )
        org_id = coerce_uuid(organization_id)
        supplier = None
        if supplier_id:
            try:
                supplier = supplier_service.get(db, org_id, supplier_id)
            except Exception:
                supplier = None
        supplier_view = supplier_form_view(supplier) if supplier else None

        expense_accounts = get_accounts(db, org_id, IFRSCategory.EXPENSES)
        payable_accounts = get_accounts(db, org_id, IFRSCategory.LIABILITIES, "AP")

        context = {
            "supplier": supplier_view,
            "expense_accounts": expense_accounts,
            "payable_accounts": payable_accounts,
        }
        context.update(get_currency_context(db, organization_id))

        return context

    @staticmethod
    def supplier_detail_context(
        db: Session,
        organization_id: str,
        supplier_id: str,
    ) -> dict:
        """Get context for supplier detail page."""
        logger.debug(
            "supplier_detail_context: org=%s supplier_id=%s",
            organization_id,
            supplier_id,
        )
        org_id = coerce_uuid(organization_id)
        supplier = None
        try:
            supplier = supplier_service.get(db, org_id, supplier_id)
        except Exception:
            supplier = None

        if not supplier or supplier.organization_id != org_id:
            return {"supplier": None, "open_invoices": []}

        open_statuses = [
            SupplierInvoiceStatus.POSTED,
            SupplierInvoiceStatus.PARTIALLY_PAID,
        ]

        balance = db.query(
            func.coalesce(
                func.sum(SupplierInvoice.total_amount - SupplierInvoice.amount_paid),
                0,
            )
        ).filter(
            SupplierInvoice.organization_id == org_id,
            SupplierInvoice.supplier_id == supplier.supplier_id,
            SupplierInvoice.status.in_(open_statuses),
        ).scalar() or Decimal("0")

        invoices = (
            db.query(SupplierInvoice)
            .filter(
                SupplierInvoice.organization_id == org_id,
                SupplierInvoice.supplier_id == supplier.supplier_id,
                SupplierInvoice.status.in_(open_statuses),
            )
            .order_by(SupplierInvoice.due_date)
            .limit(10)
            .all()
        )

        today = date.today()
        open_invoices = []
        for invoice in invoices:
            balance_due = invoice.total_amount - invoice.amount_paid
            open_invoices.append(
                {
                    "invoice_id": invoice.invoice_id,
                    "invoice_number": invoice.invoice_number,
                    "invoice_date": format_date(invoice.invoice_date),
                    "due_date": format_date(invoice.due_date),
                    "total_amount": format_currency(
                        invoice.total_amount,
                        invoice.currency_code,
                    ),
                    "balance": format_currency(
                        balance_due,
                        invoice.currency_code,
                    ),
                    "status": invoice_status_label(invoice.status),
                    "is_overdue": (
                        invoice.due_date < today
                        and invoice.status
                        not in {SupplierInvoiceStatus.PAID, SupplierInvoiceStatus.VOID}
                    ),
                }
            )

        # Get attachments
        attachments = attachment_service.list_for_entity(
            db,
            organization_id=org_id,
            entity_type="SUPPLIER",
            entity_id=supplier.supplier_id,
        )
        attachments_view = [
            {
                "attachment_id": str(att.attachment_id),
                "file_name": att.file_name,
                "file_size_display": format_file_size(att.file_size),
                "content_type": att.content_type,
                "uploaded_at": att.uploaded_at.strftime("%Y-%m-%d %H:%M"),
                "description": att.description or "",
            }
            for att in attachments
        ]

        logger.debug(
            "supplier_detail_context: found %d open invoices", len(open_invoices)
        )

        return {
            "supplier": supplier_detail_view(supplier, balance),
            "open_invoices": open_invoices,
            "attachments": attachments_view,
        }

    @staticmethod
    def delete_supplier(
        db: Session,
        organization_id: str,
        supplier_id: str,
    ) -> Optional[str]:
        """Delete a supplier. Returns error message or None on success."""
        logger.debug(
            "delete_supplier: org=%s supplier_id=%s", organization_id, supplier_id
        )
        org_id = coerce_uuid(organization_id)
        sup_id = coerce_uuid(supplier_id)

        supplier = db.get(Supplier, sup_id)
        if not supplier or supplier.organization_id != org_id:
            return "Supplier not found"

        # Check for existing invoices
        invoice_count = (
            db.query(func.count(SupplierInvoice.invoice_id))
            .filter(
                SupplierInvoice.organization_id == org_id,
                SupplierInvoice.supplier_id == sup_id,
            )
            .scalar()
            or 0
        )

        if invoice_count > 0:
            return f"Cannot delete supplier with {invoice_count} invoice(s). Deactivate instead."

        # Check for existing payments
        payment_count = (
            db.query(func.count(SupplierPayment.payment_id))
            .filter(
                SupplierPayment.organization_id == org_id,
                SupplierPayment.supplier_id == sup_id,
            )
            .scalar()
            or 0
        )

        if payment_count > 0:
            return f"Cannot delete supplier with {payment_count} payment(s). Deactivate instead."

        try:
            db.delete(supplier)
            db.commit()
            logger.info(
                "delete_supplier: deleted supplier %s for org %s", sup_id, org_id
            )
            return None
        except Exception as e:
            db.rollback()
            logger.exception("delete_supplier: failed for org %s", org_id)
            return f"Failed to delete supplier: {str(e)}"

    # =====================================================================
    # HTTP Response Methods
    # =====================================================================

    def list_suppliers_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        search: Optional[str],
        status: Optional[str],
        page: int,
        limit: int = 50,
    ) -> HTMLResponse:
        """Render supplier list page."""
        context = base_context(request, auth, "Suppliers", "ap")
        context.update(
            self.list_suppliers_context(
                db,
                str(auth.organization_id),
                search=search,
                status=status,
                page=page,
                limit=limit,
            )
        )
        return templates.TemplateResponse(request, "finance/ap/suppliers.html", context)

    def supplier_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """Render new supplier form."""
        context = base_context(request, auth, "New Supplier", "ap")
        context.update(self.supplier_form_context(db, str(auth.organization_id)))
        return templates.TemplateResponse(
            request, "finance/ap/supplier_form.html", context
        )

    def supplier_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        supplier_id: str,
    ) -> HTMLResponse:
        """Render supplier detail page."""
        context = base_context(request, auth, "Supplier Details", "ap")
        context.update(
            self.supplier_detail_context(
                db,
                str(auth.organization_id),
                supplier_id,
            )
        )
        return templates.TemplateResponse(
            request, "finance/ap/supplier_detail.html", context
        )

    def supplier_edit_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        supplier_id: str,
    ) -> HTMLResponse:
        """Render supplier edit form."""
        context = base_context(request, auth, "Edit Supplier", "ap")
        context.update(
            self.supplier_form_context(db, str(auth.organization_id), supplier_id)
        )
        return templates.TemplateResponse(
            request, "finance/ap/supplier_form.html", context
        )

    async def create_supplier_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Handle supplier creation form submission."""
        form_data = await request.form()

        try:
            org_id = auth.organization_id
            assert org_id is not None
            input_data = self.build_supplier_input(dict(form_data))

            supplier_service.create_supplier(
                db=db,
                organization_id=org_id,
                input=input_data,
            )

            return RedirectResponse(
                url="/finance/ap/suppliers?success=Supplier+created+successfully",
                status_code=303,
            )

        except Exception as e:
            logger.exception("create_supplier_response: failed")
            context = base_context(request, auth, "New Supplier", "ap")
            context.update(self.supplier_form_context(db, str(auth.organization_id)))
            context["error"] = str(e)
            context["form_data"] = dict(form_data)
            return templates.TemplateResponse(
                request, "finance/ap/supplier_form.html", context
            )

    async def update_supplier_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        supplier_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Handle supplier update form submission."""
        form_data = await request.form()

        try:
            org_id = auth.organization_id
            assert org_id is not None
            input_data = self.build_supplier_input(dict(form_data))

            supplier_service.update_supplier(
                db=db,
                organization_id=org_id,
                supplier_id=UUID(supplier_id),
                input=input_data,
            )

            return RedirectResponse(
                url="/finance/ap/suppliers?success=Supplier+updated+successfully",
                status_code=303,
            )

        except Exception as e:
            logger.exception("update_supplier_response: failed")
            context = base_context(request, auth, "Edit Supplier", "ap")
            context.update(
                self.supplier_form_context(db, str(auth.organization_id), supplier_id)
            )
            context["error"] = str(e)
            context["form_data"] = dict(form_data)
            return templates.TemplateResponse(
                request, "finance/ap/supplier_form.html", context
            )

    def delete_supplier_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        supplier_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Handle supplier deletion."""
        error = self.delete_supplier(db, str(auth.organization_id), supplier_id)

        if error:
            context = base_context(request, auth, "Supplier Details", "ap")
            context.update(
                self.supplier_detail_context(
                    db,
                    str(auth.organization_id),
                    supplier_id,
                )
            )
            context["error"] = error
            return templates.TemplateResponse(
                request, "finance/ap/supplier_detail.html", context
            )

        return RedirectResponse(url="/finance/ap/suppliers", status_code=303)

    async def upload_supplier_attachment_response(
        self,
        supplier_id: str,
        file: UploadFile,
        description: Optional[str],
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Handle supplier attachment upload."""
        try:
            org_id = auth.organization_id
            user_id = auth.person_id
            assert org_id is not None
            assert user_id is not None
            supplier = supplier_service.get(db, org_id, supplier_id)
            if not supplier or supplier.organization_id != auth.organization_id:
                return RedirectResponse(
                    url=f"/finance/ap/suppliers/{supplier_id}?error=Supplier+not+found",
                    status_code=303,
                )

            input_data = AttachmentInput(
                entity_type="SUPPLIER",
                entity_id=supplier_id,
                file_name=file.filename or "unnamed",
                content_type=file.content_type or "application/octet-stream",
                category=AttachmentCategory.SUPPLIER,
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
                url=f"/finance/ap/suppliers/{supplier_id}?success=Attachment+uploaded",
                status_code=303,
            )

        except ValueError as e:
            return RedirectResponse(
                url=f"/finance/ap/suppliers/{supplier_id}?error={str(e)}",
                status_code=303,
            )
        except Exception:
            logger.exception("upload_supplier_attachment_response: failed")
            return RedirectResponse(
                url=f"/finance/ap/suppliers/{supplier_id}?error=Upload+failed",
                status_code=303,
            )


# Module-level instance for convenience
supplier_web_service = SupplierWebService()
