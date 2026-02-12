"""
AR web view service.

Provides view-focused data for AR web routes.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, load_only

from app.config import settings
from app.models.finance.ar.customer import Customer, CustomerType, RiskCategory
from app.models.finance.ar.customer_payment import (
    CustomerPayment,
    PaymentMethod,
    PaymentStatus,
)
from app.models.finance.ar.invoice import Invoice, InvoiceStatus, InvoiceType
from app.models.finance.ar.invoice_line import InvoiceLine
from app.models.finance.ar.invoice_line_tax import InvoiceLineTax
from app.models.finance.ar.payment_allocation import PaymentAllocation
from app.models.finance.common.attachment import AttachmentCategory
from app.models.finance.core_org.cost_center import CostCenter
from app.models.finance.core_org.project import Project
from app.models.finance.gl.account import Account
from app.models.finance.gl.account_category import AccountCategory, IFRSCategory
from app.models.inventory.item import Item
from app.services.audit_info import get_audit_service
from app.services.common import coerce_uuid
from app.services.common_filters import build_active_filters
from app.services.finance.ar.ar_aging import ar_aging_service
from app.services.finance.ar.customer import CustomerInput, customer_service
from app.services.finance.ar.customer_payment import (
    CustomerPaymentInput,
    PaymentAllocationInput,
    customer_payment_service,
)
from app.services.finance.ar.invoice import (
    ARInvoiceInput,
    ARInvoiceLineInput,
    ar_invoice_service,
)
from app.services.finance.common import (
    format_currency,
    format_date,
    format_file_size,
    parse_date,
    parse_enum_safe,
)
from app.services.finance.common.attachment import AttachmentInput, attachment_service
from app.services.finance.platform.currency_context import get_currency_context
from app.services.finance.tax.tax_master import tax_code_service
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

logger = logging.getLogger(__name__)

# Keep aliases for backward compatibility with existing code
_parse_date = parse_date
_format_date = format_date
_format_currency = format_currency
_format_file_size = format_file_size


def _parse_customer_type(value: str | None) -> CustomerType:
    return parse_enum_safe(CustomerType, value, CustomerType.COMPANY)


def _customer_display_name(customer: Customer) -> str:
    return customer.trading_name or customer.legal_name


def _calculate_customer_balance_trends(
    db: Session,
    organization_id: UUID,
    customer_ids: list[UUID],
    months: int = 6,
) -> dict[UUID, list[float]]:
    """
    Calculate monthly balance trends for customers over the last N months.
    Returns a dict mapping customer_id to a list of balance values.
    """
    if not customer_ids:
        return {}

    from dateutil.relativedelta import relativedelta

    trends: dict[UUID, list[float]] = {cid: [] for cid in customer_ids}
    today = date.today()

    # Calculate balance at end of each month for the last N months
    for i in range(months - 1, -1, -1):
        # Get the last day of the month (i months ago)
        if i == 0:
            as_of_date = today
        else:
            month_start = today.replace(day=1) - relativedelta(months=i)
            # Last day of that month
            next_month = month_start + relativedelta(months=1)
            as_of_date = next_month - timedelta(days=1)

        # Query balance as of that date for all customers
        balances = (
            db.query(
                Invoice.customer_id,
                func.coalesce(
                    func.sum(Invoice.total_amount - Invoice.amount_paid), 0
                ).label("balance"),
            )
            .filter(
                Invoice.organization_id == organization_id,
                Invoice.customer_id.in_(customer_ids),
                Invoice.invoice_date <= as_of_date,
                Invoice.status.in_(
                    [
                        InvoiceStatus.POSTED,
                        InvoiceStatus.PARTIALLY_PAID,
                        InvoiceStatus.PAID,
                        InvoiceStatus.OVERDUE,
                    ]
                ),
            )
            .group_by(Invoice.customer_id)
            .all()
        )

        balance_map = {row.customer_id: float(row.balance) for row in balances}

        for cid in customer_ids:
            trends[cid].append(balance_map.get(cid, 0.0))

    return trends


def _customer_option_view(customer: Customer) -> dict:
    return {
        "customer_id": customer.customer_id,
        "customer_name": _customer_display_name(customer),
        "customer_code": customer.customer_code,
        "currency_code": customer.currency_code,
        "payment_terms_days": customer.credit_terms_days,
        "default_tax_code_id": customer.default_tax_code_id,
    }


def _customer_form_view(customer: Customer) -> dict:
    contact = customer.primary_contact or {}
    return {
        "customer_id": customer.customer_id,
        "customer_code": customer.customer_code,
        "customer_name": _customer_display_name(customer),
        "tax_id": customer.tax_identification_number,
        "default_tax_code_id": customer.default_tax_code_id,
        "currency_code": customer.currency_code,
        "payment_terms_days": customer.credit_terms_days,
        "credit_limit": customer.credit_limit,
        "credit_hold": customer.credit_hold,
        "default_revenue_account_id": customer.default_revenue_account_id,
        "default_receivable_account_id": customer.ar_control_account_id,
        "email": contact.get("email"),
        "phone": contact.get("phone"),
        "billing_address": (customer.billing_address or {}).get("address", ""),
        "shipping_address": (customer.shipping_address or {}).get("address", ""),
        "is_active": customer.is_active,
    }


def _customer_list_view(
    customer: Customer,
    balance: Decimal,
    created_by_name: str | None = None,
    balance_trend: list[float] | None = None,
) -> dict:
    return {
        "customer_id": customer.customer_id,
        "customer_code": customer.customer_code,
        "customer_name": _customer_display_name(customer),
        "legal_name": customer.legal_name,
        "trading_name": customer.trading_name,
        "tax_id": customer.tax_identification_number,
        "payment_terms_days": customer.credit_terms_days,
        "credit_limit": _format_currency(
            customer.credit_limit or Decimal("0"),
            customer.currency_code,
        ),
        "balance": _format_currency(balance, customer.currency_code),
        "balance_trend": balance_trend
        if balance_trend and any(v > 0 for v in balance_trend)
        else None,
        "is_active": customer.is_active,
        # Audit info
        "created_at": customer.created_at,
        "created_by_user_id": customer.created_by_user_id,
        "created_by_name": created_by_name,
        "updated_at": customer.updated_at,
    }


def _customer_detail_view(customer: Customer, balance: Decimal) -> dict:
    contact = customer.primary_contact or {}
    return {
        "customer_id": customer.customer_id,
        "customer_code": customer.customer_code,
        "customer_name": _customer_display_name(customer),
        "tax_id": customer.tax_identification_number,
        "default_tax_code_id": customer.default_tax_code_id,
        "currency_code": customer.currency_code,
        "payment_terms_days": customer.credit_terms_days,
        "credit_limit": _format_currency(
            customer.credit_limit or Decimal("0"),
            customer.currency_code,
        ),
        "balance": _format_currency(balance, customer.currency_code),
        "default_revenue_account_id": customer.default_revenue_account_id,
        "default_receivable_account_id": customer.ar_control_account_id,
        "email": contact.get("email"),
        "phone": contact.get("phone"),
        "billing_address": (customer.billing_address or {}).get("address", ""),
        "shipping_address": (customer.shipping_address or {}).get("address", ""),
        "is_active": customer.is_active,
    }


def _invoice_line_view(line: InvoiceLine, currency_code: str) -> dict:
    return {
        "line_id": line.line_id,
        "line_number": line.line_number,
        "description": line.description,
        "quantity": line.quantity,
        "unit_price": _format_currency(line.unit_price, currency_code),
        "discount_amount": _format_currency(line.discount_amount, currency_code),
        "tax_amount": _format_currency(line.tax_amount, currency_code),
        "line_amount": _format_currency(line.line_amount, currency_code),
        "revenue_account_id": line.revenue_account_id,
        "item_id": line.item_id,
        "cost_center_id": line.cost_center_id,
        "project_id": line.project_id,
    }


def _invoice_detail_view(invoice: Invoice, customer: Customer | None) -> dict:
    balance = invoice.total_amount - invoice.amount_paid
    today = date.today()
    return {
        "invoice_id": invoice.invoice_id,
        "invoice_number": invoice.invoice_number,
        "invoice_type": invoice.invoice_type.value,
        "customer_id": invoice.customer_id,
        "customer_name": _customer_display_name(customer) if customer else "",
        "invoice_date": _format_date(invoice.invoice_date),
        "due_date": _format_date(invoice.due_date),
        "currency_code": invoice.currency_code,
        "subtotal": _format_currency(invoice.subtotal, invoice.currency_code),
        "tax_amount": _format_currency(invoice.tax_amount, invoice.currency_code),
        "total_amount": _format_currency(invoice.total_amount, invoice.currency_code),
        "amount_paid": _format_currency(invoice.amount_paid, invoice.currency_code),
        "balance": _format_currency(balance, invoice.currency_code),
        "status": _invoice_status_label(invoice.status),
        "is_overdue": (
            invoice.due_date < today
            and invoice.status not in {InvoiceStatus.PAID, InvoiceStatus.VOID}
        ),
        "notes": invoice.notes,
        "internal_notes": invoice.internal_notes,
    }


def _receipt_detail_view(payment: CustomerPayment, customer: Customer | None) -> dict:
    return {
        "receipt_id": payment.payment_id,
        "receipt_number": payment.payment_number,
        "customer_id": payment.customer_id,
        "customer_name": _customer_display_name(customer) if customer else "",
        "receipt_date": _format_date(payment.payment_date),
        "payment_method": payment.payment_method.value,
        "reference_number": payment.reference,
        "description": payment.description,
        # Net amount received (after WHT deduction)
        "amount": _format_currency(payment.amount, payment.currency_code),
        # WHT breakdown
        "gross_amount": _format_currency(payment.gross_amount, payment.currency_code),
        "wht_amount": _format_currency(payment.wht_amount, payment.currency_code)
        if payment.wht_amount
        else None,
        "wht_code_id": payment.wht_code_id,
        "wht_certificate_number": payment.wht_certificate_number,
        "has_wht": payment.wht_amount and payment.wht_amount > 0,
        "status": _receipt_status_label(payment.status),
        "currency_code": payment.currency_code,
        "bank_account_id": payment.bank_account_id,
    }


def _allocation_view(
    allocation: PaymentAllocation,
    invoice: Invoice | None,
    currency_code: str,
) -> dict:
    return {
        "allocation_id": allocation.allocation_id,
        "invoice_id": allocation.invoice_id,
        "invoice_number": invoice.invoice_number if invoice else "",
        "allocated_amount": _format_currency(
            allocation.allocated_amount, currency_code
        ),
        "discount_taken": _format_currency(allocation.discount_taken, currency_code),
        "write_off_amount": _format_currency(
            allocation.write_off_amount, currency_code
        ),
        "exchange_difference": _format_currency(
            allocation.exchange_difference,
            currency_code,
        ),
        "allocation_date": _format_date(allocation.allocation_date),
    }


def _invoice_status_label(status: InvoiceStatus) -> str:
    if status == InvoiceStatus.PARTIALLY_PAID:
        return "PARTIAL"
    return str(status.value)


def _receipt_status_label(status: PaymentStatus) -> str:
    if status == PaymentStatus.CLEARED:
        return "POSTED"
    if status == PaymentStatus.PENDING:
        return "DRAFT"
    if status in {PaymentStatus.VOID, PaymentStatus.BOUNCED, PaymentStatus.REVERSED}:
        return "VOIDED"
    return str(status.value)


def _parse_invoice_status(value: str | None) -> InvoiceStatus | None:
    if not value:
        return None
    if value == "PARTIAL":
        return InvoiceStatus.PARTIALLY_PAID
    try:
        return InvoiceStatus(value)
    except ValueError:
        return None


def _parse_receipt_status(value: str | None) -> PaymentStatus | None:
    if not value:
        return None
    status_map = {
        "DRAFT": PaymentStatus.PENDING,
        "POSTED": PaymentStatus.CLEARED,
        "VOIDED": PaymentStatus.VOID,
    }
    return status_map.get(value)


def _get_accounts(
    db: Session,
    organization_id: UUID,
    ifrs_category: IFRSCategory,
    subledger_type: str | None = None,
) -> list[Account]:
    query = (
        db.query(Account)
        .join(AccountCategory, Account.category_id == AccountCategory.category_id)
        .filter(
            Account.organization_id == organization_id,
            Account.is_active.is_(True),
            AccountCategory.ifrs_category == ifrs_category,
        )
    )
    if subledger_type:
        query = query.filter(Account.subledger_type == subledger_type)
    return query.order_by(Account.account_code).all()


def _get_cost_centers(db: Session, organization_id: UUID) -> list[CostCenter]:
    return (
        db.query(CostCenter)
        .filter(
            CostCenter.organization_id == organization_id,
            CostCenter.is_active.is_(True),
        )
        .order_by(CostCenter.cost_center_code)
        .all()
    )


def _get_projects(db: Session, organization_id: UUID) -> list[Project]:
    return (
        db.query(Project)
        .options(
            load_only(
                Project.project_id,
                Project.project_code,
                Project.project_name,
            )
        )
        .filter(Project.organization_id == organization_id)
        .order_by(Project.project_code)
        .all()
    )


@dataclass
class InvoiceStats:
    total_outstanding: str
    past_due: str
    due_this_week: str
    this_month: str


class ARWebService:
    """View service for AR web routes."""

    @staticmethod
    def build_customer_input(form_data: dict) -> CustomerInput:
        credit_limit = form_data.get("credit_limit")
        return CustomerInput(
            customer_code=form_data.get("customer_code", ""),
            customer_type=_parse_customer_type(form_data.get("customer_type")),
            customer_name=form_data.get("customer_name", ""),
            trading_name=form_data.get("trading_name")
            or form_data.get("customer_name", ""),
            tax_id=form_data.get("tax_id"),
            currency_code=form_data.get(
                "currency_code",
                settings.default_functional_currency_code,
            ),
            payment_terms_days=int(form_data.get("payment_terms_days", 30)),
            credit_limit=Decimal(credit_limit) if credit_limit else None,
            credit_hold=form_data.get("credit_hold") is not None,
            risk_category=RiskCategory.MEDIUM,
            default_receivable_account_id=(
                UUID(form_data["default_receivable_account_id"])
                if form_data.get("default_receivable_account_id")
                else UUID("00000000-0000-0000-0000-000000000001")
            ),
            default_revenue_account_id=(
                UUID(form_data["default_revenue_account_id"])
                if form_data.get("default_revenue_account_id")
                else None
            ),
            default_tax_code_id=(
                UUID(form_data["default_tax_code_id"])
                if form_data.get("default_tax_code_id")
                else None
            ),
            billing_address={
                "address": form_data.get("billing_address", ""),
            }
            if form_data.get("billing_address")
            else None,
            shipping_address={
                "address": form_data.get("shipping_address", ""),
            }
            if form_data.get("shipping_address")
            else None,
            primary_contact={
                "email": form_data.get("email", ""),
                "phone": form_data.get("phone", ""),
            }
            if form_data.get("email") or form_data.get("phone")
            else None,
            is_active=form_data.get("is_active") is not None,
        )

    @staticmethod
    def build_invoice_input(data: dict) -> ARInvoiceInput:
        lines_data = data.get("lines", [])
        if isinstance(lines_data, str):
            lines_data = json.loads(lines_data)

        lines = []
        for line in lines_data:
            if line.get("revenue_account_id") and line.get("description"):
                # Handle both new tax_code_ids array and legacy tax_code_id field
                tax_code_ids = []
                if line.get("tax_code_ids"):
                    tax_code_ids = [
                        UUID(tc_id) for tc_id in line["tax_code_ids"] if tc_id
                    ]
                legacy_tax_code_id = (
                    UUID(line["tax_code_id"]) if line.get("tax_code_id") else None
                )

                lines.append(
                    ARInvoiceLineInput(
                        description=line.get("description", ""),
                        quantity=Decimal(str(line.get("quantity", 1))),
                        unit_price=Decimal(str(line.get("unit_price", 0))),
                        revenue_account_id=UUID(line["revenue_account_id"])
                        if line.get("revenue_account_id")
                        else None,
                        item_id=UUID(line["item_id"]) if line.get("item_id") else None,
                        tax_code_ids=tax_code_ids,
                        tax_code_id=legacy_tax_code_id,
                        cost_center_id=UUID(line["cost_center_id"])
                        if line.get("cost_center_id")
                        else None,
                        project_id=UUID(line["project_id"])
                        if line.get("project_id")
                        else None,
                    )
                )

        invoice_date = _parse_date(data.get("invoice_date")) or date.today()
        due_date = _parse_date(data.get("due_date")) or invoice_date

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
    def list_customers_context(
        db: Session,
        organization_id: str,
        search: str | None,
        status: str | None,
        page: int,
        limit: int = 50,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        offset = (page - 1) * limit

        is_active = None
        if status == "active":
            is_active = True
        elif status == "inactive":
            is_active = False

        query = db.query(Customer).filter(Customer.organization_id == org_id)
        if is_active is not None:
            query = query.filter(Customer.is_active == is_active)
        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                (Customer.customer_code.ilike(search_pattern))
                | (Customer.legal_name.ilike(search_pattern))
                | (Customer.trading_name.ilike(search_pattern))
                | (Customer.tax_identification_number.ilike(search_pattern))
            )

        total_count = (
            query.with_entities(func.count(Customer.customer_id)).scalar() or 0
        )
        customers = (
            query.order_by(Customer.legal_name).limit(limit).offset(offset).all()
        )

        open_statuses = [
            InvoiceStatus.POSTED,
            InvoiceStatus.PARTIALLY_PAID,
            InvoiceStatus.OVERDUE,
        ]
        balances = (
            db.query(
                Invoice.customer_id,
                func.coalesce(
                    func.sum(Invoice.total_amount - Invoice.amount_paid), 0
                ).label("balance"),
            )
            .filter(
                Invoice.organization_id == org_id,
                Invoice.status.in_(open_statuses),
            )
            .group_by(Invoice.customer_id)
            .all()
        )
        balance_map = {row.customer_id: row.balance for row in balances}

        # Use shared audit service for user names
        audit_service = get_audit_service(db)
        creator_names = audit_service.get_creator_names(customers)

        # Calculate balance trends for sparkline charts
        customer_ids = [c.customer_id for c in customers]
        balance_trends = _calculate_customer_balance_trends(db, org_id, customer_ids)

        customers_view = [
            _customer_list_view(
                customer,
                balance_map.get(customer.customer_id, Decimal("0")),
                creator_names.get(customer.created_by_user_id),
                balance_trends.get(customer.customer_id),
            )
            for customer in customers
        ]

        total_pages = max(1, (total_count + limit - 1) // limit)

        active_filters = build_active_filters(params={"status": status})
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
        }

    @staticmethod
    def customer_form_context(
        db: Session,
        organization_id: str,
        customer_id: str | None = None,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        customer = None
        if customer_id:
            try:
                customer = customer_service.get(db, org_id, customer_id)
            except Exception:
                customer = None
        customer_view = _customer_form_view(customer) if customer else None

        revenue_accounts = _get_accounts(db, org_id, IFRSCategory.REVENUE)
        receivable_accounts = _get_accounts(db, org_id, IFRSCategory.ASSETS, "AR")
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

        context = {
            "customer": customer_view,
            "revenue_accounts": revenue_accounts,
            "receivable_accounts": receivable_accounts,
            "tax_codes": tax_codes,
        }
        context.update(get_currency_context(db, organization_id))
        return context

    @staticmethod
    def customer_detail_context(
        db: Session,
        organization_id: str,
        customer_id: str,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        customer = None
        try:
            customer = customer_service.get(db, org_id, customer_id)
        except Exception:
            customer = None

        if not customer or customer.organization_id != org_id:
            return {"customer": None, "open_invoices": []}

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

        balance = db.query(
            func.coalesce(
                func.sum(Invoice.total_amount - Invoice.amount_paid),
                0,
            )
        ).filter(
            Invoice.organization_id == org_id,
            Invoice.customer_id == customer.customer_id,
            Invoice.status.in_(open_statuses),
        ).scalar() or Decimal("0")

        invoices = (
            db.query(Invoice)
            .filter(
                Invoice.organization_id == org_id,
                Invoice.customer_id == customer.customer_id,
                Invoice.status.in_(open_statuses),
            )
            .order_by(Invoice.due_date)
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
                    "invoice_date": _format_date(invoice.invoice_date),
                    "due_date": _format_date(invoice.due_date),
                    "total_amount": _format_currency(
                        invoice.total_amount,
                        invoice.currency_code,
                    ),
                    "balance": _format_currency(
                        balance_due,
                        invoice.currency_code,
                    ),
                    "status": _invoice_status_label(invoice.status),
                    "is_overdue": (
                        invoice.due_date < today
                        and invoice.status
                        not in {InvoiceStatus.PAID, InvoiceStatus.VOID}
                    ),
                }
            )

        customer_view = _customer_detail_view(customer, balance)
        customer_view["default_tax_code_label"] = default_tax_code_label
        customer_view["default_tax_code_id"] = (
            str(customer.default_tax_code_id) if customer.default_tax_code_id else None
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
                "file_size_display": _format_file_size(att.file_size),
                "content_type": att.content_type,
                "uploaded_at": att.uploaded_at.strftime("%Y-%m-%d %H:%M"),
                "description": att.description or "",
            }
            for att in attachments
        ]

        return {
            "customer": customer_view,
            "open_invoices": open_invoices,
            "attachments": attachments_view,
        }

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
        limit: int = 50,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        offset = (page - 1) * limit
        today = date.today()

        status_value = _parse_invoice_status(status)
        from_date = _parse_date(start_date)
        to_date = _parse_date(end_date)

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
                    "customer_name": _customer_display_name(customer),
                    "invoice_date": _format_date(invoice.invoice_date),
                    "due_date": _format_date(invoice.due_date),
                    "total_amount": _format_currency(
                        invoice.total_amount, invoice.currency_code
                    ),
                    "balance": _format_currency(balance, invoice.currency_code),
                    "status": _invoice_status_label(invoice.status),
                    "is_overdue": (
                        invoice.due_date < today
                        and invoice.status
                        not in {InvoiceStatus.PAID, InvoiceStatus.VOID}
                    ),
                }
            )

        customers_list = [
            _customer_option_view(customer)
            for customer in customer_service.list(
                db,
                organization_id=org_id,
                is_active=True,
                limit=200,
            )
        ]

        total_pages = max(1, (total_count + limit - 1) // limit)

        stats = InvoiceStats(
            total_outstanding=_format_currency(total_outstanding) or "$0.00",
            past_due=_format_currency(past_due) or "$0.00",
            due_this_week=_format_currency(due_this_week) or "$0.00",
            this_month=_format_currency(this_month) or "$0.00",
        )

        active_filters = build_active_filters(
            params={
                "status": status,
                "customer_id": customer_id,
                "start_date": start_date,
                "end_date": end_date,
            },
            labels={"start_date": "From", "end_date": "To"},
            options={"customer_id": {str(c["id"]): c["name"] for c in customers_list}},
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
        }

    @staticmethod
    def invoice_form_context(
        db: Session,
        organization_id: str,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        customers_list = [
            _customer_option_view(customer)
            for customer in customer_service.list(
                db,
                organization_id=org_id,
                is_active=True,
                limit=200,
            )
        ]

        revenue_accounts = _get_accounts(db, org_id, IFRSCategory.REVENUE)

        tax_codes = [
            {
                "tax_code_id": str(tax.tax_code_id),
                "tax_code": tax.tax_code,
                "tax_name": tax.tax_name,
                "tax_rate": tax.tax_rate,  # Raw rate (e.g., 0.075 or 50.00 for fixed)
                "rate": (tax.tax_rate * 100).quantize(Decimal("0.01"))
                if tax.tax_rate < 1
                else tax.tax_rate,  # Display rate
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

        items = (
            db.query(Item)
            .filter(
                Item.organization_id == org_id,
                Item.is_active.is_(True),
                Item.is_saleable.is_(True),
            )
            .order_by(Item.item_code)
            .all()
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

        context = {
            "customers_list": customers_list,
            "revenue_accounts": revenue_accounts,
            "tax_codes": tax_codes,
            "items": item_options,
            "cost_centers": _get_cost_centers(db, org_id),
            "projects": _get_projects(db, org_id),
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
        lines_view = [_invoice_line_view(line, invoice.currency_code) for line in lines]

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
                "file_size_display": _format_file_size(att.file_size),
                "content_type": att.content_type,
                "category": att.category.value,
                "description": att.description,
                "uploaded_at": att.uploaded_at,
                "download_url": f"/ar/attachments/{att.attachment_id}/download",
            }
            for att in attachments
        ]

        return {
            "invoice": _invoice_detail_view(invoice, customer),
            "customer": _customer_form_view(customer) if customer else None,
            "lines": lines_view,
            "attachments": attachments_view,
        }

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
        org_id = coerce_uuid(organization_id)
        offset = (page - 1) * limit

        status_value = _parse_receipt_status(status)
        from_date = _parse_date(start_date)
        to_date = _parse_date(end_date)

        query = (
            db.query(CustomerPayment, Customer)
            .join(Customer, CustomerPayment.customer_id == Customer.customer_id)
            .filter(CustomerPayment.organization_id == org_id)
        )

        if customer_id:
            query = query.filter(
                CustomerPayment.customer_id == coerce_uuid(customer_id)
            )
        if status_value:
            query = query.filter(CustomerPayment.status == status_value)
        if from_date:
            query = query.filter(CustomerPayment.payment_date >= from_date)
        if to_date:
            query = query.filter(CustomerPayment.payment_date <= to_date)
        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                or_(
                    CustomerPayment.payment_number.ilike(search_pattern),
                    CustomerPayment.reference.ilike(search_pattern),
                    CustomerPayment.description.ilike(search_pattern),
                )
            )

        total_count = (
            query.with_entities(func.count(CustomerPayment.payment_id)).scalar() or 0
        )
        receipts = (
            query.order_by(CustomerPayment.payment_date.desc())
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
                    "customer_name": _customer_display_name(customer),
                    "receipt_date": _format_date(payment.payment_date),
                    "payment_method": payment.payment_method.value,
                    "reference_number": payment.reference,
                    "amount": _format_currency(payment.amount, payment.currency_code),
                    "status": _receipt_status_label(payment.status),
                }
            )

        customers_list = [
            _customer_option_view(customer)
            for customer in customer_service.list(
                db,
                organization_id=org_id,
                is_active=True,
                limit=200,
            )
        ]

        total_pages = max(1, (total_count + limit - 1) // limit)

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
            "active_filters": active_filters,
        }

    @staticmethod
    def receipt_form_context(
        db: Session,
        organization_id: str,
        invoice_id: str | None = None,
        receipt_id: str | None = None,
        customer_id: str | None = None,
    ) -> dict:
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
                    # Build receipt view for form pre-population
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
            customer_view = _customer_option_view(customer)
            # Add WHT fields
            customer_view["is_wht_applicable"] = getattr(
                customer, "is_wht_applicable", False
            )
            customer_view["default_wht_code_id"] = (
                str(customer.default_wht_code_id)
                if getattr(customer, "default_wht_code_id", None)
                else None
            )
            customers_list.append(customer_view)

        # Get WHT tax codes for dropdown
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
        bank_accounts = _get_accounts(db, org_id, IFRSCategory.ASSETS)

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
                "customer_name": _customer_display_name(customer),
                "invoice_date": _format_date(invoice.invoice_date),
                "due_date": _format_date(invoice.due_date),
                "total_amount": _format_currency(
                    invoice.total_amount,
                    invoice.currency_code,
                ),
                "balance": _format_currency(balance, invoice.currency_code),
                "balance_raw": float(balance),  # For JS calculations
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
            _allocation_view(
                allocation,
                invoice_map.get(allocation.invoice_id),
                receipt.currency_code,
            )
            for allocation in allocations
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
                "file_size_display": _format_file_size(att.file_size),
                "content_type": att.content_type,
                "category": att.category.value,
                "description": att.description,
                "uploaded_at": att.uploaded_at,
                "download_url": f"/ar/attachments/{att.attachment_id}/download",
            }
            for att in attachments
        ]

        return {
            "receipt": _receipt_detail_view(receipt, customer),
            "customer": _customer_form_view(customer) if customer else None,
            "allocations": allocations_view,
            "attachments": attachments_view,
        }

    @staticmethod
    def aging_context(
        db: Session,
        organization_id: str,
        as_of_date: str | None,
        customer_id: str | None,
    ) -> dict:
        import logging as _log

        _logger = _log.getLogger("ar.aging_context")
        org_id = coerce_uuid(organization_id)
        ref_date = _parse_date(as_of_date)
        _logger.warning(
            "aging_context called: org=%s ref_date=%s cust=%s",
            org_id,
            ref_date,
            customer_id,
        )

        if customer_id:
            summary = ar_aging_service.calculate_customer_aging(
                db, org_id, coerce_uuid(customer_id), ref_date
            )
            aging_data = [summary]
        else:
            aging_data = ar_aging_service.get_aging_by_customer(db, org_id, ref_date)
        _logger.warning("aging_data returned %d rows", len(aging_data))
        if aging_data:
            _logger.warning(
                "first row: current=%s, over_90=%s, total=%s",
                aging_data[0].current,
                aging_data[0].over_90,
                aging_data[0].total_outstanding,
            )

        customers_list = [
            _customer_option_view(customer)
            for customer in customer_service.list(
                db,
                organization_id=org_id,
                is_active=True,
                limit=200,
            )
        ]

        # Build template-ready context from raw aging data
        currency = aging_data[0].currency_code if aging_data else "NGN"

        def fmt(v):
            return _format_currency(v, currency)

        # Aggregate totals across all customers
        total_current = sum(r.current for r in aging_data)
        total_30 = sum(r.days_31_60 for r in aging_data)
        total_60 = sum(r.days_61_90 for r in aging_data)
        total_90 = sum(r.over_90 for r in aging_data)
        grand_total = total_current + total_30 + total_60 + total_90
        total_invoices = sum(r.invoice_count for r in aging_data)

        def _pct(part: Decimal, whole: Decimal) -> float:
            return round(float(part / whole * 100), 1) if whole else 0.0

        # DSO approximation using bucket midpoints
        if grand_total:
            dso = int(
                (total_current * 15 + total_30 * 45 + total_60 * 75 + total_90 * 120)
                / grand_total
            )
        else:
            dso = 0

        aging_summary = (
            {
                "total": fmt(grand_total),
                "invoice_count": total_invoices,
                "current": fmt(total_current),
                "current_pct": _pct(total_current, grand_total),
                "days_30": fmt(total_30),
                "days_30_pct": _pct(total_30, grand_total),
                "days_60": fmt(total_60),
                "days_60_pct": _pct(total_60, grand_total),
                "days_90": fmt(total_90),
                "days_90_pct": _pct(total_90, grand_total),
                "days_90_raw": float(total_90),
                "dso": dso,
            }
            if aging_data
            else None
        )

        # Per-customer rows for the table
        customer_aging = []
        for r in aging_data:
            row_total = r.current + r.days_31_60 + r.days_61_90 + r.over_90
            customer_aging.append(
                {
                    "customer_id": r.customer_id,
                    "customer_name": r.customer_name,
                    "customer_code": r.customer_code,
                    "current": fmt(r.current),
                    "current_raw": float(r.current),
                    "days_30": fmt(r.days_31_60),
                    "days_30_raw": float(r.days_31_60),
                    "days_60": fmt(r.days_61_90),
                    "days_60_raw": float(r.days_61_90),
                    "days_90": fmt(r.over_90),
                    "days_90_raw": float(r.over_90),
                    "total": fmt(row_total),
                    "current_pct": _pct(r.current, row_total),
                    "days_30_pct": _pct(r.days_31_60, row_total),
                    "days_60_pct": _pct(r.days_61_90, row_total),
                    "days_90_pct": _pct(r.over_90, row_total),
                }
            )

        # Chart data JSON for the <script> tag
        aging_chart_data = (
            json.dumps(
                {
                    "buckets": {
                        "current": float(total_current),
                        "days_30": float(total_30),
                        "days_60": float(total_60),
                        "days_90": float(total_90),
                    },
                    "currency": currency,
                }
            )
            if aging_data
            else "{}"
        )

        return {
            "aging_summary": aging_summary,
            "customer_aging": customer_aging,
            "customers": customers_list,
            "selected_customer_id": customer_id,
            "as_of_date": as_of_date or _format_date(ref_date or date.today()),
            "aging_chart_data": aging_chart_data,
        }

    @staticmethod
    def delete_customer(
        db: Session,
        organization_id: str,
        customer_id: str,
    ) -> str | None:
        """Delete a customer. Returns error message or None on success."""
        org_id = coerce_uuid(organization_id)
        cust_id = coerce_uuid(customer_id)

        try:
            customer_service.delete_customer(db, org_id, cust_id)
            db.commit()
            return None
        except HTTPException as exc:
            db.rollback()
            return exc.detail
        except Exception as e:
            db.rollback()
            return f"Failed to delete customer: {str(e)}"

    @staticmethod
    def delete_invoice(
        db: Session,
        organization_id: str,
        invoice_id: str,
    ) -> str | None:
        """Delete an invoice. Returns error message or None on success."""
        org_id = coerce_uuid(organization_id)
        inv_id = coerce_uuid(invoice_id)

        try:
            ar_invoice_service.delete_invoice(db, org_id, inv_id)
            db.commit()
            return None
        except HTTPException as exc:
            db.rollback()
            return exc.detail
        except Exception as e:
            db.rollback()
            return f"Failed to delete invoice: {str(e)}"

    @staticmethod
    def build_receipt_input(data: dict) -> CustomerPaymentInput:
        """Build CustomerPaymentInput from form data."""
        payment_date = _parse_date(data.get("payment_date")) or date.today()

        # Parse payment method
        method_str = data.get("payment_method", "BANK_TRANSFER")
        try:
            payment_method = PaymentMethod(method_str)
        except ValueError:
            payment_method = PaymentMethod.BANK_TRANSFER

        # Parse allocations if provided
        allocations = []
        allocations_data = data.get("allocations", [])
        if isinstance(allocations_data, str):
            import json

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
        wht_certificate_number = None

        # Check if WHT is applied (has_wht checkbox or wht_amount > 0)
        has_wht = data.get("has_wht") in ("true", "1", True, "on")
        if has_wht:
            if data.get("wht_code_id"):
                wht_code_id = UUID(data["wht_code_id"])
            if data.get("wht_amount"):
                wht_amount = Decimal(str(data["wht_amount"]))
            if data.get("gross_amount"):
                gross_amount = Decimal(str(data["gross_amount"]))
            wht_certificate_number = data.get("wht_certificate_number") or None

        return CustomerPaymentInput(
            customer_id=UUID(data["customer_id"]),
            payment_date=payment_date,
            payment_method=payment_method,
            currency_code=data.get(
                "currency_code",
                settings.default_functional_currency_code,
            ),
            amount=Decimal(str(data.get("amount", 0))),
            bank_account_id=UUID(data["bank_account_id"])
            if data.get("bank_account_id")
            else None,
            reference=data.get("reference"),
            description=data.get("description"),
            allocations=allocations,
            # WHT fields
            gross_amount=gross_amount,
            wht_code_id=wht_code_id,
            wht_amount=wht_amount,
            wht_certificate_number=wht_certificate_number,
        )

    @staticmethod
    def delete_receipt(
        db: Session,
        organization_id: str,
        receipt_id: str,
    ) -> str | None:
        """Delete a receipt. Returns error message or None on success."""
        org_id = coerce_uuid(organization_id)
        pay_id = coerce_uuid(receipt_id)

        try:
            customer_payment_service.delete_receipt(db, org_id, pay_id)
            db.commit()
            return None
        except HTTPException as exc:
            db.rollback()
            return exc.detail
        except Exception as e:
            db.rollback()
            return f"Failed to delete receipt: {str(e)}"

    # =========================================================================
    # Credit Notes
    # =========================================================================

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
        """List credit notes (invoices with type CREDIT_NOTE)."""
        org_id = coerce_uuid(organization_id)
        offset = (page - 1) * limit

        status_value = _parse_invoice_status(status)
        from_date = _parse_date(start_date)
        to_date = _parse_date(end_date)

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
                    "customer_name": _customer_display_name(customer),
                    "credit_note_date": _format_date(credit_note.invoice_date),
                    "total_amount": _format_currency(
                        credit_note.total_amount, credit_note.currency_code
                    ),
                    "amount_applied": _format_currency(
                        credit_note.amount_paid, credit_note.currency_code
                    ),
                    "balance": _format_currency(
                        credit_note.total_amount - credit_note.amount_paid,
                        credit_note.currency_code,
                    ),
                    "status": _invoice_status_label(credit_note.status),
                }
            )

        customers_list = [
            _customer_option_view(customer)
            for customer in customer_service.list(
                db,
                organization_id=org_id,
                is_active=True,
                limit=200,
            )
        ]

        total_pages = max(1, (total_count + limit - 1) // limit)

        return {
            "credit_notes": credit_notes_view,
            "customers_list": customers_list,
            "stats": {
                "total_credit_notes": _format_currency(total_credit_notes) or "$0.00",
                "draft_total": _format_currency(draft_total) or "$0.00",
                "posted_total": _format_currency(posted_total) or "$0.00",
                "applied_total": _format_currency(applied_total) or "$0.00",
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
        """Context for credit note form (optionally linked to an invoice)."""
        org_id = coerce_uuid(organization_id)
        customers_list = [
            _customer_option_view(customer)
            for customer in customer_service.list(
                db,
                organization_id=org_id,
                is_active=True,
                limit=200,
            )
        ]

        revenue_accounts = _get_accounts(db, org_id, IFRSCategory.REVENUE)

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
                "customer_name": _customer_display_name(customer),
                "invoice_date": _format_date(invoice.invoice_date),
                "total_amount": _format_currency(
                    invoice.total_amount, invoice.currency_code
                ),
                "balance": _format_currency(balance, invoice.currency_code),
                "currency_code": invoice.currency_code,
            }
            open_invoices.append(view)
            if invoice_id and str(invoice.invoice_id) == invoice_id:
                selected_invoice = view

        context = {
            "customers_list": customers_list,
            "revenue_accounts": revenue_accounts,
            "tax_codes": tax_codes,
            "cost_centers": _get_cost_centers(db, org_id),
            "projects": _get_projects(db, org_id),
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
        """Detail context for a credit note."""
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
            _invoice_line_view(line, credit_note.currency_code) for line in lines
        ]

        balance = credit_note.total_amount - credit_note.amount_paid
        credit_note_view = {
            "credit_note_id": credit_note.invoice_id,
            "credit_note_number": credit_note.invoice_number,
            "customer_id": credit_note.customer_id,
            "customer_name": _customer_display_name(customer) if customer else "",
            "credit_note_date": _format_date(credit_note.invoice_date),
            "currency_code": credit_note.currency_code,
            "subtotal": _format_currency(
                credit_note.subtotal, credit_note.currency_code
            ),
            "tax_amount": _format_currency(
                credit_note.tax_amount, credit_note.currency_code
            ),
            "total_amount": _format_currency(
                credit_note.total_amount, credit_note.currency_code
            ),
            "amount_applied": _format_currency(
                credit_note.amount_paid, credit_note.currency_code
            ),
            "balance": _format_currency(balance, credit_note.currency_code),
            "status": _invoice_status_label(credit_note.status),
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
                "file_size_display": _format_file_size(att.file_size),
                "content_type": att.content_type,
                "category": att.category.value,
                "description": att.description,
                "uploaded_at": att.uploaded_at,
                "download_url": f"/ar/attachments/{att.attachment_id}/download",
            }
            for att in attachments
        ]

        return {
            "credit_note": credit_note_view,
            "customer": _customer_form_view(customer) if customer else None,
            "lines": lines_view,
            "attachments": attachments_view,
        }

    @staticmethod
    def build_credit_note_input(data: dict) -> ARInvoiceInput:
        """Build ARInvoiceInput from form data for credit note."""
        lines_data = data.get("lines", [])
        if isinstance(lines_data, str):
            lines_data = json.loads(lines_data)

        lines = []
        for line in lines_data:
            if line.get("revenue_account_id") and line.get("description"):
                # Handle both new tax_code_ids array and legacy tax_code_id field
                tax_code_ids = []
                if line.get("tax_code_ids"):
                    tax_code_ids = [
                        UUID(tc_id) for tc_id in line["tax_code_ids"] if tc_id
                    ]
                legacy_tax_code_id = (
                    UUID(line["tax_code_id"]) if line.get("tax_code_id") else None
                )

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
                        project_id=UUID(line["project_id"])
                        if line.get("project_id")
                        else None,
                    )
                )

        credit_note_date = _parse_date(data.get("credit_note_date")) or date.today()

        return ARInvoiceInput(
            customer_id=UUID(data["customer_id"]),
            invoice_type=InvoiceType.CREDIT_NOTE,
            invoice_date=credit_note_date,
            due_date=credit_note_date,  # Credit notes don't have due dates
            currency_code=data.get(
                "currency_code",
                settings.default_functional_currency_code,
            ),
            notes=data.get("reason"),
            internal_notes=data.get("notes"),
            lines=lines,
        )

    @staticmethod
    def delete_credit_note(
        db: Session,
        organization_id: str,
        credit_note_id: str,
    ) -> str | None:
        """Delete a credit note. Returns error message or None on success."""
        org_id = coerce_uuid(organization_id)
        cn_id = coerce_uuid(credit_note_id)

        try:
            ar_invoice_service.delete_credit_note(db, org_id, cn_id)
            db.commit()
            return None
        except HTTPException as exc:
            db.rollback()
            return exc.detail
        except Exception as e:
            db.rollback()
            return f"Failed to delete credit note: {str(e)}"

    def list_customers_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        search: str | None,
        status: str | None,
        page: int,
    ) -> HTMLResponse:
        context = base_context(request, auth, "Customers", "ar")
        context.update(
            self.list_customers_context(
                db,
                str(auth.organization_id),
                search=search,
                status=status,
                page=page,
            )
        )
        return templates.TemplateResponse(request, "finance/ar/customers.html", context)

    def customer_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
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
        form_data = await request.form()

        try:
            input_data = self.build_customer_input(dict(form_data))

            customer_service.create_customer(
                db=db,
                organization_id=auth.organization_id,
                input=input_data,
            )

            return RedirectResponse(
                url="/finance/ar/customers?success=Customer+created+successfully",
                status_code=303,
            )

        except Exception as e:
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
        form_data = await request.form()

        try:
            input_data = self.build_customer_input(dict(form_data))

            customer_service.update_customer(
                db=db,
                organization_id=auth.organization_id,
                customer_id=UUID(customer_id),
                input=input_data,
            )

            return RedirectResponse(
                url="/finance/ar/customers?success=Customer+updated+successfully",
                status_code=303,
            )

        except Exception as e:
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
    ) -> HTMLResponse:
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
        context = base_context(request, auth, "New AR Invoice", "ar")
        context.update(self.invoice_form_context(db, str(auth.organization_id)))
        return templates.TemplateResponse(
            request, "finance/ar/invoice_form.html", context
        )

    async def create_invoice_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | JSONResponse | RedirectResponse | dict:
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
            if "application/json" in content_type:
                return JSONResponse(
                    status_code=400,
                    content={"detail": str(e)},
                )

            context = base_context(request, auth, "New AR Invoice", "ar")
            context.update(self.invoice_form_context(db, str(auth.organization_id)))
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

    def invoice_edit_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        invoice_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Return the edit invoice form with existing invoice data."""
        org_id = coerce_uuid(auth.organization_id)
        inv_id = coerce_uuid(invoice_id)

        invoice = db.get(Invoice, inv_id)
        if not invoice or invoice.organization_id != org_id:
            return RedirectResponse(
                url="/finance/ar/invoices?success=Record+updated+successfully",
                status_code=303,
            )

        if invoice.status != InvoiceStatus.DRAFT:
            # Can't edit non-draft invoices, redirect to detail page
            return RedirectResponse(
                url=f"/finance/ar/invoices/{invoice_id}?error=Only+draft+invoices+can+be+edited",
                status_code=303,
            )

        context = base_context(request, auth, "Edit AR Invoice", "ar")
        context.update(self.invoice_form_context(db, str(auth.organization_id)))

        # Add existing invoice data
        db.get(Customer, invoice.customer_id)
        lines = (
            db.query(InvoiceLine)
            .filter(InvoiceLine.invoice_id == inv_id)
            .order_by(InvoiceLine.line_number)
            .all()
        )

        # Build invoice object for template
        context["invoice"] = {
            "invoice_id": invoice.invoice_id,
            "invoice_number": invoice.invoice_number,
            "customer_id": invoice.customer_id,
            "invoice_date": invoice.invoice_date,
            "due_date": invoice.due_date,
            "currency_code": invoice.currency_code,
            "po_number": invoice.po_number,
            "description": invoice.notes,
            "notes": invoice.notes,
            "internal_notes": invoice.internal_notes,
            "terms": invoice.payment_terms,
            "cost_center_id": None,  # Would need to pull from first line if needed
            "project_id": None,
            "lines": [
                {
                    "line_id": line.line_id,
                    "revenue_account_id": line.revenue_account_id,
                    "description": line.description,
                    "quantity": line.quantity,
                    "unit_price": line.unit_price,
                    "tax_amount": line.tax_amount,
                    "line_taxes": (
                        db.query(InvoiceLineTax)
                        .filter(InvoiceLineTax.line_id == line.line_id)
                        .all()
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

        try:
            input_data = self.build_invoice_input(data)

            invoice = ar_invoice_service.update_invoice(
                db=db,
                organization_id=auth.organization_id,
                invoice_id=coerce_uuid(invoice_id),
                input=input_data,
                updated_by_user_id=auth.user_id,
            )

            if "application/json" in content_type:
                return JSONResponse(
                    content={"success": True, "invoice_id": str(invoice.invoice_id)}
                )

            return RedirectResponse(
                url=f"/finance/ar/invoices/{invoice.invoice_id}?success=Invoice+updated+successfully",
                status_code=303,
            )

        except Exception as e:
            if "application/json" in content_type:
                return JSONResponse(
                    status_code=400,
                    content={"detail": str(e)},
                )

            context = base_context(request, auth, "Edit AR Invoice", "ar")
            context.update(self.invoice_form_context(db, str(auth.organization_id)))
            context["error"] = str(e)
            context["form_data"] = data
            return templates.TemplateResponse(
                request, "finance/ar/invoice_form.html", context
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
            ar_invoice_service.submit_invoice(
                db=db,
                organization_id=auth.organization_id,
                invoice_id=coerce_uuid(invoice_id),
                submitted_by_user_id=auth.user_id,
            )
            return RedirectResponse(
                url=f"/finance/ar/invoices/{invoice_id}?success=Invoice+submitted+for+approval",
                status_code=303,
            )
        except Exception as e:
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
        try:
            ar_invoice_service.approve_invoice(
                db=db,
                organization_id=auth.organization_id,
                invoice_id=coerce_uuid(invoice_id),
                approved_by_user_id=auth.user_id,
            )
            return RedirectResponse(
                url=f"/finance/ar/invoices/{invoice_id}?success=Invoice+approved",
                status_code=303,
            )
        except Exception as e:
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
        try:
            ar_invoice_service.post_invoice(
                db=db,
                organization_id=auth.organization_id,
                invoice_id=coerce_uuid(invoice_id),
                posted_by_user_id=auth.user_id,
            )
            return RedirectResponse(
                url=f"/finance/ar/invoices/{invoice_id}?success=Invoice+posted+to+ledger",
                status_code=303,
            )
        except Exception as e:
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
        try:
            ar_invoice_service.void_invoice(
                db=db,
                organization_id=auth.organization_id,
                invoice_id=coerce_uuid(invoice_id),
                voided_by_user_id=auth.user_id,
                reason="Voided via web interface",
            )
            return RedirectResponse(
                url=f"/finance/ar/invoices/{invoice_id}?success=Invoice+voided",
                status_code=303,
            )
        except Exception as e:
            return RedirectResponse(
                url=f"/finance/ar/invoices/{invoice_id}?error={str(e)}",
                status_code=303,
            )

    def cancel_invoice_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        invoice_id: str,
    ) -> RedirectResponse:
        """Cancel an invoice, returning to DRAFT for editing."""
        try:
            ar_invoice_service.cancel_invoice(
                db=db,
                organization_id=auth.organization_id,
                invoice_id=coerce_uuid(invoice_id),
                cancelled_by_user_id=auth.user_id,
            )
            return RedirectResponse(
                url=f"/finance/ar/invoices/{invoice_id}?success=Invoice+cancelled.+You+can+now+edit+it.",
                status_code=303,
            )
        except Exception as e:
            return RedirectResponse(
                url=f"/finance/ar/invoices/{invoice_id}?error={str(e)}",
                status_code=303,
            )

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
        content_type = request.headers.get("content-type", "")

        if "application/json" in content_type:
            data = await request.json()
        else:
            form_data = await request.form()
            data = dict(form_data)

        try:
            input_data = self.build_receipt_input(data)

            receipt = customer_payment_service.create_payment(
                db=db,
                organization_id=auth.organization_id,
                input=input_data,
                created_by_user_id=auth.user_id,
            )

            if "application/json" in content_type:
                return {"success": True, "receipt_id": str(receipt.payment_id)}

            return RedirectResponse(
                url="/finance/ar/receipts?success=Receipt+created+successfully",
                status_code=303,
            )

        except Exception as e:
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
        """Edit receipt form page."""
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

        if "application/json" in content_type:
            data = await request.json()
        else:
            form_data = await request.form()
            data = dict(form_data)

        try:
            input_data = self.build_receipt_input(data)

            customer_payment_service.update_payment(
                db=db,
                organization_id=auth.organization_id,
                payment_id=UUID(receipt_id),
                input=input_data,
                updated_by_user_id=auth.user_id,
            )

            if "application/json" in content_type:
                return {"success": True, "receipt_id": receipt_id}

            return RedirectResponse(
                url=f"/ar/receipts/{receipt_id}?success=Receipt+updated+successfully",
                status_code=303,
            )

        except Exception as e:
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
        content_type = request.headers.get("content-type", "")

        if "application/json" in content_type:
            data = await request.json()
        else:
            form_data = await request.form()
            data = dict(form_data)

        try:
            input_data = self.build_credit_note_input(data)

            credit_note = ar_invoice_service.create_invoice(
                db=db,
                organization_id=auth.organization_id,
                input=input_data,
                created_by_user_id=auth.user_id,
            )

            if "application/json" in content_type:
                return {"success": True, "credit_note_id": str(credit_note.invoice_id)}

            return RedirectResponse(
                url="/finance/ar/credit-notes?success=Credit+note+created+successfully",
                status_code=303,
            )

        except Exception as e:
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

    def credit_note_edit_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        credit_note_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Return the edit credit note form with existing data."""
        org_id = coerce_uuid(auth.organization_id)
        cn_id = coerce_uuid(credit_note_id)

        credit_note = db.get(Invoice, cn_id)
        if not credit_note or credit_note.organization_id != org_id:
            return RedirectResponse(
                url="/finance/ar/credit-notes?success=Record+updated+successfully",
                status_code=303,
            )

        if credit_note.status != InvoiceStatus.DRAFT:
            return RedirectResponse(
                url=f"/finance/ar/credit-notes/{credit_note_id}?error=Only+draft+credit+notes+can+be+edited",
                status_code=303,
            )

        context = base_context(request, auth, "Edit Credit Note", "ar")
        context.update(self.credit_note_form_context(db, str(auth.organization_id)))
        context["credit_note"] = credit_note

        return templates.TemplateResponse(
            request, "finance/ar/credit_note_form.html", context
        )

    async def update_credit_note_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        credit_note_id: str,
    ) -> HTMLResponse | JSONResponse | RedirectResponse:
        """Handle credit note update form submission."""
        content_type = request.headers.get("content-type", "")

        if "application/json" in content_type:
            data = await request.json()
        else:
            form_data = await request.form()
            data = dict(form_data)

        try:
            input_data = self.build_credit_note_input(data)

            credit_note = ar_invoice_service.update_invoice(
                db=db,
                organization_id=auth.organization_id,
                invoice_id=coerce_uuid(credit_note_id),
                input=input_data,
                updated_by_user_id=auth.user_id,
            )

            if "application/json" in content_type:
                return JSONResponse(
                    content={
                        "success": True,
                        "credit_note_id": str(credit_note.invoice_id),
                    }
                )

            return RedirectResponse(
                url=f"/finance/ar/credit-notes/{credit_note.invoice_id}?success=Credit+note+updated+successfully",
                status_code=303,
            )

        except Exception as e:
            if "application/json" in content_type:
                return JSONResponse(
                    status_code=400,
                    content={"detail": str(e)},
                )

            context = base_context(request, auth, "Edit Credit Note", "ar")
            context.update(self.credit_note_form_context(db, str(auth.organization_id)))
            context["error"] = str(e)
            context["form_data"] = data
            return templates.TemplateResponse(
                request, "finance/ar/credit_note_form.html", context
            )

    def submit_credit_note_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        credit_note_id: str,
    ) -> RedirectResponse:
        """Submit credit note for approval."""
        try:
            ar_invoice_service.submit_invoice(
                db=db,
                organization_id=auth.organization_id,
                invoice_id=coerce_uuid(credit_note_id),
                submitted_by_user_id=auth.user_id,
            )
            return RedirectResponse(
                url=f"/finance/ar/credit-notes/{credit_note_id}?success=Credit+note+submitted+for+approval",
                status_code=303,
            )
        except Exception as e:
            return RedirectResponse(
                url=f"/finance/ar/credit-notes/{credit_note_id}?error={str(e)}",
                status_code=303,
            )

    def approve_credit_note_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        credit_note_id: str,
    ) -> RedirectResponse:
        """Approve a submitted credit note."""
        try:
            ar_invoice_service.approve_invoice(
                db=db,
                organization_id=auth.organization_id,
                invoice_id=coerce_uuid(credit_note_id),
                approved_by_user_id=auth.user_id,
            )
            return RedirectResponse(
                url=f"/finance/ar/credit-notes/{credit_note_id}?success=Credit+note+approved",
                status_code=303,
            )
        except Exception as e:
            return RedirectResponse(
                url=f"/finance/ar/credit-notes/{credit_note_id}?error={str(e)}",
                status_code=303,
            )

    def post_credit_note_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        credit_note_id: str,
    ) -> RedirectResponse:
        """Post credit note to general ledger."""
        try:
            ar_invoice_service.post_invoice(
                db=db,
                organization_id=auth.organization_id,
                invoice_id=coerce_uuid(credit_note_id),
                posted_by_user_id=auth.user_id,
            )
            return RedirectResponse(
                url=f"/finance/ar/credit-notes/{credit_note_id}?success=Credit+note+posted+to+ledger",
                status_code=303,
            )
        except Exception as e:
            return RedirectResponse(
                url=f"/finance/ar/credit-notes/{credit_note_id}?error={str(e)}",
                status_code=303,
            )

    def void_credit_note_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        credit_note_id: str,
    ) -> RedirectResponse:
        """Void a credit note."""
        try:
            ar_invoice_service.void_invoice(
                db=db,
                organization_id=auth.organization_id,
                invoice_id=coerce_uuid(credit_note_id),
                voided_by_user_id=auth.user_id,
                reason="Voided via web interface",
            )
            return RedirectResponse(
                url=f"/finance/ar/credit-notes/{credit_note_id}?success=Credit+note+voided",
                status_code=303,
            )
        except Exception as e:
            return RedirectResponse(
                url=f"/finance/ar/credit-notes/{credit_note_id}?error={str(e)}",
                status_code=303,
            )

    def aging_report_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        as_of_date: str | None,
        customer_id: str | None,
    ) -> HTMLResponse:
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

    async def upload_invoice_attachment_response(
        self,
        invoice_id: str,
        file: UploadFile,
        description: str | None,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
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
                category=AttachmentCategory.INVOICE,
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
            return RedirectResponse(
                url=f"/ar/invoices/{invoice_id}?error=Upload+failed",
                status_code=303,
            )

    async def upload_receipt_attachment_response(
        self,
        receipt_id: str,
        file: UploadFile,
        description: str | None,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        try:
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
                organization_id=auth.organization_id,
                input=input_data,
                file_content=file.file,
                uploaded_by=auth.person_id,
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
            return RedirectResponse(
                url=f"/ar/receipts/{receipt_id}?error=Upload+failed",
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
        try:
            credit_note = ar_invoice_service.get(
                db, auth.organization_id, credit_note_id
            )
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
                organization_id=auth.organization_id,
                input=input_data,
                file_content=file.file,
                uploaded_by=auth.person_id,
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
            return RedirectResponse(
                url=f"/ar/credit-notes/{credit_note_id}?error=Upload+failed",
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
        try:
            customer = customer_service.get(db, auth.organization_id, customer_id)
            if not customer or customer.organization_id != auth.organization_id:
                return RedirectResponse(
                    url=f"/ar/customers/{customer_id}?error=Customer+not+found",
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
                organization_id=auth.organization_id,
                input=input_data,
                file_content=file.file,
                uploaded_by=auth.person_id,
            )

            return RedirectResponse(
                url=f"/ar/customers/{customer_id}?success=Attachment+uploaded",
                status_code=303,
            )

        except ValueError as e:
            return RedirectResponse(
                url=f"/ar/customers/{customer_id}?error={str(e)}",
                status_code=303,
            )
        except Exception:
            return RedirectResponse(
                url=f"/ar/customers/{customer_id}?error=Upload+failed",
                status_code=303,
            )

    def download_attachment_response(
        self,
        attachment_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> FileResponse | RedirectResponse:
        attachment = attachment_service.get(db, auth.organization_id, attachment_id)

        if not attachment or attachment.organization_id != auth.organization_id:
            return RedirectResponse(
                url="/finance/ar/invoices?error=Attachment+not+found", status_code=303
            )

        file_path = attachment_service.get_file_path(attachment)

        if not file_path.exists():
            return RedirectResponse(
                url="/finance/ar/invoices?error=File+not+found", status_code=303
            )

        return FileResponse(
            path=str(file_path),
            filename=attachment.file_name,
            media_type=attachment.content_type,
        )

    def delete_attachment_response(
        self,
        attachment_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        attachment = attachment_service.get(db, auth.organization_id, attachment_id)

        if not attachment or attachment.organization_id != auth.organization_id:
            return RedirectResponse(
                url="/finance/ar/invoices?error=Attachment+not+found", status_code=303
            )

        entity_type = attachment.entity_type
        entity_id = attachment.entity_id

        attachment_service.delete(db, attachment_id, auth.organization_id)

        redirect_map = {
            "CUSTOMER_INVOICE": f"/ar/invoices/{entity_id}",
            "CUSTOMER_PAYMENT": f"/ar/receipts/{entity_id}",
            "CREDIT_NOTE": f"/ar/credit-notes/{entity_id}",
            "CUSTOMER": f"/ar/customers/{entity_id}",
        }

        redirect_url = redirect_map.get(entity_type, "/ar/invoices")
        return RedirectResponse(
            url=f"{redirect_url}?success=Attachment+deleted",
            status_code=303,
        )


ar_web_service = ARWebService()
