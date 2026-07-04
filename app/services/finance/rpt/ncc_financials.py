"""NCC year-end financials (Section F).

Composes the NCC return's financial figures from erp's existing statement
services — income statement, balance sheet, and expense summary — into one
org-scoped payload, so the regulatory-pack aggregator makes a single call.

Figures use the erp chart-of-accounts categorization; mapping expense/asset
lines to NCC's exact buckets (Personnel/Interconnection/Energy/... ;
Network/Transmission/... ) is applied downstream / by a later seeding step.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session


def ncc_financials_context(
    db: Session,
    organization_id: str | UUID,
    *,
    year: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    as_of_date: str | None = None,
) -> dict:
    """Build the NCC Section F financials for an organization.

    ``year`` is a convenience for the annual return — it fills the P&L date
    range and the balance-sheet ``as_of`` to that calendar year unless explicit
    dates are given.
    """
    from app.services.finance.rpt.balance_sheet import balance_sheet_context
    from app.services.finance.rpt.expense_summary import expense_summary_context
    from app.services.finance.rpt.income_statement import income_statement_context

    if year:
        start_date = start_date or f"{year}-01-01"
        end_date = end_date or f"{year}-12-31"
        as_of_date = as_of_date or f"{year}-12-31"

    org = str(organization_id)
    inc = income_statement_context(db, org, start_date, end_date)
    bs = balance_sheet_context(db, org, as_of_date)
    exp = expense_summary_context(db, org, start_date, end_date)

    return {
        "period": {
            "year": year,
            "income_statement": {
                "start": inc.get("start_date_iso"),
                "end": inc.get("end_date_iso"),
                "name": inc.get("period_name"),
            },
            "balance_sheet_as_of": bs.get("as_of_date_iso"),
        },
        "summary": {
            "total_revenue": inc.get("total_revenue"),
            "total_operating_expenses": exp.get("total_expenses"),
            "total_operating_expenses_raw": exp.get("total_expenses_raw"),
            "net_income": inc.get("net_income"),
            "net_income_raw": inc.get("net_income_raw"),
            "total_assets": bs.get("total_assets"),
            "total_liabilities": bs.get("total_liabilities"),
            "total_equity": bs.get("total_equity"),
            "is_balanced": bs.get("is_balanced"),
        },
        "detail": {
            "income_statement_lines": inc.get("income_statement_lines"),
            "current_assets": bs.get("current_assets"),
            "non_current_assets": bs.get("non_current_assets"),
            "current_liabilities": bs.get("current_liabilities"),
            "non_current_liabilities": bs.get("non_current_liabilities"),
            "equity": bs.get("equity"),
            "expense_by_category": exp.get("expense_items"),
        },
        "note": (
            "Figures use the erp chart-of-accounts categorization; mapping to NCC's "
            "specific cost/asset buckets is applied downstream."
        ),
    }
