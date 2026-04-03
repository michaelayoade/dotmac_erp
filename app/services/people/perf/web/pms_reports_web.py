"""PMS Reports Web Service."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

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


class PMSReportsWebService:
    """Web service for OHCSF PMS reports."""

    ReportData = dict[str, Any] | list[dict[str, Any]]

    def reports_hub_response(
        self, request: Request, auth: WebAuthContext, db: Session
    ) -> HTMLResponse:
        context = base_context(request, auth, "PMS Reports", "pms-reports", db=db)
        return templates.TemplateResponse(
            request, "people/perf/pms/reports.html", context
        )

    def report_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        report_type: str,
    ) -> HTMLResponse:
        org_id = coerce_uuid(auth.organization_id)
        context = base_context(request, auth, "PMS Report", "pms-reports", db=db)
        org = db.get(Organization, org_id)
        policy = get_policy_profile_for_mode(resolve_performance_mode(org).value)

        from app.services.people.perf.ohcsf_reporting_service import (
            OHCSFReportingService,
        )

        reporting = OHCSFReportingService(db)

        # Find active cycle
        from sqlalchemy import select

        from app.models.people.perf.appraisal_cycle import (
            AppraisalCycle,
            AppraisalCycleStatus,
        )

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

        report_data: PMSReportsWebService.ReportData = {}
        report_title = "Report"

        if active_cycle:
            cycle_id = active_cycle.cycle_id
            report_runners: dict[str, Callable[[], PMSReportsWebService.ReportData]] = {
                "rating-summary": lambda: reporting.rating_summary(org_id, cycle_id),
                "by-department": lambda: reporting.rating_by_department(org_id, cycle_id),
                "by-grade": lambda: reporting.rating_by_grade_level(org_id, cycle_id),
                "distribution": lambda: reporting.distribution_org_wide(org_id, cycle_id),
                "distribution-dept": lambda: reporting.distribution_by_department(
                    org_id, cycle_id
                ),
                "distribution-grade": lambda: reporting.distribution_by_grade(
                    org_id, cycle_id
                ),
                "top-performers": lambda: reporting.top_performers(org_id, cycle_id),
                "bottom-performers": lambda: reporting.bottom_performers(org_id, cycle_id),
                "development-needs": lambda: reporting.development_needs_overview(
                    org_id, cycle_id
                ),
                "development-dept": lambda: reporting.development_needs_by_department(
                    org_id, cycle_id
                ),
                "compliance": lambda: reporting.cycle_compliance_dashboard(org_id, cycle_id),
            }
            report_map: dict[
                str, tuple[str, Callable[[], PMSReportsWebService.ReportData]]
            ] = {
                key: (title, report_runners[key])
                for key, title in policy.mandatory_report_pack
                if key in report_runners
            }

            if report_type in report_map:
                report_title, report_fn = report_map[report_type]
                report_data = report_fn()

        context.update(
            {
                "report_type": report_type,
                "report_title": report_title,
                "report_data": report_data,
                "active_cycle": active_cycle,
            }
        )
        return templates.TemplateResponse(
            request, "people/perf/pms/report_detail.html", context
        )
