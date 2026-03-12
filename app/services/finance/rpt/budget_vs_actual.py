"""Budget vs actual report context builder."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.finance.gl.account import Account
from app.models.finance.gl.fiscal_period import FiscalPeriod
from app.models.finance.gl.journal_entry import JournalEntry, JournalStatus
from app.models.finance.gl.journal_entry_line import JournalEntryLine
from app.services.common import coerce_uuid
from app.services.finance.rpt.common import (
    _format_currency,
    _format_date,
    _iso_date,
    _parse_date,
)


def budget_vs_actual_context(
    db: Session,
    organization_id: str,
    start_date: str | None = None,
    end_date: str | None = None,
    budget_id: str | None = None,
    budget_code: str | None = None,
) -> dict[str, Any]:
    """Get context for budget vs actual report."""
    from app.models.finance.gl.budget import Budget, BudgetStatus
    from app.models.finance.gl.budget_line import BudgetLine

    org_id = coerce_uuid(organization_id)
    today = date.today()
    from_date = _parse_date(start_date) or today.replace(day=1)
    to_date = _parse_date(end_date) or today

    periods = db.scalars(
        select(FiscalPeriod).where(
            FiscalPeriod.organization_id == org_id,
            FiscalPeriod.start_date <= to_date,
            FiscalPeriod.end_date >= from_date,
        )
    ).all()
    period_ids = [p.fiscal_period_id for p in periods]

    budget_stmt = (
        select(BudgetLine, Budget, Account)
        .join(Budget, BudgetLine.budget_id == Budget.budget_id)
        .join(Account, BudgetLine.account_id == Account.account_id)
        .where(
            Budget.organization_id == org_id,
            Budget.status.in_([BudgetStatus.APPROVED, BudgetStatus.ACTIVE]),
            BudgetLine.fiscal_period_id.in_(period_ids),
        )
    )

    if budget_id:
        budget_stmt = budget_stmt.where(Budget.budget_id == coerce_uuid(budget_id))
    if budget_code:
        budget_stmt = budget_stmt.where(Budget.budget_code == budget_code)

    budget_lines = db.execute(budget_stmt).all()

    budget_totals: dict[UUID, dict[str, Any]] = {}
    for line, _budget, account in budget_lines:
        budget_totals.setdefault(
            account.account_id,
            {
                "account_code": account.account_code,
                "account_name": account.account_name,
                "budget": Decimal("0"),
                "normal_balance": account.normal_balance.value,
            },
        )
        budget_totals[account.account_id]["budget"] += Decimal(
            str(line.budget_amount or 0)
        )

    account_ids = list(budget_totals.keys())
    actual_rows: list[Any] = []
    if account_ids:
        actual_rows = list(
            db.execute(
                select(
                    JournalEntryLine.account_id,
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
                .where(
                    JournalEntry.organization_id == org_id,
                    JournalEntry.status == JournalStatus.POSTED,
                    JournalEntry.posting_date >= from_date,
                    JournalEntry.posting_date <= to_date,
                    JournalEntryLine.account_id.in_(account_ids),
                )
                .group_by(JournalEntryLine.account_id)
            ).all()
        )

    actual_map = {row.account_id: row for row in actual_rows}

    rows: list[dict[str, Any]] = []
    total_budget = Decimal("0")
    total_actual = Decimal("0")

    for account_id, data in budget_totals.items():
        actual_row = actual_map.get(account_id)
        debit = Decimal(str(actual_row.debit or 0)) if actual_row else Decimal("0")
        credit = Decimal(str(actual_row.credit or 0)) if actual_row else Decimal("0")
        if data["normal_balance"] == "DEBIT":
            actual = debit - credit
        else:
            actual = credit - debit

        budget = data["budget"]
        variance = actual - budget
        variance_pct = (variance / budget * Decimal("100")) if budget else Decimal("0")

        total_budget += budget
        total_actual += actual

        rows.append(
            {
                "account_code": data["account_code"],
                "account_name": data["account_name"],
                "budget": _format_currency(budget),
                "actual": _format_currency(actual),
                "variance": _format_currency(variance),
                "variance_percent": f"{variance_pct:.2f}%",
                "variance_raw": float(variance),
            }
        )

    rows.sort(key=lambda x: x["account_code"])
    total_variance = total_actual - total_budget

    # Fetch budgets for dropdown
    from app.models.finance.gl.budget import Budget as BudgetModel

    budget_options = [
        {
            "budget_id": str(b.budget_id),
            "budget_code": b.budget_code,
            "budget_name": b.budget_name,
            "status": b.status.value if b.status else "",
        }
        for b in db.scalars(
            select(BudgetModel)
            .where(BudgetModel.organization_id == org_id)
            .order_by(BudgetModel.budget_code)
        ).all()
    ]

    return {
        "start_date": _format_date(from_date),
        "start_date_iso": _iso_date(from_date),
        "end_date": _format_date(to_date),
        "end_date_iso": _iso_date(to_date),
        "budget_id": budget_id or "",
        "budget_code": budget_code or "",
        "budgets": budget_options,
        "budget_lines": rows,
        "total_budget": _format_currency(total_budget),
        "total_actual": _format_currency(total_actual),
        "total_variance": _format_currency(total_variance),
        "total_variance_raw": float(total_variance),
    }
