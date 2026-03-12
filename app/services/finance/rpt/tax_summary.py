"""Tax summary report context builder."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.services.finance.rpt.common import (
    _format_currency,
    _format_date,
    _iso_date,
    _parse_date,
    _tax_totals_from_gl,
)


def tax_summary_context(
    db: Session,
    organization_id: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """Get context for tax summary report."""
    today = date.today()
    from_date = _parse_date(start_date) or today.replace(day=1)
    to_date = _parse_date(end_date) or today

    tax_totals = _tax_totals_from_gl(
        db=db,
        organization_id=organization_id,
        start_date=from_date,
        end_date=to_date,
    )

    output_tax = tax_totals["output_tax"]
    input_tax = tax_totals["input_tax"]
    withholding = tax_totals["withholding"]
    net_tax = tax_totals["net_tax"]
    payments = Decimal("0")

    tax_breakdown = [
        {
            "tax_type": "Output Tax",
            "output": _format_currency(output_tax),
            "input": _format_currency(Decimal("0")),
            "net": _format_currency(output_tax),
            "net_raw": float(output_tax),
        },
        {
            "tax_type": "Input Tax",
            "output": _format_currency(Decimal("0")),
            "input": _format_currency(input_tax),
            "net": _format_currency(-input_tax),
            "net_raw": float(-input_tax),
        },
        {
            "tax_type": "Withholding Tax",
            "output": _format_currency(Decimal("0")),
            "input": _format_currency(withholding),
            "net": _format_currency(-withholding),
            "net_raw": float(-withholding),
        },
    ]

    upcoming_deadlines: list[dict[str, Any]] = []

    return {
        "start_date": _format_date(from_date),
        "start_date_iso": _iso_date(from_date),
        "end_date": _format_date(to_date),
        "end_date_iso": _iso_date(to_date),
        "output_tax": _format_currency(output_tax),
        "output_tax_raw": float(output_tax),
        "input_tax": _format_currency(input_tax),
        "input_tax_raw": float(input_tax),
        "net_tax": _format_currency(net_tax),
        "net_tax_raw": float(net_tax),
        "is_payable": net_tax > 0,
        "withholding": _format_currency(withholding),
        "payments": _format_currency(payments),
        "tax_breakdown": tax_breakdown,
        "upcoming_deadlines": upcoming_deadlines,
    }
