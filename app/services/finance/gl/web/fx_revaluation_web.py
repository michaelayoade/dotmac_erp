"""GL FX Revaluation Web Service - thin context builder.

Wraps :class:`FXRevaluationService` for the FX revaluation web routes.
``preview_response`` builds a template context dict; ``post_response``
delegates to the service and returns the result so the route can flash
messages and redirect.

Per ``.claude/rules/patterns.md`` web services live under
``app/services/*/web/*.py`` and may import ``app.web.deps``; this module
intentionally does neither right now — the wrapper is purely a delegation
seam so the route can stay thin.
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.orm import Session

from app.services.finance.gl.fx_revaluation import (
    FXRevaluationResult,
    FXRevaluationService,
)

logger = logging.getLogger(__name__)


class FXRevaluationWebService:
    """Web-layer wrapper for :class:`FXRevaluationService`."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def preview_response(
        self,
        organization_id: UUID,
        fiscal_period_id: UUID,
    ) -> dict:
        """Run a read-only FX revaluation preview and return a template
        context dict.

        Keys returned (consumed by the FX revaluation preview template):
          - ``preview`` — the full :class:`FXRevaluationPreview`
          - ``period_id`` — the fiscal period UUID (for form actions)
          - ``prior_run_exists`` — bool flag for the "reason required" UX
          - ``lines`` — per-(account, currency) revaluation observations
          - ``warnings`` — human-readable warnings (e.g. missing rates)
          - ``rates_used`` — closing rates by currency
          - ``total_gain`` / ``total_loss`` — functional-currency totals
          - ``next_period_start_date`` — for the reversal journal date
        """
        service = FXRevaluationService(self.db)
        preview = service.preview(organization_id, fiscal_period_id)

        return {
            "preview": preview,
            "period_id": fiscal_period_id,
            "prior_run_exists": preview.prior_run_exists,
            "lines": preview.lines,
            "warnings": preview.warnings,
            "rates_used": preview.rates_used,
            "total_gain": preview.total_gain_functional,
            "total_loss": preview.total_loss_functional,
            "next_period_start_date": preview.next_period_start_date,
        }

    def post_response(
        self,
        organization_id: UUID,
        fiscal_period_id: UUID,
        user_id: UUID,
        reason: str | None,
    ) -> FXRevaluationResult:
        """Atomically post the period-end FX revaluation pair.

        Returns the :class:`FXRevaluationResult` so the route can flash
        success/error messages and redirect appropriately.
        """
        service = FXRevaluationService(self.db)
        return service.post(organization_id, fiscal_period_id, user_id, reason)
