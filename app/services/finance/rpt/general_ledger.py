"""General ledger detail report context builder and CSV export."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.gl.account import Account
from app.models.finance.gl.journal_entry import JournalEntry, JournalStatus
from app.models.finance.gl.journal_entry_line import JournalEntryLine
from app.services.common import coerce_uuid
from app.services.finance.rpt.common import (
    _build_csv,
    _format_currency,
    _format_date,
    _iso_date,
    _parse_date,
)


def general_ledger_context(
    db: Session,
    organization_id: str,
    account_id: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """Get context for general ledger detail report."""
    org_id = coerce_uuid(organization_id)

    # Default to current month
    today = date.today()
    from_date = _parse_date(start_date) or today.replace(day=1)
    to_date = _parse_date(end_date) or today

    # Get accounts for dropdown
    accounts = db.scalars(
        select(Account)
        .where(
            Account.organization_id == org_id,
            Account.is_active.is_(True),
        )
        .order_by(Account.account_code)
    ).all()

    account_options = [
        {
            "account_id": str(acct.account_id),
            "account_code": acct.account_code if len(acct.account_code) < 20 else "",
            "account_name": acct.account_name,
        }
        for acct in accounts
    ]

    transactions: list[dict[str, Any]] = []
    selected_account = None
    running_balance = Decimal("0")

    if account_id:
        acct_id = coerce_uuid(account_id)
        selected_account = db.get(Account, acct_id)

        if selected_account and selected_account.organization_id == org_id:
            # Get journal lines for this account
            lines = db.execute(
                select(JournalEntryLine, JournalEntry)
                .join(
                    JournalEntry,
                    JournalEntry.journal_entry_id == JournalEntryLine.journal_entry_id,
                )
                .where(
                    JournalEntryLine.account_id == acct_id,
                    JournalEntry.organization_id == org_id,
                    JournalEntry.status == JournalStatus.POSTED,
                    JournalEntry.posting_date >= from_date,
                    JournalEntry.posting_date <= to_date,
                )
                .order_by(JournalEntry.posting_date, JournalEntry.journal_entry_id)
            ).all()

            for line, entry in lines:
                debit = line.debit_amount_functional or Decimal("0")
                credit = line.credit_amount_functional or Decimal("0")

                # Calculate running balance based on normal balance
                if selected_account.normal_balance.value == "DEBIT":
                    running_balance += debit - credit
                else:
                    running_balance += credit - debit

                transactions.append(
                    {
                        "date": _format_date(entry.posting_date),
                        "journal_number": entry.journal_number,
                        "description": line.description or entry.description,
                        "reference": entry.reference or "",
                        "debit": _format_currency(debit) if debit else "",
                        "credit": _format_currency(credit) if credit else "",
                        "balance": _format_currency(running_balance),
                    }
                )

    return {
        "start_date": _format_date(from_date),
        "start_date_iso": _iso_date(from_date),
        "end_date": _format_date(to_date),
        "end_date_iso": _iso_date(to_date),
        "account_id": account_id,
        "accounts": account_options,
        "selected_account": {
            "account_code": selected_account.account_code,
            "account_name": selected_account.account_name,
        }
        if selected_account
        else None,
        "transactions": transactions,
        "ending_balance": _format_currency(running_balance),
    }


def export_general_ledger_csv(
    organization_id: str,
    db: Session,
    account_id: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> str:
    """Export general ledger as CSV."""
    ctx = general_ledger_context(db, organization_id, account_id, start_date, end_date)
    headers = [
        "Date",
        "Journal #",
        "Description",
        "Reference",
        "Debit",
        "Credit",
        "Balance",
    ]
    rows = [
        [
            txn["date"],
            txn["journal_number"],
            txn["description"],
            txn["reference"],
            txn["debit"],
            txn["credit"],
            txn["balance"],
        ]
        for txn in ctx.get("transactions", [])
    ]
    return _build_csv(headers, rows)
