"""Balance sheet (Statement of Financial Position) context builder and CSV export."""

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


def balance_sheet_context(
    db: Session,
    organization_id: str,
    as_of_date: str | None = None,
) -> dict[str, Any]:
    """Get context for balance sheet report."""
    org_id = coerce_uuid(organization_id)
    ref_date = _parse_date(as_of_date) or date.today()

    # Find fiscal period
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

    balances = _category_balances(
        db=db,
        organization_id=organization_id,
        as_of_date=ref_date,
    )

    def cat_amount(code: str) -> Decimal:
        return cast(Decimal, balances.get(code, {}).get("amount", Decimal("0")))

    current_assets = [
        ("Cash and Cash Equivalents", cat_amount("CASH") + cat_amount("BANK")),
        ("Accounts Receivable", cat_amount("AR")),
        ("Inventory", cat_amount("INV")),
        ("Other Current Assets", cat_amount("AST") + cat_amount("ASSETS")),
    ]
    non_current_assets = [
        ("Property, Plant and Equipment", cat_amount("FA") + cat_amount("FA-AD")),
    ]
    current_liabilities = [
        ("Accounts Payable", cat_amount("AP")),
        ("Tax Liabilities", cat_amount("TAX-L")),
        ("Other Current Liabilities", cat_amount("LIA")),
    ]
    non_current_liabilities = [
        ("Long-term Liabilities", cat_amount("LTL")),
    ]
    equity_lines = [
        ("Share Capital", cat_amount("EQ")),
        ("Retained Earnings", cat_amount("RE")),
        ("Other Equity", cat_amount("EQT")),
    ]

    total_assets = sum((amount for _, amount in current_assets), Decimal("0")) + sum(
        (amount for _, amount in non_current_assets), Decimal("0")
    )
    total_liabilities = sum(
        (amount for _, amount in current_liabilities), Decimal("0")
    ) + sum((amount for _, amount in non_current_liabilities), Decimal("0"))
    total_equity = sum((amount for _, amount in equity_lines), Decimal("0"))
    total_liabilities_equity = total_liabilities + total_equity

    balance_sheet_lines = {
        "current_assets": [
            {
                "name": name,
                "amount": _format_currency(amount),
                "amount_raw": float(amount),
            }
            for name, amount in current_assets
        ],
        "non_current_assets": [
            {
                "name": name,
                "amount": _format_currency(amount),
                "amount_raw": float(amount),
            }
            for name, amount in non_current_assets
        ],
        "current_liabilities": [
            {
                "name": name,
                "amount": _format_currency(amount),
                "amount_raw": float(amount),
            }
            for name, amount in current_liabilities
        ],
        "non_current_liabilities": [
            {
                "name": name,
                "amount": _format_currency(amount),
                "amount_raw": float(amount),
            }
            for name, amount in non_current_liabilities
        ],
        "equity": [
            {
                "name": name,
                "amount": _format_currency(amount),
                "amount_raw": float(amount),
            }
            for name, amount in equity_lines
        ],
    }

    return {
        "as_of_date": _format_date(ref_date),
        "as_of_date_iso": _iso_date(ref_date),
        "period_name": period.period_name if period else "No Period",
        "balance_sheet_lines": balance_sheet_lines,
        "total_assets": _format_currency(total_assets),
        "total_liabilities": _format_currency(total_liabilities),
        "total_equity": _format_currency(total_equity),
        "total_liabilities_equity": _format_currency(total_liabilities_equity),
        "is_balanced": round(total_assets, 2) == round(total_liabilities_equity, 2),
    }


def export_balance_sheet_csv(
    organization_id: str,
    db: Session,
    as_of_date: str | None = None,
) -> str:
    """Export balance sheet as CSV."""
    ctx = balance_sheet_context(db, organization_id, as_of_date)
    headers = ["Section", "Line Item", "Amount"]
    rows: list[list[str]] = []
    for section_name, section_key in [
        ("Current Assets", "current_assets"),
        ("Non-Current Assets", "non_current_assets"),
        ("Current Liabilities", "current_liabilities"),
        ("Non-Current Liabilities", "non_current_liabilities"),
        ("Equity", "equity"),
    ]:
        for item in ctx.get("balance_sheet_lines", {}).get(section_key, []):
            rows.append([section_name, item["name"], str(item["amount_raw"])])
    rows.append(["", "Total Assets", ctx["total_assets"]])
    rows.append(["", "Total Liabilities", ctx["total_liabilities"]])
    rows.append(["", "Total Equity", ctx["total_equity"]])
    return _build_csv(headers, rows)
