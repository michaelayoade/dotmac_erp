"""Cash flow statement report context builder."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.finance.gl.account import Account
from app.models.finance.gl.account_category import AccountCategory
from app.models.finance.gl.journal_entry import JournalEntry, JournalStatus
from app.models.finance.gl.journal_entry_line import JournalEntryLine
from app.services.common import coerce_uuid
from app.services.finance.rpt.common import (
    _format_currency,
    _format_date,
    _iso_date,
    _parse_date,
)


def cash_flow_context(
    db: Session,
    organization_id: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """Get context for cash flow summary report."""
    org_id = coerce_uuid(organization_id)
    today = date.today()
    from_date = _parse_date(start_date) or today.replace(day=1)
    to_date = _parse_date(end_date) or today

    cash_category_codes = {"CASH", "BANK"}
    cash_category_ids = db.scalars(
        select(AccountCategory.category_id).where(
            AccountCategory.organization_id == org_id,
            AccountCategory.category_code.in_(cash_category_codes),
        )
    ).all()

    cash_accounts = db.execute(
        select(Account.account_id, Account.account_code, Account.account_name)
        .where(
            Account.organization_id == org_id,
            Account.category_id.in_(cash_category_ids),
            Account.is_active.is_(True),
        )
        .order_by(Account.account_code)
    ).all()
    account_ids = [row.account_id for row in cash_accounts]

    movements: list[dict[str, Any]] = []
    total_inflow = Decimal("0")
    total_outflow = Decimal("0")

    if account_ids:
        rows = db.execute(
            select(
                Account.account_id,
                func.coalesce(
                    func.sum(JournalEntryLine.debit_amount_functional), 0
                ).label("debit"),
                func.coalesce(
                    func.sum(JournalEntryLine.credit_amount_functional), 0
                ).label("credit"),
            )
            .join(
                JournalEntry,
                JournalEntry.journal_entry_id == JournalEntryLine.journal_entry_id,
            )
            .join(Account, Account.account_id == JournalEntryLine.account_id)
            .where(
                JournalEntry.organization_id == org_id,
                JournalEntry.status == JournalStatus.POSTED,
                JournalEntry.posting_date >= from_date,
                JournalEntry.posting_date <= to_date,
                JournalEntryLine.account_id.in_(account_ids),
            )
            .group_by(Account.account_id)
        ).all()

        acct_map = {row.account_id: row for row in cash_accounts}
        for account_id, debit, credit in rows:
            debit = Decimal(str(debit or 0))
            credit = Decimal(str(credit or 0))
            inflow = debit
            outflow = credit
            total_inflow += inflow
            total_outflow += outflow

            account = acct_map.get(account_id)
            movements.append(
                {
                    "account_code": account.account_code if account else "",
                    "account_name": account.account_name if account else "",
                    "inflow": _format_currency(inflow),
                    "outflow": _format_currency(outflow),
                    "net": _format_currency(inflow - outflow),
                    "net_raw": float(inflow - outflow),
                }
            )

    net_cash = total_inflow - total_outflow

    return {
        "start_date": _format_date(from_date),
        "start_date_iso": _iso_date(from_date),
        "end_date": _format_date(to_date),
        "end_date_iso": _iso_date(to_date),
        "cash_movements": movements,
        "total_inflow": _format_currency(total_inflow),
        "total_outflow": _format_currency(total_outflow),
        "net_cash_flow": _format_currency(net_cash),
        "net_cash_flow_raw": float(net_cash),
    }
