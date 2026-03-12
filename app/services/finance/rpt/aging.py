"""AP and AR aging report context builders."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.ap.supplier import Supplier
from app.models.finance.ap.supplier_invoice import (
    SupplierInvoice,
    SupplierInvoiceStatus,
)
from app.models.finance.ar.customer import Customer
from app.models.finance.ar.invoice import Invoice as ARInvoice
from app.services.common import coerce_uuid
from app.services.finance.rpt.common import (
    _format_currency,
    _format_date,
    _iso_date,
    _parse_date,
)


def ap_aging_context(
    db: Session,
    organization_id: str,
    as_of_date: str | None = None,
) -> dict[str, Any]:
    """Get context for AP aging report."""
    org_id = coerce_uuid(organization_id)
    ref_date = _parse_date(as_of_date) or date.today()

    # Get open invoices
    invoices = db.execute(
        select(SupplierInvoice, Supplier)
        .join(Supplier, SupplierInvoice.supplier_id == Supplier.supplier_id)
        .where(
            SupplierInvoice.organization_id == org_id,
            SupplierInvoice.status.in_(
                [
                    SupplierInvoiceStatus.POSTED,
                    SupplierInvoiceStatus.PARTIALLY_PAID,
                ]
            ),
            SupplierInvoice.invoice_date <= ref_date,
        )
        .order_by(SupplierInvoice.due_date)
    ).all()

    # Aging buckets
    current: list[dict[str, Any]] = []
    days_1_30: list[dict[str, Any]] = []
    days_31_60: list[dict[str, Any]] = []
    days_61_90: list[dict[str, Any]] = []
    over_90: list[dict[str, Any]] = []

    total_current = Decimal("0")
    total_1_30 = Decimal("0")
    total_31_60 = Decimal("0")
    total_61_90 = Decimal("0")
    total_over_90 = Decimal("0")

    for invoice, supplier in invoices:
        due_date = invoice.due_date
        balance = invoice.balance_due or Decimal("0")

        if not due_date:
            continue

        days_overdue = (ref_date - due_date).days

        entry: dict[str, Any] = {
            "invoice_number": invoice.invoice_number,
            "supplier_name": supplier.trading_name or supplier.legal_name,
            "invoice_date": _format_date(invoice.invoice_date),
            "due_date": _format_date(due_date),
            "amount": _format_currency(balance, invoice.currency_code),
            "amount_raw": float(balance),
            "days_overdue": max(0, days_overdue),
        }

        if days_overdue <= 0:
            current.append(entry)
            total_current += balance
        elif days_overdue <= 30:
            days_1_30.append(entry)
            total_1_30 += balance
        elif days_overdue <= 60:
            days_31_60.append(entry)
            total_31_60 += balance
        elif days_overdue <= 90:
            days_61_90.append(entry)
            total_61_90 += balance
        else:
            over_90.append(entry)
            total_over_90 += balance

    grand_total = total_current + total_1_30 + total_31_60 + total_61_90 + total_over_90

    return {
        "as_of_date": _format_date(ref_date),
        "as_of_date_iso": _iso_date(ref_date),
        "current": current,
        "days_1_30": days_1_30,
        "days_31_60": days_31_60,
        "days_61_90": days_61_90,
        "over_90": over_90,
        "total_current": _format_currency(total_current),
        "total_1_30": _format_currency(total_1_30),
        "total_31_60": _format_currency(total_31_60),
        "total_61_90": _format_currency(total_61_90),
        "total_over_90": _format_currency(total_over_90),
        "grand_total": _format_currency(grand_total),
        "summary": [
            {
                "bucket": "Current",
                "amount": _format_currency(total_current),
                "amount_raw": float(total_current),
            },
            {
                "bucket": "1-30 Days",
                "amount": _format_currency(total_1_30),
                "amount_raw": float(total_1_30),
            },
            {
                "bucket": "31-60 Days",
                "amount": _format_currency(total_31_60),
                "amount_raw": float(total_31_60),
            },
            {
                "bucket": "61-90 Days",
                "amount": _format_currency(total_61_90),
                "amount_raw": float(total_61_90),
            },
            {
                "bucket": "Over 90 Days",
                "amount": _format_currency(total_over_90),
                "amount_raw": float(total_over_90),
            },
        ],
    }


def ar_aging_context(
    db: Session,
    organization_id: str,
    as_of_date: str | None = None,
) -> dict[str, Any]:
    """Get context for AR aging report."""
    org_id = coerce_uuid(organization_id)
    ref_date = _parse_date(as_of_date) or date.today()

    # Get open invoices
    from app.models.finance.ar.invoice import InvoiceStatus as ARInvoiceStatus

    invoices = db.execute(
        select(ARInvoice, Customer)
        .join(Customer, ARInvoice.customer_id == Customer.customer_id)
        .where(
            ARInvoice.organization_id == org_id,
            ARInvoice.status.in_(
                [
                    ARInvoiceStatus.POSTED,
                    ARInvoiceStatus.PARTIALLY_PAID,
                ]
            ),
            ARInvoice.invoice_date <= ref_date,
        )
        .order_by(ARInvoice.due_date)
    ).all()

    # Aging buckets
    current: list[dict[str, Any]] = []
    days_1_30: list[dict[str, Any]] = []
    days_31_60: list[dict[str, Any]] = []
    days_61_90: list[dict[str, Any]] = []
    over_90: list[dict[str, Any]] = []

    total_current = Decimal("0")
    total_1_30 = Decimal("0")
    total_31_60 = Decimal("0")
    total_61_90 = Decimal("0")
    total_over_90 = Decimal("0")

    for invoice, customer in invoices:
        due_date = invoice.due_date
        balance = invoice.balance_due or Decimal("0")

        if not due_date:
            continue

        days_overdue = (ref_date - due_date).days

        entry: dict[str, Any] = {
            "invoice_number": invoice.invoice_number,
            "customer_name": customer.trading_name or customer.legal_name,
            "invoice_date": _format_date(invoice.invoice_date),
            "due_date": _format_date(due_date),
            "amount": _format_currency(balance, invoice.currency_code),
            "amount_raw": float(balance),
            "days_overdue": max(0, days_overdue),
        }

        if days_overdue <= 0:
            current.append(entry)
            total_current += balance
        elif days_overdue <= 30:
            days_1_30.append(entry)
            total_1_30 += balance
        elif days_overdue <= 60:
            days_31_60.append(entry)
            total_31_60 += balance
        elif days_overdue <= 90:
            days_61_90.append(entry)
            total_61_90 += balance
        else:
            over_90.append(entry)
            total_over_90 += balance

    grand_total = total_current + total_1_30 + total_31_60 + total_61_90 + total_over_90

    return {
        "as_of_date": _format_date(ref_date),
        "as_of_date_iso": _iso_date(ref_date),
        "current": current,
        "days_1_30": days_1_30,
        "days_31_60": days_31_60,
        "days_61_90": days_61_90,
        "over_90": over_90,
        "total_current": _format_currency(total_current),
        "total_1_30": _format_currency(total_1_30),
        "total_31_60": _format_currency(total_31_60),
        "total_61_90": _format_currency(total_61_90),
        "total_over_90": _format_currency(total_over_90),
        "grand_total": _format_currency(grand_total),
        "summary": [
            {
                "bucket": "Current",
                "amount": _format_currency(total_current),
                "amount_raw": float(total_current),
            },
            {
                "bucket": "1-30 Days",
                "amount": _format_currency(total_1_30),
                "amount_raw": float(total_1_30),
            },
            {
                "bucket": "31-60 Days",
                "amount": _format_currency(total_31_60),
                "amount_raw": float(total_31_60),
            },
            {
                "bucket": "61-90 Days",
                "amount": _format_currency(total_61_90),
                "amount_raw": float(total_61_90),
            },
            {
                "bucket": "Over 90 Days",
                "amount": _format_currency(total_over_90),
                "amount_raw": float(total_over_90),
            },
        ],
    }
