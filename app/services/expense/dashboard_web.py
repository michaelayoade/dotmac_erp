"""Public expense dashboard facade composed from focused mixin modules."""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from uuid import UUID

from fastapi import Request
from fastapi.responses import HTMLResponse

from app.config import settings
from app.services.common import coerce_uuid
from app.services.expense.dashboard_charts import ExpenseDashboardChartsMixin
from app.services.expense.dashboard_common import _format_currency
from app.services.expense.dashboard_stats import ExpenseDashboardStatsMixin
from app.templates import templates

logger = logging.getLogger(__name__)


class ExpenseDashboardService(ExpenseDashboardChartsMixin, ExpenseDashboardStatsMixin):
    """Service facade for Expense module dashboard pages."""

    def _resolve_currency(self, db, org_id: UUID) -> str:
        """Resolve the org's presentation currency instead of hardcoding."""
        from app.services.finance.platform.org_context import org_context_service

        currency_settings = org_context_service.get_currency_settings(db, org_id)
        return currency_settings.get(
            "presentation", settings.default_presentation_currency_code
        )

    def dashboard_response(
        self, request: Request, auth, db, period: str = "month"
    ) -> HTMLResponse:
        from app.web.deps import base_context

        org_id = coerce_uuid(auth.organization_id)
        today = date.today()
        if period == "month":
            start_date = today.replace(day=1)
        elif period == "quarter":
            quarter_start_month = ((today.month - 1) // 3) * 3 + 1
            start_date = today.replace(month=quarter_start_month, day=1)
        elif period == "year":
            start_date = today.replace(month=1, day=1)
        else:
            start_date = None

        currency = self._resolve_currency(db, org_id)
        context = {
            **base_context(request, auth, "Expense Dashboard", "dashboard"),
            "stats": self._get_dashboard_stats(db, org_id, start_date, currency),
            "chart_data": self._get_chart_data(db, org_id, start_date),
            "recent_claims": self._get_recent_claims(
                db, org_id, limit=5, currency=currency
            ),
            "selected_period": period,
            "currency_zero": _format_currency(Decimal(0), currency),
            "presentation_currency_code": currency,
        }

        try:
            from app.services.coach.coach_service import CoachService

            coach_svc = CoachService(db)
            context["coach_insights"] = (
                coach_svc.top_insights_for_module(org_id, ["EFFICIENCY", "COMPLIANCE"])
                if coach_svc.is_enabled()
                else []
            )
        except (ImportError, AttributeError, ValueError) as e:
            logger.warning("Coach service unavailable for expense dashboard: %s", e)
            context["coach_insights"] = []
        except Exception:
            logger.exception(
                "Unexpected error loading coach insights for expense dashboard"
            )
            context["coach_insights"] = []

        return templates.TemplateResponse(request, "expense/dashboard.html", context)

    def claims_dashboard_response(
        self, request: Request, auth, db, period: str = "month"
    ) -> HTMLResponse:
        from app.web.deps import base_context

        org_id = coerce_uuid(auth.organization_id)
        today = date.today()
        if period == "month":
            start_date = today.replace(day=1)
        elif period == "quarter":
            quarter_start_month = ((today.month - 1) // 3) * 3 + 1
            start_date = today.replace(month=quarter_start_month, day=1)
        elif period == "year":
            start_date = today.replace(month=1, day=1)
        else:
            start_date = None

        currency = self._resolve_currency(db, org_id)
        context = {
            **base_context(request, auth, "Expense Claims", "claims"),
            "stats": self._get_claims_stats(db, org_id, start_date, currency),
            "chart_data": self._get_claims_chart_data(db, org_id, start_date),
            "recent_claims": self._get_recent_claims_detailed(
                db, org_id, limit=8, currency=currency
            ),
            "selected_period": period,
            "currency_zero": _format_currency(Decimal(0), currency),
            "presentation_currency_code": currency,
        }
        return templates.TemplateResponse(
            request, "expense/claims_dashboard.html", context
        )


expense_dashboard_service = ExpenseDashboardService()
