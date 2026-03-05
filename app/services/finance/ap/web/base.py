"""
AP Web Service Base - Shared utilities and view transformers.

Provides common functions for AP web services including:
- View transformers for suppliers, invoices, payments
- Parsing helpers for status values
- Account and reference data queries
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, load_only

from app.models.finance.ap.ap_payment_allocation import APPaymentAllocation
from app.models.finance.ap.supplier import Supplier, SupplierType
from app.models.finance.ap.supplier_invoice import (
    SupplierInvoice,
    SupplierInvoiceStatus,
)
from app.models.finance.ap.supplier_invoice_line import SupplierInvoiceLine
from app.models.finance.ap.supplier_payment import APPaymentStatus, SupplierPayment
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
from app.services.recent_activity import get_recent_activity

logger = logging.getLogger(__name__)


# ==============================================================================
# Parsing Utilities
# ==============================================================================


def parse_supplier_type(value: str | None) -> SupplierType:
    """Parse supplier type from string value."""
    parsed = parse_enum_safe(SupplierType, value, SupplierType.VENDOR)
    return parsed or SupplierType.VENDOR


def parse_invoice_status(value: str | None) -> SupplierInvoiceStatus | None:
    """Parse invoice status from string value."""
    if not value:
        return None
    status_map = {
        "PENDING": SupplierInvoiceStatus.PENDING_APPROVAL,
        "PARTIAL": SupplierInvoiceStatus.PARTIALLY_PAID,
    }
    if value in status_map:
        return status_map[value]
    try:
        return SupplierInvoiceStatus(value)
    except ValueError:
        return None


def parse_payment_status(value: str | None) -> APPaymentStatus | None:
    """Parse payment status from string value."""
    if not value:
        return None
    status_map = {
        "POSTED": APPaymentStatus.CLEARED,
        "RECONCILED": APPaymentStatus.CLEARED,
        "VOIDED": APPaymentStatus.VOID,
    }
    if value in status_map:
        return status_map[value]
    try:
        return APPaymentStatus(value)
    except ValueError:
        return None


# ==============================================================================
# Display/Label Utilities
# ==============================================================================


def supplier_display_name(supplier: Supplier) -> str:
    """Get display name for a supplier."""
    return supplier.trading_name or supplier.legal_name


def invoice_status_label(status: SupplierInvoiceStatus) -> str:
    """Get display label for invoice status."""
    if status == SupplierInvoiceStatus.PENDING_APPROVAL:
        return "PENDING"
    if status == SupplierInvoiceStatus.PARTIALLY_PAID:
        return "PARTIAL"
    return str(status.value)


def payment_status_label(status: APPaymentStatus) -> str:
    """Get display label for payment status."""
    if status in {APPaymentStatus.SENT, APPaymentStatus.CLEARED}:
        return "POSTED"
    if status == APPaymentStatus.VOID:
        return "VOIDED"
    return str(status.value)


# ==============================================================================
# View Transformers - Suppliers
# ==============================================================================


def supplier_option_view(supplier: Supplier) -> dict:
    """Transform supplier to option/select view."""
    return {
        "supplier_id": str(supplier.supplier_id),
        "supplier_name": supplier_display_name(supplier),
        "supplier_code": supplier.supplier_code,
        "currency_code": supplier.currency_code,
        "payment_terms_days": supplier.payment_terms_days,
        "withholding_tax_applicable": getattr(
            supplier, "withholding_tax_applicable", False
        ),
        "withholding_tax_code_id": str(supplier.withholding_tax_code_id)
        if getattr(supplier, "withholding_tax_code_id", None)
        else "",
        "default_tax_code_id": str(supplier.default_tax_code_id)
        if getattr(supplier, "default_tax_code_id", None)
        else "",
    }


def supplier_form_view(supplier: Supplier) -> dict:
    """Transform supplier to form edit view."""
    contact = supplier.primary_contact or {}
    return {
        "supplier_id": supplier.supplier_id,
        "supplier_code": supplier.supplier_code,
        "supplier_name": supplier_display_name(supplier),
        "tax_id": supplier.tax_identification_number,
        "currency_code": supplier.currency_code,
        "payment_terms_days": supplier.payment_terms_days,
        "payment_method": None,
        "default_expense_account_id": supplier.default_expense_account_id,
        "default_payable_account_id": supplier.ap_control_account_id,
        "default_tax_code_id": str(supplier.default_tax_code_id)
        if supplier.default_tax_code_id
        else None,
        "email": contact.get("email"),
        "phone": contact.get("phone"),
        "address": (supplier.billing_address or {}).get("address", ""),
        "is_active": supplier.is_active,
    }


def supplier_list_view(
    supplier: Supplier,
    balance: Decimal,
    created_by_name: str | None = None,
    balance_trend: list[float] | None = None,
) -> dict:
    """Transform supplier to list view."""
    contact = supplier.primary_contact or {}
    return {
        "supplier_id": supplier.supplier_id,
        "supplier_code": supplier.supplier_code,
        "supplier_name": supplier_display_name(supplier),
        "tax_id": supplier.tax_identification_number,
        "contact_email": contact.get("email"),
        "payment_terms_days": supplier.payment_terms_days,
        "balance": format_currency(balance, supplier.currency_code),
        "balance_trend": balance_trend
        if balance_trend and any(v > 0 for v in balance_trend)
        else None,
        "is_active": supplier.is_active,
        "created_at": supplier.created_at,
        "created_by_user_id": supplier.created_by_user_id,
        "created_by_name": created_by_name,
        "updated_at": supplier.updated_at,
    }


def supplier_detail_view(supplier: Supplier, balance: Decimal) -> dict:
    """Transform supplier to detail view."""
    contact = supplier.primary_contact or {}
    return {
        "supplier_id": supplier.supplier_id,
        "supplier_code": supplier.supplier_code,
        "supplier_name": supplier_display_name(supplier),
        "tax_id": supplier.tax_identification_number,
        "currency_code": supplier.currency_code,
        "payment_terms_days": supplier.payment_terms_days,
        "balance": format_currency(balance, supplier.currency_code),
        "default_expense_account_id": supplier.default_expense_account_id,
        "default_payable_account_id": supplier.ap_control_account_id,
        "email": contact.get("email"),
        "phone": contact.get("phone"),
        "address": (supplier.billing_address or {}).get("address", ""),
        "is_active": supplier.is_active,
    }


# ==============================================================================
# View Transformers - Invoices
# ==============================================================================


def invoice_line_view(line: SupplierInvoiceLine, currency_code: str) -> dict:
    """Transform invoice line to view."""
    line_amount_raw = float(line.line_amount) if line.line_amount else 0.0
    return {
        "line_id": line.line_id,
        "line_number": line.line_number,
        "description": line.description,
        "quantity": line.quantity,
        "unit_price": format_currency(line.unit_price, currency_code),
        "tax_amount": format_currency(line.tax_amount, currency_code),
        "tax_amount_raw": float(line.tax_amount),
        "tax_code_id": line.tax_code_id,
        "line_amount_raw": line_amount_raw,
        "line_amount": format_currency(line.line_amount, currency_code),
        "display_line_amount_raw": line_amount_raw,
        "display_line_amount": format_currency(line.line_amount, currency_code),
        "expense_account_id": line.expense_account_id,
        "asset_account_id": line.asset_account_id,
        "cost_center_id": line.cost_center_id,
        "project_id": line.project_id,
    }


def invoice_detail_view(invoice: SupplierInvoice, supplier: Supplier | None) -> dict:
    """Transform invoice to detail view."""
    balance = invoice.total_amount - invoice.amount_paid
    today = date.today()
    return {
        "invoice_id": invoice.invoice_id,
        "invoice_number": invoice.invoice_number,
        "supplier_invoice_number": invoice.supplier_invoice_number,
        "invoice_type": invoice.invoice_type.value,
        "supplier_id": invoice.supplier_id,
        "supplier_name": supplier_display_name(supplier) if supplier else "",
        "invoice_date": format_date(invoice.invoice_date, format="%d %b %Y"),
        "received_date": format_date(invoice.received_date, format="%d %b %Y"),
        "due_date": format_date(invoice.due_date, format="%d %b %Y"),
        "currency_code": invoice.currency_code,
        "supplier_tin": supplier.tax_identification_number if supplier else None,
        "subtotal": format_currency(invoice.subtotal, invoice.currency_code),
        "display_subtotal": format_currency(invoice.subtotal, invoice.currency_code),
        "display_subtotal_raw": float(invoice.subtotal),
        "tax_amount": format_currency(invoice.tax_amount, invoice.currency_code),
        "display_tax_amount": format_currency(
            invoice.tax_amount, invoice.currency_code
        ),
        "display_tax_amount_raw": float(invoice.tax_amount),
        "display_tax_added": format_currency(invoice.tax_amount, invoice.currency_code),
        "display_tax_added_raw": float(invoice.tax_amount),
        "display_tax_included": format_currency(Decimal("0"), invoice.currency_code),
        "display_tax_included_raw": 0.0,
        "total_amount": format_currency(invoice.total_amount, invoice.currency_code),
        "total_amount_raw": float(invoice.total_amount),
        "amount_paid": format_currency(invoice.amount_paid, invoice.currency_code),
        "amount_paid_raw": float(invoice.amount_paid),
        "balance": format_currency(balance, invoice.currency_code),
        "balance_raw": float(balance),
        "withholding_tax": format_currency(
            invoice.withholding_tax_amount, invoice.currency_code
        )
        if invoice.withholding_tax_amount
        else None,
        "withholding_tax_raw": float(invoice.withholding_tax_amount)
        if invoice.withholding_tax_amount
        else 0,
        "status": invoice_status_label(invoice.status),
        "comments": getattr(invoice, "comments", None),
        "is_overdue": (
            invoice.due_date < today
            and invoice.status
            not in {SupplierInvoiceStatus.PAID, SupplierInvoiceStatus.VOID}
        ),
    }


# ==============================================================================
# View Transformers - Payments
# ==============================================================================


def payment_detail_view(
    payment: SupplierPayment,
    supplier: Supplier | None,
    bank_account_name: str = "",
    wht_code_name: str = "",
) -> dict:
    """Transform payment to detail view."""
    wht_amount = payment.withholding_tax_amount or 0
    gross_amount = payment.gross_amount or payment.amount
    has_wht = wht_amount > 0
    return {
        "payment_id": payment.payment_id,
        "payment_number": payment.payment_number,
        "supplier_id": payment.supplier_id,
        "supplier_name": supplier_display_name(supplier) if supplier else "",
        "payment_date": format_date(payment.payment_date, format="%d %b %Y"),
        "payment_method": payment.payment_method.value,
        "reference_number": payment.reference,
        "amount": format_currency(payment.amount, payment.currency_code),
        "amount_raw": float(payment.amount),
        "status": payment_status_label(payment.status),
        "currency_code": payment.currency_code,
        "bank_account_name": bank_account_name,
        "has_wht": has_wht,
        "gross_amount": format_currency(gross_amount, payment.currency_code),
        "withholding_tax_amount": format_currency(wht_amount, payment.currency_code),
        "wht_code_name": wht_code_name,
    }


def allocation_view(
    allocation: APPaymentAllocation,
    invoice: SupplierInvoice | None,
    currency_code: str,
) -> dict:
    """Transform payment allocation to view."""
    return {
        "allocation_id": allocation.allocation_id,
        "invoice_id": allocation.invoice_id,
        "invoice_number": invoice.invoice_number if invoice else "",
        "allocated_amount": format_currency(allocation.allocated_amount, currency_code),
        "discount_taken": format_currency(allocation.discount_taken, currency_code),
        "exchange_difference": format_currency(
            allocation.exchange_difference,
            currency_code,
        ),
        "allocation_date": format_date(allocation.allocation_date, format="%d %b %Y"),
    }


# ==============================================================================
# Reference Data Queries
# ==============================================================================


def get_accounts(
    db: Session,
    organization_id: UUID,
    ifrs_category: IFRSCategory,
    subledger_type: str | None = None,
) -> list[Account]:
    """Get accounts filtered by IFRS category and optional subledger type."""
    query = (
        select(Account)
        .join(AccountCategory, Account.category_id == AccountCategory.category_id)
        .where(
            Account.organization_id == organization_id,
            Account.is_active.is_(True),
            AccountCategory.ifrs_category == ifrs_category,
        )
    )
    if subledger_type:
        query = query.where(Account.subledger_type == subledger_type)
    return list(db.scalars(query.order_by(Account.account_code)).all())


def get_cost_centers(db: Session, organization_id: UUID) -> list[CostCenter]:
    """Get active cost centers for organization."""
    return list(
        db.scalars(
            select(CostCenter)
            .where(
                CostCenter.organization_id == organization_id,
                CostCenter.is_active.is_(True),
            )
            .order_by(CostCenter.cost_center_code)
        ).all()
    )


def get_projects(db: Session, organization_id: UUID) -> list[Project]:
    """Get projects for organization."""
    return list(
        db.scalars(
            select(Project)
            .options(
                load_only(
                    Project.project_id,
                    Project.project_code,
                    Project.project_name,
                )
            )
            .where(Project.organization_id == organization_id)
            .order_by(Project.project_code)
        ).all()
    )


def calculate_supplier_balance_trends(
    db: Session,
    organization_id: UUID,
    supplier_ids: list[UUID],
    months: int = 6,
) -> dict[UUID, list[float]]:
    """
    Calculate monthly balance trends for suppliers over the last N months.

    Returns a dict mapping supplier_id to a list of balance values.
    """
    if not supplier_ids:
        return {}

    from dateutil.relativedelta import relativedelta

    trends: dict[UUID, list[float]] = {sid: [] for sid in supplier_ids}
    today = date.today()

    for i in range(months - 1, -1, -1):
        if i == 0:
            as_of_date = today
        else:
            month_start = today.replace(day=1) - relativedelta(months=i)
            next_month = month_start + relativedelta(months=1)
            as_of_date = next_month - timedelta(days=1)

        balances = db.execute(
            select(
                SupplierInvoice.supplier_id,
                func.coalesce(
                    func.sum(
                        SupplierInvoice.total_amount - SupplierInvoice.amount_paid
                    ),
                    0,
                ).label("balance"),
            )
            .where(
                SupplierInvoice.organization_id == organization_id,
                SupplierInvoice.supplier_id.in_(supplier_ids),
                SupplierInvoice.invoice_date <= as_of_date,
                SupplierInvoice.status.in_(
                    [
                        SupplierInvoiceStatus.POSTED,
                        SupplierInvoiceStatus.PARTIALLY_PAID,
                        SupplierInvoiceStatus.PAID,
                    ]
                ),
            )
            .group_by(SupplierInvoice.supplier_id)
        ).all()

        balance_map = {row.supplier_id: float(row.balance) for row in balances}

        for sid in supplier_ids:
            trends[sid].append(balance_map.get(sid, 0.0))

    return trends


def recent_activity_view(
    db: Session,
    organization_id: UUID,
    *,
    table_schema: str,
    table_name: str,
    record_id: str,
    limit: int = 10,
) -> list[dict]:
    """Return latest immutable audit log activity for a single record."""
    return get_recent_activity(
        db,
        organization_id,
        table_schema=table_schema,
        table_name=table_name,
        record_id=record_id,
        limit=limit,
    )


# ==============================================================================
# Data Classes
# ==============================================================================


@dataclass
class InvoiceStats:
    """Statistics for invoice list view."""

    total_outstanding: str
    past_due: str
    due_this_week: str
    pending_count: int


# Re-export common utilities for convenience
__all__ = [
    # Parsing utilities
    "parse_date",
    "format_date",
    "format_currency",
    "format_file_size",
    "parse_supplier_type",
    "parse_invoice_status",
    "parse_payment_status",
    # Display utilities
    "supplier_display_name",
    "invoice_status_label",
    "payment_status_label",
    # View transformers - suppliers
    "supplier_option_view",
    "supplier_form_view",
    "supplier_list_view",
    "supplier_detail_view",
    # View transformers - invoices
    "invoice_line_view",
    "invoice_detail_view",
    # View transformers - payments
    "payment_detail_view",
    "allocation_view",
    # Reference data queries
    "get_accounts",
    "get_cost_centers",
    "get_projects",
    "calculate_supplier_balance_trends",
    "recent_activity_view",
    # Data classes
    "InvoiceStats",
    # Logger
    "logger",
]
