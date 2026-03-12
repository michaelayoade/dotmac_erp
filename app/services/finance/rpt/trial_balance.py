"""Trial balance report context builder and CSV export."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.finance.gl.account import Account
from app.models.finance.gl.account_category import AccountCategory, IFRSCategory
from app.models.finance.gl.fiscal_period import FiscalPeriod
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


def trial_balance_context(
    db: Session,
    organization_id: str,
    as_of_date: str | None = None,
) -> dict[str, Any]:
    """Get context for trial balance report."""
    org_id = coerce_uuid(organization_id)
    ref_date = _parse_date(as_of_date) or date.today()

    # Find the fiscal period for the date
    period = db.scalars(
        select(FiscalPeriod)
        .where(
            FiscalPeriod.organization_id == org_id,
            FiscalPeriod.start_date <= ref_date,
            FiscalPeriod.end_date >= ref_date,
        )
        .order_by(FiscalPeriod.start_date.desc())
    ).first()

    if not period:
        period = db.scalars(
            select(FiscalPeriod)
            .where(FiscalPeriod.organization_id == org_id)
            .order_by(FiscalPeriod.end_date.desc())
        ).first()

    balances: list[dict[str, Any]] = []
    total_debit = Decimal("0")
    total_credit = Decimal("0")

    # Group by IFRS category
    assets: list[dict[str, Any]] = []
    liabilities: list[dict[str, Any]] = []
    equity: list[dict[str, Any]] = []
    revenue: list[dict[str, Any]] = []
    expenses: list[dict[str, Any]] = []

    rows = db.execute(
        select(
            Account.account_code,
            Account.account_name,
            AccountCategory.ifrs_category,
            Account.is_active,
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
            JournalEntry.posting_date <= ref_date,
        )
        .group_by(
            Account.account_code,
            Account.account_name,
            AccountCategory.ifrs_category,
            Account.is_active,
        )
        .order_by(Account.account_code)
    ).all()

    for account_code, account_name, ifrs_category, is_active, debit, credit in rows:
        debit = Decimal(str(debit or 0))
        credit = Decimal(str(credit or 0))

        # Net the balance — TB shows one figure per account, not gross
        net = debit - credit
        if abs(net) < Decimal("0.01"):
            continue  # skip zero-balance accounts

        if net > 0:
            total_debit += net
            net_debit = net
            net_credit = Decimal("0")
        else:
            total_credit += abs(net)
            net_debit = Decimal("0")
            net_credit = abs(net)

        # If account_code is truncated (exactly 20 chars = VARCHAR limit),
        # hide it and show only account_name.
        display_code = account_code if len(account_code) < 20 else ""

        entry: dict[str, Any] = {
            "account_code": display_code,
            "account_name": account_name,
            "debit": _format_currency(net_debit) if net_debit else "",
            "credit": _format_currency(net_credit) if net_credit else "",
            "debit_raw": float(net_debit),
            "credit_raw": float(net_credit),
            "is_inactive": not is_active,
        }

        if ifrs_category == IFRSCategory.ASSETS:
            assets.append(entry)
        elif ifrs_category == IFRSCategory.LIABILITIES:
            liabilities.append(entry)
        elif ifrs_category == IFRSCategory.EQUITY:
            equity.append(entry)
        elif ifrs_category == IFRSCategory.REVENUE:
            revenue.append(entry)
        elif ifrs_category == IFRSCategory.EXPENSES:
            expenses.append(entry)
        else:
            balances.append(entry)

    return {
        "as_of_date": _format_date(ref_date),
        "as_of_date_iso": _iso_date(ref_date),
        "period_name": period.period_name if period else "No Period",
        "assets": assets,
        "liabilities": liabilities,
        "equity": equity,
        "revenue": revenue,
        "expenses": expenses,
        "other_balances": balances,
        "total_debit": _format_currency(total_debit),
        "total_credit": _format_currency(total_credit),
        "is_balanced": round(total_debit, 2) == round(total_credit, 2),
    }


def export_trial_balance_csv(
    organization_id: str,
    db: Session,
    as_of_date: str | None = None,
) -> str:
    """Export trial balance as CSV."""
    ctx = trial_balance_context(db, organization_id, as_of_date)
    headers = ["Category", "Account Code", "Account Name", "Debit", "Credit"]
    rows: list[list[str]] = []
    for section_name, section_key in [
        ("Assets", "assets"),
        ("Liabilities", "liabilities"),
        ("Equity", "equity"),
        ("Revenue", "revenue"),
        ("Expenses", "expenses"),
    ]:
        for item in ctx.get(section_key, []):
            rows.append(
                [
                    section_name,
                    item["account_code"],
                    item["account_name"],
                    str(item["debit_raw"]),
                    str(item["credit_raw"]),
                ]
            )
    rows.append(["", "", "TOTAL", ctx["total_debit"], ctx["total_credit"]])
    return _build_csv(headers, rows)
