"""
GL Web Service - Base utilities and view transformers.

Provides common utilities used across GL web service modules.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

from app.models.finance.gl.account import Account
from app.models.finance.gl.account_category import AccountCategory, IFRSCategory
from app.models.finance.gl.fiscal_period import FiscalPeriod
from app.models.finance.gl.fiscal_year import FiscalYear
from app.models.finance.gl.journal_entry import JournalEntry, JournalStatus
from app.models.finance.gl.journal_entry_line import JournalEntryLine
from app.services.formatters import format_currency as format_currency  # noqa: F401
from app.services.formatters import format_date as format_date  # noqa: F401
from app.services.formatters import parse_date as parse_date  # noqa: F401

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Parsing utilities
# -----------------------------------------------------------------------------


def ifrs_label(category: IFRSCategory | str | None) -> str:
    """Get a human-readable label for an IFRS category.

    Accepts either an IFRSCategory enum or the raw persisted string value.
    """
    label_map = {
        IFRSCategory.ASSETS: "ASSET",
        IFRSCategory.LIABILITIES: "LIABILITY",
        IFRSCategory.EQUITY: "EQUITY",
        IFRSCategory.REVENUE: "REVENUE",
        IFRSCategory.EXPENSES: "EXPENSE",
        IFRSCategory.OTHER_COMPREHENSIVE_INCOME: "OCI",
    }
    if category is None:
        return ""
    if isinstance(category, str) and not isinstance(category, IFRSCategory):
        try:
            category = IFRSCategory(category)
        except ValueError:
            return category
    return label_map.get(category, category.value)


def parse_category(value: str | None) -> IFRSCategory | None:
    """Parse a category string to IFRSCategory enum."""
    if not value:
        return None
    mapping = {
        "ASSET": IFRSCategory.ASSETS,
        "LIABILITY": IFRSCategory.LIABILITIES,
        "EQUITY": IFRSCategory.EQUITY,
        "REVENUE": IFRSCategory.REVENUE,
        "EXPENSE": IFRSCategory.EXPENSES,
    }
    return mapping.get(value)


def parse_status(value: str | None) -> JournalStatus | None:
    """Parse a status string to JournalStatus enum."""
    if not value:
        return None
    try:
        return JournalStatus(value)
    except ValueError:
        return None


# -----------------------------------------------------------------------------
# View transformers
# -----------------------------------------------------------------------------


def category_option_view(category: AccountCategory) -> dict:
    """Transform an account category for select options."""
    return {
        "category_id": category.category_id,
        "category_code": category.category_code,
        "category_name": category.category_name,
        "ifrs_category": category.ifrs_category.value,
        "ifrs_label": ifrs_label(category.ifrs_category),
    }


def account_form_view(account: Account) -> dict:
    """Transform an account for form editing.

    Nullable string columns are returned as empty strings so the form template
    renders blank input values rather than the literal string "None" — which
    happens when Jinja2 stringifies Python None in an `<input value="...">`
    attribute. Posting "None" back to the server fails the DB length check on
    short VARCHAR columns (e.g. default_currency_code VARCHAR(3)).
    """
    return {
        "account_id": account.account_id,
        "account_code": account.account_code,
        "account_name": account.account_name,
        "description": account.description or "",
        "search_terms": account.search_terms or "",
        "category_id": account.category_id,
        "account_type": account.account_type.value,
        "normal_balance": account.normal_balance.value,
        "is_multi_currency": account.is_multi_currency,
        "default_currency_code": account.default_currency_code or "",
        "is_active": account.is_active,
        "is_posting_allowed": account.is_posting_allowed,
        "is_budgetable": account.is_budgetable,
        "is_reconciliation_required": account.is_reconciliation_required,
        "subledger_type": account.subledger_type or "",
        "is_cash_equivalent": account.is_cash_equivalent,
        "is_financial_instrument": account.is_financial_instrument,
    }


def account_detail_view(account: Account) -> dict:
    """Transform an account for detail display."""
    category = account.category
    return {
        "account_id": account.account_id,
        "account_code": account.account_code,
        "account_name": account.account_name,
        "description": account.description,
        "category_id": account.category_id,
        "category_name": category.category_name if category else "",
        "category_code": category.category_code if category else "",
        "ifrs_category": ifrs_label(category.ifrs_category) if category else "",
        "account_type": account.account_type.value,
        "normal_balance": account.normal_balance.value,
        "is_active": account.is_active,
        "is_posting_allowed": account.is_posting_allowed,
        "is_budgetable": account.is_budgetable,
        "is_reconciliation_required": account.is_reconciliation_required,
        "subledger_type": account.subledger_type,
        "is_cash_equivalent": account.is_cash_equivalent,
        "is_financial_instrument": account.is_financial_instrument,
    }


def journal_entry_view(entry: JournalEntry) -> dict:
    """Transform a journal entry for both list display and edit form pre-fill.

    Dates are returned as ISO strings (YYYY-MM-DD) so they bind cleanly to
    `<input type="date">`; templates that need the human-readable form should
    apply `format_date` themselves.
    """
    fiscal_period_id = getattr(entry, "fiscal_period_id", None)
    exchange_rate = getattr(entry, "exchange_rate", None)
    return {
        "journal_entry_id": str(entry.journal_entry_id)
        if entry.journal_entry_id
        else None,
        "fiscal_period_id": str(fiscal_period_id) if fiscal_period_id else None,
        "journal_number": entry.journal_number,
        "journal_type": entry.journal_type.value,
        "entry_date": entry.entry_date.isoformat() if entry.entry_date else "",
        "posting_date": entry.posting_date.isoformat() if entry.posting_date else "",
        "description": entry.description,
        "reference": entry.reference,
        "status": entry.status.value,
        "source_module": entry.source_module,
        "source_document_type": entry.source_document_type,
        "source_document_id": str(entry.source_document_id)
        if entry.source_document_id
        else "",
        "total_debit": float(entry.total_debit)
        if entry.total_debit is not None
        else 0.0,
        "total_credit": float(entry.total_credit)
        if entry.total_credit is not None
        else 0.0,
        "currency_code": entry.currency_code,
        "exchange_rate": float(exchange_rate) if exchange_rate is not None else 1.0,
        "created_at": entry.created_at.isoformat() if entry.created_at else "",
    }


def journal_line_view(
    line: JournalEntryLine,
    account_name: str | None = None,
    account_code: str | None = None,
) -> dict:
    """Transform a journal entry line for display. Returns JSON-safe scalars."""
    return {
        "line_id": str(line.line_id) if line.line_id else None,
        "line_number": line.line_number,
        "account_id": str(line.account_id) if line.account_id else None,
        "account_code": account_code or "",
        "account_name": account_name or "",
        "description": line.description,
        "debit_amount": float(line.debit_amount)
        if line.debit_amount is not None
        else 0.0,
        "credit_amount": float(line.credit_amount)
        if line.credit_amount is not None
        else 0.0,
        "debit_amount_functional": float(line.debit_amount_functional)
        if line.debit_amount_functional is not None
        else 0.0,
        "credit_amount_functional": float(line.credit_amount_functional)
        if line.credit_amount_functional is not None
        else 0.0,
    }


def period_option_view(period: FiscalPeriod) -> dict:
    """Transform a fiscal period for select options."""
    return {
        "fiscal_period_id": period.fiscal_period_id,
        "period_name": period.period_name,
        "period_number": period.period_number,
        "start_date": format_date(period.start_date),
        "end_date": format_date(period.end_date),
        "status": period.status.value,
    }


def fiscal_year_option_view(year: FiscalYear) -> dict:
    """Transform a fiscal year for select options."""
    return {
        "fiscal_year_id": year.fiscal_year_id,
        "year_name": year.year_name,
        "year_code": year.year_code,
        "start_date": format_date(year.start_date),
        "end_date": format_date(year.end_date),
        "status": "Closed" if year.is_closed else "Open",
    }


# -----------------------------------------------------------------------------
# Data classes
# -----------------------------------------------------------------------------


@dataclass
class TrialBalanceTotals:
    """Totals for trial balance display."""

    total_debits: Decimal = Decimal("0")
    total_credits: Decimal = Decimal("0")
