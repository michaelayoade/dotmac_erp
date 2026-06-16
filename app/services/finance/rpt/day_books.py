"""Books of prime entry (day books) — context builders and CSV export.

Day books present transactions in their book of original entry, in date
order, with period subtotals — the auditor's chronological view that sits
underneath the ledger. This module hosts each day book as it is added:

* Sales Day Book — AR customer invoices (credit sales).

VAT note: this organisation accounts for VAT on a **cash basis** (output VAT
on AR receipts, input VAT on AP payments — see ``feedback_vat_cash_basis``).
Day books are *invoice-dated*, so the VAT column here is a memo of the tax
charged on the invoice and will NOT equal the VAT return for the period.
Treat it as analytical, not as the return basis.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from app.models.finance.ap.supplier import Supplier
from app.models.finance.ap.supplier_invoice import (
    SupplierInvoice,
    SupplierInvoiceStatus,
    SupplierInvoiceType,
)
from app.models.finance.ar.invoice import Invoice, InvoiceStatus, InvoiceType
from app.models.finance.gl.account import Account
from app.models.finance.gl.account_category import AccountCategory
from app.models.finance.gl.journal_entry import JournalEntry, JournalStatus
from app.models.finance.gl.journal_entry_line import JournalEntryLine
from app.services.common import coerce_uuid
from app.services.finance.common.source_types import INVOICE_JOURNAL_SOURCE_TYPES
from app.services.finance.ap.invoice_query import (
    build_invoice_query as build_supplier_invoice_query,
)
from app.services.finance.ar.invoice_query import build_invoice_query
from app.services.finance.gl.journal_query import build_journal_query
from app.services.finance.rpt.common import (
    _build_csv,
    _build_xlsx,
    _format_currency,
    _format_date,
    _iso_date,
    _parse_date,
)

# Column headers shared by the Sales Day Book CSV and Excel exports.
_SALES_EXPORT_HEADERS = [
    "Date",
    "Invoice No",
    "Customer",
    "Reference",
    "Currency",
    "Status",
    "Net",
    "VAT",
    "Gross",
]
# Index of the first numeric column (Net) — used for Excel cell typing.
_SALES_NUMERIC_FROM = 6

# Statuses excluded from a day book when no explicit status filter is given:
# DRAFT has not been posted to the ledger; VOID has been reversed out.
_SALES_EXCLUDED_STATUSES = (InvoiceStatus.DRAFT, InvoiceStatus.VOID)


def _customer_name(invoice: Invoice) -> str:
    customer = invoice.customer
    if customer is None:
        return ""
    return customer.trading_name or customer.legal_name or ""


def sales_day_book_context(
    db: Any,
    organization_id: str,
    start_date: str | None = None,
    end_date: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """Build context for the Sales Day Book (AR invoices, chronological)."""
    org_id = coerce_uuid(organization_id)

    today = date.today()
    from_date = _parse_date(start_date) or today.replace(day=1)
    to_date = _parse_date(end_date) or today

    query = build_invoice_query(
        db=db,
        organization_id=str(org_id),
        status=status or None,
        start_date=_iso_date(from_date),
        end_date=_iso_date(to_date),
    )
    # A sales day book records sales invoices only — credit notes belong in
    # the Sales Returns Day Book, and proformas are not real sales.
    query = query.where(Invoice.invoice_type == InvoiceType.STANDARD)
    if not status:
        query = query.where(Invoice.status.notin_(_SALES_EXCLUDED_STATUSES))
    query = query.order_by(Invoice.invoice_date, Invoice.invoice_number)

    invoices = list(db.scalars(query).all())

    rows: list[dict[str, Any]] = []
    total_net = Decimal("0")
    total_vat = Decimal("0")
    total_gross = Decimal("0")
    for inv in invoices:
        net = inv.subtotal or Decimal("0")
        vat = inv.tax_amount or Decimal("0")
        gross = inv.total_amount or Decimal("0")
        total_net += net
        total_vat += vat
        total_gross += gross
        rows.append(
            {
                "invoice_date": _format_date(inv.invoice_date),
                "invoice_date_iso": _iso_date(inv.invoice_date),
                "invoice_number": inv.invoice_number,
                "customer_name": _customer_name(inv),
                "reference": inv.purpose or "",
                "currency_code": inv.currency_code,
                "status": inv.status.value if inv.status else "",
                "net": _format_currency(net),
                "net_raw": float(net),
                "vat": _format_currency(vat),
                "vat_raw": float(vat),
                "gross": _format_currency(gross),
                "gross_raw": float(gross),
            }
        )

    return {
        "rows": rows,
        "row_count": len(rows),
        "start_date": _format_date(from_date),
        "start_date_iso": _iso_date(from_date),
        "end_date": _format_date(to_date),
        "end_date_iso": _iso_date(to_date),
        "status": status or "",
        "total_net": _format_currency(total_net),
        "total_net_raw": float(total_net),
        "total_vat": _format_currency(total_vat),
        "total_vat_raw": float(total_vat),
        "total_gross": _format_currency(total_gross),
        "total_gross_raw": float(total_gross),
        # VAT here is invoice-dated; the org accounts for VAT on a cash basis,
        # so this is a memo figure, not the VAT return basis for the period.
        "vat_basis_note": (
            "VAT shown is the tax charged on invoices in this period (memo). "
            "This organisation accounts for VAT on a cash basis, so it will "
            "not equal the VAT return for the period."
        ),
        # Document-grain basis: these rows summarise the AR sub-ledger by
        # invoice date, whereas the general ledger posts by posting date. The
        # two won't tie line-for-line — period totals can differ at period
        # boundaries, and the ledger splits each invoice across control,
        # revenue and (deferred) VAT accounts. Use the GL trial balance /
        # control-account reconciliation for the ledger view.
        "gl_recon_note": (
            "Document basis: rows come from the AR sub-ledger by invoice date "
            "and summarise each invoice as issued. The general ledger posts by "
            "posting date and splits each invoice across receivables, revenue "
            "and VAT accounts, so these totals will not tie line-for-line to "
            "GL postings — reconcile via the trial balance / AR control account."
        ),
    }


def _sales_day_book_export_rows(ctx: dict[str, Any]) -> list[list[str]]:
    """Flatten the Sales Day Book context into export rows + a totals line."""
    rows: list[list[str]] = [
        [
            r["invoice_date_iso"],
            r["invoice_number"],
            r["customer_name"],
            r["reference"],
            r["currency_code"],
            r["status"],
            str(r["net_raw"]),
            str(r["vat_raw"]),
            str(r["gross_raw"]),
        ]
        for r in ctx["rows"]
    ]
    rows.append(
        [
            "",
            "",
            "",
            "",
            "",
            "TOTAL",
            str(ctx["total_net_raw"]),
            str(ctx["total_vat_raw"]),
            str(ctx["total_gross_raw"]),
        ]
    )
    return rows


def export_sales_day_book_csv(
    organization_id: str,
    db: Any,
    start_date: str | None = None,
    end_date: str | None = None,
    status: str | None = None,
) -> str:
    """Export the Sales Day Book as CSV (flat rows + totals line)."""
    ctx = sales_day_book_context(
        db, organization_id, start_date, end_date, status=status
    )
    return _build_csv(_SALES_EXPORT_HEADERS, _sales_day_book_export_rows(ctx))


def export_sales_day_book_xlsx(
    organization_id: str,
    db: Any,
    start_date: str | None = None,
    end_date: str | None = None,
    status: str | None = None,
) -> bytes:
    """Export the Sales Day Book as an Excel workbook."""
    ctx = sales_day_book_context(
        db, organization_id, start_date, end_date, status=status
    )
    return _build_xlsx(
        _SALES_EXPORT_HEADERS,
        _sales_day_book_export_rows(ctx),
        sheet_name="Sales Day Book",
        numeric_from=_SALES_NUMERIC_FROM,
    )


# ───────────────────────── Purchases Day Book ─────────────────────────

_PURCHASES_EXCLUDED_STATUSES = (
    SupplierInvoiceStatus.DRAFT,
    SupplierInvoiceStatus.VOID,
)

_PURCHASES_EXPORT_HEADERS = [
    "Date",
    "Invoice No",
    "Supplier Inv #",
    "Supplier",
    "Reference",
    "Currency",
    "Status",
    "Net",
    "VAT",
    "WHT",
    "Gross",
]
# Index of the first numeric column (Net).
_PURCHASES_NUMERIC_FROM = 7


def _supplier_name_map(db: Any, org_id: Any, supplier_ids: set[Any]) -> dict[str, str]:
    """Batch-resolve supplier_id -> display name (no relationship on the model)."""
    if not supplier_ids:
        return {}
    from sqlalchemy import select

    rows = db.execute(
        select(
            Supplier.supplier_id,
            Supplier.trading_name,
            Supplier.legal_name,
        ).where(
            Supplier.organization_id == org_id,
            Supplier.supplier_id.in_(supplier_ids),
        )
    ).all()
    return {str(r[0]): (r[1] or r[2] or "") for r in rows}


def purchases_day_book_context(
    db: Any,
    organization_id: str,
    start_date: str | None = None,
    end_date: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """Build context for the Purchases Day Book (AP invoices, chronological)."""
    org_id = coerce_uuid(organization_id)

    today = date.today()
    from_date = _parse_date(start_date) or today.replace(day=1)
    to_date = _parse_date(end_date) or today

    query = build_supplier_invoice_query(
        db=db,
        organization_id=str(org_id),
        status=status or None,
        start_date=_iso_date(from_date),
        end_date=_iso_date(to_date),
    )
    # A purchases day book records purchase invoices only — supplier credit
    # notes belong in the Purchases Returns Day Book, and debit notes are extra
    # charges, not credit purchases.
    query = query.where(SupplierInvoice.invoice_type == SupplierInvoiceType.STANDARD)
    if not status:
        query = query.where(SupplierInvoice.status.notin_(_PURCHASES_EXCLUDED_STATUSES))
    query = query.order_by(SupplierInvoice.invoice_date, SupplierInvoice.invoice_number)

    invoices = list(db.scalars(query).all())
    names = _supplier_name_map(db, org_id, {inv.supplier_id for inv in invoices})

    rows: list[dict[str, Any]] = []
    total_net = Decimal("0")
    total_vat = Decimal("0")
    total_wht = Decimal("0")
    total_gross = Decimal("0")
    for inv in invoices:
        net = inv.subtotal or Decimal("0")
        vat = inv.tax_amount or Decimal("0")
        wht = inv.withholding_tax_amount or Decimal("0")
        gross = inv.total_amount or Decimal("0")
        total_net += net
        total_vat += vat
        total_wht += wht
        total_gross += gross
        rows.append(
            {
                "invoice_date": _format_date(inv.invoice_date),
                "invoice_date_iso": _iso_date(inv.invoice_date),
                "invoice_number": inv.invoice_number,
                "supplier_invoice_number": inv.supplier_invoice_number or "",
                "supplier_name": names.get(str(inv.supplier_id), ""),
                "reference": inv.purpose or "",
                "currency_code": inv.currency_code,
                "status": inv.status.value if inv.status else "",
                "net": _format_currency(net),
                "net_raw": float(net),
                "vat": _format_currency(vat),
                "vat_raw": float(vat),
                "wht": _format_currency(wht),
                "wht_raw": float(wht),
                "gross": _format_currency(gross),
                "gross_raw": float(gross),
            }
        )

    return {
        "rows": rows,
        "row_count": len(rows),
        "start_date": _format_date(from_date),
        "start_date_iso": _iso_date(from_date),
        "end_date": _format_date(to_date),
        "end_date_iso": _iso_date(to_date),
        "status": status or "",
        "total_net": _format_currency(total_net),
        "total_net_raw": float(total_net),
        "total_vat": _format_currency(total_vat),
        "total_vat_raw": float(total_vat),
        "total_wht": _format_currency(total_wht),
        "total_wht_raw": float(total_wht),
        "total_gross": _format_currency(total_gross),
        "total_gross_raw": float(total_gross),
        "vat_basis_note": (
            "VAT shown is the input tax on invoices in this period (memo). "
            "This organisation accounts for VAT on a cash basis, so it will "
            "not equal the VAT return for the period."
        ),
        "gl_recon_note": (
            "Document basis: rows come from the AP sub-ledger by invoice date "
            "and summarise each supplier invoice as recorded. The general "
            "ledger posts by posting date and splits each invoice across "
            "payables, expense/asset, VAT and WHT accounts, so these totals "
            "will not tie line-for-line to GL postings — reconcile via the "
            "trial balance / AP control account."
        ),
    }


def _purchases_day_book_export_rows(ctx: dict[str, Any]) -> list[list[str]]:
    """Flatten the Purchases Day Book context into export rows + totals line."""
    rows: list[list[str]] = [
        [
            r["invoice_date_iso"],
            r["invoice_number"],
            r["supplier_invoice_number"],
            r["supplier_name"],
            r["reference"],
            r["currency_code"],
            r["status"],
            str(r["net_raw"]),
            str(r["vat_raw"]),
            str(r["wht_raw"]),
            str(r["gross_raw"]),
        ]
        for r in ctx["rows"]
    ]
    rows.append(
        [
            "",
            "",
            "",
            "",
            "",
            "",
            "TOTAL",
            str(ctx["total_net_raw"]),
            str(ctx["total_vat_raw"]),
            str(ctx["total_wht_raw"]),
            str(ctx["total_gross_raw"]),
        ]
    )
    return rows


def export_purchases_day_book_csv(
    organization_id: str,
    db: Any,
    start_date: str | None = None,
    end_date: str | None = None,
    status: str | None = None,
) -> str:
    """Export the Purchases Day Book as CSV."""
    ctx = purchases_day_book_context(
        db, organization_id, start_date, end_date, status=status
    )
    return _build_csv(_PURCHASES_EXPORT_HEADERS, _purchases_day_book_export_rows(ctx))


def export_purchases_day_book_xlsx(
    organization_id: str,
    db: Any,
    start_date: str | None = None,
    end_date: str | None = None,
    status: str | None = None,
) -> bytes:
    """Export the Purchases Day Book as an Excel workbook."""
    ctx = purchases_day_book_context(
        db, organization_id, start_date, end_date, status=status
    )
    return _build_xlsx(
        _PURCHASES_EXPORT_HEADERS,
        _purchases_day_book_export_rows(ctx),
        sheet_name="Purchases Day Book",
        numeric_from=_PURCHASES_NUMERIC_FROM,
    )


# ───────────────────────────── Cash Book ─────────────────────────────
#
# A true cash book: every posting to a cash or bank GL account (account
# categories BANK and CASH, incl. petty cash) within the period — so it
# captures bank charges, interest, transfers and manual cash journals, not
# just AR receipts / AP payments. For a cash/bank (asset) account a DEBIT is
# money in (inflow) and a CREDIT is money out (outflow). Opening and closing
# balances are shown per account. Figures are in the functional (presentation)
# currency so multi-currency accounts total consistently.

_CASH_BOOK_EXPORT_HEADERS = [
    "Date",
    "Account",
    "Journal No",
    "Description",
    "Inflow",
    "Outflow",
]
_CASH_BOOK_NUMERIC_FROM = 4

# Account categories that constitute "cash and cash equivalents".
_CASH_BANK_CATEGORY_CODES = ("BANK", "CASH")


def _cash_bank_accounts(db: Any, org_id: Any) -> dict[Any, dict[str, str]]:
    """Return cash/bank GL accounts -> {code, name, label}, keyed by account_id."""
    from sqlalchemy import select

    rows = db.execute(
        select(
            Account.account_id,
            Account.account_code,
            Account.account_name,
        )
        .join(AccountCategory, Account.category_id == AccountCategory.category_id)
        .where(
            Account.organization_id == org_id,
            AccountCategory.category_code.in_(_CASH_BANK_CATEGORY_CODES),
        )
        .order_by(Account.account_code)
    ).all()
    return {
        r[0]: {
            "code": r[1] or "",
            "name": r[2] or "",
            "label": f"{r[1]} · {r[2]}" if r[1] else (r[2] or ""),
        }
        for r in rows
    }


def cash_book_context(
    db: Any,
    organization_id: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """Build context for the Cash Book (all postings to cash/bank GL accounts)."""
    from sqlalchemy import func, select

    org_id = coerce_uuid(organization_id)

    today = date.today()
    from_date = _parse_date(start_date) or today.replace(day=1)
    to_date = _parse_date(end_date) or today

    accounts = _cash_bank_accounts(db, org_id)
    acct_ids = list(accounts.keys())

    openings: dict[Any, Decimal] = {aid: Decimal("0") for aid in acct_ids}
    period_in: dict[Any, Decimal] = {aid: Decimal("0") for aid in acct_ids}
    period_out: dict[Any, Decimal] = {aid: Decimal("0") for aid in acct_ids}
    rows: list[dict[str, Any]] = []

    if acct_ids:
        # Opening balance per account = net (debit - credit) of all posted
        # lines before the period start.
        opening_rows = db.execute(
            select(
                JournalEntryLine.account_id,
                func.coalesce(func.sum(JournalEntryLine.debit_amount_functional), 0),
                func.coalesce(func.sum(JournalEntryLine.credit_amount_functional), 0),
            )
            .join(
                JournalEntry,
                JournalEntry.journal_entry_id == JournalEntryLine.journal_entry_id,
            )
            .where(
                JournalEntry.organization_id == org_id,
                JournalEntry.status == JournalStatus.POSTED,
                JournalEntry.posting_date < from_date,
                JournalEntryLine.account_id.in_(acct_ids),
            )
            .group_by(JournalEntryLine.account_id)
        ).all()
        for aid, dr, cr in opening_rows:
            openings[aid] = (dr or Decimal("0")) - (cr or Decimal("0"))

        # Period detail — one row per cash/bank posting line.
        detail = db.execute(
            select(
                JournalEntry.posting_date,
                JournalEntry.journal_number,
                JournalEntry.description,
                JournalEntryLine.account_id,
                JournalEntryLine.debit_amount_functional,
                JournalEntryLine.credit_amount_functional,
            )
            .join(
                JournalEntry,
                JournalEntry.journal_entry_id == JournalEntryLine.journal_entry_id,
            )
            .where(
                JournalEntry.organization_id == org_id,
                JournalEntry.status == JournalStatus.POSTED,
                JournalEntry.posting_date >= from_date,
                JournalEntry.posting_date <= to_date,
                JournalEntryLine.account_id.in_(acct_ids),
            )
            .order_by(JournalEntry.posting_date, JournalEntry.journal_number)
        ).all()
        for posting_date, jnum, desc, aid, dr, cr in detail:
            inflow = dr or Decimal("0")
            outflow = cr or Decimal("0")
            period_in[aid] += inflow
            period_out[aid] += outflow
            rows.append(
                {
                    "date": _format_date(posting_date),
                    "date_iso": _iso_date(posting_date),
                    "account": accounts[aid]["label"],
                    "journal_number": jnum,
                    "description": desc or "",
                    "inflow": _format_currency(inflow) if inflow else "",
                    "inflow_raw": float(inflow),
                    "outflow": _format_currency(outflow) if outflow else "",
                    "outflow_raw": float(outflow),
                }
            )

    # Per-account summary (opening, inflows, outflows, closing).
    accounts_summary: list[dict[str, Any]] = []
    total_open = total_in = total_out = total_close = Decimal("0")
    for aid in acct_ids:
        opening = openings[aid]
        inflow = period_in[aid]
        outflow = period_out[aid]
        closing = opening + inflow - outflow
        total_open += opening
        total_in += inflow
        total_out += outflow
        total_close += closing
        accounts_summary.append(
            {
                "account": accounts[aid]["label"],
                "opening": _format_currency(opening),
                "opening_raw": float(opening),
                "inflows": _format_currency(inflow),
                "inflows_raw": float(inflow),
                "outflows": _format_currency(outflow),
                "outflows_raw": float(outflow),
                "closing": _format_currency(closing),
                "closing_raw": float(closing),
            }
        )

    return {
        "rows": rows,
        "row_count": len(rows),
        "accounts_summary": accounts_summary,
        "account_count": len(acct_ids),
        "start_date": _format_date(from_date),
        "start_date_iso": _iso_date(from_date),
        "end_date": _format_date(to_date),
        "end_date_iso": _iso_date(to_date),
        "total_opening": _format_currency(total_open),
        "total_opening_raw": float(total_open),
        "total_inflows": _format_currency(total_in),
        "total_inflows_raw": float(total_in),
        "total_outflows": _format_currency(total_out),
        "total_outflows_raw": float(total_out),
        "total_closing": _format_currency(total_close),
        "total_closing_raw": float(total_close),
        "basis_note": (
            "Covers every posting to cash and bank GL accounts (categories "
            "Bank and Cash, including petty cash). Figures are in the "
            "functional/presentation currency."
        ),
    }


def _cash_book_export_rows(ctx: dict[str, Any]) -> list[list[str]]:
    """Flatten the Cash Book detail into export rows + totals line."""
    rows: list[list[str]] = [
        [
            r["date_iso"],
            r["account"],
            r["journal_number"],
            r["description"],
            str(r["inflow_raw"]),
            str(r["outflow_raw"]),
        ]
        for r in ctx["rows"]
    ]
    rows.append(
        [
            "",
            "",
            "",
            "TOTAL",
            str(ctx["total_inflows_raw"]),
            str(ctx["total_outflows_raw"]),
        ]
    )
    return rows


def export_cash_book_csv(
    organization_id: str,
    db: Any,
    start_date: str | None = None,
    end_date: str | None = None,
) -> str:
    """Export the Cash Book as CSV."""
    ctx = cash_book_context(db, organization_id, start_date, end_date)
    return _build_csv(_CASH_BOOK_EXPORT_HEADERS, _cash_book_export_rows(ctx))


def export_cash_book_xlsx(
    organization_id: str,
    db: Any,
    start_date: str | None = None,
    end_date: str | None = None,
) -> bytes:
    """Export the Cash Book as an Excel workbook."""
    ctx = cash_book_context(db, organization_id, start_date, end_date)
    return _build_xlsx(
        _CASH_BOOK_EXPORT_HEADERS,
        _cash_book_export_rows(ctx),
        sheet_name="Cash Book",
        numeric_from=_CASH_BOOK_NUMERIC_FROM,
    )


# ─────────────────────────── Journal (Proper) ───────────────────────────
#
# The Journal Proper: posted GL journals in date order with debit/credit
# control totals, EXCLUDING entries that already belong to another book of
# prime entry — anything touching a cash/bank account (Cash Book) and the
# trade-invoice documents (Sales/Purchases & their returns books). What
# remains is genuine "other" entries: accruals, depreciation, reclasses,
# opening/closing entries, corrections. The full unfiltered journal listing
# lives at /finance/gl/journals.

# Source document types whose journals already belong to another book of prime
# entry, so they are kept out of the Journal Proper:
#  - INVOICE / SUPPLIER_INVOICE      -> the trade invoice itself (Sales /
#    Purchases & their returns day books).
#  - *_INVOICE_VAT_DEFERRAL          -> the cash-basis VAT leg companion to each
#    invoice (output VAT deferred at invoice time, reclassed on receipt — see
#    feedback_vat_cash_basis). A mechanical byproduct of a document already in
#    the Sales/Purchases day books (where that VAT is shown against the
#    invoice), not a genuine adjusting entry, so it does not belong in the
#    Journal Proper. The complete journal-grain record remains at
#    /finance/gl/journals.
# Sourced from the shared constant so the posting layer and this report cannot
# drift apart (see app/services/finance/common/source_types.py).
_JOURNAL_PROPER_EXCLUDED_SOURCES = INVOICE_JOURNAL_SOURCE_TYPES

_JOURNAL_EXPORT_HEADERS = [
    "Date",
    "Journal No",
    "Description",
    "Reference",
    "Status",
    "Debit",
    "Credit",
]
_JOURNAL_NUMERIC_FROM = 5


def journal_day_book_context(
    db: Any,
    organization_id: str,
    start_date: str | None = None,
    end_date: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """Build context for the Journal day book (GL journal entries)."""
    org_id = coerce_uuid(organization_id)

    today = date.today()
    from_date = _parse_date(start_date) or today.replace(day=1)
    to_date = _parse_date(end_date) or today

    from sqlalchemy import or_, select

    # A day book records posted entries; default to POSTED when unfiltered.
    effective_status = status or JournalStatus.POSTED.value
    query = build_journal_query(
        db=db,
        organization_id=str(org_id),
        status=effective_status,
        start_date=_iso_date(from_date),
        end_date=_iso_date(to_date),
    )

    # Journal *Proper*: exclude entries that belong to another book of prime
    # entry so the six books stay a clean partition (no double-counting).
    #  - any journal touching a cash/bank account -> Cash Book
    #  - trade-invoice-sourced journals -> Sales/Purchases (& returns) books
    cash_bank_ids = list(_cash_bank_accounts(db, org_id).keys())
    if cash_bank_ids:
        cash_journal_ids = (
            select(JournalEntryLine.journal_entry_id)
            .where(JournalEntryLine.account_id.in_(cash_bank_ids))
            .distinct()
        )
        query = query.where(JournalEntry.journal_entry_id.notin_(cash_journal_ids))
    query = query.where(
        or_(
            JournalEntry.source_document_type.is_(None),
            JournalEntry.source_document_type.notin_(_JOURNAL_PROPER_EXCLUDED_SOURCES),
        )
    )

    query = query.order_by(JournalEntry.posting_date, JournalEntry.journal_number)

    entries = list(db.scalars(query).all())

    rows: list[dict[str, Any]] = []
    total_debit = Decimal("0")
    total_credit = Decimal("0")
    for je in entries:
        debit = je.total_debit or Decimal("0")
        credit = je.total_credit or Decimal("0")
        total_debit += debit
        total_credit += credit
        rows.append(
            {
                "posting_date": _format_date(je.posting_date),
                "posting_date_iso": _iso_date(je.posting_date),
                "journal_number": je.journal_number,
                "description": je.description or "",
                "reference": je.reference or "",
                "status": je.status.value if je.status else "",
                "debit": _format_currency(debit),
                "debit_raw": float(debit),
                "credit": _format_currency(credit),
                "credit_raw": float(credit),
            }
        )

    return {
        "rows": rows,
        "row_count": len(rows),
        "start_date": _format_date(from_date),
        "start_date_iso": _iso_date(from_date),
        "end_date": _format_date(to_date),
        "end_date_iso": _iso_date(to_date),
        "status": status or "",
        "total_debit": _format_currency(total_debit),
        "total_debit_raw": float(total_debit),
        "total_credit": _format_currency(total_credit),
        "total_credit_raw": float(total_credit),
        "is_balanced": total_debit == total_credit,
        "scope_note": (
            "Journal Proper — genuine adjusting entries (accruals, "
            "depreciation, reclasses, opening/closing, corrections). Excludes "
            "entries already in the Cash Book (cash/bank postings), the "
            "Sales/Purchases day books (trade invoices), and the cash-basis "
            "VAT deferral legs companion to those invoices. For every posted "
            "journal, see the GL journal listing at /finance/gl/journals."
        ),
    }


def _journal_day_book_export_rows(ctx: dict[str, Any]) -> list[list[str]]:
    """Flatten the Journal day book context into export rows + totals line."""
    rows: list[list[str]] = [
        [
            r["posting_date_iso"],
            r["journal_number"],
            r["description"],
            r["reference"],
            r["status"],
            str(r["debit_raw"]),
            str(r["credit_raw"]),
        ]
        for r in ctx["rows"]
    ]
    rows.append(
        [
            "",
            "",
            "",
            "",
            "TOTAL",
            str(ctx["total_debit_raw"]),
            str(ctx["total_credit_raw"]),
        ]
    )
    return rows


def export_journal_day_book_csv(
    organization_id: str,
    db: Any,
    start_date: str | None = None,
    end_date: str | None = None,
    status: str | None = None,
) -> str:
    """Export the Journal day book as CSV."""
    ctx = journal_day_book_context(
        db, organization_id, start_date, end_date, status=status
    )
    return _build_csv(_JOURNAL_EXPORT_HEADERS, _journal_day_book_export_rows(ctx))


def export_journal_day_book_xlsx(
    organization_id: str,
    db: Any,
    start_date: str | None = None,
    end_date: str | None = None,
    status: str | None = None,
) -> bytes:
    """Export the Journal day book as an Excel workbook."""
    ctx = journal_day_book_context(
        db, organization_id, start_date, end_date, status=status
    )
    return _build_xlsx(
        _JOURNAL_EXPORT_HEADERS,
        _journal_day_book_export_rows(ctx),
        sheet_name="Journal",
        numeric_from=_JOURNAL_NUMERIC_FROM,
    )


# ─────────────────────── Sales Returns Day Book ───────────────────────
#
# Returns inward: AR credit notes (Invoice rows with invoice_type CREDIT_NOTE).
# Amounts are shown as positive magnitudes — the heading conveys they reduce
# revenue/receivables.

_SALES_RETURNS_EXPORT_HEADERS = [
    "Date",
    "Credit Note No",
    "Customer",
    "Reference",
    "Currency",
    "Status",
    "Net",
    "VAT",
    "Gross",
]
_SALES_RETURNS_NUMERIC_FROM = 6


def sales_returns_day_book_context(
    db: Any,
    organization_id: str,
    start_date: str | None = None,
    end_date: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """Build context for the Sales Returns Day Book (AR credit notes)."""
    org_id = coerce_uuid(organization_id)

    today = date.today()
    from_date = _parse_date(start_date) or today.replace(day=1)
    to_date = _parse_date(end_date) or today

    query = build_invoice_query(
        db=db,
        organization_id=str(org_id),
        status=status or None,
        start_date=_iso_date(from_date),
        end_date=_iso_date(to_date),
    ).where(Invoice.invoice_type == InvoiceType.CREDIT_NOTE)
    if not status:
        query = query.where(Invoice.status.notin_(_SALES_EXCLUDED_STATUSES))
    query = query.order_by(Invoice.invoice_date, Invoice.invoice_number)

    credit_notes = list(db.scalars(query).all())

    rows: list[dict[str, Any]] = []
    total_net = Decimal("0")
    total_vat = Decimal("0")
    total_gross = Decimal("0")
    for cn in credit_notes:
        net = cn.subtotal or Decimal("0")
        vat = cn.tax_amount or Decimal("0")
        gross = cn.total_amount or Decimal("0")
        total_net += net
        total_vat += vat
        total_gross += gross
        rows.append(
            {
                "invoice_date": _format_date(cn.invoice_date),
                "invoice_date_iso": _iso_date(cn.invoice_date),
                "invoice_number": cn.invoice_number,
                "customer_name": _customer_name(cn),
                "reference": cn.purpose or "",
                "currency_code": cn.currency_code,
                "status": cn.status.value if cn.status else "",
                "net": _format_currency(net),
                "net_raw": float(net),
                "vat": _format_currency(vat),
                "vat_raw": float(vat),
                "gross": _format_currency(gross),
                "gross_raw": float(gross),
            }
        )

    return {
        "rows": rows,
        "row_count": len(rows),
        "start_date": _format_date(from_date),
        "start_date_iso": _iso_date(from_date),
        "end_date": _format_date(to_date),
        "end_date_iso": _iso_date(to_date),
        "status": status or "",
        "total_net": _format_currency(total_net),
        "total_net_raw": float(total_net),
        "total_vat": _format_currency(total_vat),
        "total_vat_raw": float(total_vat),
        "total_gross": _format_currency(total_gross),
        "total_gross_raw": float(total_gross),
        "vat_basis_note": (
            "VAT shown is the tax on credit notes in this period (memo). "
            "This organisation accounts for VAT on a cash basis, so it will "
            "not equal the VAT return for the period."
        ),
    }


def _sales_returns_export_rows(ctx: dict[str, Any]) -> list[list[str]]:
    """Flatten the Sales Returns Day Book context into export rows + totals."""
    rows: list[list[str]] = [
        [
            r["invoice_date_iso"],
            r["invoice_number"],
            r["customer_name"],
            r["reference"],
            r["currency_code"],
            r["status"],
            str(r["net_raw"]),
            str(r["vat_raw"]),
            str(r["gross_raw"]),
        ]
        for r in ctx["rows"]
    ]
    rows.append(
        [
            "",
            "",
            "",
            "",
            "",
            "TOTAL",
            str(ctx["total_net_raw"]),
            str(ctx["total_vat_raw"]),
            str(ctx["total_gross_raw"]),
        ]
    )
    return rows


def export_sales_returns_day_book_csv(
    organization_id: str,
    db: Any,
    start_date: str | None = None,
    end_date: str | None = None,
    status: str | None = None,
) -> str:
    """Export the Sales Returns Day Book as CSV."""
    ctx = sales_returns_day_book_context(
        db, organization_id, start_date, end_date, status=status
    )
    return _build_csv(_SALES_RETURNS_EXPORT_HEADERS, _sales_returns_export_rows(ctx))


def export_sales_returns_day_book_xlsx(
    organization_id: str,
    db: Any,
    start_date: str | None = None,
    end_date: str | None = None,
    status: str | None = None,
) -> bytes:
    """Export the Sales Returns Day Book as an Excel workbook."""
    ctx = sales_returns_day_book_context(
        db, organization_id, start_date, end_date, status=status
    )
    return _build_xlsx(
        _SALES_RETURNS_EXPORT_HEADERS,
        _sales_returns_export_rows(ctx),
        sheet_name="Sales Returns",
        numeric_from=_SALES_RETURNS_NUMERIC_FROM,
    )


# ───────────────────── Purchases Returns Day Book ─────────────────────
#
# Returns outward: supplier credit notes (SupplierInvoice rows with
# invoice_type CREDIT_NOTE). In this system a purchase return is recorded as
# an AP credit note, whose GL posting already fully reverses the original
# purchase (Dr AP, Cr expense/inventory, reverse input VAT & WHT). Amounts are
# stored negative; the formatter renders them in accounting parentheses.

_PURCHASES_RETURNS_EXPORT_HEADERS = [
    "Date",
    "Credit Note No",
    "Supplier Inv #",
    "Supplier",
    "Reference",
    "Currency",
    "Status",
    "Net",
    "VAT",
    "WHT",
    "Gross",
]
_PURCHASES_RETURNS_NUMERIC_FROM = 7


def purchases_returns_day_book_context(
    db: Any,
    organization_id: str,
    start_date: str | None = None,
    end_date: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """Build context for the Purchases Returns Day Book (supplier credit notes)."""
    org_id = coerce_uuid(organization_id)

    today = date.today()
    from_date = _parse_date(start_date) or today.replace(day=1)
    to_date = _parse_date(end_date) or today

    query = build_supplier_invoice_query(
        db=db,
        organization_id=str(org_id),
        status=status or None,
        start_date=_iso_date(from_date),
        end_date=_iso_date(to_date),
    ).where(SupplierInvoice.invoice_type == SupplierInvoiceType.CREDIT_NOTE)
    if not status:
        query = query.where(SupplierInvoice.status.notin_(_PURCHASES_EXCLUDED_STATUSES))
    query = query.order_by(SupplierInvoice.invoice_date, SupplierInvoice.invoice_number)

    credit_notes = list(db.scalars(query).all())
    names = _supplier_name_map(db, org_id, {cn.supplier_id for cn in credit_notes})

    rows: list[dict[str, Any]] = []
    total_net = Decimal("0")
    total_vat = Decimal("0")
    total_wht = Decimal("0")
    total_gross = Decimal("0")
    for cn in credit_notes:
        net = cn.subtotal or Decimal("0")
        vat = cn.tax_amount or Decimal("0")
        wht = cn.withholding_tax_amount or Decimal("0")
        gross = cn.total_amount or Decimal("0")
        total_net += net
        total_vat += vat
        total_wht += wht
        total_gross += gross
        rows.append(
            {
                "invoice_date": _format_date(cn.invoice_date),
                "invoice_date_iso": _iso_date(cn.invoice_date),
                "invoice_number": cn.invoice_number,
                "supplier_invoice_number": cn.supplier_invoice_number or "",
                "supplier_name": names.get(str(cn.supplier_id), ""),
                "reference": cn.purpose or "",
                "currency_code": cn.currency_code,
                "status": cn.status.value if cn.status else "",
                "net": _format_currency(net),
                "net_raw": float(net),
                "vat": _format_currency(vat),
                "vat_raw": float(vat),
                "wht": _format_currency(wht),
                "wht_raw": float(wht),
                "gross": _format_currency(gross),
                "gross_raw": float(gross),
            }
        )

    return {
        "rows": rows,
        "row_count": len(rows),
        "start_date": _format_date(from_date),
        "start_date_iso": _iso_date(from_date),
        "end_date": _format_date(to_date),
        "end_date_iso": _iso_date(to_date),
        "status": status or "",
        "total_net": _format_currency(total_net),
        "total_net_raw": float(total_net),
        "total_vat": _format_currency(total_vat),
        "total_vat_raw": float(total_vat),
        "total_wht": _format_currency(total_wht),
        "total_wht_raw": float(total_wht),
        "total_gross": _format_currency(total_gross),
        "total_gross_raw": float(total_gross),
        "vat_basis_note": (
            "VAT shown is the input tax reversed on supplier credit notes in "
            "this period (memo). This organisation accounts for VAT on a cash "
            "basis, so it will not equal the VAT return for the period."
        ),
    }


def _purchases_returns_export_rows(ctx: dict[str, Any]) -> list[list[str]]:
    """Flatten the Purchases Returns Day Book context into export rows + totals."""
    rows: list[list[str]] = [
        [
            r["invoice_date_iso"],
            r["invoice_number"],
            r["supplier_invoice_number"],
            r["supplier_name"],
            r["reference"],
            r["currency_code"],
            r["status"],
            str(r["net_raw"]),
            str(r["vat_raw"]),
            str(r["wht_raw"]),
            str(r["gross_raw"]),
        ]
        for r in ctx["rows"]
    ]
    rows.append(
        [
            "",
            "",
            "",
            "",
            "",
            "",
            "TOTAL",
            str(ctx["total_net_raw"]),
            str(ctx["total_vat_raw"]),
            str(ctx["total_wht_raw"]),
            str(ctx["total_gross_raw"]),
        ]
    )
    return rows


def export_purchases_returns_day_book_csv(
    organization_id: str,
    db: Any,
    start_date: str | None = None,
    end_date: str | None = None,
    status: str | None = None,
) -> str:
    """Export the Purchases Returns Day Book as CSV."""
    ctx = purchases_returns_day_book_context(
        db, organization_id, start_date, end_date, status=status
    )
    return _build_csv(
        _PURCHASES_RETURNS_EXPORT_HEADERS, _purchases_returns_export_rows(ctx)
    )


def export_purchases_returns_day_book_xlsx(
    organization_id: str,
    db: Any,
    start_date: str | None = None,
    end_date: str | None = None,
    status: str | None = None,
) -> bytes:
    """Export the Purchases Returns Day Book as an Excel workbook."""
    ctx = purchases_returns_day_book_context(
        db, organization_id, start_date, end_date, status=status
    )
    return _build_xlsx(
        _PURCHASES_RETURNS_EXPORT_HEADERS,
        _purchases_returns_export_rows(ctx),
        sheet_name="Purchases Returns",
        numeric_from=_PURCHASES_RETURNS_NUMERIC_FROM,
    )
