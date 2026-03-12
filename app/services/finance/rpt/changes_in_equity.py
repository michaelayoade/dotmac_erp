"""Changes in equity report context builder."""

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
from app.services.finance.rpt.common import (
    _format_currency,
    _format_date,
    _iso_date,
    _parse_date,
)


def changes_in_equity_context(
    db: Session,
    organization_id: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """Get context for statement of changes in equity."""
    org_id = coerce_uuid(organization_id)
    today = date.today()
    from_date = _parse_date(start_date) or today.replace(day=1)
    to_date = _parse_date(end_date) or today

    equity_rows = db.execute(
        select(
            Account.account_id,
            Account.account_code,
            Account.account_name,
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
        .join(Account, Account.account_id == JournalEntryLine.account_id)
        .join(AccountCategory, Account.category_id == AccountCategory.category_id)
        .where(
            JournalEntry.organization_id == org_id,
            JournalEntry.status == JournalStatus.POSTED,
            JournalEntry.posting_date >= from_date,
            JournalEntry.posting_date <= to_date,
            AccountCategory.ifrs_category == IFRSCategory.EQUITY,
        )
        .group_by(Account.account_id, Account.account_code, Account.account_name)
        .order_by(Account.account_code)
    ).all()

    opening_rows = db.execute(
        select(
            Account.account_id,
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
        .join(Account, Account.account_id == JournalEntryLine.account_id)
        .join(AccountCategory, Account.category_id == AccountCategory.category_id)
        .where(
            JournalEntry.organization_id == org_id,
            JournalEntry.status == JournalStatus.POSTED,
            JournalEntry.posting_date < from_date,
            AccountCategory.ifrs_category == IFRSCategory.EQUITY,
        )
        .group_by(Account.account_id)
    ).all()
    opening_map = {row.account_id: (row.debit, row.credit) for row in opening_rows}

    line_items: list[dict[str, Any]] = []
    total_opening = Decimal("0")
    total_change = Decimal("0")
    total_closing = Decimal("0")

    for account_id, code, name, debit, credit in equity_rows:
        debit = Decimal(str(debit or 0))
        credit = Decimal(str(credit or 0))
        opening_debit, opening_credit = opening_map.get(account_id, (0, 0))
        opening = Decimal(str(opening_credit or 0)) - Decimal(str(opening_debit or 0))
        change = credit - debit
        closing = opening + change

        total_opening += opening
        total_change += change
        total_closing += closing

        line_items.append(
            {
                "account_code": code,
                "account_name": name,
                "opening_balance": _format_currency(opening),
                "change": _format_currency(change),
                "closing_balance": _format_currency(closing),
                "closing_balance_raw": float(closing),
            }
        )

    # Net income for the period
    revenue_expense = db.execute(
        select(
            AccountCategory.ifrs_category,
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
        .join(Account, Account.account_id == JournalEntryLine.account_id)
        .join(AccountCategory, Account.category_id == AccountCategory.category_id)
        .where(
            JournalEntry.organization_id == org_id,
            JournalEntry.status == JournalStatus.POSTED,
            JournalEntry.posting_date >= from_date,
            JournalEntry.posting_date <= to_date,
            AccountCategory.ifrs_category.in_(
                [
                    IFRSCategory.REVENUE,
                    IFRSCategory.EXPENSES,
                ]
            ),
        )
        .group_by(AccountCategory.ifrs_category)
    ).all()
    total_revenue = Decimal("0")
    total_expenses = Decimal("0")
    for ifrs_category, debit, credit in revenue_expense:
        debit = Decimal(str(debit or 0))
        credit = Decimal(str(credit or 0))
        if ifrs_category == IFRSCategory.REVENUE:
            total_revenue += credit - debit
        elif ifrs_category == IFRSCategory.EXPENSES:
            total_expenses += debit - credit

    net_income = total_revenue - total_expenses

    return {
        "start_date": _format_date(from_date),
        "start_date_iso": _iso_date(from_date),
        "end_date": _format_date(to_date),
        "end_date_iso": _iso_date(to_date),
        "equity_lines": line_items,
        "opening_equity": _format_currency(total_opening),
        "change_in_equity": _format_currency(total_change),
        "closing_equity": _format_currency(total_closing),
        "net_income": _format_currency(net_income),
        "net_income_raw": float(net_income),
    }
