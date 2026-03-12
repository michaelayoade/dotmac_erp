"""Expense summary report context builder."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.finance.gl.account import Account
from app.models.finance.gl.account_category import AccountCategory, IFRSCategory
from app.models.finance.gl.journal_entry import JournalEntry, JournalStatus
from app.models.finance.gl.journal_entry_line import JournalEntryLine
from app.services.common import coerce_uuid
from app.services.finance.platform.org_context import org_context_service
from app.services.finance.rpt.common import (
    _format_currency,
    _format_date,
    _iso_date,
    _parse_date,
)


def expense_summary_context(
    db: Session,
    organization_id: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """Get context for expense summary report."""
    org_id = coerce_uuid(organization_id)
    presentation_currency_code = org_context_service.get_presentation_currency(
        db,
        org_id,
    )
    today = date.today()
    from_date = _parse_date(start_date) or today.replace(day=1)
    to_date = _parse_date(end_date) or today

    expense_items: list[dict[str, Any]] = []
    total_expenses = Decimal("0")

    # Aggregate posted ledger lines within the date range for expense accounts.
    expense_rows = db.execute(
        select(
            Account.account_code,
            Account.account_name,
            AccountCategory.category_name,
            func.coalesce(func.sum(JournalEntryLine.debit_amount_functional), 0).label(
                "debit"
            ),
            func.coalesce(func.sum(JournalEntryLine.credit_amount_functional), 0).label(
                "credit"
            ),
        )
        .join(
            JournalEntry,
            JournalEntry.journal_entry_id == JournalEntryLine.journal_entry_id,
        )
        .join(Account, JournalEntryLine.account_id == Account.account_id)
        .join(AccountCategory, Account.category_id == AccountCategory.category_id)
        .where(
            JournalEntry.organization_id == org_id,
            JournalEntry.status == JournalStatus.POSTED,
            JournalEntry.posting_date >= from_date,
            JournalEntry.posting_date <= to_date,
            AccountCategory.ifrs_category == IFRSCategory.EXPENSES,
        )
        .group_by(
            Account.account_code,
            Account.account_name,
            AccountCategory.category_name,
        )
        .order_by(Account.account_code)
    ).all()

    for account_code, account_name, category_name, debit, credit in expense_rows:
        debit = Decimal(str(debit or 0))
        credit = Decimal(str(credit or 0))
        amount = debit - credit
        total_expenses += amount
        expense_items.append(
            {
                "account_code": account_code,
                "account_name": account_name,
                "category": category_name,
                "amount": _format_currency(amount),
                "amount_raw": float(amount),
            }
        )

    # Sort by amount descending
    expense_items.sort(key=lambda x: x["amount_raw"], reverse=True)

    # Top 5 expense categories
    top_expenses = expense_items[:5]

    return {
        "start_date": _format_date(from_date),
        "start_date_iso": _iso_date(from_date),
        "end_date": _format_date(to_date),
        "end_date_iso": _iso_date(to_date),
        "expense_items": expense_items,
        "top_expenses": top_expenses,
        "total_expenses": _format_currency(total_expenses),
        "total_expenses_raw": float(total_expenses),
        "presentation_currency_code": presentation_currency_code,
    }
