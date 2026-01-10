"""
Dashboard web view service.

Provides view-focused data for the IFRS dashboard.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from app.services.ifrs.dashboard import dashboard_service


def _format_currency(amount: Decimal, currency: str = "USD") -> str:
    if currency == "USD":
        return f"${amount:,.2f}"
    return f"{currency} {amount:,.2f}"


class DashboardWebService:
    """View service for IFRS dashboard web route."""

    @staticmethod
    def dashboard_context(db: Session, organization_id) -> dict:
        stats = dashboard_service.get_stats(db, organization_id)
        recent_journals = dashboard_service.get_recent_journals(db, organization_id, limit=10)
        fiscal_periods = dashboard_service.get_fiscal_periods(db, organization_id, limit=8)

        stats_view = {
            "total_revenue": _format_currency(stats.total_revenue),
            "total_expenses": _format_currency(stats.total_expenses),
            "net_income": _format_currency(stats.net_income),
            "open_invoices": stats.open_invoices,
            "pending_amount": _format_currency(stats.pending_amount),
        }

        journals_view = [
            {
                "entry_number": journal.entry_number,
                "entry_date": journal.entry_date,
                "description": journal.description,
                "total_debit": _format_currency(journal.total_debit),
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

        return {
            "stats": stats_view,
            "recent_journals": journals_view,
            "fiscal_periods": periods_view,
        }


dashboard_web_service = DashboardWebService()
