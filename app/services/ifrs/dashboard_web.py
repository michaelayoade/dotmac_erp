"""
Dashboard web view service.

Provides view-focused data for the IFRS dashboard.
"""

from __future__ import annotations

import json
from decimal import Decimal

from sqlalchemy.orm import Session

from app.services.ifrs.dashboard import dashboard_service
from app.services.ifrs.platform.org_context import org_context_service


def _format_currency(amount: Decimal, currency_code: str) -> str:
    return f"{currency_code} {amount:,.2f}"


class DashboardWebService:
    """View service for IFRS dashboard web route."""

    @staticmethod
    def dashboard_context(db: Session, organization_id) -> dict:
        currency_settings = org_context_service.get_currency_settings(db, organization_id)
        presentation_currency_code = currency_settings["presentation"]
        stats = dashboard_service.get_stats(db, organization_id)
        recent_journals = dashboard_service.get_recent_journals(db, organization_id, limit=10)
        fiscal_periods = dashboard_service.get_fiscal_periods(db, organization_id, limit=8)

        # Chart data
        monthly_trend = dashboard_service.get_monthly_revenue_expenses(db, organization_id, months=12)
        account_balances = dashboard_service.get_account_balances_by_ifrs_category(db, organization_id)
        top_customers = dashboard_service.get_top_customers(db, organization_id, limit=5)
        top_suppliers = dashboard_service.get_top_suppliers(db, organization_id, limit=5)
        cash_flow = dashboard_service.get_monthly_cash_flow(db, organization_id, months=6)
        invoice_status = dashboard_service.get_invoice_status_breakdown(db, organization_id)

        stats_view = {
            "total_revenue": _format_currency(stats.total_revenue, presentation_currency_code),
            "total_expenses": _format_currency(stats.total_expenses, presentation_currency_code),
            "net_income": _format_currency(stats.net_income, presentation_currency_code),
            "open_invoices": stats.open_invoices,
            "pending_amount": _format_currency(stats.pending_amount, presentation_currency_code),
        }

        journals_view = [
            {
                "id": journal.entry_number,  # For link generation
                "entry_number": journal.entry_number,
                "entry_date": journal.entry_date,
                "description": journal.description,
                "total_debit": _format_currency(journal.total_debit, presentation_currency_code),
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
            "presentation_currency_code": presentation_currency_code,
        }


dashboard_web_service = DashboardWebService()
