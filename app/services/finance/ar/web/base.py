"""
AR Web Service Base - Shared utilities and view transformers.

Provides common functions for AR web services including:
- View transformers for customers, invoices, receipts
- Parsing helpers for status values
- Account and reference data queries
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session, load_only

from app.models.finance.ar.customer import Customer, CustomerType
from app.models.finance.ar.customer_payment import CustomerPayment, PaymentStatus
from app.models.finance.ar.invoice import Invoice, InvoiceStatus
from app.models.finance.ar.invoice_line import InvoiceLine
from app.models.finance.ar.payment_allocation import PaymentAllocation
from app.models.finance.core_org.cost_center import CostCenter
from app.models.finance.core_org.project import Project
from app.models.finance.gl.account import Account
from app.models.finance.gl.account_category import AccountCategory, IFRSCategory
from app.services.finance.common import (
    format_currency,
    format_date,
    format_file_size,
    parse_date,
    parse_enum_safe,
)

logger = logging.getLogger(__name__)


# ==============================================================================
# Parsing Utilities
# ==============================================================================


def parse_customer_type(value: Optional[str]) -> CustomerType:
    """Parse customer type from string value."""
    parsed = parse_enum_safe(CustomerType, value, CustomerType.COMPANY)
    return parsed or CustomerType.COMPANY


def parse_invoice_status(value: Optional[str]) -> Optional[InvoiceStatus]:
    """Parse invoice status from string value."""
    if not value:
        return None
    if value == "PARTIAL":
        return InvoiceStatus.PARTIALLY_PAID
    try:
        return InvoiceStatus(value)
    except ValueError:
        return None


def parse_receipt_status(value: Optional[str]) -> Optional[PaymentStatus]:
    """Parse receipt/payment status from string value."""
    if not value:
        return None
    status_map = {
        "DRAFT": PaymentStatus.PENDING,
        "POSTED": PaymentStatus.CLEARED,
        "VOIDED": PaymentStatus.VOID,
    }
    return status_map.get(value)


# ==============================================================================
# Display/Label Utilities
# ==============================================================================


def customer_display_name(customer: Customer) -> str:
    """Get display name for a customer."""
    return customer.trading_name or customer.legal_name


def invoice_status_label(status: InvoiceStatus) -> str:
    """Get display label for invoice status."""
    if status == InvoiceStatus.PARTIALLY_PAID:
        return "PARTIAL"
    return str(status.value)


def receipt_status_label(status: PaymentStatus) -> str:
    """Get display label for receipt/payment status."""
    if status == PaymentStatus.CLEARED:
        return "POSTED"
    if status == PaymentStatus.PENDING:
        return "DRAFT"
    if status in {PaymentStatus.VOID, PaymentStatus.BOUNCED, PaymentStatus.REVERSED}:
        return "VOIDED"
    return str(status.value)


# ==============================================================================
# View Transformers - Customers
# ==============================================================================


def customer_option_view(customer: Customer) -> dict:
    """Transform customer to option/select view."""
    return {
        "customer_id": customer.customer_id,
        "customer_name": customer_display_name(customer),
        "customer_code": customer.customer_code,
        "currency_code": customer.currency_code,
        "payment_terms_days": customer.credit_terms_days,
    }


def customer_form_view(customer: Customer) -> dict:
    """Transform customer to form edit view."""
    contact = customer.primary_contact or {}
    return {
        "customer_id": customer.customer_id,
        "customer_code": customer.customer_code,
        "customer_name": customer_display_name(customer),
        "tax_id": customer.tax_identification_number,
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


def customer_list_view(
    customer: Customer,
    balance: Decimal,
    created_by_name: str | None = None,
    balance_trend: list[float] | None = None,
) -> dict:
    """Transform customer to list view."""
    return {
        "customer_id": customer.customer_id,
        "customer_code": customer.customer_code,
        "customer_name": customer_display_name(customer),
        "tax_id": customer.tax_identification_number,
        "payment_terms_days": customer.credit_terms_days,
        "credit_limit": format_currency(
            customer.credit_limit or Decimal("0"),
            customer.currency_code,
        ),
        "balance": format_currency(balance, customer.currency_code),
        "balance_trend": balance_trend
        if balance_trend and any(v > 0 for v in balance_trend)
        else None,
        "is_active": customer.is_active,
        "created_at": customer.created_at,
        "created_by_user_id": customer.created_by_user_id,
        "created_by_name": created_by_name,
        "updated_at": customer.updated_at,
    }


def customer_detail_view(customer: Customer, balance: Decimal) -> dict:
    """Transform customer to detail view."""
    contact = customer.primary_contact or {}
    return {
        "customer_id": customer.customer_id,
        "customer_code": customer.customer_code,
        "customer_name": customer_display_name(customer),
        "tax_id": customer.tax_identification_number,
        "currency_code": customer.currency_code,
        "payment_terms_days": customer.credit_terms_days,
        "credit_limit": format_currency(
            customer.credit_limit or Decimal("0"),
            customer.currency_code,
        ),
        "balance": format_currency(balance, customer.currency_code),
        "default_revenue_account_id": customer.default_revenue_account_id,
        "default_receivable_account_id": customer.ar_control_account_id,
        "email": contact.get("email"),
        "phone": contact.get("phone"),
        "billing_address": (customer.billing_address or {}).get("address", ""),
        "shipping_address": (customer.shipping_address or {}).get("address", ""),
        "is_active": customer.is_active,
    }


# ==============================================================================
# View Transformers - Invoices
# ==============================================================================


def invoice_line_view(line: InvoiceLine, currency_code: str) -> dict:
    """Transform invoice line to view."""
    return {
        "line_id": line.line_id,
        "line_number": line.line_number,
        "description": line.description,
        "quantity": line.quantity,
        "unit_price": format_currency(line.unit_price, currency_code),
        "discount_amount": format_currency(line.discount_amount, currency_code),
        "tax_amount": format_currency(line.tax_amount, currency_code),
        "line_amount": format_currency(line.line_amount, currency_code),
        "revenue_account_id": line.revenue_account_id,
        "item_id": line.item_id,
        "cost_center_id": line.cost_center_id,
        "project_id": line.project_id,
    }


def invoice_detail_view(invoice: Invoice, customer: Optional[Customer]) -> dict:
    """Transform invoice to detail view."""
    balance = invoice.total_amount - invoice.amount_paid
    today = date.today()
    return {
        "invoice_id": invoice.invoice_id,
        "invoice_number": invoice.invoice_number,
        "invoice_type": invoice.invoice_type.value,
        "customer_id": invoice.customer_id,
        "customer_name": customer_display_name(customer) if customer else "",
        "invoice_date": format_date(invoice.invoice_date),
        "due_date": format_date(invoice.due_date),
        "currency_code": invoice.currency_code,
        "subtotal": format_currency(invoice.subtotal, invoice.currency_code),
        "tax_amount": format_currency(invoice.tax_amount, invoice.currency_code),
        "total_amount": format_currency(invoice.total_amount, invoice.currency_code),
        "amount_paid": format_currency(invoice.amount_paid, invoice.currency_code),
        "balance": format_currency(balance, invoice.currency_code),
        "status": invoice_status_label(invoice.status),
        "is_overdue": (
            invoice.due_date < today
            and invoice.status not in {InvoiceStatus.PAID, InvoiceStatus.VOID}
        ),
        "notes": invoice.notes,
        "internal_notes": invoice.internal_notes,
    }


# ==============================================================================
# View Transformers - Receipts/Payments
# ==============================================================================


def receipt_detail_view(payment: CustomerPayment, customer: Optional[Customer]) -> dict:
    """Transform receipt/payment to detail view."""
    return {
        "receipt_id": payment.payment_id,
        "receipt_number": payment.payment_number,
        "customer_id": payment.customer_id,
        "customer_name": customer_display_name(customer) if customer else "",
        "receipt_date": format_date(payment.payment_date),
        "payment_method": payment.payment_method.value,
        "reference_number": payment.reference,
        "description": payment.description,
        "amount": format_currency(payment.amount, payment.currency_code),
        "gross_amount": format_currency(payment.gross_amount, payment.currency_code),
        "wht_amount": format_currency(payment.wht_amount, payment.currency_code)
        if payment.wht_amount
        else None,
        "wht_code_id": payment.wht_code_id,
        "wht_certificate_number": payment.wht_certificate_number,
        "has_wht": payment.wht_amount and payment.wht_amount > 0,
        "status": receipt_status_label(payment.status),
        "currency_code": payment.currency_code,
        "bank_account_id": payment.bank_account_id,
    }


def allocation_view(
    allocation: PaymentAllocation,
    invoice: Optional[Invoice],
    currency_code: str,
) -> dict:
    """Transform payment allocation to view."""
    return {
        "allocation_id": allocation.allocation_id,
        "invoice_id": allocation.invoice_id,
        "invoice_number": invoice.invoice_number if invoice else "",
        "allocated_amount": format_currency(allocation.allocated_amount, currency_code),
        "discount_taken": format_currency(allocation.discount_taken, currency_code),
        "write_off_amount": format_currency(allocation.write_off_amount, currency_code),
        "exchange_difference": format_currency(
            allocation.exchange_difference,
            currency_code,
        ),
        "allocation_date": format_date(allocation.allocation_date),
    }


# ==============================================================================
# Reference Data Queries
# ==============================================================================


def get_accounts(
    db: Session,
    organization_id: UUID,
    ifrs_category: IFRSCategory,
    subledger_type: Optional[str] = None,
) -> List[Account]:
    """Get accounts filtered by IFRS category and optional subledger type."""
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


def get_cost_centers(db: Session, organization_id: UUID) -> List[CostCenter]:
    """Get active cost centers for organization."""
    return (
        db.query(CostCenter)
        .filter(
            CostCenter.organization_id == organization_id,
            CostCenter.is_active.is_(True),
        )
        .order_by(CostCenter.cost_center_code)
        .all()
    )


def get_projects(db: Session, organization_id: UUID) -> List[Project]:
    """Get projects for organization."""
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


def calculate_customer_balance_trends(
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

    for i in range(months - 1, -1, -1):
        if i == 0:
            as_of_date = today
        else:
            month_start = today.replace(day=1) - relativedelta(months=i)
            next_month = month_start + relativedelta(months=1)
            as_of_date = next_month - timedelta(days=1)

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


# ==============================================================================
# Data Classes
# ==============================================================================


@dataclass
class InvoiceStats:
    """Statistics for invoice list view."""

    total_outstanding: str
    past_due: str
    due_this_week: str
    this_month: str


# Re-export common utilities for convenience
__all__ = [
    # Parsing utilities
    "parse_date",
    "format_date",
    "format_currency",
    "format_file_size",
    "parse_customer_type",
    "parse_invoice_status",
    "parse_receipt_status",
    # Display utilities
    "customer_display_name",
    "invoice_status_label",
    "receipt_status_label",
    # View transformers - customers
    "customer_option_view",
    "customer_form_view",
    "customer_list_view",
    "customer_detail_view",
    # View transformers - invoices
    "invoice_line_view",
    "invoice_detail_view",
    # View transformers - receipts
    "receipt_detail_view",
    "allocation_view",
    # Reference data queries
    "get_accounts",
    "get_cost_centers",
    "get_projects",
    "calculate_customer_balance_trends",
    # Data classes
    "InvoiceStats",
    # Logger
    "logger",
]
