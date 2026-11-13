"""Supplier payout report with line-item breakdown."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services import formatters as _date_formatters
from app.models.finance.ap.ap_payment_allocation import APPaymentAllocation
from app.models.finance.ap.supplier import Supplier
from app.models.finance.ap.supplier_invoice import SupplierInvoice
from app.models.finance.ap.supplier_payment import APPaymentStatus, SupplierPayment
from app.services.common import coerce_uuid
from app.services.finance.rpt.common import (
    _build_csv,
    _format_currency,
    _iso_date,
    _parse_date,
)


@dataclass(slots=True)
class _AllocationRow:
    allocation: APPaymentAllocation
    invoice: SupplierInvoice | None


def _supplier_name(supplier: Supplier | None) -> str:
    if supplier is None:
        return ""
    return supplier.trading_name or supplier.legal_name


def _status_options() -> list[dict[str, str]]:
    return [
        {"value": status.value, "label": status.value.replace("_", " ").title()}
        for status in APPaymentStatus
    ]


def vendor_payout_breakdown_context(
    db: Session,
    organization_id: str,
    start_date: str | None = None,
    end_date: str | None = None,
    supplier_id: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """Build supplier payout report context grouped by payment with allocations."""
    org_id = coerce_uuid(organization_id)
    today = date.today()
    from_date = _parse_date(start_date) or today.replace(day=1)
    to_date = _parse_date(end_date) or today

    status_enum: APPaymentStatus | None = None
    if status:
        try:
            status_enum = APPaymentStatus(status)
        except ValueError:
            status_enum = None

    payments_stmt = (
        select(SupplierPayment, Supplier)
        .join(Supplier, Supplier.supplier_id == SupplierPayment.supplier_id)
        .where(
            SupplierPayment.organization_id == org_id,
            SupplierPayment.payment_date >= from_date,
            SupplierPayment.payment_date <= to_date,
        )
        .order_by(
            SupplierPayment.payment_date.desc(),
            SupplierPayment.created_at.desc(),
        )
    )

    if supplier_id:
        payments_stmt = payments_stmt.where(
            SupplierPayment.supplier_id == coerce_uuid(supplier_id)
        )

    if status_enum is not None:
        payments_stmt = payments_stmt.where(SupplierPayment.status == status_enum)
    else:
        payments_stmt = payments_stmt.where(
            SupplierPayment.status.in_(tuple(APPaymentStatus.effective()))
        )

    payment_rows = db.execute(payments_stmt).all()
    payments = [payment for payment, _supplier in payment_rows]
    payment_ids = [payment.payment_id for payment in payments]

    allocations_by_payment: dict[Any, list[_AllocationRow]] = defaultdict(list)
    if payment_ids:
        allocation_rows = db.execute(
            select(APPaymentAllocation, SupplierInvoice)
            .join(
                SupplierInvoice,
                SupplierInvoice.invoice_id == APPaymentAllocation.invoice_id,
                isouter=True,
            )
            .where(APPaymentAllocation.payment_id.in_(payment_ids))
            .order_by(
                APPaymentAllocation.payment_id,
                APPaymentAllocation.allocation_date,
                APPaymentAllocation.created_at,
            )
        ).all()
        for allocation, invoice in allocation_rows:
            allocations_by_payment[allocation.payment_id].append(
                _AllocationRow(allocation=allocation, invoice=invoice)
            )

    supplier_option_rows = db.scalars(
        select(Supplier)
        .where(
            Supplier.organization_id == org_id,
            Supplier.is_active.is_(True),
        )
        .order_by(Supplier.trading_name, Supplier.legal_name)
    ).all()
    supplier_options = [
        {
            "supplier_id": str(supplier.supplier_id),
            "supplier_name": _supplier_name(supplier),
            "supplier_code": supplier.supplier_code,
            "supplier_display": (
                f"{_supplier_name(supplier)} ({supplier.supplier_code})"
                if supplier.supplier_code
                else _supplier_name(supplier)
            ),
        }
        for supplier in supplier_option_rows
    ]
    selected_supplier_name = ""
    selected_supplier_display = ""
    if supplier_id:
        selected_supplier = db.get(Supplier, coerce_uuid(supplier_id))
        if selected_supplier and selected_supplier.organization_id == org_id:
            selected_supplier_name = _supplier_name(selected_supplier)
            selected_supplier_display = (
                f"{selected_supplier_name} ({selected_supplier.supplier_code})"
                if selected_supplier.supplier_code
                else selected_supplier_name
            )

    grouped_payments: list[dict[str, Any]] = []
    total_net = Decimal("0")
    total_gross = Decimal("0")
    total_wht = Decimal("0")
    total_allocated = Decimal("0")
    total_unallocated = Decimal("0")
    unique_suppliers: set[str] = set()
    supplier_totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    total_lines = 0
    largest_payment_number = ""
    largest_payment_amount = Decimal("0")

    for payment, supplier in payment_rows:
        supplier_name = _supplier_name(supplier)
        payment_allocations = allocations_by_payment.get(payment.payment_id, [])
        gross_amount = payment.gross_amount or payment.amount
        wht_amount = payment.withholding_tax_amount or Decimal("0")
        allocated_total = Decimal("0")
        discount_total = Decimal("0")
        fx_total = Decimal("0")
        lines: list[dict[str, Any]] = []

        for row in payment_allocations:
            allocated_total += row.allocation.allocated_amount or Decimal("0")
            discount_total += row.allocation.discount_taken or Decimal("0")
            fx_total += row.allocation.exchange_difference or Decimal("0")
            total_lines += 1
            lines.append(
                {
                    "line_type": "invoice",
                    "invoice_id": str(row.invoice.invoice_id) if row.invoice else "",
                    "invoice_number": row.invoice.invoice_number
                    if row.invoice
                    else "—",
                    "invoice_date": _date_formatters.format_date_display(
                        row.invoice.invoice_date
                    )
                    if row.invoice and row.invoice.invoice_date
                    else "—",
                    "description": "Invoice allocation",
                    "allocated_amount": _format_currency(
                        row.allocation.allocated_amount,
                        payment.currency_code,
                    ),
                    "allocated_amount_raw": float(row.allocation.allocated_amount or 0),
                    "discount_taken": _format_currency(
                        row.allocation.discount_taken,
                        payment.currency_code,
                    ),
                    "discount_taken_raw": float(row.allocation.discount_taken or 0),
                    "exchange_difference": _format_currency(
                        row.allocation.exchange_difference,
                        payment.currency_code,
                    ),
                    "exchange_difference_raw": float(
                        row.allocation.exchange_difference or 0
                    ),
                    "allocation_date": _date_formatters.format_date_display(
                        row.allocation.allocation_date
                    ),
                }
            )

        unallocated_amount = gross_amount - allocated_total - wht_amount
        if wht_amount > 0:
            total_lines += 1
            lines.append(
                {
                    "line_type": "wht",
                    "invoice_id": "",
                    "invoice_number": "—",
                    "invoice_date": "—",
                    "description": "Withholding tax deduction",
                    "allocated_amount": _format_currency(
                        wht_amount, payment.currency_code
                    ),
                    "allocated_amount_raw": float(wht_amount),
                    "discount_taken": _format_currency(
                        Decimal("0"), payment.currency_code
                    ),
                    "discount_taken_raw": 0.0,
                    "exchange_difference": _format_currency(
                        Decimal("0"), payment.currency_code
                    ),
                    "exchange_difference_raw": 0.0,
                    "allocation_date": _date_formatters.format_date_display(
                        payment.payment_date
                    ),
                }
            )

        if unallocated_amount != 0:
            total_lines += 1
            lines.append(
                {
                    "line_type": "unallocated",
                    "invoice_id": "",
                    "invoice_number": "—",
                    "invoice_date": "—",
                    "description": "Unallocated remainder",
                    "allocated_amount": _format_currency(
                        unallocated_amount, payment.currency_code
                    ),
                    "allocated_amount_raw": float(unallocated_amount),
                    "discount_taken": _format_currency(
                        Decimal("0"), payment.currency_code
                    ),
                    "discount_taken_raw": 0.0,
                    "exchange_difference": _format_currency(
                        Decimal("0"), payment.currency_code
                    ),
                    "exchange_difference_raw": 0.0,
                    "allocation_date": _date_formatters.format_date_display(
                        payment.payment_date
                    ),
                }
            )
            total_unallocated += unallocated_amount

        grouped_payments.append(
            {
                "payment_id": str(payment.payment_id),
                "payment_number": payment.payment_number,
                "payment_date": _date_formatters.format_date_display(
                    payment.payment_date
                ),
                "supplier_name": supplier_name,
                "supplier_id": str(payment.supplier_id),
                "status": payment.status.value,
                "payment_method": payment.payment_method.value.replace(
                    "_", " "
                ).title(),
                "reference": payment.reference or "—",
                "currency_code": payment.currency_code,
                "gross_amount": _format_currency(gross_amount, payment.currency_code),
                "gross_amount_raw": float(gross_amount),
                "net_amount": _format_currency(payment.amount, payment.currency_code),
                "net_amount_raw": float(payment.amount),
                "withholding_tax_amount": _format_currency(
                    wht_amount, payment.currency_code
                ),
                "withholding_tax_amount_raw": float(wht_amount),
                "allocated_total": _format_currency(
                    allocated_total, payment.currency_code
                ),
                "allocated_total_raw": float(allocated_total),
                "discount_total": _format_currency(
                    discount_total, payment.currency_code
                ),
                "discount_total_raw": float(discount_total),
                "fx_total": _format_currency(fx_total, payment.currency_code),
                "fx_total_raw": float(fx_total),
                "line_count": len(lines),
                "lines": lines,
            }
        )

        total_net += payment.amount
        total_gross += gross_amount
        total_wht += wht_amount
        total_allocated += allocated_total
        if supplier_name:
            unique_suppliers.add(supplier_name)
            supplier_totals[supplier_name] += payment.amount
        if payment.amount > largest_payment_amount:
            largest_payment_amount = payment.amount
            largest_payment_number = payment.payment_number

    average_payout = (
        total_net / len(grouped_payments) if grouped_payments else Decimal("0")
    )
    top_supplier_name = ""
    top_supplier_total = Decimal("0")
    if supplier_totals:
        top_supplier_name, top_supplier_total = max(
            supplier_totals.items(),
            key=lambda item: item[1],
        )

    return {
        "payments": grouped_payments,
        "supplier_options": supplier_options,
        "status_options": _status_options(),
        "start_date": _date_formatters.format_date_display(from_date),
        "start_date_iso": _iso_date(from_date),
        "end_date": _date_formatters.format_date_display(to_date),
        "end_date_iso": _iso_date(to_date),
        "supplier_id": supplier_id or "",
        "selected_supplier_name": selected_supplier_name,
        "selected_supplier_display": selected_supplier_display,
        "status": status or "",
        "total_payments": len(grouped_payments),
        "total_suppliers": len(unique_suppliers),
        "total_lines": total_lines,
        "total_net": _format_currency(total_net),
        "total_net_raw": float(total_net),
        "total_gross": _format_currency(total_gross),
        "total_gross_raw": float(total_gross),
        "total_wht": _format_currency(total_wht),
        "total_wht_raw": float(total_wht),
        "total_allocated": _format_currency(total_allocated),
        "total_allocated_raw": float(total_allocated),
        "total_unallocated": _format_currency(total_unallocated),
        "total_unallocated_raw": float(total_unallocated),
        "average_payout": _format_currency(average_payout),
        "average_payout_raw": float(average_payout),
        "top_supplier_name": top_supplier_name,
        "top_supplier_total": _format_currency(top_supplier_total),
        "top_supplier_total_raw": float(top_supplier_total),
        "largest_payment_number": largest_payment_number,
        "largest_payment_amount": _format_currency(largest_payment_amount),
        "largest_payment_amount_raw": float(largest_payment_amount),
    }


def export_vendor_payout_breakdown_csv(
    organization_id: str,
    db: Session,
    start_date: str | None = None,
    end_date: str | None = None,
    supplier_id: str | None = None,
    status: str | None = None,
) -> str:
    """Export the supplier payout breakdown report as CSV."""
    context = vendor_payout_breakdown_context(
        db=db,
        organization_id=organization_id,
        start_date=start_date,
        end_date=end_date,
        supplier_id=supplier_id,
        status=status,
    )

    headers = [
        "Payment Date",
        "Payment Number",
        "Supplier",
        "Status",
        "Method",
        "Reference",
        "Currency",
        "Gross Amount",
        "WHT Amount",
        "Net Paid",
        "Line Type",
        "Invoice Number",
        "Line Description",
        "Allocated Amount",
        "Discount Taken",
        "Exchange Difference",
        "Allocation Date",
    ]
    rows: list[list[str]] = []
    for payment in context["payments"]:
        if not payment["lines"]:
            rows.append(
                [
                    payment["payment_date"],
                    payment["payment_number"],
                    payment["supplier_name"],
                    payment["status"],
                    payment["payment_method"],
                    payment["reference"],
                    payment["currency_code"],
                    payment["gross_amount"],
                    payment["withholding_tax_amount"],
                    payment["net_amount"],
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                ]
            )
            continue

        for line in payment["lines"]:
            rows.append(
                [
                    payment["payment_date"],
                    payment["payment_number"],
                    payment["supplier_name"],
                    payment["status"],
                    payment["payment_method"],
                    payment["reference"],
                    payment["currency_code"],
                    payment["gross_amount"],
                    payment["withholding_tax_amount"],
                    payment["net_amount"],
                    line["line_type"],
                    line["invoice_number"],
                    line["description"],
                    line["allocated_amount"],
                    line["discount_taken"],
                    line["exchange_difference"],
                    line["allocation_date"],
                ]
            )

    return _build_csv(headers, rows)
