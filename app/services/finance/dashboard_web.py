"""
Dashboard web view service.

Provides view-focused data for the IFRS dashboard.
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services import formatters as _fmt
from app.services.finance.dashboard import dashboard_service
from app.services.finance.platform.currency_context import get_currency_context
from app.services.finance.platform.org_context import org_context_service

logger = logging.getLogger(__name__)


def _resolve_currency_prefix(presentation_code: str, currency_context: dict) -> str:
    for currency in currency_context.get("currencies", []):
        if currency.get("code") == presentation_code:
            symbol = currency.get("symbol") or ""
            if symbol:
                return symbol
            break
    return f"{presentation_code} "


def _format_currency(amount: Decimal, currency_prefix: str) -> str:
    """Format amount with currency prefix using accounting parentheses for negatives."""
    if amount is not None:
        try:
            dec = Decimal(str(amount))
            if dec < 0:
                abs_formatted = _fmt.format_currency(abs(dec), show_symbol=False)
                return f"({currency_prefix}{abs_formatted})"
        except (ValueError, TypeError, ArithmeticError):
            pass
    return f"{currency_prefix}{_fmt.format_currency(amount, show_symbol=False)}"


def _resolve_year_selection(
    year_param: str | None,
    available_years: list[int],
) -> int | None:
    if year_param is None:
        return available_years[0] if available_years else None
    if year_param.strip().lower() == "all":
        return None
    try:
        year = int(year_param)
    except ValueError:
        return available_years[0] if available_years else None
    return (
        year
        if year in available_years
        else (available_years[0] if available_years else None)
    )


class DashboardWebService:
    """View service for IFRS dashboard web route."""

    @staticmethod
    def dashboard_context(
        db: Session, organization_id, year: str | None = None
    ) -> dict:
        currency_settings = org_context_service.get_currency_settings(
            db, organization_id
        )
        presentation_currency_code = currency_settings["presentation"]
        currency_context = get_currency_context(db, str(organization_id))
        currency_prefix = _resolve_currency_prefix(
            presentation_currency_code, currency_context
        )
        currency_zero = f"{currency_prefix}0.00"
        available_years = dashboard_service.get_available_years(db, organization_id)
        selected_year = _resolve_year_selection(year, available_years)
        stats = dashboard_service.get_stats(db, organization_id, year=selected_year)
        recent_journals = dashboard_service.get_recent_journals(
            db, organization_id, limit=10, year=selected_year
        )
        fiscal_periods = dashboard_service.get_fiscal_periods(
            db, organization_id, limit=8, year=selected_year
        )

        # Chart data
        monthly_trend = dashboard_service.get_monthly_revenue_expenses(
            db, organization_id, months=12, year=selected_year
        )
        account_balances = dashboard_service.get_account_balances_by_ifrs_category(
            db, organization_id, year=selected_year
        )
        top_customers = dashboard_service.get_top_customers(
            db, organization_id, limit=5, year=selected_year
        )
        top_suppliers = dashboard_service.get_top_suppliers(
            db, organization_id, limit=5, year=selected_year
        )
        cash_flow = dashboard_service.get_monthly_cash_flow(
            db, organization_id, months=6, year=selected_year
        )
        invoice_status = dashboard_service.get_invoice_status_breakdown(
            db, organization_id, year=selected_year
        )
        subledger_recon = dashboard_service.get_subledger_reconciliation(
            db, organization_id, year=selected_year
        )

        # Pre-computed analytics snapshot (None when stale or unavailable).
        from app.services.analytics.dashboard_metrics import DashboardMetricsService

        metrics_snapshot = DashboardMetricsService(db).get_org_snapshot(organization_id)

        # Bank balance — live total across active accounts (not year-filtered).
        from app.models.finance.banking.bank_account import (
            BankAccount,
            BankAccountStatus,
        )

        active_accounts = list(
            db.scalars(
                select(BankAccount).where(
                    BankAccount.organization_id == organization_id,
                    BankAccount.status == BankAccountStatus.active,
                )
            ).all()
        )
        total_bank_balance = sum(
            (a.last_statement_balance or Decimal("0") for a in active_accounts),
            Decimal("0"),
        )

        stats_view = {
            "total_revenue": _format_currency(stats.total_revenue, currency_prefix),
            "total_expenses": _format_currency(stats.total_expenses, currency_prefix),
            "net_income": _format_currency(stats.net_income, currency_prefix),
            "cogs_spend": _format_currency(stats.cogs_spend, currency_prefix),
            "opex_spend": _format_currency(stats.opex_spend, currency_prefix),
            "ar_control_balance": _format_currency(
                stats.ar_control_balance, currency_prefix
            ),
            "ap_control_balance": _format_currency(
                stats.ap_control_balance, currency_prefix
            ),
            "net_ar_ap": _format_currency(
                stats.ar_control_balance - stats.ap_control_balance,
                currency_prefix,
            ),
            "cash_inflow": _format_currency(stats.cash_inflow, currency_prefix),
            "cash_outflow": _format_currency(stats.cash_outflow, currency_prefix),
            "net_cash_flow": _format_currency(stats.net_cash_flow, currency_prefix),
            "bank_balance": _format_currency(total_bank_balance, currency_prefix),
            # Aging data
            "aging_current": _format_currency(stats.aging_current, currency_prefix),
            "aging_30": _format_currency(stats.aging_30, currency_prefix),
            "aging_60": _format_currency(stats.aging_60, currency_prefix),
            "aging_90": _format_currency(stats.aging_90, currency_prefix),
            "aging_current_pct": stats.aging_current_pct,
            "aging_30_pct": stats.aging_30_pct,
            "aging_60_pct": stats.aging_60_pct,
            "aging_90_pct": stats.aging_90_pct,
            # Trend data
            "revenue_trend": stats.revenue_trend,
            "income_trend": stats.income_trend,
            "expenses_trend": stats.expenses_trend,
            "cash_flow_trend": stats.cash_flow_trend,
        }
        subledger_recon_view = {
            "ar_ok": subledger_recon["ar_ok"],
            "ap_ok": subledger_recon["ap_ok"],
            "ar_diff": _format_currency(subledger_recon["ar_diff"], currency_prefix),
            "ap_diff": _format_currency(subledger_recon["ap_diff"], currency_prefix),
        }

        journals_view = [
            {
                "id": journal.entry_number,  # For link generation
                "entry_number": journal.entry_number,
                "entry_date": journal.entry_date,
                "description": journal.description,
                "total_debit": _format_currency(journal.total_debit, currency_prefix),
                "status": journal.status,
            }
            for journal in recent_journals
        ]

        periods_view = [
            {
                "period_name": period.period_name,
                "start_date": period.start_date,
                "end_date": period.end_date,
                "status": period.status,
            }
            for period in fiscal_periods
        ]

        # Prepare chart data as JSON for JavaScript
        chart_data = {
            "monthly_trend": monthly_trend,
            "account_balances": account_balances,
            "top_customers": top_customers,
            "top_suppliers": top_suppliers,
            "cash_flow": cash_flow,
            "invoice_status": invoice_status,
        }

        return {
            "stats": stats_view,
            "recent_journals": journals_view,
            "fiscal_periods": periods_view,
            "chart_data": chart_data,
            "chart_data_json": json.dumps(chart_data),
            "currency_prefix": currency_prefix,
            "currency_zero": currency_zero,
            "presentation_currency_code": presentation_currency_code,
            "available_years": available_years,
            "selected_year": selected_year,
            "selected_year_label": str(selected_year) if selected_year else "All years",
            "subledger_reconciliation": subledger_recon_view,
            "metrics_snapshot": metrics_snapshot,
        }


dashboard_web_service = DashboardWebService()
