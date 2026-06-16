"""
AR Customer Web Service - Customer-related web view methods.

Provides view-focused data and operations for AR customer web routes.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
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
from app.services.finance.ar.customer_family import CustomerFamilyResolver
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

    def ar_home_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """Render the AR landing page."""
        context = base_context(request, auth, "Accounts Receivable", "ar")
        return templates.TemplateResponse(request, "finance/ar/index.html", context)

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
        show_subs: bool = False,
    ) -> dict:
        """Get context for customer listing page.

        By default the list is *collapsed*: only top-level accounts (resellers
        and standalones) are shown, with each parent's balance rolled up across
        its sub-accounts and the sub-accounts available to expand inline. The
        flat list (every customer, including sub-accounts) is shown when the
        caller searches, drills into a specific parent, or sets ``show_subs``.
        """
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

        # Collapse to top-level accounts unless searching, drilling into a
        # specific parent, or explicitly asked to show sub-accounts flat.
        collapsed = not show_subs and not search and not parent_customer_id
        query = build_customer_query(
            db=db,
            organization_id=organization_id,
            search=search,
            status=status,
            parent_customer_id=parent_customer_id,
            top_level_only=collapsed,
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

        # Stat-card counts — org-wide totals, independent of the current
        # pagination/search/collapse view (the template reads these three and
        # silently defaults them to 0 when missing).
        active_count = (
            db.scalar(
                select(func.count())
                .select_from(Customer)
                .where(
                    Customer.organization_id == org_id,
                    Customer.is_active.is_(True),
                )
            )
            or 0
        )
        inactive_count = (
            db.scalar(
                select(func.count())
                .select_from(Customer)
                .where(
                    Customer.organization_id == org_id,
                    Customer.is_active.is_(False),
                )
            )
            or 0
        )
        with_balance_subq = (
            select(Invoice.customer_id)
            .where(
                Invoice.organization_id == org_id,
                Invoice.status.in_(open_statuses),
            )
            .group_by(Invoice.customer_id)
            .having(
                func.coalesce(func.sum(Invoice.total_amount - Invoice.amount_paid), 0)
                > 0
            )
            .subquery()
        )
        with_balance_count = (
            db.scalar(select(func.count()).select_from(with_balance_subq)) or 0
        )

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

        # Roll up family balances and load sub-accounts for inline expansion
        # (collapsed view only): each displayed parent shows its own balance
        # plus the sum of its sub-accounts, and carries the sub-account rows.
        sub_accounts_map: dict[UUID, list[dict]] = {}
        family_balance_map: dict[UUID, Decimal] = {}
        if collapsed:
            parent_ids_on_page = [
                cid for cid in customer_ids if child_count_map.get(cid, 0) > 0
            ]
            if parent_ids_on_page:
                children = db.scalars(
                    select(Customer)
                    .where(
                        Customer.organization_id == org_id,
                        Customer.parent_customer_id.in_(parent_ids_on_page),
                    )
                    .order_by(Customer.legal_name)
                ).all()
                for child in children:
                    pid = child.parent_customer_id
                    if pid is None:
                        continue
                    child_bal = balance_map.get(child.customer_id, Decimal("0"))
                    sub_accounts_map.setdefault(pid, []).append(
                        {
                            "customer_id": child.customer_id,
                            "customer_code": child.customer_code,
                            "customer_name": customer_display_name(child),
                            "is_active": child.is_active,
                            "balance": format_currency(child_bal, child.currency_code),
                        }
                    )
                    family_balance_map[pid] = (
                        family_balance_map.get(pid, Decimal("0")) + child_bal
                    )
                for pid in parent_ids_on_page:
                    family_balance_map[pid] = family_balance_map.get(
                        pid, Decimal("0")
                    ) + balance_map.get(pid, Decimal("0"))

        customers_view = []
        for customer in customers:
            cid = customer.customer_id
            is_parent = child_count_map.get(cid, 0) > 0
            display_balance = (
                family_balance_map.get(cid, balance_map.get(cid, Decimal("0")))
                if collapsed and is_parent
                else balance_map.get(cid, Decimal("0"))
            )
            view = customer_list_view(
                customer,
                display_balance,
                creator_names.get(customer.created_by_user_id)
                if customer.created_by_user_id
                else None,
                balance_trends.get(cid),
                child_count=child_count_map.get(cid, 0),
            )
            view["is_parent"] = is_parent
            view["sub_accounts"] = sub_accounts_map.get(cid, [])
            customers_view.append(view)

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
            "active_count": active_count,
            "inactive_count": inactive_count,
            "with_balance_count": with_balance_count,
            "total_pages": total_pages,
            "active_filters": active_filters,
            "sort": sort or "",
            "sort_dir": sort_dir_norm,
            "collapsed": collapsed,
            "show_subs": show_subs,
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

        # Consolidated account family: a reseller/parent rolls up its
        # sub-accounts; a standalone customer or sub-account is just itself
        # (family_ids == [self]), so the rest of this builder is unchanged for
        # non-parents while parents transparently aggregate the whole family.
        family_resolver = CustomerFamilyResolver(db)
        family_ids = family_resolver.family_ids(org_id, customer.customer_id)
        is_consolidated = len(family_ids) > 1
        attribution = (
            family_resolver.attribution_map(org_id, family_ids)
            if is_consolidated
            else {}
        )

        def _sub_account(cid: UUID) -> str:
            return attribution.get(cid, {}).get("code", "") if is_consolidated else ""

        balance = db.scalar(
            select(
                func.coalesce(
                    func.sum(Invoice.total_amount - Invoice.amount_paid),
                    0,
                )
            ).where(
                Invoice.organization_id == org_id,
                Invoice.customer_id.in_(family_ids),
                Invoice.status.in_(open_statuses),
            )
        ) or Decimal("0")

        from datetime import date

        today = date.today()

        # All invoices (all statuses) across the account family
        all_invoices_query = db.scalars(
            select(Invoice)
            .where(
                Invoice.organization_id == org_id,
                Invoice.customer_id.in_(family_ids),
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
                    "sub_account": _sub_account(inv.customer_id),
                }
            )

        # Receipts
        receipts_query = db.scalars(
            select(CustomerPayment)
            .where(
                CustomerPayment.organization_id == org_id,
                CustomerPayment.customer_id.in_(family_ids),
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
                    "sub_account": _sub_account(r.customer_id),
                    "reference": r.reference or "-",
                    "status": r.status.value if r.status else "-",
                }
            )

        # Quotes
        quotes_query = db.scalars(
            select(Quote)
            .where(
                Quote.organization_id == org_id,
                Quote.customer_id.in_(family_ids),
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
                    "sub_account": _sub_account(q.customer_id),
                }
            )

        # Sales Orders
        sales_orders_query = db.scalars(
            select(SalesOrder)
            .where(
                SalesOrder.organization_id == org_id,
                SalesOrder.customer_id.in_(family_ids),
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
                    "sub_account": _sub_account(so.customer_id),
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

        # Per-member open balances, so the consolidated view can show each
        # sub-account's balance and the parent's own contribution.
        sub_balance_map: dict[UUID, Decimal] = {}
        if is_consolidated:
            sub_balance_map = {
                row.customer_id: row.balance
                for row in db.execute(
                    select(
                        Invoice.customer_id,
                        func.coalesce(
                            func.sum(Invoice.total_amount - Invoice.amount_paid),
                            0,
                        ).label("balance"),
                    )
                    .where(
                        Invoice.organization_id == org_id,
                        Invoice.customer_id.in_(family_ids),
                        Invoice.status.in_(open_statuses),
                    )
                    .group_by(Invoice.customer_id)
                ).all()
            }

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
            child_balance = sub_balance_map.get(child.customer_id, Decimal("0"))
            child_customers_view.append(
                {
                    "customer_id": child.customer_id,
                    "customer_code": child.customer_code,
                    "customer_name": customer_display_name(child),
                    "is_active": child.is_active,
                    "balance": format_currency(child_balance, customer.currency_code),
                }
            )

        own_balance = sub_balance_map.get(customer.customer_id, Decimal("0"))

        return {
            "customer": customer_view,
            "invoices": invoices_view,
            "receipts": receipts_view,
            "quotes": quotes_view,
            "sales_orders": sales_orders_view,
            "attachments": attachments_view,
            "child_customers": child_customers_view,
            # Consolidated-account metadata (parent rolling up its sub-accounts)
            "is_consolidated": is_consolidated,
            "family_count": len(family_ids),
            "consolidated_balance": format_currency(balance, customer.currency_code),
            "own_balance": format_currency(own_balance, customer.currency_code),
        }

    @staticmethod
    def consolidated_statement_context(
        db: Session,
        organization_id: str,
        customer_id: str,
    ) -> dict:
        """Build a statement of account for a customer / consolidated family.

        Lists every charge (invoice) and credit (payment) in date order with a
        running balance, attributed by sub-account when consolidated, plus an
        aging summary across the whole family. The running total of charges
        less credits is the closing balance.
        """
        from datetime import date

        org_id = coerce_uuid(organization_id)
        customer = None
        try:
            customer = customer_service.get(db, org_id, customer_id)
        except Exception:
            customer = None
        if not customer or customer.organization_id != org_id:
            return {"customer": None, "transactions": [], "is_consolidated": False}

        resolver = CustomerFamilyResolver(db)
        family_ids = resolver.family_ids(org_id, customer.customer_id)
        is_consolidated = len(family_ids) > 1
        attribution = (
            resolver.attribution_map(org_id, family_ids) if is_consolidated else {}
        )

        def _sub(cid: UUID) -> str:
            return attribution.get(cid, {}).get("code", "") if is_consolidated else ""

        statement_statuses = [
            InvoiceStatus.POSTED,
            InvoiceStatus.PARTIALLY_PAID,
            InvoiceStatus.OVERDUE,
            InvoiceStatus.PAID,
        ]
        invoices = list(
            db.scalars(
                select(Invoice)
                .where(
                    Invoice.organization_id == org_id,
                    Invoice.customer_id.in_(family_ids),
                    Invoice.status.in_(statement_statuses),
                )
                .order_by(Invoice.invoice_date)
            ).all()
        )
        payments = list(
            db.scalars(
                select(CustomerPayment)
                .where(
                    CustomerPayment.organization_id == org_id,
                    CustomerPayment.customer_id.in_(family_ids),
                )
                .order_by(CustomerPayment.payment_date)
            ).all()
        )

        # Interleave charges and credits chronologically (invoices before
        # payments on the same day) and accumulate a running balance.
        events: list[tuple[date, int, str, Any]] = []
        for inv in invoices:
            events.append((inv.invoice_date, 0, "invoice", inv))
        for pmt in payments:
            events.append((pmt.payment_date, 1, "payment", pmt))
        events.sort(key=lambda e: (e[0], e[1]))

        ccy = customer.currency_code
        running = Decimal("0")
        total_charges = Decimal("0")
        total_credits = Decimal("0")
        transactions: list[dict] = []
        for _, _, kind, obj in events:
            if kind == "invoice":
                charge = obj.total_amount or Decimal("0")
                running += charge
                total_charges += charge
                transactions.append(
                    {
                        "date": format_date(obj.invoice_date),
                        "type": "Invoice",
                        "reference": obj.invoice_number,
                        "sub_account": _sub(obj.customer_id),
                        "charge": format_currency(charge, ccy),
                        "credit": "",
                        "balance": format_currency(running, ccy),
                    }
                )
            else:
                credit = obj.amount or Decimal("0")
                running -= credit
                total_credits += credit
                transactions.append(
                    {
                        "date": format_date(obj.payment_date),
                        "type": "Payment",
                        "reference": obj.payment_number,
                        "sub_account": _sub(obj.customer_id),
                        "charge": "",
                        "credit": format_currency(credit, ccy),
                        "balance": format_currency(running, ccy),
                    }
                )

        closing_balance = running

        # Aging on open invoices (by due date), aggregated across the family.
        today = date.today()
        open_statuses = {
            InvoiceStatus.POSTED,
            InvoiceStatus.PARTIALLY_PAID,
            InvoiceStatus.OVERDUE,
        }
        buckets = {
            "current": Decimal("0"),
            "d30": Decimal("0"),
            "d60": Decimal("0"),
            "d90": Decimal("0"),
            "d90p": Decimal("0"),
        }
        for inv in invoices:
            if inv.status not in open_statuses:
                continue
            bal = (inv.total_amount or Decimal("0")) - (inv.amount_paid or Decimal("0"))
            if bal <= 0:
                continue
            days = (today - inv.due_date).days if inv.due_date else 0
            if days <= 0:
                buckets["current"] += bal
            elif days <= 30:
                buckets["d30"] += bal
            elif days <= 60:
                buckets["d60"] += bal
            elif days <= 90:
                buckets["d90"] += bal
            else:
                buckets["d90p"] += bal

        return {
            "customer": customer_detail_view(customer, closing_balance),
            "is_consolidated": is_consolidated,
            "family_count": len(family_ids),
            "statement_date": format_date(today),
            "transactions": transactions,
            "total_charges": format_currency(total_charges, ccy),
            "total_credits": format_currency(total_credits, ccy),
            "closing_balance": format_currency(closing_balance, ccy),
            "currency_code": ccy,
            "aging": {k: format_currency(v, ccy) for k, v in buckets.items()},
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
        limit: int = 50,
        show_subs: bool = False,
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
                limit=limit,
                sort=sort,
                sort_dir=sort_dir,
                parent_customer_id=parent_customer_id,
                show_subs=show_subs,
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

    def customer_statement_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        customer_id: str,
    ) -> HTMLResponse:
        """Render a (consolidated) statement of account."""
        context = base_context(request, auth, "Statement of Account", "ar")
        context.update(
            self.consolidated_statement_context(
                db,
                str(auth.organization_id),
                customer_id,
            )
        )
        return templates.TemplateResponse(
            request, "finance/ar/customer_statement.html", context
        )

    def customer_statement_pdf_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        customer_id: str,
    ) -> Response:
        """Render the (consolidated) statement of account as a PDF download."""
        from app.services.finance.rpt.pdf import ReportPDFService

        org_id = str(auth.organization_id)
        ctx = self.consolidated_statement_context(db, org_id, customer_id)
        if not ctx.get("customer"):
            raise HTTPException(status_code=404, detail="Customer not found")
        pdf_bytes = ReportPDFService(db).render("customer_statement", org_id, ctx)
        code = ctx["customer"].get("customer_code", "account")
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="statement_{code}.pdf"'
            },
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

            customer = customer_service.create_customer(
                db=db,
                organization_id=org_id,
                input=input_data,
            )
            customer_id = customer.customer_id
            db.commit()

            return RedirectResponse(
                url=f"/finance/ar/customers/{customer_id}?success=Customer+created+successfully",
                status_code=303,
            )

        except Exception as e:
            db.rollback()
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
            db.commit()

            return RedirectResponse(
                url="/finance/ar/customers?success=Customer+updated+successfully",
                status_code=303,
            )

        except Exception as e:
            db.rollback()
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
