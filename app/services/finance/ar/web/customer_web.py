"""
AR Customer Web Service - Customer-related web view methods.

Provides view-focused data and operations for AR customer web routes.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.finance.ar.customer import Customer
from app.models.finance.ar.customer_payment import CustomerPayment
from app.models.finance.ar.invoice import Invoice, InvoiceStatus
from app.models.finance.ar.quote import Quote  # noqa: F811
from app.models.finance.ar.sales_order import SalesOrder  # noqa: F811
from app.models.finance.common.attachment import AttachmentCategory
from app.models.finance.gl.account_category import IFRSCategory
from app.services.audit_info import get_audit_service
from app.services.common import coerce_uuid
from app.services.common_filters import build_active_filters
from app.services.finance.ar.customer import CustomerInput, customer_service
from app.services.finance.ar.web.base import (
    calculate_customer_balance_trends,
    customer_detail_view,
    customer_display_name,
    customer_form_view,
    customer_list_view,
    format_currency,
    format_date,
    format_file_size,
    get_accounts,
    invoice_status_label,
)
from app.services.finance.common.attachment import AttachmentInput, attachment_service
from app.services.finance.platform.currency_context import get_currency_context
from app.services.finance.tax.tax_master import tax_code_service
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

logger = logging.getLogger(__name__)


class CustomerWebService:
    """Web service methods for AR customers."""

    @staticmethod
    def build_customer_input(
        db: Session, form_data: dict, organization_id: UUID
    ) -> CustomerInput:
        """Build CustomerInput from form data."""
        payload = dict(form_data)
        return customer_service.build_input_from_payload(
            db=db,
            organization_id=organization_id,
            payload=payload,
        )

    @staticmethod
    def list_customers_context(
        db: Session,
        organization_id: str,
        search: str | None,
        status: str | None,
        page: int,
        sort: str | None = None,
        sort_dir: str | None = None,
        limit: int = 50,
        parent_customer_id: str | None = None,
    ) -> dict:
        """Get context for customer listing page."""
        logger.debug(
            "list_customers_context: org=%s search=%r status=%s page=%d",
            organization_id,
            search,
            status,
            page,
        )
        offset = (page - 1) * limit
        org_id = coerce_uuid(organization_id)
        from app.services.finance.ar.customer_query import build_customer_query

        sort_dir_norm = (sort_dir or "asc").lower()
        if sort_dir_norm not in {"asc", "desc"}:
            sort_dir_norm = "asc"

        query = build_customer_query(
            db=db,
            organization_id=organization_id,
            search=search,
            status=status,
            parent_customer_id=parent_customer_id,
        )

        count_subq = query.with_only_columns(Customer.customer_id).subquery()
        total_count = db.scalar(select(func.count()).select_from(count_subq)) or 0

        order_map = {
            "customer_code": Customer.customer_code,
            "legal_name": Customer.legal_name,
            "status": Customer.is_active,
        }
        order_col = order_map.get(sort or "", Customer.legal_name)
        order_expr = order_col.asc() if sort_dir_norm == "asc" else order_col.desc()

        customers = list(
            db.scalars(
                query.order_by(order_expr, Customer.legal_name)
                .limit(limit)
                .offset(offset)
            ).all()
        )

        open_statuses = [
            InvoiceStatus.POSTED,
            InvoiceStatus.PARTIALLY_PAID,
            InvoiceStatus.OVERDUE,
        ]
        balances = db.execute(
            select(
                Invoice.customer_id,
                func.coalesce(
                    func.sum(Invoice.total_amount - Invoice.amount_paid), 0
                ).label("balance"),
            )
            .where(
                Invoice.organization_id == org_id,
                Invoice.status.in_(open_statuses),
            )
            .group_by(Invoice.customer_id)
        )
        balances = balances.all()
        balance_map = {row.customer_id: row.balance for row in balances}

        # Use shared audit service for user names
        audit_service = get_audit_service(db)
        creator_ids = [
            customer.created_by_user_id
            for customer in customers
            if customer.created_by_user_id
        ]
        creator_names = audit_service.get_user_names_batch(creator_ids)

        # Calculate balance trends for sparkline charts
        customer_ids = [c.customer_id for c in customers]
        balance_trends = calculate_customer_balance_trends(db, org_id, customer_ids)

        # Count child customers per parent for sub-account badges
        child_count_map: dict[UUID, int] = {}
        if customer_ids:
            child_counts = db.execute(
                select(
                    Customer.parent_customer_id,
                    func.count(Customer.customer_id).label("cnt"),
                )
                .where(
                    Customer.organization_id == org_id,
                    Customer.parent_customer_id.in_(customer_ids),
                )
                .group_by(Customer.parent_customer_id)
            ).all()
            child_count_map = {row.parent_customer_id: row.cnt for row in child_counts}

        customers_view = [
            customer_list_view(
                customer,
                balance_map.get(customer.customer_id, Decimal("0")),
                creator_names.get(customer.created_by_user_id)
                if customer.created_by_user_id
                else None,
                balance_trends.get(customer.customer_id),
                child_count=child_count_map.get(customer.customer_id, 0),
            )
            for customer in customers
        ]

        total_pages = max(1, (total_count + limit - 1) // limit)

        logger.debug("list_customers_context: found %d customers", total_count)

        active_filters = build_active_filters(
            params={"status": status},
        )
        return {
            "customers": customers_view,
            "search": search,
            "status": status,
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
    def customer_form_context(
        db: Session,
        organization_id: str,
        customer_id: str | None = None,
    ) -> dict:
        """Get context for customer create/edit form."""
        logger.debug(
            "customer_form_context: org=%s customer_id=%s", organization_id, customer_id
        )
        org_id = coerce_uuid(organization_id)
        customer = None
        if customer_id:
            try:
                customer = customer_service.get(db, org_id, customer_id)
            except Exception:
                customer = None
        customer_view = customer_form_view(customer) if customer else None

        revenue_accounts = get_accounts(db, org_id, IFRSCategory.REVENUE)
        receivable_accounts = get_accounts(db, org_id, IFRSCategory.ASSETS, "AR")
        tax_codes = [
            {
                "tax_code_id": str(tax.tax_code_id),
                "tax_code": tax.tax_code,
                "tax_name": tax.tax_name,
            }
            for tax in tax_code_service.list(
                db,
                organization_id=org_id,
                is_active=True,
                applies_to_sales=True,
                limit=200,
            )
        ]

        # Parent customer candidates (exclude self if editing)
        parent_query = (
            select(Customer)
            .where(
                Customer.organization_id == org_id,
                Customer.is_active.is_(True),
            )
            .order_by(Customer.legal_name)
            .limit(500)
        )
        if customer:
            parent_query = parent_query.where(
                Customer.customer_id != customer.customer_id,
            )
        parent_customers = [
            {
                "customer_id": str(c.customer_id),
                "display_name": customer_display_name(c),
                "customer_code": c.customer_code,
            }
            for c in db.scalars(parent_query).all()
        ]

        context = {
            "customer": customer_view,
            "revenue_accounts": revenue_accounts,
            "receivable_accounts": receivable_accounts,
            "tax_codes": tax_codes,
            "parent_customers": parent_customers,
        }
        context.update(get_currency_context(db, organization_id))

        return context

    @staticmethod
    def customer_detail_context(
        db: Session,
        organization_id: str,
        customer_id: str,
    ) -> dict:
        """Get context for customer detail page."""
        logger.debug(
            "customer_detail_context: org=%s customer_id=%s",
            organization_id,
            customer_id,
        )
        org_id = coerce_uuid(organization_id)
        customer = None
        try:
            customer = customer_service.get(db, org_id, customer_id)
        except Exception:
            customer = None

        if not customer or customer.organization_id != org_id:
            return {
                "customer": None,
                "invoices": [],
                "receipts": [],
                "quotes": [],
                "sales_orders": [],
            }

        default_tax_code_label = None
        if customer.default_tax_code_id:
            try:
                tax_code = tax_code_service.get(
                    db, str(customer.default_tax_code_id), org_id
                )
                if tax_code and tax_code.organization_id == org_id:
                    default_tax_code_label = (
                        f"{tax_code.tax_code} - {tax_code.tax_name}"
                    )
            except Exception:
                default_tax_code_label = None

        open_statuses = [
            InvoiceStatus.POSTED,
            InvoiceStatus.PARTIALLY_PAID,
            InvoiceStatus.OVERDUE,
        ]

        balance = db.scalar(
            select(
                func.coalesce(
                    func.sum(Invoice.total_amount - Invoice.amount_paid),
                    0,
                )
            ).where(
                Invoice.organization_id == org_id,
                Invoice.customer_id == customer.customer_id,
                Invoice.status.in_(open_statuses),
            )
        ) or Decimal("0")

        from datetime import date

        today = date.today()

        # All invoices (all statuses)
        all_invoices_query = db.scalars(
            select(Invoice)
            .where(
                Invoice.organization_id == org_id,
                Invoice.customer_id == customer.customer_id,
            )
            .order_by(Invoice.invoice_date.desc())
            .limit(20)
        )
        all_invoices_query = all_invoices_query.all()
        invoices_view: list[dict] = []
        for inv in all_invoices_query:
            balance_due = inv.total_amount - inv.amount_paid
            invoices_view.append(
                {
                    "invoice_id": inv.invoice_id,
                    "invoice_number": inv.invoice_number,
                    "invoice_date": format_date(inv.invoice_date),
                    "due_date": format_date(inv.due_date),
                    "total_amount": format_currency(
                        inv.total_amount, inv.currency_code
                    ),
                    "balance": format_currency(balance_due, inv.currency_code),
                    "status": invoice_status_label(inv.status),
                    "is_overdue": (
                        inv.due_date < today
                        and inv.status not in {InvoiceStatus.PAID, InvoiceStatus.VOID}
                    ),
                }
            )

        # Receipts
        receipts_query = db.scalars(
            select(CustomerPayment)
            .where(
                CustomerPayment.organization_id == org_id,
                CustomerPayment.customer_id == customer.customer_id,
            )
            .order_by(CustomerPayment.payment_date.desc())
            .limit(20)
        )
        receipts_query = receipts_query.all()
        receipts_view: list[dict] = []
        for r in receipts_query:
            receipts_view.append(
                {
                    "payment_id": r.payment_id,
                    "payment_number": r.payment_number,
                    "payment_date": format_date(r.payment_date),
                    "amount": format_currency(r.amount, r.currency_code),
                    "payment_method": (
                        r.payment_method.value.replace("_", " ").title()
                        if r.payment_method
                        else "-"
                    ),
                    "reference": r.reference or "-",
                    "status": r.status.value if r.status else "-",
                }
            )

        # Quotes
        quotes_query = db.scalars(
            select(Quote)
            .where(
                Quote.organization_id == org_id,
                Quote.customer_id == customer.customer_id,
            )
            .order_by(Quote.quote_date.desc())
            .limit(20)
        )
        quotes_query = quotes_query.all()
        quotes_view: list[dict] = []
        for q in quotes_query:
            quotes_view.append(
                {
                    "quote_id": q.quote_id,
                    "quote_number": q.quote_number,
                    "quote_date": format_date(q.quote_date),
                    "valid_until": format_date(q.valid_until) if q.valid_until else "-",
                    "total_amount": (
                        format_currency(q.total_amount, q.currency_code)
                        if q.total_amount
                        else "-"
                    ),
                    "status": q.status.value if q.status else "-",
                }
            )

        # Sales Orders
        sales_orders_query = db.scalars(
            select(SalesOrder)
            .where(
                SalesOrder.organization_id == org_id,
                SalesOrder.customer_id == customer.customer_id,
            )
            .order_by(SalesOrder.order_date.desc())
            .limit(20)
        )
        sales_orders_query = sales_orders_query.all()
        sales_orders_view: list[dict] = []
        for so in sales_orders_query:
            sales_orders_view.append(
                {
                    "so_id": so.so_id,
                    "so_number": so.so_number,
                    "order_date": format_date(so.order_date),
                    "total_amount": (
                        format_currency(so.total_amount, so.currency_code)
                        if so.total_amount
                        else "-"
                    ),
                    "status": so.status.value if so.status else "-",
                }
            )

        # Get attachments
        attachments = attachment_service.list_for_entity(
            db,
            organization_id=org_id,
            entity_type="CUSTOMER",
            entity_id=customer.customer_id,
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
            "customer_detail_context: found %d invoices, %d receipts, "
            "%d quotes, %d sales orders",
            len(invoices_view),
            len(receipts_view),
            len(quotes_view),
            len(sales_orders_view),
        )

        customer_view = customer_detail_view(customer, balance)
        customer_view["default_tax_code_label"] = default_tax_code_label
        customer_view["default_tax_code_id"] = (
            str(customer.default_tax_code_id) if customer.default_tax_code_id else None
        )

        # Load child (sub-account) customers
        child_customers_view: list[dict] = []
        children = db.scalars(
            select(Customer)
            .where(
                Customer.organization_id == org_id,
                Customer.parent_customer_id == customer.customer_id,
            )
            .order_by(Customer.legal_name)
            .limit(100)
        ).all()
        for child in children:
            child_customers_view.append(
                {
                    "customer_id": child.customer_id,
                    "customer_code": child.customer_code,
                    "customer_name": customer_display_name(child),
                    "is_active": child.is_active,
                }
            )

        return {
            "customer": customer_view,
            "invoices": invoices_view,
            "receipts": receipts_view,
            "quotes": quotes_view,
            "sales_orders": sales_orders_view,
            "attachments": attachments_view,
            "child_customers": child_customers_view,
        }

    @staticmethod
    def delete_customer(
        db: Session,
        organization_id: str,
        customer_id: str,
    ) -> str | None:
        """Delete a customer. Returns error message or None on success."""
        logger.debug(
            "delete_customer: org=%s customer_id=%s", organization_id, customer_id
        )
        org_id = coerce_uuid(organization_id)
        cust_id = coerce_uuid(customer_id)

        try:
            customer_service.delete_customer(db, org_id, cust_id)
            logger.info(
                "delete_customer: deleted customer %s for org %s", cust_id, org_id
            )
            return None
        except HTTPException as exc:
            return exc.detail
        except Exception as e:
            logger.exception("delete_customer: failed for org %s", org_id)
            return f"Failed to delete customer: {str(e)}"

    # =====================================================================
    # HTTP Response Methods
    # =====================================================================

    def list_customers_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        search: str | None,
        status: str | None,
        page: int,
        sort: str | None = None,
        sort_dir: str | None = None,
        parent_customer_id: str | None = None,
    ) -> HTMLResponse:
        """Render customer list page."""
        context = base_context(request, auth, "Customers", "ar")
        context.update(
            self.list_customers_context(
                db,
                str(auth.organization_id),
                search=search,
                status=status,
                page=page,
                sort=sort,
                sort_dir=sort_dir,
                parent_customer_id=parent_customer_id,
            )
        )
        return templates.TemplateResponse(request, "finance/ar/customers.html", context)

    def customer_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """Render new customer form."""
        context = base_context(request, auth, "New Customer", "ar")
        context.update(self.customer_form_context(db, str(auth.organization_id)))
        return templates.TemplateResponse(
            request, "finance/ar/customer_form.html", context
        )

    def customer_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        customer_id: str,
    ) -> HTMLResponse:
        """Render customer detail page."""
        context = base_context(request, auth, "Customer Details", "ar")
        context.update(
            self.customer_detail_context(
                db,
                str(auth.organization_id),
                customer_id,
            )
        )
        return templates.TemplateResponse(
            request, "finance/ar/customer_detail.html", context
        )

    def customer_edit_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        customer_id: str,
    ) -> HTMLResponse:
        """Render customer edit form."""
        context = base_context(request, auth, "Edit Customer", "ar")
        context.update(
            self.customer_form_context(db, str(auth.organization_id), customer_id)
        )
        return templates.TemplateResponse(
            request, "finance/ar/customer_form.html", context
        )

    async def create_customer_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Handle customer creation form submission."""
        form_data = await request.form()

        try:
            org_id = auth.organization_id
            if org_id is None:
                raise HTTPException(status_code=401, detail="Authentication required")
            input_data = self.build_customer_input(db, dict(form_data), org_id)

            customer_service.create_customer(
                db=db,
                organization_id=org_id,
                input=input_data,
            )

            return RedirectResponse(
                url="/finance/ar/customers?success=Customer+created+successfully",
                status_code=303,
            )

        except Exception as e:
            logger.exception("create_customer_response: failed")
            context = base_context(request, auth, "New Customer", "ar")
            context.update(self.customer_form_context(db, str(auth.organization_id)))
            context["error"] = str(e)
            context["form_data"] = dict(form_data)
            return templates.TemplateResponse(
                request, "finance/ar/customer_form.html", context
            )

    async def update_customer_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        customer_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Handle customer update form submission."""
        form_data = await request.form()

        try:
            org_id = auth.organization_id
            if org_id is None:
                raise HTTPException(status_code=401, detail="Authentication required")
            input_data = self.build_customer_input(db, dict(form_data), org_id)

            customer_service.update_customer(
                db=db,
                organization_id=org_id,
                customer_id=UUID(customer_id),
                input=input_data,
            )

            return RedirectResponse(
                url="/finance/ar/customers?success=Customer+updated+successfully",
                status_code=303,
            )

        except Exception as e:
            logger.exception("update_customer_response: failed")
            context = base_context(request, auth, "Edit Customer", "ar")
            context.update(
                self.customer_form_context(db, str(auth.organization_id), customer_id)
            )
            context["error"] = str(e)
            context["form_data"] = dict(form_data)
            return templates.TemplateResponse(
                request, "finance/ar/customer_form.html", context
            )

    def delete_customer_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        customer_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Handle customer deletion."""
        error = self.delete_customer(db, str(auth.organization_id), customer_id)

        if error:
            context = base_context(request, auth, "Customer Details", "ar")
            context.update(
                self.customer_detail_context(
                    db,
                    str(auth.organization_id),
                    customer_id,
                )
            )
            context["error"] = error
            return templates.TemplateResponse(
                request, "finance/ar/customer_detail.html", context
            )

        return RedirectResponse(
            url="/finance/ar/customers?success=Record+deleted+successfully",
            status_code=303,
        )

    async def upload_customer_attachment_response(
        self,
        customer_id: str,
        file: UploadFile,
        description: str | None,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Handle customer attachment upload."""
        try:
            org_id = auth.organization_id
            user_id = auth.person_id
            if org_id is None:
                raise HTTPException(status_code=401, detail="Authentication required")
            if user_id is None:
                raise HTTPException(status_code=401, detail="Authentication required")
            customer = customer_service.get(db, org_id, customer_id)
            if not customer or customer.organization_id != auth.organization_id:
                return RedirectResponse(
                    url=f"/finance/ar/customers/{customer_id}?error=Customer+not+found",
                    status_code=303,
                )

            input_data = AttachmentInput(
                entity_type="CUSTOMER",
                entity_id=customer_id,
                file_name=file.filename or "unnamed",
                content_type=file.content_type or "application/octet-stream",
                category=AttachmentCategory.CUSTOMER,
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
                url=f"/finance/ar/customers/{customer_id}?success=Attachment+uploaded",
                status_code=303,
            )

        except ValueError as e:
            return RedirectResponse(
                url=f"/finance/ar/customers/{customer_id}?error={str(e)}",
                status_code=303,
            )
        except Exception:
            logger.exception("upload_customer_attachment_response: failed")
            return RedirectResponse(
                url=f"/finance/ar/customers/{customer_id}?error=Upload+failed",
                status_code=303,
            )
