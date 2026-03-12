"""Income statement (Statement of Profit or Loss) context builder and CSV export."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.gl.fiscal_period import FiscalPeriod
from app.services.common import coerce_uuid
from app.services.finance.rpt.common import (
    _build_csv,
    _category_balances,
    _format_currency,
    _format_date,
    _iso_date,
    _parse_date,
)


def income_statement_context(
    db: Session,
    organization_id: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """Get context for income statement report."""
    org_id = coerce_uuid(organization_id)

    # Default to current month
    today = date.today()
    from_date = _parse_date(start_date) or today.replace(day=1)
    to_date = _parse_date(end_date) or today

    # Find fiscal period
    period = db.scalars(
        select(FiscalPeriod)
        .where(
            FiscalPeriod.organization_id == org_id,
            FiscalPeriod.start_date <= to_date,
            FiscalPeriod.end_date >= from_date,
        )
        .order_by(FiscalPeriod.start_date.desc())
    ).first()

    balances = _category_balances(
        db=db,
        organization_id=organization_id,
        start_date=from_date,
        end_date=to_date,
    )

    def cat_amount(code: str) -> Decimal:
        return cast(Decimal, balances.get(code, {}).get("amount", Decimal("0")))

    revenue = cat_amount("REV")
    other_income = Decimal("0")
    cogs = cat_amount("COS")
    operating_expenses = cat_amount("EXP")
    profit_for_period = revenue + other_income - cogs - operating_expenses
    oci = cat_amount("OCI")
    total_comprehensive_income = profit_for_period + oci

    income_statement_lines = [
        {
            "name": "Revenue",
            "amount": _format_currency(revenue),
            "amount_raw": float(revenue),
        },
        {
            "name": "Other Income",
            "amount": _format_currency(other_income),
            "amount_raw": float(other_income),
        },
        {
            "name": "Cost of Sales",
            "amount": _format_currency(cogs),
            "amount_raw": float(cogs),
        },
        {
            "name": "Operating Expenses",
            "amount": _format_currency(operating_expenses),
            "amount_raw": float(operating_expenses),
        },
        {
            "name": "Profit for the Period",
            "amount": _format_currency(profit_for_period),
            "amount_raw": float(profit_for_period),
        },
        {
            "name": "Other Comprehensive Income",
            "amount": _format_currency(oci),
            "amount_raw": float(oci),
        },
        {
            "name": "Total Comprehensive Income",
            "amount": _format_currency(total_comprehensive_income),
            "amount_raw": float(total_comprehensive_income),
        },
    ]

    return {
        "start_date": _format_date(from_date),
        "start_date_iso": _iso_date(from_date),
        "end_date": _format_date(to_date),
        "end_date_iso": _iso_date(to_date),
        "period_name": period.period_name if period else "No Period",
        "income_statement_lines": income_statement_lines,
        "total_revenue": _format_currency(revenue + other_income),
        "total_expenses": _format_currency(cogs + operating_expenses),
        "net_income": _format_currency(profit_for_period),
        "net_income_raw": float(profit_for_period),
        "is_profit": profit_for_period >= 0,
    }


def export_income_statement_csv(
    organization_id: str,
    db: Session,
    start_date: str | None = None,
    end_date: str | None = None,
) -> str:
    """Export income statement as CSV."""
    ctx = income_statement_context(db, organization_id, start_date, end_date)
    headers = ["Line Item", "Amount"]
    rows = [
        [item["name"], str(item["amount_raw"])]
        for item in ctx.get("income_statement_lines", [])
    ]
    return _build_csv(headers, rows)
