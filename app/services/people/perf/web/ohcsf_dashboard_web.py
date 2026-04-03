"""
OHCSF PMS Dashboard Web Service.

Builds template context for the PMS compliance dashboard, showing
active cycle stats, contract signing rates, review completion, and
rating distribution.
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.models.finance.core_org.organization import Organization
from app.services.common import coerce_uuid
from app.services.people.perf.performance_mode_policy import (
    get_policy_profile_for_mode,
    resolve_performance_mode,
)
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

logger = logging.getLogger(__name__)


class OHCSFDashboardWebService:
    """Web service for the OHCSF PMS compliance dashboard."""

    def dashboard_response(
        self, request: Request, auth: WebAuthContext, db: Session
    ) -> HTMLResponse:
        """Render the PMS compliance dashboard."""
        org_id: UUID = coerce_uuid(auth.organization_id)
        context = base_context(request, auth, "PMS Dashboard", "pms-dashboard", db=db)
        org = db.get(Organization, org_id)
        policy = get_policy_profile_for_mode(resolve_performance_mode(org).value)

        from sqlalchemy import select

        from app.models.people.perf.appraisal_cycle import (
            AppraisalCycle,
            AppraisalCycleStatus,
        )
        from app.services.people.perf.ohcsf_reporting_service import (
            OHCSFReportingService,
        )

        # Find the active annual cycle (most recent if multiple)
        active_cycle = db.scalar(
            select(AppraisalCycle)
            .where(
                AppraisalCycle.organization_id == org_id,
                AppraisalCycle.status
                == AppraisalCycleStatus(policy.active_cycle_status),
                AppraisalCycle.cycle_type == policy.active_cycle_type,
            )
            .order_by(AppraisalCycle.start_date.desc())
        )

        reporting = OHCSFReportingService(db)

        if active_cycle:
            try:
                compliance = reporting.cycle_compliance_dashboard(
                    org_id, active_cycle.cycle_id
                )
            except Exception:
                logger.exception(
                    "Failed to load compliance dashboard for cycle %s",
                    active_cycle.cycle_id,
                )
                compliance = {}

            try:
                rating_dist = reporting.rating_summary(org_id, active_cycle.cycle_id)
            except Exception:
                logger.exception(
                    "Failed to load rating summary for cycle %s",
                    active_cycle.cycle_id,
                )
                rating_dist = {"ratings": [], "total": 0}
        else:
            compliance = {}
            rating_dist = {"ratings": [], "total": 0}

        context.update(
            {
                "active_cycle": active_cycle,
                "compliance": compliance,
                "rating_distribution": rating_dist,
            }
        )

        return templates.TemplateResponse(
            request, "people/perf/pms/dashboard.html", context
        )
