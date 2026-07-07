"""Performance management service implementation.

Handles appraisal cycles, KRAs, KPIs, appraisals, and scorecards.
Adapted from DotMac People for the unified ERP platform.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Any, TypedDict, cast
from uuid import UUID

from sqlalchemy import and_, delete, false, func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.models.people.perf import (
    DepartmentPerformanceTemplate,
    KPI,
    KRA,
    Appraisal,
    AppraisalCycle,
    AppraisalCycleStatus,
    AppraisalFeedback,
    AppraisalKRAScore,
    AppraisalStatus,
    AppraisalTemplate,
    AppraisalTemplateProfile,
    AppraisalTemplateKRA,
    KPIStatus,
    Scorecard,
    ScorecardItem,
)
from app.models.finance.core_org import Organization, PerformanceMode
from app.models.people.hr import Department
from app.models.people.hr.employee import Employee, EmployeeStatus
from app.models.people.perf.pip import PerformanceImprovementPlan
from app.models.people.perf.pms_enums import PIPStatus
from app.models.support.ticket import Ticket, TicketStatus
from app.services.common import PaginatedResult, PaginationParams
from app.services.people.perf.performance_mode_policy import (
    enforce_private_write_mode,
    resolve_performance_mode,
)

logger = logging.getLogger(__name__)
UNDERPERFORMANCE_SCORE_THRESHOLD = Decimal("50.00")
SUPPORT_TICKET_METRIC_KEYS = frozenset(
    {
        "support.tickets_resolved",
        "support.open_backlog",
        "support.resolution_rate",
        "support.avg_resolution_days",
    }
)
LOWER_IS_BETTER_SUPPORT_METRICS = frozenset(
    {
        "support.open_backlog",
        "support.avg_resolution_days",
    }
)
SCORECARD_PERSPECTIVES = frozenset({"FINANCIAL", "CUSTOMER", "PROCESS", "LEARNING"})
DEPARTMENT_PERSPECTIVE_WEIGHTS: dict[str, dict[str, Decimal]] = {
    "customer_experience": {
        "FINANCIAL": Decimal("10.00"),
        "CUSTOMER": Decimal("40.00"),
        "PROCESS": Decimal("35.00"),
        "LEARNING": Decimal("15.00"),
    },
    "sales": {
        "FINANCIAL": Decimal("50.00"),
        "CUSTOMER": Decimal("25.00"),
        "PROCESS": Decimal("15.00"),
        "LEARNING": Decimal("10.00"),
    },
    "projects": {
        "FINANCIAL": Decimal("10.00"),
        "CUSTOMER": Decimal("15.00"),
        "PROCESS": Decimal("60.00"),
        "LEARNING": Decimal("15.00"),
    },
    "procurement": {
        "FINANCIAL": Decimal("25.00"),
        "CUSTOMER": Decimal("15.00"),
        "PROCESS": Decimal("50.00"),
        "LEARNING": Decimal("10.00"),
    },
    "inventory": {
        "FINANCIAL": Decimal("15.00"),
        "CUSTOMER": Decimal("10.00"),
        "PROCESS": Decimal("60.00"),
        "LEARNING": Decimal("15.00"),
    },
    "finance": {
        "FINANCIAL": Decimal("40.00"),
        "CUSTOMER": Decimal("20.00"),
        "PROCESS": Decimal("30.00"),
        "LEARNING": Decimal("10.00"),
    },
    "hr": {
        "FINANCIAL": Decimal("10.00"),
        "CUSTOMER": Decimal("30.00"),
        "PROCESS": Decimal("40.00"),
        "LEARNING": Decimal("20.00"),
    },
    "generic": {
        "FINANCIAL": Decimal("10.00"),
        "CUSTOMER": Decimal("20.00"),
        "PROCESS": Decimal("50.00"),
        "LEARNING": Decimal("20.00"),
    },
}
LEARNING_TEMPLATE_DEFAULT = {
    "kra_name": "Learning and Growth",
    "kpi_name": "Complete Role Development Plan",
    "target_value": Decimal("100.00"),
    "unit_of_measure": "%",
    "weightage": Decimal("0.00"),
    "metric_source_key": "learning.development_plan_completion",
    "lower_is_better": False,
}
PERSPECTIVE_TEMPLATE_DEFAULTS: dict[str, dict[str, object]] = {
    "FINANCIAL": {
        "kra_name": "Financial Stewardship",
        "kpi_name": "Improve Cost and Value Contribution",
        "target_value": Decimal("100.00"),
        "unit_of_measure": "%",
        "weightage": Decimal("0.00"),
        "metric_source_key": None,
        "lower_is_better": False,
    },
    "CUSTOMER": {
        "kra_name": "Customer and Stakeholder Service",
        "kpi_name": "Maintain Stakeholder Service Quality",
        "target_value": Decimal("90.00"),
        "unit_of_measure": "%",
        "weightage": Decimal("0.00"),
        "metric_source_key": None,
        "lower_is_better": False,
    },
    "PROCESS": {
        "kra_name": "Internal Process Delivery",
        "kpi_name": "Complete Assigned Work On Time",
        "target_value": Decimal("95.00"),
        "unit_of_measure": "%",
        "weightage": Decimal("0.00"),
        "metric_source_key": None,
        "lower_is_better": False,
    },
    "LEARNING": LEARNING_TEMPLATE_DEFAULT,
}
DEPARTMENT_TEMPLATE_LIBRARY: dict[str, list[dict[str, object]]] = {
    "customer_experience": [
        {
            "kra_name": "Customer Support Delivery",
            "kpi_name": "Resolve Assigned Tickets",
            "target_value": Decimal("20.00"),
            "unit_of_measure": "tickets",
            "weightage": Decimal("40.00"),
            "metric_source_key": "support.tickets_resolved",
            "lower_is_better": False,
        },
        {
            "kra_name": "SLA Compliance",
            "kpi_name": "Meet Ticket SLA",
            "target_value": Decimal("95.00"),
            "unit_of_measure": "%",
            "weightage": Decimal("30.00"),
            "metric_source_key": "support.resolution_rate",
            "lower_is_better": False,
        },
        {
            "kra_name": "Backlog Control",
            "kpi_name": "Keep Open Backlog Low",
            "target_value": Decimal("5.00"),
            "unit_of_measure": "tickets",
            "weightage": Decimal("20.00"),
            "metric_source_key": "support.open_backlog",
            "lower_is_better": True,
        },
        {
            "kra_name": "Resolution Speed",
            "kpi_name": "Average Resolution Time",
            "target_value": Decimal("2.00"),
            "unit_of_measure": "days",
            "weightage": Decimal("10.00"),
            "metric_source_key": "support.avg_resolution_days",
            "lower_is_better": True,
        },
    ],
    "sales": [
        {
            "kra_name": "Revenue Growth",
            "kpi_name": "Achieve Sales Revenue Target",
            "target_value": Decimal("100.00"),
            "unit_of_measure": "%",
            "weightage": Decimal("45.00"),
            "metric_source_key": "sales.revenue_attainment",
            "lower_is_better": False,
        },
        {
            "kra_name": "Pipeline Development",
            "kpi_name": "Create Qualified Opportunities",
            "target_value": Decimal("10.00"),
            "unit_of_measure": "opportunities",
            "weightage": Decimal("25.00"),
            "metric_source_key": "sales.qualified_opportunities",
            "lower_is_better": False,
        },
        {
            "kra_name": "Deal Conversion",
            "kpi_name": "Improve Win Rate",
            "target_value": Decimal("30.00"),
            "unit_of_measure": "%",
            "weightage": Decimal("20.00"),
            "metric_source_key": "sales.win_rate",
            "lower_is_better": False,
        },
        {
            "kra_name": "Customer Relationship",
            "kpi_name": "Complete Account Reviews",
            "target_value": Decimal("5.00"),
            "unit_of_measure": "reviews",
            "weightage": Decimal("10.00"),
            "metric_source_key": "sales.account_reviews",
            "lower_is_better": False,
        },
    ],
    "projects": [
        {
            "kra_name": "Project Delivery",
            "kpi_name": "Complete Assigned Project Tasks",
            "target_value": Decimal("90.00"),
            "unit_of_measure": "%",
            "weightage": Decimal("40.00"),
            "metric_source_key": "projects.task_completion_rate",
            "lower_is_better": False,
        },
        {
            "kra_name": "Schedule Control",
            "kpi_name": "Deliver Milestones On Time",
            "target_value": Decimal("90.00"),
            "unit_of_measure": "%",
            "weightage": Decimal("30.00"),
            "metric_source_key": "projects.on_time_milestones",
            "lower_is_better": False,
        },
        {
            "kra_name": "Issue Resolution",
            "kpi_name": "Resolve Project Issues",
            "target_value": Decimal("95.00"),
            "unit_of_measure": "%",
            "weightage": Decimal("20.00"),
            "metric_source_key": "projects.issue_resolution_rate",
            "lower_is_better": False,
        },
        {
            "kra_name": "Quality Control",
            "kpi_name": "Limit Rework",
            "target_value": Decimal("5.00"),
            "unit_of_measure": "%",
            "weightage": Decimal("10.00"),
            "metric_source_key": "projects.rework_rate",
            "lower_is_better": True,
        },
    ],
    "procurement": [
        {
            "kra_name": "Purchase Order Processing",
            "kpi_name": "Process Purchase Orders On Time",
            "target_value": Decimal("95.00"),
            "unit_of_measure": "%",
            "weightage": Decimal("40.00"),
            "metric_source_key": "procurement.po_on_time_rate",
            "lower_is_better": False,
        },
        {
            "kra_name": "Supplier Performance",
            "kpi_name": "Maintain Supplier Delivery Compliance",
            "target_value": Decimal("90.00"),
            "unit_of_measure": "%",
            "weightage": Decimal("30.00"),
            "metric_source_key": "procurement.supplier_delivery_rate",
            "lower_is_better": False,
        },
        {
            "kra_name": "Cost Management",
            "kpi_name": "Achieve Procurement Savings",
            "target_value": Decimal("5.00"),
            "unit_of_measure": "%",
            "weightage": Decimal("20.00"),
            "metric_source_key": "procurement.savings_rate",
            "lower_is_better": False,
        },
        {
            "kra_name": "Request Fulfilment",
            "kpi_name": "Close Material Requests",
            "target_value": Decimal("95.00"),
            "unit_of_measure": "%",
            "weightage": Decimal("10.00"),
            "metric_source_key": "procurement.material_request_close_rate",
            "lower_is_better": False,
        },
    ],
    "inventory": [
        {
            "kra_name": "Inventory Accuracy",
            "kpi_name": "Maintain Stock Accuracy",
            "target_value": Decimal("98.00"),
            "unit_of_measure": "%",
            "weightage": Decimal("45.00"),
            "metric_source_key": "inventory.stock_accuracy",
            "lower_is_better": False,
        },
        {
            "kra_name": "Fulfilment",
            "kpi_name": "Fulfil Material Requests On Time",
            "target_value": Decimal("95.00"),
            "unit_of_measure": "%",
            "weightage": Decimal("30.00"),
            "metric_source_key": "inventory.material_fulfilment_rate",
            "lower_is_better": False,
        },
        {
            "kra_name": "Stock Control",
            "kpi_name": "Reduce Stock Variances",
            "target_value": Decimal("2.00"),
            "unit_of_measure": "%",
            "weightage": Decimal("15.00"),
            "metric_source_key": "inventory.variance_rate",
            "lower_is_better": True,
        },
        {
            "kra_name": "Cycle Counts",
            "kpi_name": "Complete Scheduled Cycle Counts",
            "target_value": Decimal("100.00"),
            "unit_of_measure": "%",
            "weightage": Decimal("10.00"),
            "metric_source_key": "inventory.cycle_count_completion",
            "lower_is_better": False,
        },
    ],
    "finance": [
        {
            "kra_name": "Billing Accuracy",
            "kpi_name": "Process Invoices Accurately",
            "target_value": Decimal("98.00"),
            "unit_of_measure": "%",
            "weightage": Decimal("35.00"),
            "metric_source_key": "finance.invoice_accuracy",
            "lower_is_better": False,
        },
        {
            "kra_name": "Collections",
            "kpi_name": "Achieve Collection Target",
            "target_value": Decimal("95.00"),
            "unit_of_measure": "%",
            "weightage": Decimal("30.00"),
            "metric_source_key": "finance.collection_rate",
            "lower_is_better": False,
        },
        {
            "kra_name": "Expense Control",
            "kpi_name": "Review Expense Claims On Time",
            "target_value": Decimal("95.00"),
            "unit_of_measure": "%",
            "weightage": Decimal("20.00"),
            "metric_source_key": "finance.expense_review_rate",
            "lower_is_better": False,
        },
        {
            "kra_name": "Reporting",
            "kpi_name": "Submit Reports On Time",
            "target_value": Decimal("100.00"),
            "unit_of_measure": "%",
            "weightage": Decimal("15.00"),
            "metric_source_key": "finance.report_timeliness",
            "lower_is_better": False,
        },
    ],
    "hr": [
        {
            "kra_name": "Employee Operations",
            "kpi_name": "Close HR Requests On Time",
            "target_value": Decimal("95.00"),
            "unit_of_measure": "%",
            "weightage": Decimal("35.00"),
            "metric_source_key": "hr.request_close_rate",
            "lower_is_better": False,
        },
        {
            "kra_name": "Hiring Support",
            "kpi_name": "Fill Approved Vacancies",
            "target_value": Decimal("90.00"),
            "unit_of_measure": "%",
            "weightage": Decimal("25.00"),
            "metric_source_key": "hr.vacancy_fill_rate",
            "lower_is_better": False,
        },
        {
            "kra_name": "Attendance Governance",
            "kpi_name": "Resolve Attendance Exceptions",
            "target_value": Decimal("95.00"),
            "unit_of_measure": "%",
            "weightage": Decimal("20.00"),
            "metric_source_key": "hr.attendance_exception_resolution",
            "lower_is_better": False,
        },
        {
            "kra_name": "Employee Experience",
            "kpi_name": "Maintain Employee Satisfaction",
            "target_value": Decimal("4.00"),
            "unit_of_measure": "rating",
            "weightage": Decimal("20.00"),
            "metric_source_key": "hr.employee_satisfaction",
            "lower_is_better": False,
        },
    ],
    "generic": [
        {
            "kra_name": "Operational Delivery",
            "kpi_name": "Complete Assigned Work",
            "target_value": Decimal("100.00"),
            "unit_of_measure": "%",
            "weightage": Decimal("50.00"),
            "metric_source_key": None,
            "lower_is_better": False,
        },
        {
            "kra_name": "Quality",
            "kpi_name": "Meet Quality Expectations",
            "target_value": Decimal("90.00"),
            "unit_of_measure": "%",
            "weightage": Decimal("30.00"),
            "metric_source_key": None,
            "lower_is_better": False,
        },
        {
            "kra_name": "Collaboration",
            "kpi_name": "Support Team Delivery",
            "target_value": Decimal("90.00"),
            "unit_of_measure": "%",
            "weightage": Decimal("20.00"),
            "metric_source_key": None,
            "lower_is_better": False,
        },
    ],
}

if TYPE_CHECKING:
    from app.web.deps import WebAuthContext

__all__ = ["PerformanceService"]


class PerformanceServiceError(Exception):
    """Base error for performance service."""

    pass


class AppraisalCycleNotFoundError(PerformanceServiceError):
    """Appraisal cycle not found."""

    def __init__(self, cycle_id: UUID):
        self.cycle_id = cycle_id
        super().__init__(f"Appraisal cycle {cycle_id} not found")


class KRANotFoundError(PerformanceServiceError):
    """KRA not found."""

    def __init__(self, kra_id: UUID):
        self.kra_id = kra_id
        super().__init__(f"KRA {kra_id} not found")


class KPINotFoundError(PerformanceServiceError):
    """KPI not found."""

    def __init__(self, kpi_id: UUID):
        self.kpi_id = kpi_id
        super().__init__(f"KPI {kpi_id} not found")


class AppraisalNotFoundError(PerformanceServiceError):
    """Appraisal not found."""

    def __init__(self, appraisal_id: UUID):
        self.appraisal_id = appraisal_id
        super().__init__(f"Appraisal {appraisal_id} not found")


class ScorecardNotFoundError(PerformanceServiceError):
    """Scorecard not found."""

    def __init__(self, scorecard_id: UUID):
        self.scorecard_id = scorecard_id
        super().__init__(f"Scorecard {scorecard_id} not found")


class AppraisalStatusError(PerformanceServiceError):
    """Invalid appraisal status transition."""

    def __init__(self, current: str, target: str):
        self.current = current
        self.target = target
        super().__init__(f"Cannot transition from {current} to {target}")


# Valid status transitions for appraisals
APPRAISAL_STATUS_TRANSITIONS = {
    AppraisalStatus.DRAFT: {
        AppraisalStatus.SELF_ASSESSMENT,
        AppraisalStatus.CANCELLED,
    },
    AppraisalStatus.SELF_ASSESSMENT: {
        AppraisalStatus.PENDING_REVIEW,
        AppraisalStatus.DRAFT,
    },
    AppraisalStatus.PENDING_REVIEW: {
        AppraisalStatus.UNDER_REVIEW,
    },
    AppraisalStatus.UNDER_REVIEW: {
        AppraisalStatus.PENDING_CALIBRATION,
        AppraisalStatus.SELF_ASSESSMENT,
    },
    AppraisalStatus.PENDING_CALIBRATION: {
        AppraisalStatus.CALIBRATION,
    },
    AppraisalStatus.CALIBRATION: {
        AppraisalStatus.COMPLETED,
        AppraisalStatus.UNDER_REVIEW,
    },
    AppraisalStatus.COMPLETED: set(),  # Terminal state
    AppraisalStatus.CANCELLED: set(),  # Terminal state
}


class PerformanceService:
    """Service for performance management operations.

    Handles:
    - Appraisal cycle management
    - Key Result Areas (KRAs)
    - Key Performance Indicators (KPIs)
    - Employee appraisals with multi-stage workflow
    - Balanced scorecards
    """

    def __init__(
        self,
        db: Session,
        ctx: WebAuthContext | None = None,
    ) -> None:
        self.db = db
        self.ctx = ctx

    def _resolve_org_mode(self, org_id: UUID) -> PerformanceMode:
        organization = self.db.get(Organization, org_id)
        return resolve_performance_mode(organization)

    def _ensure_private_write_mode(self, org_id: UUID) -> None:
        try:
            enforce_private_write_mode(self.db, org_id)
        except ValueError as exc:
            raise PerformanceServiceError(str(exc)) from exc

    @staticmethod
    def _allowed_template_profiles_for_mode(
        mode: PerformanceMode,
    ) -> set[AppraisalTemplateProfile]:
        if mode == PerformanceMode.PRIVATE:
            return {AppraisalTemplateProfile.PRIVATE, AppraisalTemplateProfile.BOTH}
        if mode == PerformanceMode.GOVERNMENT_PMS:
            return {AppraisalTemplateProfile.PMS, AppraisalTemplateProfile.BOTH}
        return {
            AppraisalTemplateProfile.PRIVATE,
            AppraisalTemplateProfile.PMS,
            AppraisalTemplateProfile.BOTH,
        }

    def allowed_template_profiles_for_org(
        self, org_id: UUID
    ) -> set[AppraisalTemplateProfile]:
        return self._allowed_template_profiles_for_mode(self._resolve_org_mode(org_id))

    def _validate_template_pms_config(
        self,
        *,
        org_id: UUID,
        template_profile: AppraisalTemplateProfile,
        pms_config: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not pms_config:
            return None

        mode = self._resolve_org_mode(org_id)
        if mode == PerformanceMode.PRIVATE:
            raise PerformanceServiceError(
                "PMS template configuration is not allowed in PRIVATE mode"
            )
        if template_profile == AppraisalTemplateProfile.PRIVATE:
            raise PerformanceServiceError(
                "PMS template configuration requires template_profile PMS or BOTH"
            )

        objective = int(pms_config.get("objective_weight_pct", 0))
        process = int(pms_config.get("process_weight_pct", 0))
        competency = int(pms_config.get("competency_weight_pct", 0))
        if objective + process + competency != 100:
            raise PerformanceServiceError(
                "PMS template weights must total 100 "
                f"(got {objective + process + competency})"
            )

        required_competency_count = int(pms_config.get("required_competency_count", 0))
        required_focus_count = int(
            pms_config.get("required_development_focus_count", 0)
        )
        if required_competency_count < 0:
            raise PerformanceServiceError(
                "required_competency_count must be greater than or equal to 0"
            )
        if required_focus_count < 0:
            raise PerformanceServiceError(
                "required_development_focus_count must be greater than or equal to 0"
            )
        if required_focus_count > required_competency_count:
            raise PerformanceServiceError(
                "required_development_focus_count cannot exceed "
                "required_competency_count"
            )

        return {
            "objective_weight_pct": objective,
            "process_weight_pct": process,
            "competency_weight_pct": competency,
            "required_competency_count": required_competency_count,
            "required_development_focus_count": required_focus_count,
            "evidence_required": bool(pms_config.get("evidence_required", True)),
        }

    def _ensure_underperformance_pip_resolution(
        self, org_id: UUID, appraisal: Appraisal
    ) -> None:
        """Block appraisal completion until underperformance PIP is resolved."""
        if appraisal.final_score is None:
            return
        final_score = Decimal(str(appraisal.final_score))
        if final_score <= Decimal("5"):
            final_score = final_score * Decimal("20")
        if final_score >= UNDERPERFORMANCE_SCORE_THRESHOLD:
            return

        pip = self.db.scalar(
            select(PerformanceImprovementPlan).where(
                PerformanceImprovementPlan.organization_id == org_id,
                PerformanceImprovementPlan.appraisal_id == appraisal.appraisal_id,
            )
        )
        if pip is None:
            from app.services.people.perf.underperformance_service import (
                UnderperformanceService,
            )

            UnderperformanceService(self.db).flag_for_pip(
                org_id,
                appraisal.employee_id,
                trigger_type="score_below_50",
                triggering_appraisal_id=appraisal.appraisal_id,
            )
            raise PerformanceServiceError(
                "Cannot complete appraisal: underperformance detected (score below 50). "
                "A PIP has been created and must be resolved first."
            )

        if pip.status not in {
            PIPStatus.IMPROVED,
            PIPStatus.ESCALATED,
            PIPStatus.CLOSED,
        }:
            raise PerformanceServiceError(
                "Cannot complete appraisal: linked PIP is not resolved "
                f"(current status: {pip.status.value})."
            )

    def _ensure_not_prior_year_carryover(
        self,
        appraisal: Appraisal,
        *,
        action: str,
    ) -> None:
        """Prevent workflow operations on carryover appraisals."""
        if getattr(appraisal, "is_prior_year_carryover", False) is True:
            raise PerformanceServiceError(
                f"Cannot {action} a prior-year carryover appraisal"
            )

    def _get_appraisal_cycle(self, appraisal: Appraisal) -> AppraisalCycle | None:
        cycle = getattr(appraisal, "cycle", None)
        if cycle is not None:
            return cast(AppraisalCycle, cycle)
        if appraisal.cycle_id is None:
            return None
        return self.db.scalar(
            select(AppraisalCycle).where(
                AppraisalCycle.organization_id == appraisal.organization_id,
                AppraisalCycle.cycle_id == appraisal.cycle_id,
            )
        )

    def _enforce_phase_deadline(
        self,
        appraisal: Appraisal,
        *,
        deadline_field: str,
        phase_label: str,
    ) -> None:
        cycle = self._get_appraisal_cycle(appraisal)
        if cycle is None:
            return

        deadline = getattr(cycle, deadline_field, None)
        if not isinstance(deadline, date):
            return
        if date.today() > deadline:
            raise PerformanceServiceError(
                f"Cannot submit {phase_label}: cycle deadline was {deadline.isoformat()}"
            )

    @staticmethod
    def _rating_label_from_value(rating: int) -> str:
        if rating >= 5:
            return "Outstanding"
        if rating == 4:
            return "Excellent"
        if rating == 3:
            return "Good"
        if rating == 2:
            return "Fair"
        return "Poor"

    @staticmethod
    def _normalize_absence_evidence(
        approved_absence_evidence: dict[str, Any] | None,
    ) -> dict[str, str] | None:
        """Validate and normalize approved-absence documentary evidence."""
        if approved_absence_evidence is None:
            return None
        if not isinstance(approved_absence_evidence, dict):
            raise PerformanceServiceError("Approved absence evidence must be an object")

        normalized: dict[str, str] = {}
        for key in (
            "document_type",
            "document_reference",
            "approval_reference",
            "validation_reference",
            "approving_authority",
            "audit_reference",
            "approval_date",
            "notes",
        ):
            value = approved_absence_evidence.get(key)
            if value is None:
                continue
            text_value = str(value).strip()
            if text_value:
                normalized[key] = text_value

        if not normalized:
            return None

        required = (
            "document_type",
            "document_reference",
            "approval_reference",
            "validation_reference",
        )
        missing = [key for key in required if not normalized.get(key)]
        if missing:
            raise PerformanceServiceError(
                "Approved absence evidence is missing required fields: "
                + ", ".join(missing)
            )

        approval_date = normalized.get("approval_date")
        if approval_date:
            try:
                date.fromisoformat(approval_date)
            except ValueError as exc:
                raise PerformanceServiceError(
                    "Approved absence evidence approval_date must be in YYYY-MM-DD format"
                ) from exc

        return normalized

    # =========================================================================
    # Appraisal Cycles
    # =========================================================================

    def list_cycles(
        self,
        org_id: UUID,
        *,
        search: str | None = None,
        status: AppraisalCycleStatus | None = None,
        year: int | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[AppraisalCycle]:
        """List appraisal cycles."""
        query = select(AppraisalCycle).where(AppraisalCycle.organization_id == org_id)

        if search:
            search_term = f"%{search}%"
            query = query.where(
                or_(
                    AppraisalCycle.cycle_name.ilike(search_term),
                    AppraisalCycle.cycle_code.ilike(search_term),
                )
            )

        if status:
            query = query.where(AppraisalCycle.status == status)

        if year:
            year_start = date(year, 1, 1)
            year_end = date(year, 12, 31)
            query = query.where(
                and_(
                    AppraisalCycle.start_date >= year_start,
                    AppraisalCycle.start_date <= year_end,
                )
            )

        query = query.order_by(AppraisalCycle.start_date.desc())

        # Count total
        count_query = select(func.count()).select_from(query.subquery())
        total = self.db.scalar(count_query) or 0

        # Apply pagination
        if pagination:
            query = query.offset(pagination.offset).limit(pagination.limit)

        items = list(self.db.scalars(query).all())

        return PaginatedResult(
            items=items,
            total=total,
            offset=pagination.offset if pagination else 0,
            limit=pagination.limit if pagination else len(items),
        )

    def list_appraisal_cycles(
        self,
        org_id: UUID,
        *,
        search: str | None = None,
        status: AppraisalCycleStatus | None = None,
        year: int | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[AppraisalCycle]:
        """Compatibility wrapper for appraisal cycles listing."""
        return self.list_cycles(
            org_id=org_id,
            search=search,
            status=status,
            year=year,
            pagination=pagination,
        )

    def get_cycle(self, org_id: UUID, cycle_id: UUID) -> AppraisalCycle:
        """Get an appraisal cycle by ID."""
        cycle = self.db.scalar(
            select(AppraisalCycle).where(
                AppraisalCycle.cycle_id == cycle_id,
                AppraisalCycle.organization_id == org_id,
            )
        )
        if not cycle:
            raise AppraisalCycleNotFoundError(cycle_id)
        return cycle

    def create_cycle(
        self,
        org_id: UUID,
        *,
        cycle_code: str,
        cycle_name: str,
        review_period_start: date,
        review_period_end: date,
        start_date: date,
        end_date: date,
        self_assessment_deadline: date | None = None,
        manager_review_deadline: date | None = None,
        calibration_deadline: date | None = None,
        include_probation_employees: bool = False,
        min_tenure_months: int = 3,
        description: str | None = None,
    ) -> AppraisalCycle:
        """Create a new appraisal cycle."""
        cycle = AppraisalCycle(
            organization_id=org_id,
            cycle_code=cycle_code,
            cycle_name=cycle_name,
            review_period_start=review_period_start,
            review_period_end=review_period_end,
            start_date=start_date,
            end_date=end_date,
            self_assessment_deadline=self_assessment_deadline,
            manager_review_deadline=manager_review_deadline,
            calibration_deadline=calibration_deadline,
            include_probation_employees=include_probation_employees,
            min_tenure_months=min_tenure_months,
            description=description,
            status=AppraisalCycleStatus.DRAFT,
        )

        self.db.add(cycle)
        self.db.flush()
        return cycle

    def update_cycle(
        self,
        org_id: UUID,
        cycle_id: UUID,
        **kwargs,
    ) -> AppraisalCycle:
        """Update an appraisal cycle."""
        cycle = self.get_cycle(org_id, cycle_id)

        for key, value in kwargs.items():
            if value is not None and hasattr(cycle, key):
                setattr(cycle, key, value)

        self.db.flush()
        return cycle

    def delete_cycle(self, org_id: UUID, cycle_id: UUID) -> None:
        """Delete an appraisal cycle."""
        cycle = self.get_cycle(org_id, cycle_id)
        self.db.delete(cycle)
        self.db.flush()

    def start_cycle(self, org_id: UUID, cycle_id: UUID) -> AppraisalCycle:
        """Start an appraisal cycle."""
        cycle = self.get_cycle(org_id, cycle_id)
        cycle.status = AppraisalCycleStatus.ACTIVE
        self.db.flush()
        return cycle

    def close_cycle(self, org_id: UUID, cycle_id: UUID) -> AppraisalCycle:
        """Close an appraisal cycle."""
        cycle = self.get_cycle(org_id, cycle_id)
        cycle.status = AppraisalCycleStatus.COMPLETED
        self.db.flush()
        return cycle

    # =========================================================================
    # Key Result Areas (KRAs)
    # =========================================================================

    def list_kras(
        self,
        org_id: UUID,
        *,
        department_id: UUID | None = None,
        designation_id: UUID | None = None,
        is_active: bool | None = None,
        category: str | None = None,
        search: str | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[KRA]:
        """List KRAs."""
        query = select(KRA).where(KRA.organization_id == org_id)

        if department_id:
            query = query.where(KRA.department_id == department_id)

        if designation_id:
            query = query.where(KRA.designation_id == designation_id)

        if is_active is not None:
            query = query.where(KRA.is_active == is_active)

        if category:
            query = query.where(KRA.category == category)

        if search:
            search_term = f"%{search}%"
            query = query.where(
                or_(
                    KRA.kra_code.ilike(search_term),
                    KRA.kra_name.ilike(search_term),
                )
            )

        query = query.order_by(KRA.kra_name)

        # Count total
        count_query = select(func.count()).select_from(query.subquery())
        total = self.db.scalar(count_query) or 0

        # Apply pagination
        if pagination:
            query = query.offset(pagination.offset).limit(pagination.limit)

        items = list(self.db.scalars(query).all())

        return PaginatedResult(
            items=items,
            total=total,
            offset=pagination.offset if pagination else 0,
            limit=pagination.limit if pagination else len(items),
        )

    def get_kra(self, org_id: UUID, kra_id: UUID) -> KRA:
        """Get a KRA by ID."""
        kra = self.db.scalar(
            select(KRA).where(
                KRA.kra_id == kra_id,
                KRA.organization_id == org_id,
            )
        )
        if not kra:
            raise KRANotFoundError(kra_id)
        return kra

    def create_kra(
        self,
        org_id: UUID,
        *,
        kra_code: str,
        kra_name: str,
        department_id: UUID | None = None,
        designation_id: UUID | None = None,
        default_weightage: Decimal = Decimal("0"),
        category: str | None = None,
        measurement_criteria: str | None = None,
        is_active: bool = True,
        description: str | None = None,
    ) -> KRA:
        """Create a new KRA."""
        kra = KRA(
            organization_id=org_id,
            kra_code=kra_code,
            kra_name=kra_name,
            department_id=department_id,
            designation_id=designation_id,
            default_weightage=default_weightage,
            category=category,
            measurement_criteria=measurement_criteria,
            is_active=is_active,
            description=description,
        )

        self.db.add(kra)
        self.db.flush()
        return kra

    def update_kra(
        self,
        org_id: UUID,
        kra_id: UUID,
        **kwargs,
    ) -> KRA:
        """Update a KRA."""
        kra = self.get_kra(org_id, kra_id)

        for key, value in kwargs.items():
            if value is not None and hasattr(kra, key):
                setattr(kra, key, value)

        self.db.flush()
        return kra

    def delete_kra(self, org_id: UUID, kra_id: UUID) -> None:
        """Delete a KRA."""
        kra = self.get_kra(org_id, kra_id)
        self.db.delete(kra)
        self.db.flush()

    @staticmethod
    def _department_template_key(department_name: str) -> str:
        normalized = department_name.lower()
        if any(term in normalized for term in ("customer", "support", "experience")):
            return "customer_experience"
        if any(
            term in normalized
            for term in ("enterprise sales", "sales", "business development")
        ):
            return "sales"
        if any(
            term in normalized for term in ("project", "delivery", "implementation")
        ):
            return "projects"
        if any(term in normalized for term in ("procurement", "purchase", "sourcing")):
            return "procurement"
        if any(term in normalized for term in ("inventory", "warehouse", "store")):
            return "inventory"
        if any(term in normalized for term in ("finance", "account", "billing")):
            return "finance"
        if any(term in normalized for term in ("human", "hr", "people")):
            return "hr"
        return "generic"

    @staticmethod
    def _code_part(value: str, *, max_length: int = 12) -> str:
        cleaned = "".join(ch for ch in value.upper() if ch.isalnum())
        return (cleaned or "GEN")[:max_length]

    @staticmethod
    def _infer_template_perspective(template: dict[str, object]) -> str:
        metric_key = str(template.get("metric_source_key") or "").lower()
        text = " ".join(
            str(template.get(key) or "").lower()
            for key in ("kra_name", "kpi_name", "unit_of_measure")
        )
        if metric_key.startswith("learning.") or any(
            term in text for term in ("learning", "training", "certification", "skill")
        ):
            return "LEARNING"
        if any(
            term in metric_key or term in text
            for term in (
                "revenue",
                "collection",
                "cost",
                "saving",
                "expense",
                "backlog",
                "variance",
                "financial",
            )
        ):
            return "FINANCIAL"
        if any(
            term in metric_key or term in text
            for term in (
                "customer",
                "sla",
                "feedback",
                "supplier",
                "account",
                "satisfaction",
                "resolution_rate",
            )
        ):
            return "CUSTOMER"
        return "PROCESS"

    @classmethod
    def _balanced_department_templates(
        cls,
        department_key: str,
    ) -> list[dict[str, object]]:
        defaults = [dict(item) for item in DEPARTMENT_TEMPLATE_LIBRARY[department_key]]

        for item in defaults:
            item["scorecard_perspective"] = cls._infer_template_perspective(item)

        represented = {cast(str, item["scorecard_perspective"]) for item in defaults}
        for perspective in SCORECARD_PERSPECTIVES - represented:
            item = dict(PERSPECTIVE_TEMPLATE_DEFAULTS[perspective])
            item["scorecard_perspective"] = perspective
            defaults.append(item)

        items_by_perspective: dict[str, list[dict[str, object]]] = {}
        for item in defaults:
            perspective = cast(str, item["scorecard_perspective"])
            items_by_perspective.setdefault(perspective, []).append(item)

        target_weights = DEPARTMENT_PERSPECTIVE_WEIGHTS[department_key]
        for perspective, items in items_by_perspective.items():
            target_weight = target_weights[perspective]
            item_weight = (target_weight / Decimal(len(items))).quantize(
                Decimal("0.01"),
                rounding=ROUND_HALF_UP,
            )
            for item in items[:-1]:
                item["weightage"] = item_weight
            items[-1]["weightage"] = (
                target_weight - (item_weight * Decimal(len(items) - 1))
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return defaults

    @staticmethod
    def _scorecard_perspective_from_kpi(kpi: KPI) -> str | None:
        for source in (kpi.notes, kpi.description):
            if not source:
                continue
            for line in source.splitlines():
                key, _, value = line.partition(":")
                if key.strip().lower() != "scorecard perspective":
                    continue
                perspective = value.strip().upper().replace(" ", "_")
                if perspective == "INTERNAL_PROCESS":
                    perspective = "PROCESS"
                if perspective in SCORECARD_PERSPECTIVES:
                    return perspective
        return None

    def generate_department_performance_templates(
        self,
        org_id: UUID,
    ) -> dict[str, int]:
        """Generate default department performance templates for active departments."""
        self._ensure_private_write_mode(org_id)
        departments = list(
            self.db.scalars(
                select(Department)
                .where(
                    Department.organization_id == org_id,
                    Department.is_active.is_(True),
                )
                .order_by(Department.department_name)
            ).all()
        )

        created = 0
        skipped = 0
        for department in departments:
            template_key = self._department_template_key(department.department_name)
            defaults = self._balanced_department_templates(template_key)
            existing_by_key = {
                (template.kra_name, template.kpi_name): template
                for template in self.db.scalars(
                    select(DepartmentPerformanceTemplate).where(
                        DepartmentPerformanceTemplate.organization_id == org_id,
                        DepartmentPerformanceTemplate.department_id
                        == department.department_id,
                    )
                ).all()
            }

            for default in defaults:
                key = (str(default["kra_name"]), str(default["kpi_name"]))
                existing = existing_by_key.get(key)
                if existing:
                    existing.scorecard_perspective = cast(
                        str,
                        default["scorecard_perspective"],
                    )
                    existing.weightage = cast(Decimal, default["weightage"])
                    existing.metric_source_key = cast(
                        str | None,
                        default["metric_source_key"],
                    )
                    existing.lower_is_better = bool(default["lower_is_better"])
                    skipped += 1
                    continue

                template = DepartmentPerformanceTemplate(
                    organization_id=org_id,
                    department_id=department.department_id,
                    kra_name=str(default["kra_name"]),
                    kpi_name=str(default["kpi_name"]),
                    description=(
                        f"Auto-generated default for {department.department_name}"
                    ),
                    target_value=cast(Decimal, default["target_value"]),
                    unit_of_measure=cast(str | None, default["unit_of_measure"]),
                    weightage=cast(Decimal, default["weightage"]),
                    scorecard_perspective=cast(str, default["scorecard_perspective"]),
                    metric_source_key=cast(str | None, default["metric_source_key"]),
                    lower_is_better=bool(default["lower_is_better"]),
                    is_active=True,
                )
                self.db.add(template)
                created += 1

        self.db.flush()
        return {
            "departments": len(departments),
            "created": created,
            "skipped": skipped,
        }

    def generate_employee_kpis_from_department_templates(
        self,
        org_id: UUID,
        *,
        period_start: date,
        period_end: date,
    ) -> dict[str, int]:
        """Create employee KRAs/KPIs from active department templates."""
        self._ensure_private_write_mode(org_id)
        employees = list(
            self.db.scalars(
                select(Employee)
                .where(
                    Employee.organization_id == org_id,
                    Employee.status == EmployeeStatus.ACTIVE,
                    Employee.department_id.isnot(None),
                )
                .order_by(Employee.employee_code)
            ).all()
        )

        department_ids = {employee.department_id for employee in employees}
        templates_by_department: dict[UUID, list[DepartmentPerformanceTemplate]] = {}
        if department_ids:
            templates = list(
                self.db.scalars(
                    select(DepartmentPerformanceTemplate).where(
                        DepartmentPerformanceTemplate.organization_id == org_id,
                        DepartmentPerformanceTemplate.department_id.in_(department_ids),
                        DepartmentPerformanceTemplate.is_active.is_(True),
                    )
                ).all()
            )
            for template in templates:
                templates_by_department.setdefault(template.department_id, []).append(
                    template
                )

        kras_by_key: dict[tuple[UUID, str], KRA] = {}
        created_kras = 0
        created_kpis = 0
        skipped_kpis = 0

        for employee in employees:
            department_templates = templates_by_department.get(
                cast(UUID, employee.department_id),
                [],
            )
            for template in department_templates:
                kra_key = (template.department_id, template.kra_name)
                kra = kras_by_key.get(kra_key)
                if kra is None:
                    kra = self.db.scalar(
                        select(KRA).where(
                            KRA.organization_id == org_id,
                            KRA.department_id == template.department_id,
                            KRA.kra_name == template.kra_name,
                        )
                    )
                if kra is None:
                    kra_code = (
                        "AUTO-"
                        f"{self._code_part(str(employee.department_id), max_length=6)}-"
                        f"{self._code_part(template.kra_name, max_length=10)}"
                    )
                    kra = KRA(
                        organization_id=org_id,
                        kra_code=kra_code[:30],
                        kra_name=template.kra_name,
                        department_id=template.department_id,
                        default_weightage=template.weightage,
                        category="PERFORMANCE",
                        measurement_criteria=template.metric_source_key,
                        is_active=True,
                        description="Auto-generated from department performance template",
                    )
                    self.db.add(kra)
                    self.db.flush()
                    created_kras += 1
                kras_by_key[kra_key] = kra

                existing_kpi = self.db.scalar(
                    select(KPI.kpi_id).where(
                        KPI.organization_id == org_id,
                        KPI.employee_id == employee.employee_id,
                        KPI.kpi_name == template.kpi_name,
                        KPI.period_start == period_start,
                        KPI.period_end == period_end,
                    )
                )
                if existing_kpi:
                    skipped_kpis += 1
                    continue

                notes_parts = []
                if template.metric_source_key:
                    notes_parts.append(f"Metric key: {template.metric_source_key}")
                notes_parts.append(
                    f"Scorecard perspective: {template.scorecard_perspective}"
                )
                notes_parts.append(
                    "Auto-generated from department performance template"
                )
                kpi = KPI(
                    organization_id=org_id,
                    employee_id=employee.employee_id,
                    kra_id=kra.kra_id,
                    kpi_name=template.kpi_name,
                    description=template.description,
                    period_start=period_start,
                    period_end=period_end,
                    target_value=template.target_value,
                    unit_of_measure=template.unit_of_measure,
                    weightage=template.weightage,
                    notes="\n".join(notes_parts),
                    status=KPIStatus.ACTIVE,
                )
                self._sync_kpi_actual_from_system_metric(org_id, kpi)
                self.db.add(kpi)
                created_kpis += 1

        self.db.flush()
        return {
            "employees": len(employees),
            "created_kras": created_kras,
            "created_kpis": created_kpis,
            "skipped_kpis": skipped_kpis,
        }

    # =========================================================================
    # Appraisal Templates
    # =========================================================================

    def list_templates(
        self,
        org_id: UUID,
        *,
        department_id: UUID | None = None,
        designation_id: UUID | None = None,
        template_profiles: set[AppraisalTemplateProfile] | None = None,
        is_active: bool | None = None,
        search: str | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[AppraisalTemplate]:
        """List appraisal templates."""
        query = select(AppraisalTemplate).where(
            AppraisalTemplate.organization_id == org_id
        )

        if department_id:
            query = query.where(AppraisalTemplate.department_id == department_id)

        if designation_id:
            query = query.where(AppraisalTemplate.designation_id == designation_id)

        if template_profiles:
            query = query.where(
                AppraisalTemplate.template_profile.in_(template_profiles)
            )

        if is_active is not None:
            query = query.where(AppraisalTemplate.is_active == is_active)

        if search:
            search_term = f"%{search}%"
            query = query.where(
                or_(
                    AppraisalTemplate.template_code.ilike(search_term),
                    AppraisalTemplate.template_name.ilike(search_term),
                )
            )

        query = query.options(
            joinedload(AppraisalTemplate.kras).joinedload(AppraisalTemplateKRA.kra)
        )
        query = query.order_by(AppraisalTemplate.template_name)

        count_query = select(func.count()).select_from(query.subquery())
        total = self.db.scalar(count_query) or 0

        if pagination:
            query = query.offset(pagination.offset).limit(pagination.limit)

        items = list(self.db.scalars(query).unique().all())

        return PaginatedResult(
            items=items,
            total=total,
            offset=pagination.offset if pagination else 0,
            limit=pagination.limit if pagination else len(items),
        )

    def get_template(self, org_id: UUID, template_id: UUID) -> AppraisalTemplate:
        """Get an appraisal template by ID."""
        template = self.db.scalar(
            select(AppraisalTemplate)
            .options(
                joinedload(AppraisalTemplate.kras).joinedload(AppraisalTemplateKRA.kra)
            )
            .where(
                AppraisalTemplate.template_id == template_id,
                AppraisalTemplate.organization_id == org_id,
            )
        )
        if not template:
            raise PerformanceServiceError(f"Template {template_id} not found")
        return template

    def create_template(
        self,
        org_id: UUID,
        *,
        template_code: str,
        template_name: str,
        description: str | None = None,
        department_id: UUID | None = None,
        designation_id: UUID | None = None,
        rating_scale_max: int = 5,
        template_profile: AppraisalTemplateProfile
        | str = AppraisalTemplateProfile.BOTH,
        is_active: bool = True,
        kras: list[dict] | None = None,
        pms_config: dict[str, Any] | None = None,
    ) -> AppraisalTemplate:
        """Create a new appraisal template."""
        if isinstance(template_profile, str):
            template_profile = AppraisalTemplateProfile(
                template_profile.strip().upper()
            )
        normalized_pms_config = self._validate_template_pms_config(
            org_id=org_id,
            template_profile=template_profile,
            pms_config=pms_config,
        )
        template = AppraisalTemplate(
            organization_id=org_id,
            template_code=template_code,
            template_name=template_name,
            description=description,
            pms_config=normalized_pms_config,
            template_profile=template_profile,
            department_id=department_id,
            designation_id=designation_id,
            rating_scale_max=rating_scale_max,
            is_active=is_active,
        )
        self.db.add(template)
        self.db.flush()

        if kras:
            for idx, kra in enumerate(kras):
                self.db.add(
                    AppraisalTemplateKRA(
                        organization_id=org_id,
                        template_id=template.template_id,
                        kra_id=kra["kra_id"],
                        weightage=kra["weightage"],
                        sequence=kra.get("sequence", idx),
                    )
                )
        self.db.flush()
        return template

    def update_template(
        self,
        org_id: UUID,
        template_id: UUID,
        **kwargs,
    ) -> AppraisalTemplate:
        """Update an appraisal template."""
        kras = kwargs.pop("kras", None)
        template_profile = kwargs.get("template_profile")
        if isinstance(template_profile, str):
            kwargs["template_profile"] = AppraisalTemplateProfile(
                template_profile.strip().upper()
            )
        template = self.get_template(org_id, template_id)
        effective_profile = cast(
            AppraisalTemplateProfile,
            kwargs.get("template_profile", template.template_profile),
        )
        if "pms_config" in kwargs:
            kwargs["pms_config"] = self._validate_template_pms_config(
                org_id=org_id,
                template_profile=effective_profile,
                pms_config=cast(dict[str, Any] | None, kwargs.get("pms_config")),
            )
        elif effective_profile == AppraisalTemplateProfile.PRIVATE:
            kwargs["pms_config"] = None

        for key, value in kwargs.items():
            if (value is not None or key == "pms_config") and hasattr(template, key):
                setattr(template, key, value)

        if kras is not None:
            self.db.execute(
                delete(AppraisalTemplateKRA).where(
                    AppraisalTemplateKRA.template_id == template_id
                )
            )
            for idx, kra in enumerate(kras):
                self.db.add(
                    AppraisalTemplateKRA(
                        organization_id=org_id,
                        template_id=template_id,
                        kra_id=kra["kra_id"],
                        weightage=kra["weightage"],
                        sequence=kra.get("sequence", idx),
                    )
                )

        self.db.flush()
        return template

    def delete_template(self, org_id: UUID, template_id: UUID) -> None:
        """Delete an appraisal template."""
        template = self.get_template(org_id, template_id)
        self.db.execute(
            delete(AppraisalTemplateKRA).where(
                AppraisalTemplateKRA.template_id == template_id
            )
        )
        self.db.delete(template)
        self.db.flush()

    # =========================================================================
    # Key Performance Indicators (KPIs)
    # =========================================================================

    def list_kpis(
        self,
        org_id: UUID,
        *,
        employee_id: UUID | None = None,
        kra_id: UUID | None = None,
        status: KPIStatus | None = None,
        search: str | None = None,
        is_active: bool | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[KPI]:
        """List KPIs."""
        query = select(KPI).where(KPI.organization_id == org_id)

        if employee_id:
            query = query.where(KPI.employee_id == employee_id)

        if kra_id:
            query = query.where(KPI.kra_id == kra_id)

        if status:
            query = query.where(KPI.status == status)

        if search:
            search_term = f"%{search}%"
            query = query.where(
                or_(
                    KPI.kpi_name.ilike(search_term),
                    KPI.description.ilike(search_term),
                )
            )

        if is_active is not None:
            if is_active:
                query = query.where(KPI.status == KPIStatus.ACTIVE)
            else:
                query = query.where(KPI.status != KPIStatus.ACTIVE)

        if from_date:
            query = query.where(KPI.period_start >= from_date)

        if to_date:
            query = query.where(KPI.period_end <= to_date)

        query = query.order_by(KPI.period_start.desc())

        # Count total
        count_query = select(func.count()).select_from(query.subquery())
        total = self.db.scalar(count_query) or 0

        # Apply pagination
        if pagination:
            query = query.offset(pagination.offset).limit(pagination.limit)

        items = list(self.db.scalars(query).all())

        return PaginatedResult(
            items=items,
            total=total,
            offset=pagination.offset if pagination else 0,
            limit=pagination.limit if pagination else len(items),
        )

    def get_kpi(self, org_id: UUID, kpi_id: UUID) -> KPI:
        """Get a KPI by ID."""
        kpi = self.db.scalar(
            select(KPI).where(
                KPI.kpi_id == kpi_id,
                KPI.organization_id == org_id,
            )
        )
        if not kpi:
            raise KPINotFoundError(kpi_id)
        return kpi

    def create_kpi(
        self,
        org_id: UUID,
        *,
        employee_id: UUID,
        kra_id: UUID | None = None,
        kpi_name: str,
        period_start: date,
        period_end: date,
        target_value: Decimal,
        unit_of_measure: str | None = None,
        threshold_value: Decimal | None = None,
        stretch_value: Decimal | None = None,
        weightage: Decimal = Decimal("0"),
        notes: str | None = None,
        description: str | None = None,
    ) -> KPI:
        """Create a new KPI."""
        kpi = KPI(
            organization_id=org_id,
            employee_id=employee_id,
            kra_id=kra_id,
            kpi_name=kpi_name,
            period_start=period_start,
            period_end=period_end,
            target_value=target_value,
            unit_of_measure=unit_of_measure,
            threshold_value=threshold_value,
            stretch_value=stretch_value,
            weightage=weightage,
            notes=notes,
            description=description,
            status=KPIStatus.DRAFT,
        )

        self.db.add(kpi)
        self.db.flush()
        return kpi

    def update_kpi(
        self,
        org_id: UUID,
        kpi_id: UUID,
        **kwargs,
    ) -> KPI:
        """Update a KPI."""
        kpi = self.get_kpi(org_id, kpi_id)
        for key, value in kwargs.items():
            if value is not None and hasattr(kpi, key):
                setattr(kpi, key, value)
        self.db.flush()
        return kpi

    def update_kpi_progress(
        self,
        org_id: UUID,
        kpi_id: UUID,
        *,
        actual_value: Decimal,
        evidence: str | None = None,
        notes: str | None = None,
    ) -> KPI:
        """Update KPI progress."""
        kpi = self.get_kpi(org_id, kpi_id)

        kpi.actual_value = actual_value
        if evidence:
            kpi.evidence = evidence
        if notes:
            kpi.notes = notes

        # Calculate achievement percentage
        if kpi.target_value and kpi.target_value > 0:
            kpi.achievement_percentage = (
                actual_value / kpi.target_value * 100
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # Update status based on achievement
        if kpi.achievement_percentage:
            if kpi.achievement_percentage >= 100:
                kpi.status = KPIStatus.ACHIEVED
            elif kpi.achievement_percentage >= 80:
                kpi.status = KPIStatus.ON_TRACK
            else:
                kpi.status = KPIStatus.AT_RISK

        self.db.flush()
        return kpi

    @staticmethod
    def _support_ticket_metric_key(kpi: KPI) -> str | None:
        text = " ".join(
            part.lower() for part in (kpi.kpi_name, kpi.description, kpi.notes) if part
        )
        for metric_key in SUPPORT_TICKET_METRIC_KEYS:
            if metric_key in text:
                return metric_key
        return None

    @staticmethod
    def _apply_kpi_actual_value(
        kpi: KPI,
        actual_value: Decimal,
        *,
        lower_is_better: bool = False,
        evidence: str | None = None,
        notes: str | None = None,
    ) -> None:
        kpi.actual_value = actual_value.quantize(Decimal("0.01"))
        if evidence:
            kpi.evidence = evidence
        if notes:
            kpi.notes = notes

        if kpi.target_value and kpi.target_value > 0:
            if lower_is_better:
                if actual_value <= 0:
                    achievement = Decimal("100")
                else:
                    achievement = kpi.target_value / actual_value * Decimal("100")
            else:
                achievement = actual_value / kpi.target_value * Decimal("100")
            kpi.achievement_percentage = achievement.quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )

        if kpi.achievement_percentage is not None:
            if kpi.achievement_percentage >= 100:
                kpi.status = KPIStatus.ACHIEVED
            elif kpi.achievement_percentage >= 80:
                kpi.status = KPIStatus.ON_TRACK
            else:
                kpi.status = KPIStatus.AT_RISK

    def _calculate_support_ticket_metric(
        self,
        org_id: UUID,
        *,
        employee_id: UUID,
        metric_key: str,
        period_start: date,
        period_end: date,
    ) -> Decimal:
        resolved_statuses = [TicketStatus.RESOLVED, TicketStatus.CLOSED]
        open_statuses = [TicketStatus.OPEN, TicketStatus.REPLIED, TicketStatus.ON_HOLD]

        if metric_key == "support.tickets_resolved":
            value = self.db.scalar(
                select(func.count(Ticket.ticket_id)).where(
                    Ticket.organization_id == org_id,
                    Ticket.assigned_to_id == employee_id,
                    Ticket.status.in_(resolved_statuses),
                    Ticket.resolution_date >= period_start,
                    Ticket.resolution_date <= period_end,
                )
            )
            return Decimal(value or 0)

        if metric_key == "support.open_backlog":
            value = self.db.scalar(
                select(func.count(Ticket.ticket_id)).where(
                    Ticket.organization_id == org_id,
                    Ticket.assigned_to_id == employee_id,
                    Ticket.status.in_(open_statuses),
                    Ticket.opening_date <= period_end,
                )
            )
            return Decimal(value or 0)

        if metric_key == "support.resolution_rate":
            total = self.db.scalar(
                select(func.count(Ticket.ticket_id)).where(
                    Ticket.organization_id == org_id,
                    Ticket.assigned_to_id == employee_id,
                    Ticket.opening_date >= period_start,
                    Ticket.opening_date <= period_end,
                )
            )
            if not total:
                return Decimal("0")
            resolved = self.db.scalar(
                select(func.count(Ticket.ticket_id)).where(
                    Ticket.organization_id == org_id,
                    Ticket.assigned_to_id == employee_id,
                    Ticket.opening_date >= period_start,
                    Ticket.opening_date <= period_end,
                    Ticket.status.in_(resolved_statuses),
                )
            )
            return (Decimal(resolved or 0) / Decimal(total) * Decimal("100")).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )

        if metric_key == "support.avg_resolution_days":
            tickets = self.db.scalars(
                select(Ticket).where(
                    Ticket.organization_id == org_id,
                    Ticket.assigned_to_id == employee_id,
                    Ticket.status.in_(resolved_statuses),
                    Ticket.opening_date.isnot(None),
                    Ticket.resolution_date.isnot(None),
                    Ticket.resolution_date >= period_start,
                    Ticket.resolution_date <= period_end,
                )
            ).all()
            durations = [
                (ticket.resolution_date - ticket.opening_date).days
                for ticket in tickets
                if ticket.resolution_date and ticket.opening_date
            ]
            if not durations:
                return Decimal("0")
            return (Decimal(sum(durations)) / Decimal(len(durations))).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )

        raise PerformanceServiceError(f"Unsupported support metric: {metric_key}")

    def _sync_kpi_actual_from_system_metric(self, org_id: UUID, kpi: KPI) -> str | None:
        """Populate KPI actual value when its metric is backed by system data."""
        metric_key = self._support_ticket_metric_key(kpi)
        if metric_key is None:
            return None

        actual_value = self._calculate_support_ticket_metric(
            org_id,
            employee_id=kpi.employee_id,
            metric_key=metric_key,
            period_start=kpi.period_start,
            period_end=kpi.period_end,
        )
        self._apply_kpi_actual_value(
            kpi,
            actual_value,
            lower_is_better=metric_key in LOWER_IS_BETTER_SUPPORT_METRICS,
            evidence=f"Auto-calculated from support.ticket metric {metric_key}",
            notes=f"Metric key: {metric_key}",
        )
        return metric_key

    @staticmethod
    def _apply_scorecard_item_score(
        item: ScorecardItem,
        *,
        lower_is_better: bool = False,
    ) -> None:
        """Calculate scorecard item score from target, actual, and weight."""
        if item.actual_value is None or not item.target_value or item.target_value <= 0:
            return

        if lower_is_better:
            if item.actual_value <= 0:
                item.score = Decimal("100.00")
            else:
                item.score = (
                    item.target_value / item.actual_value * Decimal("100")
                ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        else:
            item.score = (
                item.actual_value / item.target_value * Decimal("100")
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        if item.score > 100:
            item.score = Decimal("100.00")
        if item.weightage:
            item.weighted_score = (
                item.score * item.weightage / Decimal("100")
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def sync_support_ticket_kpi_progress(
        self,
        org_id: UUID,
        *,
        employee_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Update tagged KPI actual values from support.ticket metrics."""
        query = select(KPI).where(KPI.organization_id == org_id)
        if employee_id:
            query = query.where(KPI.employee_id == employee_id)
        query = query.where(
            KPI.status.notin_([KPIStatus.CANCELLED, KPIStatus.DEFERRED])
        )

        updated = 0
        skipped = 0
        metric_counts: dict[str, int] = {}
        for kpi in self.db.scalars(query).all():
            metric_key = self._sync_kpi_actual_from_system_metric(org_id, kpi)
            if metric_key is None:
                skipped += 1
                continue
            updated += 1
            metric_counts[metric_key] = metric_counts.get(metric_key, 0) + 1

        self.db.flush()
        return {
            "updated": updated,
            "skipped": skipped,
            "metric_counts": metric_counts,
        }

    def delete_kpi(self, org_id: UUID, kpi_id: UUID) -> None:
        """Delete a KPI."""
        kpi = self.get_kpi(org_id, kpi_id)
        self.db.delete(kpi)
        self.db.flush()

    # =========================================================================
    # Appraisals
    # =========================================================================

    def list_appraisals(
        self,
        org_id: UUID,
        *,
        employee_id: UUID | None = None,
        cycle_id: UUID | None = None,
        manager_id: UUID | None = None,
        status: AppraisalStatus | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[Appraisal]:
        """List appraisals."""
        query = select(Appraisal).where(Appraisal.organization_id == org_id)

        if employee_id:
            query = query.where(Appraisal.employee_id == employee_id)

        if cycle_id:
            query = query.where(Appraisal.cycle_id == cycle_id)

        if manager_id:
            query = query.where(Appraisal.manager_id == manager_id)

        if status:
            query = query.where(Appraisal.status == status)

        query = query.options(joinedload(Appraisal.kra_scores))
        query = query.order_by(Appraisal.created_at.desc())

        # Count total
        count_query = select(func.count()).select_from(query.subquery())
        total = self.db.scalar(count_query) or 0

        # Apply pagination
        if pagination:
            query = query.offset(pagination.offset).limit(pagination.limit)

        items = list(self.db.scalars(query).unique().all())

        return PaginatedResult(
            items=items,
            total=total,
            offset=pagination.offset if pagination else 0,
            limit=pagination.limit if pagination else len(items),
        )

    def get_appraisal(self, org_id: UUID, appraisal_id: UUID) -> Appraisal:
        """Get an appraisal by ID."""
        appraisal = self.db.scalar(
            select(Appraisal)
            .options(joinedload(Appraisal.kra_scores))
            .where(
                Appraisal.appraisal_id == appraisal_id,
                Appraisal.organization_id == org_id,
            )
        )
        if not appraisal:
            raise AppraisalNotFoundError(appraisal_id)
        return appraisal

    def create_appraisal(
        self,
        org_id: UUID,
        *,
        employee_id: UUID,
        cycle_id: UUID,
        manager_id: UUID,
        template_id: UUID | None = None,
        kra_scores: list[dict] | None = None,
        absence_months: int | None = None,
        approved_absence_evidence: dict[str, Any] | None = None,
    ) -> Appraisal:
        """Create a new appraisal."""
        self._ensure_private_write_mode(org_id)
        # Verify cycle exists
        self.get_cycle(org_id, cycle_id)
        if template_id is not None:
            template = self.get_template(org_id, template_id)
            allowed_profiles = self.allowed_template_profiles_for_org(org_id)
            if template.template_profile not in allowed_profiles:
                raise PerformanceServiceError(
                    "Selected template profile is not allowed for this organization mode"
                )
        if absence_months is not None and absence_months < 0:
            raise PerformanceServiceError("Absence months cannot be negative")
        normalized_absence_evidence = self._normalize_absence_evidence(
            approved_absence_evidence
        )

        # Approved absence policy:
        # - absence <= 6 months: normal appraisal
        # - absence > 6 months: create carryover appraisal from prior year
        is_carryover = False
        carryover_source_id = None
        carryover_final_score: Decimal | None = None
        carryover_final_rating: int | None = None
        carryover_rating_label: str | None = None
        if absence_months is not None and absence_months > 6:
            if not normalized_absence_evidence:
                raise PerformanceServiceError(
                    "Approved absence documentation is required for absence beyond 6 months"
                )
            prior = self.db.scalar(
                select(Appraisal)
                .where(
                    Appraisal.organization_id == org_id,
                    Appraisal.employee_id == employee_id,
                    Appraisal.status == AppraisalStatus.COMPLETED,
                    Appraisal.cycle_id != cycle_id,
                    Appraisal.is_prior_year_carryover.is_(False),
                )
                .order_by(
                    Appraisal.completed_on.desc().nulls_last(),
                    Appraisal.created_at.desc(),
                )
            )
            if prior is None:
                raise PerformanceServiceError(
                    "No prior-year completed appraisal found for carryover"
                )
            is_carryover = True
            carryover_source_id = prior.appraisal_id
            carryover_final_score = prior.final_score
            carryover_final_rating = prior.final_rating
            carryover_rating_label = prior.rating_label

        appraisal = Appraisal(
            organization_id=org_id,
            employee_id=employee_id,
            cycle_id=cycle_id,
            manager_id=manager_id,
            template_id=template_id,
            status=AppraisalStatus.COMPLETED if is_carryover else AppraisalStatus.DRAFT,
            is_prior_year_carryover=is_carryover,
            carryover_source_id=carryover_source_id,
            absence_months=absence_months,
            approved_absence_evidence=normalized_absence_evidence,
            final_score=carryover_final_score,
            final_rating=carryover_final_rating,
            rating_label=carryover_rating_label,
            completed_on=date.today() if is_carryover else None,
        )

        self.db.add(appraisal)
        self.db.flush()

        # Add KRA scores
        if kra_scores and not is_carryover:
            for score_data in kra_scores:
                score = AppraisalKRAScore(
                    organization_id=org_id,
                    appraisal_id=appraisal.appraisal_id,
                    kra_id=score_data["kra_id"],
                    weightage=score_data.get("weightage", Decimal("0")),
                )
                self.db.add(score)

        self.db.flush()
        return appraisal

    def update_appraisal(
        self,
        org_id: UUID,
        appraisal_id: UUID,
        **kwargs,
    ) -> Appraisal:
        """Update an appraisal."""
        self._ensure_private_write_mode(org_id)
        appraisal = self.get_appraisal(org_id, appraisal_id)
        self._ensure_not_prior_year_carryover(appraisal, action="update")

        template_id = kwargs.get("template_id")
        if template_id is not None:
            template = self.get_template(org_id, template_id)
            allowed_profiles = self.allowed_template_profiles_for_org(org_id)
            if template.template_profile not in allowed_profiles:
                raise PerformanceServiceError(
                    "Selected template profile is not allowed for this organization mode"
                )

        requested_status = kwargs.get("status")
        if requested_status is not None and requested_status != appraisal.status:
            allowed = APPRAISAL_STATUS_TRANSITIONS.get(appraisal.status, set())
            if requested_status not in allowed:
                raise AppraisalStatusError(
                    appraisal.status.value,
                    requested_status.value
                    if isinstance(requested_status, AppraisalStatus)
                    else str(requested_status),
                )

        if "absence_months" in kwargs:
            absence_months = kwargs["absence_months"]
            if absence_months is not None and absence_months < 0:
                raise PerformanceServiceError("Absence months cannot be negative")
            if absence_months is not None and absence_months > 6:
                evidence_candidate = kwargs.get(
                    "approved_absence_evidence", appraisal.approved_absence_evidence
                )
                normalized = self._normalize_absence_evidence(evidence_candidate)
                if not normalized:
                    raise PerformanceServiceError(
                        "Approved absence evidence is required when absence exceeds 6 months"
                    )
                kwargs["approved_absence_evidence"] = normalized
            elif absence_months is not None:
                kwargs["approved_absence_evidence"] = None
        elif "approved_absence_evidence" in kwargs:
            kwargs["approved_absence_evidence"] = self._normalize_absence_evidence(
                kwargs["approved_absence_evidence"]
            )

        if "approved_absence_evidence" in kwargs:
            appraisal.approved_absence_evidence = kwargs.pop(
                "approved_absence_evidence"
            )

        for key, value in kwargs.items():
            if value is not None and hasattr(appraisal, key):
                setattr(appraisal, key, value)
        self.db.flush()
        return appraisal

    def delete_appraisal(self, org_id: UUID, appraisal_id: UUID) -> None:
        """Delete an appraisal if still draft."""
        appraisal = self.get_appraisal(org_id, appraisal_id)
        if appraisal.status != AppraisalStatus.DRAFT:
            raise AppraisalStatusError(
                appraisal.status.value, AppraisalStatus.DRAFT.value
            )
        self.db.delete(appraisal)
        self.db.flush()

    def submit_self_assessment(
        self,
        org_id: UUID,
        appraisal_id: UUID,
        *,
        self_overall_rating: int,
        self_summary: str | None = None,
        achievements: str | None = None,
        challenges: str | None = None,
        development_needs: str | None = None,
        kra_ratings: list[dict] | None = None,
    ) -> Appraisal:
        """Submit employee self-assessment."""
        self._ensure_private_write_mode(org_id)
        appraisal = self.get_appraisal(org_id, appraisal_id)
        self._ensure_not_prior_year_carryover(
            appraisal, action="submit self-assessment"
        )

        if appraisal.status not in {
            AppraisalStatus.DRAFT,
            AppraisalStatus.SELF_ASSESSMENT,
        }:
            raise AppraisalStatusError(
                appraisal.status.value, AppraisalStatus.SELF_ASSESSMENT.value
            )
        self._enforce_phase_deadline(
            appraisal,
            deadline_field="self_assessment_deadline",
            phase_label="self-assessment",
        )

        appraisal.self_assessment_date = date.today()
        appraisal.self_overall_rating = self_overall_rating
        appraisal.self_summary = self_summary
        appraisal.achievements = achievements
        appraisal.challenges = challenges
        appraisal.development_needs = development_needs
        appraisal.status = AppraisalStatus.UNDER_REVIEW

        # Update KRA self ratings
        if kra_ratings:
            for rating in kra_ratings:
                score = self.db.get(AppraisalKRAScore, rating["score_id"])
                if score and score.appraisal_id == appraisal_id:
                    score.self_rating = rating["rating"]
                    score.self_comments = rating.get("comments")

        self.db.flush()
        return appraisal

    def submit_manager_review(
        self,
        org_id: UUID,
        appraisal_id: UUID,
        *,
        manager_overall_rating: int,
        manager_summary: str | None = None,
        manager_recommendations: str | None = None,
        kra_ratings: list[dict] | None = None,
    ) -> Appraisal:
        """Submit manager review."""
        self._ensure_private_write_mode(org_id)
        appraisal = self.get_appraisal(org_id, appraisal_id)
        self._ensure_not_prior_year_carryover(appraisal, action="submit manager review")

        if appraisal.status != AppraisalStatus.UNDER_REVIEW:
            raise AppraisalStatusError(
                appraisal.status.value, AppraisalStatus.CALIBRATION.value
            )
        self._enforce_phase_deadline(
            appraisal,
            deadline_field="manager_review_deadline",
            phase_label="manager review",
        )

        appraisal.manager_review_date = date.today()
        appraisal.manager_overall_rating = manager_overall_rating
        appraisal.manager_summary = manager_summary
        appraisal.manager_recommendations = manager_recommendations
        appraisal.status = AppraisalStatus.CALIBRATION

        # Update KRA manager ratings
        if kra_ratings:
            for rating in kra_ratings:
                score = self.db.get(AppraisalKRAScore, rating["score_id"])
                if score and score.appraisal_id == appraisal_id:
                    score.manager_rating = rating["rating"]
                    score.manager_comments = rating.get("comments")

        self.db.flush()
        return appraisal

    def submit_calibration(
        self,
        org_id: UUID,
        appraisal_id: UUID,
        *,
        calibrated_rating: int,
        calibration_notes: str | None = None,
        rating_label: str | None = None,
    ) -> Appraisal:
        """Submit HR calibration."""
        self._ensure_private_write_mode(org_id)
        appraisal = self.get_appraisal(org_id, appraisal_id)
        self._ensure_not_prior_year_carryover(appraisal, action="submit calibration")

        if appraisal.status != AppraisalStatus.CALIBRATION:
            raise AppraisalStatusError(
                appraisal.status.value, AppraisalStatus.COMPLETED.value
            )
        self._enforce_phase_deadline(
            appraisal,
            deadline_field="calibration_deadline",
            phase_label="calibration",
        )

        appraisal.calibration_date = date.today()
        appraisal.calibrated_rating = calibrated_rating
        appraisal.calibration_notes = calibration_notes
        appraisal.final_rating = calibrated_rating
        appraisal.rating_label = rating_label

        # Calculate final KRA scores
        for score in appraisal.kra_scores:
            score.final_rating = score.manager_rating or score.self_rating
            if score.final_rating and score.weightage:
                score.weighted_score = (
                    Decimal(str(score.final_rating)) * score.weightage / Decimal("100")
                ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # Calculate overall score
        total_weighted = sum(
            ((s.weighted_score or Decimal("0")) for s in appraisal.kra_scores),
            Decimal("0"),
        )
        appraisal.final_score = total_weighted

        # Proactive trigger: create PIP as soon as underperformance is detected.
        from app.services.people.perf.underperformance_service import (
            UnderperformanceService,
        )

        UnderperformanceService(self.db).ensure_pip_for_underperformance(
            org_id,
            appraisal_id=appraisal.appraisal_id,
            employee_id=appraisal.employee_id,
            final_score=appraisal.final_score,
            trigger_type="score_below_50",
        )
        self._ensure_underperformance_pip_resolution(org_id, appraisal)

        appraisal.status = AppraisalStatus.COMPLETED
        appraisal.completed_on = date.today()

        self.db.flush()
        return appraisal

    def reconcile_department_ratings(
        self,
        org_id: UUID,
        *,
        cycle_id: UUID,
        committee_level: str,
        reconciled_by_id: UUID,
        entries: list[dict],
        notes: str | None = None,
    ) -> dict:
        """
        Reconcile completed employee appraisal ratings at committee level.

        This implements explicit staff-committee reconciliation over completed
        appraisal results, supporting JUNIOR and SENIOR committee stages.
        """
        level = committee_level.strip().upper()
        if level not in {"JUNIOR", "SENIOR"}:
            raise PerformanceServiceError(
                "committee_level must be either JUNIOR or SENIOR"
            )
        if not entries:
            raise PerformanceServiceError(
                "At least one appraisal reconciliation entry is required"
            )

        adjusted = 0
        endorsed = 0
        processed_ids: list[UUID] = []
        for idx, entry in enumerate(entries, start=1):
            appraisal_id = entry.get("appraisal_id")
            if appraisal_id is None:
                raise PerformanceServiceError(f"Entry {idx} is missing appraisal_id")

            appraisal = self.get_appraisal(org_id, UUID(str(appraisal_id)))
            if appraisal.cycle_id != cycle_id:
                raise PerformanceServiceError(
                    f"Appraisal {appraisal.appraisal_id} is not in cycle {cycle_id}"
                )
            if appraisal.status != AppraisalStatus.COMPLETED:
                raise PerformanceServiceError(
                    f"Appraisal {appraisal.appraisal_id} must be COMPLETED for committee reconciliation"
                )

            proposed_rating_raw = entry.get("final_rating")
            if proposed_rating_raw is None:
                raise PerformanceServiceError(f"Entry {idx} is missing final_rating")
            proposed_rating = int(proposed_rating_raw)
            if proposed_rating < 1 or proposed_rating > 5:
                raise PerformanceServiceError(
                    f"Entry {idx} final_rating must be between 1 and 5"
                )

            prior_rating = appraisal.final_rating
            if prior_rating != proposed_rating:
                appraisal.calibrated_rating = proposed_rating
                appraisal.final_rating = proposed_rating
                appraisal.rating_label = self._rating_label_from_value(proposed_rating)
                appraisal.committee_decision = "ADJUSTED"
                adjusted += 1
            else:
                appraisal.committee_decision = "ENDORSED"
                endorsed += 1

            comment = str(entry.get("note") or "").strip()
            combined_notes: list[str] = [f"{level} STAFF COMMITTEE"]
            if notes:
                combined_notes.append(notes.strip())
            if comment:
                combined_notes.append(comment)
            appraisal.committee_notes = " | ".join(
                [segment for segment in combined_notes if segment]
            )
            appraisal.committee_review_date = date.today()
            processed_ids.append(appraisal.appraisal_id)

        self.db.flush()
        logger.info(
            "Department rating reconciliation completed: org=%s cycle=%s level=%s adjusted=%d endorsed=%d by=%s",
            org_id,
            cycle_id,
            level,
            adjusted,
            endorsed,
            reconciled_by_id,
        )
        return {
            "committee_level": level,
            "reconciled_by_id": str(reconciled_by_id),
            "cycle_id": str(cycle_id),
            "processed_count": len(processed_ids),
            "adjusted_count": adjusted,
            "endorsed_count": endorsed,
            "appraisal_ids": [str(aid) for aid in processed_ids],
        }

    # =========================================================================
    # Scorecards
    # =========================================================================

    def list_scorecards(
        self,
        org_id: UUID,
        *,
        employee_id: UUID | None = None,
        department_id: UUID | None = None,
        cycle_id: UUID | None = None,
        is_finalized: bool | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[Scorecard]:
        """List scorecards."""
        query = select(Scorecard).where(Scorecard.organization_id == org_id)

        if department_id:
            from app.models.people.hr.employee import Employee

            query = query.join(
                Employee, Scorecard.employee_id == Employee.employee_id
            ).where(
                Employee.department_id == department_id,
                Employee.organization_id == org_id,
            )

        if employee_id:
            query = query.where(Scorecard.employee_id == employee_id)

        if cycle_id:
            cycle = self.db.get(AppraisalCycle, cycle_id)
            if cycle and cycle.organization_id == org_id:
                query = query.where(
                    Scorecard.period_start >= cycle.review_period_start,
                    Scorecard.period_end <= cycle.review_period_end,
                )
            else:
                query = query.where(false())

        if is_finalized is not None:
            query = query.where(Scorecard.is_finalized == is_finalized)

        query = query.options(joinedload(Scorecard.items))
        query = query.order_by(Scorecard.period_start.desc())

        # Count total
        count_query = select(func.count()).select_from(query.subquery())
        total = self.db.scalar(count_query) or 0

        # Apply pagination
        if pagination:
            query = query.offset(pagination.offset).limit(pagination.limit)

        items = list(self.db.scalars(query).unique().all())

        return PaginatedResult(
            items=items,
            total=total,
            offset=pagination.offset if pagination else 0,
            limit=pagination.limit if pagination else len(items),
        )

    def get_scorecard(self, org_id: UUID, scorecard_id: UUID) -> Scorecard:
        """Get a scorecard by ID."""
        scorecard = self.db.scalar(
            select(Scorecard)
            .options(joinedload(Scorecard.items))
            .where(
                Scorecard.scorecard_id == scorecard_id,
                Scorecard.organization_id == org_id,
            )
        )
        if not scorecard:
            raise ScorecardNotFoundError(scorecard_id)
        return scorecard

    def create_scorecard(
        self,
        org_id: UUID,
        *,
        employee_id: UUID,
        period_start: date,
        period_end: date,
        period_label: str | None = None,
        items: list[dict] | None = None,
    ) -> Scorecard:
        """Create a new scorecard."""
        self._ensure_private_write_mode(org_id)
        scorecard = Scorecard(
            organization_id=org_id,
            employee_id=employee_id,
            period_start=period_start,
            period_end=period_end,
            period_label=period_label,
            is_finalized=False,
        )

        self.db.add(scorecard)
        self.db.flush()

        # Add scorecard items
        if items:
            for idx, item_data in enumerate(items):
                item = ScorecardItem(
                    organization_id=org_id,
                    scorecard_id=scorecard.scorecard_id,
                    perspective=item_data["perspective"],
                    metric_name=item_data["metric_name"],
                    target_value=item_data.get("target_value"),
                    unit_of_measure=item_data.get("unit_of_measure"),
                    weightage=item_data.get("weightage", Decimal("0")),
                    sequence=idx,
                    description=item_data.get("description"),
                )
                self.db.add(item)

        self.db.flush()
        return scorecard

    def generate_active_employee_scorecards(
        self,
        org_id: UUID,
        *,
        period_start: date,
        period_end: date,
        period_label: str | None = None,
    ) -> dict[str, int]:
        """Create missing scorecards for every active employee in the period."""
        self._ensure_private_write_mode(org_id)

        employees = list(
            self.db.scalars(
                select(Employee)
                .where(
                    Employee.organization_id == org_id,
                    Employee.status == EmployeeStatus.ACTIVE,
                )
                .order_by(Employee.employee_code)
            ).all()
        )
        employee_ids = [employee.employee_id for employee in employees]
        if not employee_ids:
            return {"created": 0, "skipped": 0, "employees": 0, "items": 0}

        existing_employee_ids = set(
            self.db.scalars(
                select(Scorecard.employee_id).where(
                    Scorecard.organization_id == org_id,
                    Scorecard.employee_id.in_(employee_ids),
                    Scorecard.period_start == period_start,
                    Scorecard.period_end == period_end,
                )
            ).all()
        )

        kpis_by_employee: dict[UUID, list[KPI]] = {
            employee_id: [] for employee_id in employee_ids
        }
        kpis = list(
            self.db.scalars(
                select(KPI).where(
                    KPI.organization_id == org_id,
                    KPI.employee_id.in_(employee_ids),
                    KPI.period_start <= period_end,
                    KPI.period_end >= period_start,
                    KPI.status.notin_([KPIStatus.CANCELLED, KPIStatus.DEFERRED]),
                )
            ).all()
        )
        for kpi in kpis:
            kpis_by_employee.setdefault(kpi.employee_id, []).append(kpi)

        created = 0
        skipped = 0
        item_count = 0
        for employee in employees:
            if employee.employee_id in existing_employee_ids:
                skipped += 1
                continue

            scorecard = Scorecard(
                organization_id=org_id,
                employee_id=employee.employee_id,
                period_start=period_start,
                period_end=period_end,
                period_label=period_label,
                is_finalized=False,
            )
            self.db.add(scorecard)
            self.db.flush()

            for idx, kpi in enumerate(kpis_by_employee.get(employee.employee_id, [])):
                metric_key = self._sync_kpi_actual_from_system_metric(org_id, kpi)
                perspective = self._scorecard_perspective_from_kpi(kpi) or (
                    "CUSTOMER" if metric_key else "PROCESS"
                )
                item = ScorecardItem(
                    organization_id=org_id,
                    scorecard_id=scorecard.scorecard_id,
                    perspective=perspective,
                    metric_name=kpi.kpi_name,
                    description=kpi.description or kpi.notes,
                    target_value=kpi.target_value,
                    actual_value=kpi.actual_value,
                    unit_of_measure=kpi.unit_of_measure,
                    weightage=kpi.weightage or Decimal("0"),
                    status=kpi.status.value if kpi.status else None,
                    sequence=idx,
                )
                self._apply_scorecard_item_score(
                    item,
                    lower_is_better=metric_key in LOWER_IS_BETTER_SUPPORT_METRICS,
                )
                self.db.add(item)
                item_count += 1

            created += 1

        self.db.flush()
        return {
            "created": created,
            "skipped": skipped,
            "employees": len(employees),
            "items": item_count,
        }

    def populate_scorecard_from_kpis(
        self,
        org_id: UUID,
        scorecard_id: UUID,
    ) -> dict[str, int]:
        """Add missing KPI metrics to an existing scorecard."""
        self._ensure_private_write_mode(org_id)
        scorecard = self.get_scorecard(org_id, scorecard_id)
        if scorecard.is_finalized:
            raise PerformanceServiceError("Cannot update finalized scorecard")

        existing_items_by_name = {
            (item.metric_name or "").strip().lower(): item for item in scorecard.items
        }
        kpis = list(
            self.db.scalars(
                select(KPI).where(
                    KPI.organization_id == org_id,
                    KPI.employee_id == scorecard.employee_id,
                    KPI.period_start <= scorecard.period_end,
                    KPI.period_end >= scorecard.period_start,
                    KPI.status.notin_([KPIStatus.CANCELLED, KPIStatus.DEFERRED]),
                )
            ).all()
        )

        added = 0
        updated = 0
        for kpi in kpis:
            metric_name_key = kpi.kpi_name.strip().lower()
            metric_key = self._sync_kpi_actual_from_system_metric(org_id, kpi)
            perspective = self._scorecard_perspective_from_kpi(kpi) or (
                "CUSTOMER" if metric_key else "PROCESS"
            )
            existing_item = existing_items_by_name.get(metric_name_key)
            if existing_item is not None:
                existing_item.perspective = perspective
                existing_item.target_value = kpi.target_value
                existing_item.actual_value = kpi.actual_value
                existing_item.unit_of_measure = kpi.unit_of_measure
                existing_item.weightage = kpi.weightage or Decimal("0")
                existing_item.status = kpi.status.value if kpi.status else None
                self._apply_scorecard_item_score(
                    existing_item,
                    lower_is_better=metric_key in LOWER_IS_BETTER_SUPPORT_METRICS,
                )
                updated += 1
                continue

            item = ScorecardItem(
                organization_id=org_id,
                scorecard_id=scorecard.scorecard_id,
                perspective=perspective,
                metric_name=kpi.kpi_name,
                description=kpi.description or kpi.notes,
                target_value=kpi.target_value,
                actual_value=kpi.actual_value,
                unit_of_measure=kpi.unit_of_measure,
                weightage=kpi.weightage or Decimal("0"),
                status=kpi.status.value if kpi.status else None,
                sequence=len(scorecard.items) + added,
            )
            self._apply_scorecard_item_score(
                item,
                lower_is_better=metric_key in LOWER_IS_BETTER_SUPPORT_METRICS,
            )
            self.db.add(item)
            existing_items_by_name[metric_name_key] = item
            added += 1

        self.db.flush()
        return {"added": added, "updated": updated, "available": len(kpis)}

    def update_scorecard_item(
        self,
        org_id: UUID,
        scorecard_id: UUID,
        item_id: UUID,
        *,
        actual_value: Decimal,
    ) -> ScorecardItem:
        """Update a scorecard item's actual value."""
        self._ensure_private_write_mode(org_id)
        scorecard = self.get_scorecard(org_id, scorecard_id)

        if scorecard.is_finalized:
            raise PerformanceServiceError("Cannot update finalized scorecard")

        item = self.db.get(ScorecardItem, item_id)
        if not item or item.scorecard_id != scorecard_id:
            raise PerformanceServiceError(f"Item {item_id} not found in scorecard")

        item.actual_value = actual_value

        # Calculate score (0-100 based on target achievement)
        if item.target_value and item.target_value > 0:
            item.score = (actual_value / item.target_value * 100).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            # Cap at 100
            if item.score > 100:
                item.score = Decimal("100")

        # Calculate weighted score
        if item.score and item.weightage:
            item.weighted_score = (
                item.score * item.weightage / Decimal("100")
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        self.db.flush()
        return item

    def finalize_scorecard(
        self,
        org_id: UUID,
        scorecard_id: UUID,
        *,
        summary: str | None = None,
    ) -> Scorecard:
        """Finalize a scorecard."""
        scorecard = self.get_scorecard(org_id, scorecard_id)

        if scorecard.is_finalized:
            raise PerformanceServiceError("Scorecard is already finalized")

        # Calculate perspective scores
        perspectives: dict[str, list[Decimal]] = {
            "FINANCIAL": [],
            "CUSTOMER": [],
            "PROCESS": [],
            "LEARNING": [],
        }

        for item in scorecard.items:
            if item.perspective in perspectives and item.weighted_score:
                perspectives[item.perspective].append(item.weighted_score)

        scorecard.financial_score = (
            sum(perspectives["FINANCIAL"], Decimal("0"))
            if perspectives["FINANCIAL"]
            else None
        )
        scorecard.customer_score = (
            sum(perspectives["CUSTOMER"], Decimal("0"))
            if perspectives["CUSTOMER"]
            else None
        )
        scorecard.process_score = (
            sum(perspectives["PROCESS"], Decimal("0"))
            if perspectives["PROCESS"]
            else None
        )
        scorecard.learning_score = (
            sum(perspectives["LEARNING"], Decimal("0"))
            if perspectives["LEARNING"]
            else None
        )

        # Calculate overall score
        all_weighted = sum(
            ((item.weighted_score or Decimal("0")) for item in scorecard.items),
            Decimal("0"),
        )
        scorecard.overall_score = all_weighted

        # Determine rating (1-5 based on score)
        if scorecard.overall_score:
            if scorecard.overall_score >= 90:
                scorecard.overall_rating = 5
                scorecard.rating_label = "Exceptional"
            elif scorecard.overall_score >= 80:
                scorecard.overall_rating = 4
                scorecard.rating_label = "Exceeds Expectations"
            elif scorecard.overall_score >= 70:
                scorecard.overall_rating = 3
                scorecard.rating_label = "Meets Expectations"
            elif scorecard.overall_score >= 60:
                scorecard.overall_rating = 2
                scorecard.rating_label = "Needs Improvement"
            else:
                scorecard.overall_rating = 1
                scorecard.rating_label = "Unsatisfactory"

        scorecard.summary = summary
        scorecard.is_finalized = True
        scorecard.finalized_on = date.today()

        self.db.flush()
        return scorecard

    # =========================================================================
    # Reporting
    # =========================================================================

    def get_performance_stats(self, org_id: UUID) -> dict:
        """Get performance statistics for dashboard."""
        # Active cycles
        active_cycles = (
            self.db.scalar(
                select(func.count(AppraisalCycle.cycle_id)).where(
                    AppraisalCycle.organization_id == org_id,
                    AppraisalCycle.status == AppraisalCycleStatus.ACTIVE,
                )
            )
            or 0
        )

        # Pending self assessment
        pending_self = (
            self.db.scalar(
                select(func.count(Appraisal.appraisal_id)).where(
                    Appraisal.organization_id == org_id,
                    Appraisal.status.in_(
                        [AppraisalStatus.DRAFT, AppraisalStatus.SELF_ASSESSMENT]
                    ),
                )
            )
            or 0
        )

        # Pending manager review
        pending_manager = (
            self.db.scalar(
                select(func.count(Appraisal.appraisal_id)).where(
                    Appraisal.organization_id == org_id,
                    Appraisal.status == AppraisalStatus.UNDER_REVIEW,
                )
            )
            or 0
        )

        # Pending calibration
        pending_calibration = (
            self.db.scalar(
                select(func.count(Appraisal.appraisal_id)).where(
                    Appraisal.organization_id == org_id,
                    Appraisal.status == AppraisalStatus.CALIBRATION,
                )
            )
            or 0
        )

        # Completed appraisals (this year)
        year_start = date(date.today().year, 1, 1)
        completed = (
            self.db.scalar(
                select(func.count(Appraisal.appraisal_id)).where(
                    Appraisal.organization_id == org_id,
                    Appraisal.status == AppraisalStatus.COMPLETED,
                    Appraisal.completed_on >= year_start,
                )
            )
            or 0
        )

        # Average rating
        avg_rating = self.db.scalar(
            select(func.avg(Appraisal.final_rating)).where(
                Appraisal.organization_id == org_id,
                Appraisal.status == AppraisalStatus.COMPLETED,
            )
        )

        return {
            "active_cycles": active_cycles,
            "pending_self_assessment": pending_self,
            "pending_manager_review": pending_manager,
            "pending_calibration": pending_calibration,
            "completed_appraisals": completed,
            "average_rating": Decimal(str(avg_rating)).quantize(Decimal("0.1"))
            if avg_rating
            else None,
        }

    def get_cycle_statistics(self, org_id: UUID, cycle_id: UUID) -> dict:
        """Get statistics for a specific appraisal cycle."""
        self.get_cycle(org_id, cycle_id)

        total = (
            self.db.scalar(
                select(func.count(Appraisal.appraisal_id)).where(
                    Appraisal.organization_id == org_id,
                    Appraisal.cycle_id == cycle_id,
                )
            )
            or 0
        )

        results = self.db.execute(
            select(Appraisal.status, func.count(Appraisal.appraisal_id))
            .where(
                Appraisal.organization_id == org_id,
                Appraisal.cycle_id == cycle_id,
            )
            .group_by(Appraisal.status)
        ).all()
        status_counts = {status.value: count for status, count in results}

        avg_final_rating = self.db.scalar(
            select(func.avg(Appraisal.final_rating)).where(
                Appraisal.organization_id == org_id,
                Appraisal.cycle_id == cycle_id,
                Appraisal.final_rating.isnot(None),
            )
        )

        return {
            "cycle_id": cycle_id,
            "total": total,
            "status_counts": status_counts,
            "average_final_rating": float(avg_final_rating)
            if avg_final_rating is not None
            else None,
        }

    # ─────────────────────────────────────────────────────────────────────────────
    # Performance Reports
    # ─────────────────────────────────────────────────────────────────────────────

    def get_ratings_distribution_report(
        self,
        org_id: UUID,
        *,
        cycle_id: UUID | None = None,
    ) -> dict:
        """Get performance ratings distribution report.

        Returns rating distribution across all completed appraisals,
        optionally filtered by cycle.
        """
        # Base filter for completed appraisals
        filters = [
            Appraisal.organization_id == org_id,
            Appraisal.status == AppraisalStatus.COMPLETED,
            Appraisal.final_rating.isnot(None),
        ]
        if cycle_id:
            filters.append(Appraisal.cycle_id == cycle_id)

        # Get rating distribution (1-5 scale)
        results = self.db.execute(
            select(Appraisal.final_rating, func.count(Appraisal.appraisal_id))
            .where(*filters)
            .group_by(Appraisal.final_rating)
            .order_by(Appraisal.final_rating.desc())
        ).all()

        total_appraisals = sum(count for _, count in results)

        # Build distribution
        rating_labels = {
            5: "Exceptional",
            4: "Exceeds Expectations",
            3: "Meets Expectations",
            2: "Needs Improvement",
            1: "Unsatisfactory",
        }

        distribution = []
        rating_counts = {rating: count for rating, count in results}

        for rating in range(5, 0, -1):
            count = rating_counts.get(rating, 0)
            pct = (
                round(count / total_appraisals * 100, 1) if total_appraisals > 0 else 0
            )
            distribution.append(
                {
                    "rating": rating,
                    "label": rating_labels[rating],
                    "count": count,
                    "percentage": pct,
                }
            )

        # Calculate average rating
        avg_rating = self.db.scalar(
            select(func.avg(Appraisal.final_rating)).where(*filters)
        )

        # Get cycles for filter dropdown
        cycles = self.db.scalars(
            select(AppraisalCycle)
            .where(AppraisalCycle.organization_id == org_id)
            .order_by(AppraisalCycle.created_at.desc())
        ).all()

        return {
            "distribution": distribution,
            "total_appraisals": total_appraisals,
            "average_rating": round(float(avg_rating), 1) if avg_rating else None,
            "cycles": cycles,
            "selected_cycle_id": cycle_id,
        }

    def get_performance_by_department_report(
        self,
        org_id: UUID,
        *,
        cycle_id: UUID | None = None,
    ) -> dict:
        """Get performance breakdown by department.

        Returns average ratings and appraisal counts per department.
        """
        from app.models.people.hr import Department, Employee

        # Base filters
        filters = [
            Appraisal.organization_id == org_id,
            Appraisal.status == AppraisalStatus.COMPLETED,
        ]
        if cycle_id:
            filters.append(Appraisal.cycle_id == cycle_id)

        # Query aggregated by department
        results = self.db.execute(
            select(
                Department.department_id,
                Department.department_name,
                func.count(Appraisal.appraisal_id).label("appraisal_count"),
                func.avg(Appraisal.final_rating).label("avg_rating"),
                func.avg(Appraisal.final_score).label("avg_score"),
            )
            .select_from(Appraisal)
            .join(Employee, Appraisal.employee_id == Employee.employee_id)
            .join(Department, Employee.department_id == Department.department_id)
            .where(*filters)
            .group_by(Department.department_id, Department.department_name)
            .order_by(func.avg(Appraisal.final_rating).desc())
        ).all()

        departments = []
        total_appraisals = sum(r.appraisal_count for r in results)

        for row in results:
            departments.append(
                {
                    "department_id": row.department_id,
                    "department_name": row.department_name,
                    "appraisal_count": row.appraisal_count,
                    "average_rating": round(float(row.avg_rating), 1)
                    if row.avg_rating
                    else None,
                    "average_score": round(float(row.avg_score), 1)
                    if row.avg_score
                    else None,
                    "percentage": round(row.appraisal_count / total_appraisals * 100, 1)
                    if total_appraisals > 0
                    else 0,
                }
            )

        # Overall stats
        overall_avg = self.db.scalar(
            select(func.avg(Appraisal.final_rating)).where(*filters)
        )

        return {
            "departments": departments,
            "total_departments": len(departments),
            "total_appraisals": total_appraisals,
            "overall_average_rating": round(float(overall_avg), 1)
            if overall_avg
            else None,
        }

    def get_kpi_achievement_report(
        self,
        org_id: UUID,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
        department_id: UUID | None = None,
    ) -> dict:
        """Get KPI achievement rates report.

        Returns KPI achievement statistics by status and category.
        """
        from app.models.people.hr import Employee

        # Base filters
        filters = [KPI.organization_id == org_id]

        if department_id:
            subquery = select(Employee.employee_id).where(
                Employee.department_id == department_id,
                Employee.organization_id == org_id,
            )
            filters.append(KPI.employee_id.in_(subquery))
        if start_date:
            filters.append(KPI.period_start >= start_date)
        if end_date:
            filters.append(KPI.period_end <= end_date)

        # Status breakdown
        status_results = self.db.execute(
            select(KPI.status, func.count(KPI.kpi_id))
            .where(*filters)
            .group_by(KPI.status)
        ).all()

        total_kpis = sum(count for _, count in status_results)
        status_breakdown = []

        for status, count in status_results:
            status_breakdown.append(
                {
                    "status": status.value,
                    "count": count,
                    "percentage": round(count / total_kpis * 100, 1)
                    if total_kpis > 0
                    else 0,
                }
            )

        # Achievement statistics for achieved KPIs
        completed_kpis = self.db.scalars(
            select(KPI).where(
                *filters,
                KPI.status == KPIStatus.ACHIEVED,
                KPI.target_value.isnot(None),
                KPI.actual_value.isnot(None),
            )
        ).all()

        achieved = 0
        exceeded = 0
        partial = 0

        for kpi in completed_kpis:
            if kpi.actual_value is None or kpi.target_value is None:
                continue
            if kpi.actual_value >= kpi.target_value:
                if kpi.actual_value > kpi.target_value:
                    exceeded += 1
                else:
                    achieved += 1
            else:
                partial += 1

        total_completed = len(completed_kpis)
        achievement_stats = {
            "total_completed": total_completed,
            "achieved": achieved,
            "exceeded": exceeded,
            "partial": partial,
            "achievement_rate": round((achieved + exceeded) / total_completed * 100, 1)
            if total_completed > 0
            else 0,
        }

        # Top performing KPIs (by achievement percentage)
        class TopKPIEntry(TypedDict):
            kpi_id: UUID
            kpi_title: str
            employee_id: UUID
            achievement_percentage: float

        top_kpis: list[TopKPIEntry] = []
        for kpi in completed_kpis:
            if kpi.target_value is None or kpi.actual_value is None:
                continue
            if kpi.target_value > 0:
                achievement_pct = float(kpi.actual_value / kpi.target_value * 100)
                if achievement_pct >= 100:
                    top_kpis.append(
                        {
                            "kpi_id": kpi.kpi_id,
                            "kpi_title": kpi.kpi_name,
                            "employee_id": kpi.employee_id,
                            "achievement_percentage": round(achievement_pct, 1),
                        }
                    )

        top_kpis.sort(key=lambda x: x["achievement_percentage"], reverse=True)

        return {
            "total_kpis": total_kpis,
            "status_breakdown": status_breakdown,
            "achievement_stats": achievement_stats,
            "top_kpis": top_kpis[:10],
        }

    def get_performance_trends_report(
        self,
        org_id: UUID,
        *,
        department_id: UUID | None = None,
    ) -> dict:
        """Get performance trends across cycles.

        Returns historical performance data by appraisal cycle.
        """
        from app.models.people.hr import Employee

        employee_subquery = None
        if department_id:
            employee_subquery = select(Employee.employee_id).where(
                Employee.department_id == department_id,
                Employee.organization_id == org_id,
            )

        # Get all cycles with their statistics
        cycles = self.db.scalars(
            select(AppraisalCycle)
            .where(AppraisalCycle.organization_id == org_id)
            .order_by(AppraisalCycle.review_period_start.desc())
        ).all()

        cycle_data = []
        all_ratings = []

        for cycle in cycles:
            # Get stats for this cycle
            base_filters = [
                Appraisal.organization_id == org_id,
                Appraisal.cycle_id == cycle.cycle_id,
            ]
            if employee_subquery is not None:
                base_filters.append(Appraisal.employee_id.in_(employee_subquery))

            total = (
                self.db.scalar(
                    select(func.count(Appraisal.appraisal_id)).where(*base_filters)
                )
                or 0
            )

            completed = (
                self.db.scalar(
                    select(func.count(Appraisal.appraisal_id)).where(
                        *base_filters,
                        Appraisal.status == AppraisalStatus.COMPLETED,
                    )
                )
                or 0
            )

            avg_rating = self.db.scalar(
                select(func.avg(Appraisal.final_rating)).where(
                    *base_filters,
                    Appraisal.final_rating.isnot(None),
                )
            )

            completion_rate = round(completed / total * 100, 1) if total > 0 else 0

            cycle_data.append(
                {
                    "cycle_id": cycle.cycle_id,
                    "cycle_name": cycle.cycle_name,
                    "period_start": cycle.review_period_start,
                    "period_end": cycle.review_period_end,
                    "status": cycle.status.value,
                    "total_appraisals": total,
                    "completed_appraisals": completed,
                    "completion_rate": completion_rate,
                    "average_rating": round(float(avg_rating), 1)
                    if avg_rating
                    else None,
                }
            )

            if avg_rating:
                all_ratings.append(float(avg_rating))

        # Overall trend
        overall_avg = (
            round(sum(all_ratings) / len(all_ratings), 1) if all_ratings else None
        )

        return {
            "cycles": cycle_data,
            "total_cycles": len(cycles),
            "overall_average_rating": overall_avg,
        }

    # ─────────────────────────────────────────────────────────────────────────────
    # 360° Feedback
    # ─────────────────────────────────────────────────────────────────────────────

    def list_feedback(
        self,
        org_id: UUID,
        *,
        appraisal_id: UUID | None = None,
        feedback_from_id: UUID | None = None,
        feedback_type: str | None = None,
        submitted: bool | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[AppraisalFeedback]:
        """List feedback entries."""
        query = select(AppraisalFeedback).where(
            AppraisalFeedback.organization_id == org_id
        )

        if appraisal_id:
            query = query.where(AppraisalFeedback.appraisal_id == appraisal_id)

        if feedback_from_id:
            query = query.where(AppraisalFeedback.feedback_from_id == feedback_from_id)

        if feedback_type:
            query = query.where(AppraisalFeedback.feedback_type == feedback_type)

        if submitted is not None:
            if submitted:
                query = query.where(AppraisalFeedback.submitted_on.isnot(None))
            else:
                query = query.where(AppraisalFeedback.submitted_on.is_(None))

        query = query.options(
            joinedload(AppraisalFeedback.feedback_from),
            joinedload(AppraisalFeedback.appraisal).joinedload(Appraisal.employee),
        )
        query = query.order_by(AppraisalFeedback.created_at.desc())

        count_query = select(func.count()).select_from(query.subquery())
        total = self.db.scalar(count_query) or 0

        if pagination:
            query = query.offset(pagination.offset).limit(pagination.limit)

        items = list(self.db.scalars(query).unique().all())

        return PaginatedResult(
            items=items,
            total=total,
            offset=pagination.offset if pagination else 0,
            limit=pagination.limit if pagination else len(items),
        )

    def get_feedback(self, org_id: UUID, feedback_id: UUID) -> AppraisalFeedback:
        """Get feedback by ID."""
        feedback = self.db.scalar(
            select(AppraisalFeedback)
            .options(
                joinedload(AppraisalFeedback.feedback_from),
                joinedload(AppraisalFeedback.appraisal).joinedload(Appraisal.employee),
            )
            .where(
                AppraisalFeedback.feedback_id == feedback_id,
                AppraisalFeedback.organization_id == org_id,
            )
        )
        if not feedback:
            raise PerformanceServiceError(f"Feedback {feedback_id} not found")
        return feedback

    def request_feedback(
        self,
        org_id: UUID,
        *,
        appraisal_id: UUID,
        feedback_from_id: UUID,
        feedback_type: str,
        is_anonymous: bool = False,
    ) -> AppraisalFeedback:
        """Request feedback from an employee."""
        # Verify appraisal exists
        self.get_appraisal(org_id, appraisal_id)

        feedback = AppraisalFeedback(
            organization_id=org_id,
            appraisal_id=appraisal_id,
            feedback_from_id=feedback_from_id,
            feedback_type=feedback_type,
            is_anonymous=is_anonymous,
        )
        self.db.add(feedback)
        self.db.flush()
        return feedback

    def submit_feedback(
        self,
        org_id: UUID,
        feedback_id: UUID,
        *,
        overall_rating: int | None = None,
        strengths: str | None = None,
        areas_for_improvement: str | None = None,
        general_comments: str | None = None,
    ) -> AppraisalFeedback:
        """Submit feedback."""
        feedback = self.get_feedback(org_id, feedback_id)

        feedback.overall_rating = overall_rating
        feedback.strengths = strengths
        feedback.areas_for_improvement = areas_for_improvement
        feedback.general_comments = general_comments
        feedback.submitted_on = date.today()

        self.db.flush()
        return feedback

    def delete_feedback(self, org_id: UUID, feedback_id: UUID) -> None:
        """Delete a feedback request."""
        feedback = self.get_feedback(org_id, feedback_id)
        if feedback.submitted_on:
            raise PerformanceServiceError("Cannot delete submitted feedback")
        self.db.delete(feedback)
        self.db.flush()

    def get_pending_feedback_for_employee(
        self, org_id: UUID, employee_id: UUID
    ) -> list[AppraisalFeedback]:
        """Get pending feedback requests for an employee."""
        result = (
            self.db.scalars(
                select(AppraisalFeedback)
                .options(
                    joinedload(AppraisalFeedback.appraisal).joinedload(
                        Appraisal.employee
                    ),
                )
                .where(
                    AppraisalFeedback.organization_id == org_id,
                    AppraisalFeedback.feedback_from_id == employee_id,
                    AppraisalFeedback.submitted_on.is_(None),
                )
                .order_by(AppraisalFeedback.created_at.desc())
            )
            .unique()
            .all()
        )
        return list(result)
