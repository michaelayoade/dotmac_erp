"""
Shared helpers for financial report context builders.

Contains utility functions and common query patterns used across
multiple report modules.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from app.models.finance.gl.account import Account
from app.models.finance.gl.account_category import AccountCategory, IFRSCategory
from app.models.finance.gl.fiscal_period import FiscalPeriod
from app.models.finance.gl.journal_entry import JournalEntry, JournalStatus
from app.models.finance.gl.journal_entry_line import JournalEntryLine
from app.models.finance.rpt.report_definition import ReportType
from app.services.common import coerce_uuid
from app.services.formatters import format_currency as _format_currency
from app.services.formatters import format_date as _format_date
from app.services.formatters import parse_date as _parse_date

# Journal entry types that represent actual cash movement.
# Used to filter GL queries for cash basis reporting.
CASH_BASIS_DOC_TYPES: frozenset[str] = frozenset(
    {
        "CUSTOMER_PAYMENT",
        "SUPPLIER_PAYMENT",
        "EXPENSE_PAYMENT",
        "BANK_TRANSFER",
    }
)

logger = logging.getLogger(__name__)

# Re-export for use by report modules
__all__ = [
    "Account",
    "AccountCategory",
    "CASH_BASIS_DOC_TYPES",
    "FiscalPeriod",
    "IFRSCategory",
    "JournalEntry",
    "JournalEntryLine",
    "JournalStatus",
    "ReportType",
    "coerce_uuid",
    "_format_currency",
    "_format_date",
    "_parse_date",
    "_iso_date",
    "_build_csv",
    "_build_xlsx",
    "_ifrs_label",
    "_report_type_label",
    "_amount_from_category",
    "_apply_cash_basis_filter",
    "_apply_cash_basis_filter_pll",
    "_category_balances",
    "_tax_totals_from_gl",
    "_cash_basis_vat_totals",
    "_cash_basis_wht_totals",
    "_cash_basis_revenue_totals",
    "_cash_basis_revenue_by_customer",
]


def _iso_date(d: date) -> str:
    """Format date as YYYY-MM-DD for HTML5 date inputs."""
    return d.isoformat()


def _build_csv(headers: list[str], rows: list[list[str]]) -> str:
    """Build a CSV string from headers and rows."""
    import csv
    import io

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerows(rows)
    return output.getvalue()


def _coerce_xlsx_cell(value: Any) -> Any:
    """Coerce a string cell to a number where it round-trips cleanly.

    Report rows are built as strings (shared with the CSV path). For Excel we
    want numeric columns to be real numbers so totals/sorting work, so attempt
    a clean int/float conversion and fall back to the original string.
    """
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return value
    try:
        if text.lstrip("-").isdigit():
            return int(text)
        return float(text)
    except (ValueError, TypeError):
        return value


def _build_xlsx(
    headers: list[str],
    rows: list[list[str]],
    *,
    sheet_name: str = "Report",
    numeric_from: int | None = None,
) -> bytes:
    """Build an .xlsx workbook (bytes) from headers and rows.

    ``numeric_from`` is the column index at/after which string cells are
    coerced to numbers (e.g. amount columns); ``None`` keeps every cell as-is.
    Shared by report exporters so Excel output stays consistent.
    """
    import io

    from openpyxl import Workbook
    from openpyxl.styles import Font

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = sheet_name[:31] or "Report"

    worksheet.append(headers)
    for cell in worksheet[1]:
        cell.font = Font(bold=True)

    for row in rows:
        out_row = [
            _coerce_xlsx_cell(v)
            if numeric_from is not None and i >= numeric_from
            else v
            for i, v in enumerate(row)
        ]
        worksheet.append(out_row)

    # Reasonable column widths from header/content length.
    for idx, header in enumerate(headers, start=1):
        longest = max(
            [len(str(header))]
            + [len(str(r[idx - 1])) for r in rows if idx - 1 < len(r)]
            or [len(str(header))]
        )
        worksheet.column_dimensions[
            worksheet.cell(row=1, column=idx).column_letter
        ].width = min(max(longest + 2, 10), 48)

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _ifrs_label(category: IFRSCategory | str | None) -> str:
    label_map: dict[IFRSCategory, str] = {
        IFRSCategory.ASSETS: "Assets",
        IFRSCategory.LIABILITIES: "Liabilities",
        IFRSCategory.EQUITY: "Equity",
        IFRSCategory.REVENUE: "Revenue",
        IFRSCategory.EXPENSES: "Expenses",
        IFRSCategory.OTHER_COMPREHENSIVE_INCOME: "Other Comprehensive Income",
    }
    if category is None:
        return ""
    if isinstance(category, str) and not isinstance(category, IFRSCategory):
        try:
            category = IFRSCategory(category)
        except ValueError:
            return category.replace("_", " ").title()
    if isinstance(category, IFRSCategory):
        if category in label_map:
            return label_map[category]
        return str(category.value)
    return category


def _report_type_label(report_type: ReportType) -> str:
    labels: dict[ReportType, str] = {
        ReportType.BALANCE_SHEET: "Statement of Financial Position",
        ReportType.INCOME_STATEMENT: "Statement of Profit or Loss",
        ReportType.CASH_FLOW: "Cash Flow Statement",
        ReportType.CHANGES_IN_EQUITY: "Changes in Equity",
        ReportType.TRIAL_BALANCE: "Trial Balance",
        ReportType.GENERAL_LEDGER: "General Ledger",
        ReportType.SUBLEDGER: "Subledger",
        ReportType.AGING: "Aging Report",
        ReportType.BUDGET_VS_ACTUAL: "Budget vs Actual",
        ReportType.TAX: "Tax Report",
        ReportType.REGULATORY: "Regulatory Report",
        ReportType.CUSTOM: "Custom Report",
    }
    if report_type in labels:
        return labels[report_type]
    return str(report_type.value)


def _amount_from_category(
    ifrs_category: IFRSCategory,
    debit: Decimal,
    credit: Decimal,
) -> Decimal:
    if ifrs_category in {IFRSCategory.ASSETS, IFRSCategory.EXPENSES}:
        return debit - credit
    return credit - debit


def _apply_cash_basis_filter_pll(
    stmt: Any,
    db: Session,
    organization_id: Any,
) -> Any:
    """Restrict a PostedLedgerLine-joined SELECT to cash-movement lines only.

    Mirrors ``_apply_cash_basis_filter`` but operates on the denormalized
    ``posted_ledger_line`` table used by the dashboard KPIs. Includes
    lines where:
    1. ``source_document_type`` is an explicit payment type, OR
    2. The line's journal touches a cash/bank account (catches manual
       journals with NULL source_document_type).
    """
    # Local imports to avoid module-load circularity.
    from sqlalchemy.orm import aliased

    from app.models.finance.gl.posted_ledger_line import PostedLedgerLine

    cash_category_ids = list(
        db.scalars(
            select(AccountCategory.category_id).where(
                AccountCategory.organization_id == organization_id,
                AccountCategory.category_code.in_({"CASH", "BANK"}),
            )
        ).all()
    )

    cash_touch_conditions = [Account.is_cash_equivalent.is_(True)]
    if cash_category_ids:
        cash_touch_conditions.append(Account.category_id.in_(cash_category_ids))

    # Aliased inner PostedLedgerLine so we can correlate against the
    # outer query's PostedLedgerLine row.
    pll_inner = aliased(PostedLedgerLine)
    cash_touch = exists(
        select(pll_inner.ledger_line_id)
        .join(Account, Account.account_id == pll_inner.account_id)
        .where(
            pll_inner.journal_entry_id == PostedLedgerLine.journal_entry_id,
            or_(*cash_touch_conditions),
        )
    )

    return stmt.where(
        or_(
            PostedLedgerLine.source_document_type.in_(CASH_BASIS_DOC_TYPES),
            cash_touch,
        )
    )


def _apply_cash_basis_filter(
    stmt: Any,
    db: Session,
    organization_id: Any,
) -> Any:
    """Restrict a JournalEntry-joined SELECT to cash-movement entries only.

    Includes entries where:
    1. source_document_type is an explicit payment type, OR
    2. The journal touches a cash/bank account (handles manual journals
       with NULL source_document_type).
    """
    cash_category_ids = list(
        db.scalars(
            select(AccountCategory.category_id).where(
                AccountCategory.organization_id == organization_id,
                AccountCategory.category_code.in_({"CASH", "BANK"}),
            )
        ).all()
    )

    # Correlated EXISTS: at least one line touches a cash/bank account
    cash_touch_conditions = [Account.is_cash_equivalent.is_(True)]
    if cash_category_ids:
        cash_touch_conditions.append(Account.category_id.in_(cash_category_ids))

    cash_touch = exists(
        select(JournalEntryLine.line_id)
        .join(Account, Account.account_id == JournalEntryLine.account_id)
        .where(
            JournalEntryLine.journal_entry_id == JournalEntry.journal_entry_id,
            or_(*cash_touch_conditions),
        )
    )

    return stmt.where(
        or_(
            JournalEntry.source_document_type.in_(CASH_BASIS_DOC_TYPES),
            cash_touch,
        )
    )


def _category_balances(
    db: Session,
    organization_id: str,
    start_date: date | None = None,
    end_date: date | None = None,
    as_of_date: date | None = None,
    basis: str = "accrual",
) -> dict[str, dict[str, Any]]:
    org_id = coerce_uuid(organization_id)

    stmt = (
        select(
            AccountCategory.category_code,
            AccountCategory.ifrs_category,
            func.coalesce(func.sum(JournalEntryLine.debit_amount_functional), 0).label(
                "debit"
            ),
            func.coalesce(func.sum(JournalEntryLine.credit_amount_functional), 0).label(
                "credit"
            ),
        )
        .join(Account, Account.category_id == AccountCategory.category_id)
        .join(JournalEntryLine, JournalEntryLine.account_id == Account.account_id)
        .join(
            JournalEntry,
            JournalEntry.journal_entry_id == JournalEntryLine.journal_entry_id,
        )
        .where(
            JournalEntry.organization_id == org_id,
            JournalEntry.status == JournalStatus.POSTED,
        )
    )

    if as_of_date:
        stmt = stmt.where(JournalEntry.posting_date <= as_of_date)
    else:
        if start_date:
            stmt = stmt.where(JournalEntry.posting_date >= start_date)
        if end_date:
            stmt = stmt.where(JournalEntry.posting_date <= end_date)

    if basis == "cash":
        stmt = _apply_cash_basis_filter(stmt, db, org_id)

    rows = db.execute(
        stmt.group_by(
            AccountCategory.category_code,
            AccountCategory.ifrs_category,
        )
    ).all()

    balances: dict[str, dict[str, Any]] = {}
    for code, ifrs_category, debit, credit in rows:
        debit = Decimal(str(debit or 0))
        credit = Decimal(str(credit or 0))
        balances[code] = {
            "ifrs_category": ifrs_category,
            "amount": _amount_from_category(ifrs_category, debit, credit),
        }

    return balances


def _tax_totals_from_gl(
    db: Session,
    organization_id: str,
    start_date: date,
    end_date: date,
) -> dict[str, Decimal]:
    """Aggregate tax totals from GL by querying the TAX-L category.

    Groups accounts by name pattern:
    - VAT/Output tax → output_tax (liability, credit-normal)
    - WHT → withholding (liability, credit-normal)
    - Everything else under TAX-L → input_tax proxy
    """
    org_id = coerce_uuid(organization_id)

    rows = db.execute(
        select(
            Account.account_code,
            Account.account_name,
            func.coalesce(func.sum(JournalEntryLine.debit_amount_functional), 0).label(
                "debit"
            ),
            func.coalesce(func.sum(JournalEntryLine.credit_amount_functional), 0).label(
                "credit"
            ),
        )
        .join(AccountCategory, Account.category_id == AccountCategory.category_id)
        .join(JournalEntryLine, JournalEntryLine.account_id == Account.account_id)
        .join(
            JournalEntry,
            JournalEntry.journal_entry_id == JournalEntryLine.journal_entry_id,
        )
        .where(
            JournalEntry.organization_id == org_id,
            JournalEntry.status == JournalStatus.POSTED,
            JournalEntry.posting_date >= start_date,
            JournalEntry.posting_date <= end_date,
            AccountCategory.category_code == "TAX-L",
        )
        .group_by(Account.account_code, Account.account_name)
    ).all()

    output_tax = Decimal("0")
    input_tax = Decimal("0")
    withholding = Decimal("0")

    for _code, name, debit, credit in rows:
        debit = Decimal(str(debit or 0))
        credit = Decimal(str(credit or 0))
        balance = credit - debit  # Liability accounts are credit-normal

        name_lower = (name or "").lower()
        if "vat" in name_lower or "output" in name_lower:
            output_tax += balance
        elif "wht" in name_lower or "withholding" in name_lower:
            withholding += balance
        else:
            # Other tax liabilities (income tax, education tax, etc.)
            input_tax += balance

    net_tax = output_tax - input_tax - withholding

    return {
        "output_tax": output_tax,
        "input_tax": input_tax,
        "withholding": withholding,
        "net_tax": net_tax,
    }


# ---------------------------------------------------------------------------
# Cash-basis derivation helpers
# ---------------------------------------------------------------------------
#
# Nigerian VAT (and most pay-when-paid tax regimes) is computed on cash
# actually received / paid, not on invoiced amounts. The accrual-basis
# tax_transaction subledger records VAT at invoice time, so it cannot
# answer "how much VAT did I owe on cash actually moved this period".
#
# The helpers below derive cash-basis totals by walking AR/AP payment
# allocations and prorating each allocation against the underlying
# invoice's VAT/subtotal/total ratio. They never write to tax_transaction
# — they are pure read-side reporting.
#
# Status filters: only allocations whose parent payment is in a
# settled-or-pending state are counted (VOID / BOUNCED / REVERSED /
# REJECTED are excluded). Invoice-side VOID and DRAFT are excluded too.
#
# Credit notes (invoice_type=CREDIT_NOTE) flow through the same query
# and contribute as positive output VAT against negative-direction
# allocations — typical accounting outcome of "credit applied to
# subsequent receipt", which nets correctly.


def _ar_cash_allocations_subquery(
    organization_id: Any,
    start_date: date,
    end_date: date,
) -> Any:
    """SELECT allocation joined to invoice + payment for AR cash basis.

    Yields (allocation_date, invoice_type, subtotal, tax_amount, total_amount,
    allocated_amount). Filters by parent payment.payment_date in window and
    by non-VOID payment + invoice statuses.
    """
    from app.models.finance.ar.customer_payment import (
        CustomerPayment,
        PaymentStatus,
    )
    from app.models.finance.ar.invoice import Invoice, InvoiceStatus
    from app.models.finance.ar.payment_allocation import PaymentAllocation

    org_id = coerce_uuid(organization_id)
    excluded_payment = {
        PaymentStatus.VOID,
        PaymentStatus.BOUNCED,
        PaymentStatus.REVERSED,
    }
    excluded_invoice = {InvoiceStatus.VOID, InvoiceStatus.DRAFT}

    return (
        select(
            PaymentAllocation.allocation_date.label("allocation_date"),
            CustomerPayment.customer_id.label("customer_id"),
            Invoice.invoice_id.label("invoice_id"),
            Invoice.invoice_type.label("invoice_type"),
            Invoice.subtotal.label("invoice_subtotal"),
            Invoice.tax_amount.label("invoice_tax"),
            Invoice.total_amount.label("invoice_total"),
            PaymentAllocation.allocated_amount.label("allocated_amount"),
        )
        .join(
            CustomerPayment,
            CustomerPayment.payment_id == PaymentAllocation.payment_id,
        )
        .join(Invoice, Invoice.invoice_id == PaymentAllocation.invoice_id)
        .where(
            CustomerPayment.organization_id == org_id,
            CustomerPayment.payment_date >= start_date,
            CustomerPayment.payment_date <= end_date,
            CustomerPayment.status.notin_(excluded_payment),
            Invoice.status.notin_(excluded_invoice),
        )
    )


def _ap_cash_allocations_subquery(
    organization_id: Any,
    start_date: date,
    end_date: date,
) -> Any:
    """SELECT allocation joined to supplier_invoice + payment for AP cash basis."""
    from app.models.finance.ap.ap_payment_allocation import APPaymentAllocation
    from app.models.finance.ap.supplier_invoice import (
        SupplierInvoice,
        SupplierInvoiceStatus,
    )
    from app.models.finance.ap.supplier_payment import APPaymentStatus, SupplierPayment

    org_id = coerce_uuid(organization_id)
    excluded_payment = {
        APPaymentStatus.VOID,
        APPaymentStatus.REJECTED,
        APPaymentStatus.DRAFT,
    }
    excluded_invoice = {SupplierInvoiceStatus.VOID, SupplierInvoiceStatus.DRAFT}

    return (
        select(
            APPaymentAllocation.allocation_date.label("allocation_date"),
            SupplierPayment.supplier_id.label("supplier_id"),
            SupplierInvoice.invoice_id.label("invoice_id"),
            SupplierInvoice.subtotal.label("invoice_subtotal"),
            SupplierInvoice.tax_amount.label("invoice_tax"),
            SupplierInvoice.total_amount.label("invoice_total"),
            APPaymentAllocation.allocated_amount.label("allocated_amount"),
        )
        .join(
            SupplierPayment,
            SupplierPayment.payment_id == APPaymentAllocation.payment_id,
        )
        .join(
            SupplierInvoice,
            SupplierInvoice.invoice_id == APPaymentAllocation.invoice_id,
        )
        .where(
            SupplierPayment.organization_id == org_id,
            SupplierPayment.payment_date >= start_date,
            SupplierPayment.payment_date <= end_date,
            SupplierPayment.status.notin_(excluded_payment),
            SupplierInvoice.status.notin_(excluded_invoice),
        )
    )


def _prorate(
    allocated: Decimal | None,
    component: Decimal | None,
    total: Decimal | None,
) -> Decimal:
    """Return ``allocated * component / total``, safely handling zero/None."""
    if not allocated or not component or not total:
        return Decimal("0")
    if total == 0:
        return Decimal("0")
    return (Decimal(allocated) * Decimal(component)) / Decimal(total)


def _cash_basis_vat_totals(
    db: Session,
    organization_id: Any,
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    """Compute VAT totals on cash basis.

    For each AR receipt and AP payment in [start_date, end_date], prorate
    the allocated amount by the underlying invoice's VAT/subtotal/total
    ratio. Output VAT is recognized only on cash collected; input VAT
    only on cash paid.

    Returns a dict shaped to feed the FIRS Form 002 fields:

        output_vat: VAT collected on receipts
        output_base: net subtotal of taxable receipts (VAT > 0 invoices)
        output_zero_rated: subtotal of zero-rated receipts (VAT == 0)
        output_credit_notes: subtotal of credit-note allocations (subtracts)
        output_credit_notes_vat: VAT reversed via credit-note allocations
        input_vat: VAT recoverable on supplier payments
        input_base: net subtotal of taxable supplier payments
        rate_breakdown: list[{rate, transaction_type, base, tax}]
    """
    # Local imports — see _ar_cash_allocations_subquery for rationale.
    from app.models.finance.ar.invoice import InvoiceType

    out_stmt = _ar_cash_allocations_subquery(organization_id, start_date, end_date)
    in_stmt = _ap_cash_allocations_subquery(organization_id, start_date, end_date)

    output_vat = Decimal("0")
    output_base = Decimal("0")
    output_zero_rated = Decimal("0")
    output_cn_vat = Decimal("0")
    output_cn_base = Decimal("0")

    # Bucket per effective rate (rounded to 4dp, e.g. 0.075 for 7.5% VAT)
    rate_buckets: dict[tuple[str, str], dict[str, Decimal]] = {}

    def _bucket(direction: str, rate: Decimal, base: Decimal, vat: Decimal) -> None:
        key = (direction, f"{rate:.4f}")
        b = rate_buckets.setdefault(key, {"base": Decimal("0"), "tax": Decimal("0")})
        b["base"] += base
        b["tax"] += vat

    for row in db.execute(out_stmt).all():
        allocated = Decimal(row.allocated_amount or 0)
        inv_total = Decimal(row.invoice_total or 0)
        inv_tax = Decimal(row.invoice_tax or 0)
        inv_sub = Decimal(row.invoice_subtotal or 0)
        prorated_vat = _prorate(allocated, inv_tax, inv_total)
        prorated_base = _prorate(allocated, inv_sub, inv_total)
        # Effective rate = tax / subtotal on the originating invoice.
        rate = (inv_tax / inv_sub) if inv_sub > 0 else Decimal("0")

        if row.invoice_type == InvoiceType.CREDIT_NOTE:
            # Credit notes reverse output VAT and subtotal.
            output_cn_vat += prorated_vat
            output_cn_base += prorated_base
            _bucket("OUTPUT_CN", rate, prorated_base, prorated_vat)
            continue

        if inv_tax > 0:
            output_vat += prorated_vat
            output_base += prorated_base
            _bucket("OUTPUT", rate, prorated_base, prorated_vat)
        else:
            output_zero_rated += prorated_base
            _bucket("OUTPUT_ZERO", Decimal("0"), prorated_base, Decimal("0"))

    input_vat = Decimal("0")
    input_base = Decimal("0")

    for row in db.execute(in_stmt).all():
        allocated = Decimal(row.allocated_amount or 0)
        inv_total = Decimal(row.invoice_total or 0)
        inv_tax = Decimal(row.invoice_tax or 0)
        inv_sub = Decimal(row.invoice_subtotal or 0)
        prorated_vat = _prorate(allocated, inv_tax, inv_total)
        prorated_base = _prorate(allocated, inv_sub, inv_total)
        rate = (inv_tax / inv_sub) if inv_sub > 0 else Decimal("0")

        if inv_tax > 0:
            input_vat += prorated_vat
            input_base += prorated_base
            _bucket("INPUT", rate, prorated_base, prorated_vat)

    rate_breakdown = [
        {
            "rate": float(rate_str),
            "transaction_type": direction,
            "base_amount": float(b["base"]),
            "tax_amount": float(b["tax"]),
        }
        for (direction, rate_str), b in sorted(rate_buckets.items())
    ]

    return {
        "output_vat": output_vat,
        "output_base": output_base,
        "output_zero_rated": output_zero_rated,
        "output_credit_notes_vat": output_cn_vat,
        "output_credit_notes_base": output_cn_base,
        "net_output_vat": output_vat - output_cn_vat,
        "net_output_base": output_base - output_cn_base,
        "input_vat": input_vat,
        "input_base": input_base,
        "net_vat_payable": (output_vat - output_cn_vat) - input_vat,
        "rate_breakdown": rate_breakdown,
    }


def _cash_basis_wht_totals(
    db: Session,
    organization_id: Any,
    start_date: date,
    end_date: date,
) -> dict[str, Decimal]:
    """Compute WHT totals on cash basis.

    AR side (deducted by customers): sum of customer_payment.wht_amount
    for receipts dated in window.

    AP side (withheld from suppliers): sum of supplier_payment.withholding_tax_amount
    for payments dated in window.
    """
    from app.models.finance.ap.supplier_payment import APPaymentStatus, SupplierPayment
    from app.models.finance.ar.customer_payment import (
        CustomerPayment,
        PaymentStatus,
    )

    org_id = coerce_uuid(organization_id)

    ar_stmt = select(func.coalesce(func.sum(CustomerPayment.wht_amount), 0)).where(
        CustomerPayment.organization_id == org_id,
        CustomerPayment.payment_date >= start_date,
        CustomerPayment.payment_date <= end_date,
        CustomerPayment.status.notin_(
            {PaymentStatus.VOID, PaymentStatus.BOUNCED, PaymentStatus.REVERSED}
        ),
    )
    ap_stmt = select(
        func.coalesce(func.sum(SupplierPayment.withholding_tax_amount), 0)
    ).where(
        SupplierPayment.organization_id == org_id,
        SupplierPayment.payment_date >= start_date,
        SupplierPayment.payment_date <= end_date,
        SupplierPayment.status.notin_(
            {APPaymentStatus.VOID, APPaymentStatus.REJECTED, APPaymentStatus.DRAFT}
        ),
    )

    wht_deducted = Decimal(db.scalar(ar_stmt) or 0)  # AR: customers deducted from us
    wht_withheld = Decimal(db.scalar(ap_stmt) or 0)  # AP: we withheld from suppliers

    return {
        "wht_deducted_by_customers": wht_deducted,
        "wht_withheld_from_suppliers": wht_withheld,
        "net_wht_payable": wht_withheld - wht_deducted,
    }


def _cash_basis_revenue_totals(
    db: Session,
    organization_id: Any,
    start_date: date,
    end_date: date,
) -> dict[str, Decimal]:
    """Compute total revenue (subtotal, net of VAT) on cash basis.

    Sums prorated invoice subtotal across all AR receipts in the window.
    Credit-note allocations subtract.
    """
    from app.models.finance.ar.invoice import InvoiceType

    stmt = _ar_cash_allocations_subquery(organization_id, start_date, end_date)

    gross = Decimal("0")
    net_subtotal = Decimal("0")
    cn_subtotal = Decimal("0")

    for row in db.execute(stmt).all():
        allocated = Decimal(row.allocated_amount or 0)
        inv_total = Decimal(row.invoice_total or 0)
        inv_sub = Decimal(row.invoice_subtotal or 0)
        prorated_sub = _prorate(allocated, inv_sub, inv_total)

        gross += allocated
        if row.invoice_type == InvoiceType.CREDIT_NOTE:
            cn_subtotal += prorated_sub
        else:
            net_subtotal += prorated_sub

    return {
        "gross_collected": gross,
        "subtotal_collected": net_subtotal,
        "credit_notes_subtotal": cn_subtotal,
        "net_revenue": net_subtotal - cn_subtotal,
    }


def _cash_basis_revenue_by_customer(
    db: Session,
    organization_id: Any,
    start_date: date,
    end_date: date,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Top customers by cash-basis revenue (subtotal of receipts).

    Returns a list of dicts ordered by net_revenue desc.
    """
    from app.models.finance.ar.customer import Customer
    from app.models.finance.ar.invoice import InvoiceType

    stmt = _ar_cash_allocations_subquery(organization_id, start_date, end_date)

    per_customer: dict[Any, dict[str, Any]] = {}
    for row in db.execute(stmt).all():
        allocated = Decimal(row.allocated_amount or 0)
        inv_total = Decimal(row.invoice_total or 0)
        inv_sub = Decimal(row.invoice_subtotal or 0)
        inv_tax = Decimal(row.invoice_tax or 0)
        prorated_sub = _prorate(allocated, inv_sub, inv_total)
        prorated_vat = _prorate(allocated, inv_tax, inv_total)

        is_cn = row.invoice_type == InvoiceType.CREDIT_NOTE
        bucket = per_customer.setdefault(
            row.customer_id,
            {
                "customer_id": row.customer_id,
                "gross_collected": Decimal("0"),
                "subtotal_collected": Decimal("0"),
                "credit_notes_subtotal": Decimal("0"),
                "vat_collected": Decimal("0"),
                "credit_notes_vat": Decimal("0"),
            },
        )
        bucket["gross_collected"] += allocated
        if is_cn:
            bucket["credit_notes_subtotal"] += prorated_sub
            bucket["credit_notes_vat"] += prorated_vat
        else:
            bucket["subtotal_collected"] += prorated_sub
            bucket["vat_collected"] += prorated_vat

    if not per_customer:
        return []

    customer_ids = list(per_customer.keys())
    name_rows = db.execute(
        select(Customer.customer_id, Customer.legal_name).where(
            Customer.customer_id.in_(customer_ids)
        )
    ).all()
    name_map = {cid: name for cid, name in name_rows}

    results = []
    for cid, b in per_customer.items():
        net_sub = b["subtotal_collected"] - b["credit_notes_subtotal"]
        net_vat = b["vat_collected"] - b["credit_notes_vat"]
        results.append(
            {
                "customer_id": cid,
                "customer_name": name_map.get(cid, ""),
                "gross_collected": b["gross_collected"],
                "subtotal_collected": b["subtotal_collected"],
                "credit_notes_subtotal": b["credit_notes_subtotal"],
                "net_revenue": net_sub,
                "vat_collected": b["vat_collected"],
                "credit_notes_vat": b["credit_notes_vat"],
                "net_vat": net_vat,
            }
        )

    results.sort(key=lambda r: r["net_revenue"], reverse=True)
    return results[:limit]
