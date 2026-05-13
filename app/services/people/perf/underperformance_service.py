"""
Underperformance Detection Service — OHCSF Performance Management System.

Detects employees who trigger Performance Improvement Plan (PIP) criteria
based on OHCSF guidelines:

  - Annual trigger: Fair (raw_score_percentage < 70) on ≥ 50% of KPIs in
    year-end appraisal.
  - Quarterly trigger: 3 quarterly appraisals with composite score < 70.
  - Probation milestone: Employees approaching 21 months of service without
    a confirmation date need a final Progress Report.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.services.people.hr.org_resolver import OrgResolver

logger = logging.getLogger(__name__)

__all__ = [
    "UnderperformanceServiceError",
    "UnderperformanceService",
]

# Thresholds (OHCSF guidelines)
_FAIR_SCORE_THRESHOLD = Decimal("70")  # Below this = "Fair" rating
_FAIR_KPI_RATIO_THRESHOLD = Decimal("0.5")  # ≥ 50% fair KPIs triggers annual PIP
_QUARTERLY_BELOW_TRIGGER = 3  # 3 quarters below threshold triggers PIP
_APPRAISAL_UNDERPERFORMANCE_TRIGGER = Decimal("50.00")  # < 50 triggers PIP
_PROBATION_MONTHS = 21  # Milestone at 21 months of service
_PROBATION_WINDOW_DAYS = 30  # Flag within 30 days of milestone


# =============================================================================
# Error classes
# =============================================================================


class UnderperformanceServiceError(Exception):
    """Base error for UnderperformanceService."""


# =============================================================================
# Service
# =============================================================================


class UnderperformanceService:
    """
    Service for detecting underperformance triggers and probation milestones.

    All detection methods query the database for a given org/cycle, then
    evaluate each result against threshold logic. The internal helper methods
    (_evaluate_*) are pure-logic functions that can be tested without a DB.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    # -------------------------------------------------------------------------
    # Public API — database-backed detection
    # -------------------------------------------------------------------------

    def detect_annual_trigger(
        self,
        org_id: UUID,
        cycle_id: UUID,
    ) -> list[dict[str, Any]]:
        """
        Find employees who should be flagged for PIP via the annual trigger.

        An employee is flagged when their year-end appraisal has ≥ 50% of
        scored KPIs rated "Fair" (raw_score_percentage < 70).

        Args:
            org_id: Organisation to scope the query.
            cycle_id: Appraisal cycle to evaluate.

        Returns:
            List of dicts with keys:
              employee_id, appraisal_id, fair_count, total_kpis, percentage
        """
        from app.models.people.perf.appraisal import Appraisal

        stmt = select(Appraisal).where(
            and_(
                Appraisal.organization_id == org_id,
                Appraisal.cycle_id == cycle_id,
                Appraisal.is_quarterly == False,  # noqa: E712
            )
        )
        appraisals = list(self.db.scalars(stmt).all())
        logger.info(
            "detect_annual_trigger: found %d year-end appraisals for cycle %s",
            len(appraisals),
            cycle_id,
        )

        flagged: list[dict[str, Any]] = []
        for appraisal in appraisals:
            result = self._evaluate_annual_trigger(appraisal)
            if result is not None:
                flagged.append(result)

        logger.info(
            "detect_annual_trigger: flagged %d employees in cycle %s",
            len(flagged),
            cycle_id,
        )
        return flagged

    def detect_quarterly_trigger(
        self,
        org_id: UUID,
        cycle_id: UUID,
    ) -> list[dict[str, Any]]:
        """
        Find employees who should be flagged for PIP via the quarterly trigger.

        An employee is flagged when they have 3 or more quarterly appraisals
        in the cycle with a composite final_score below 70.

        Args:
            org_id: Organisation to scope the query.
            cycle_id: Appraisal cycle to evaluate.

        Returns:
            List of dicts with keys:
              employee_id, quarters_below, quarterly_scores
        """
        from app.models.people.perf.appraisal import Appraisal

        stmt = (
            select(Appraisal)
            .where(
                and_(
                    Appraisal.organization_id == org_id,
                    Appraisal.cycle_id == cycle_id,
                    Appraisal.is_quarterly == True,  # noqa: E712
                )
            )
            .order_by(Appraisal.employee_id)
        )
        quarterly_appraisals = list(self.db.scalars(stmt).all())
        logger.info(
            "detect_quarterly_trigger: found %d quarterly appraisals for cycle %s",
            len(quarterly_appraisals),
            cycle_id,
        )

        # Group by employee
        employee_scores: dict[UUID, list[float]] = {}
        for appraisal in quarterly_appraisals:
            if appraisal.final_score is None:
                continue
            emp_id = appraisal.employee_id
            employee_scores.setdefault(emp_id, [])
            employee_scores[emp_id].append(float(appraisal.final_score))

        flagged: list[dict[str, Any]] = []
        for emp_id, scores in employee_scores.items():
            result = self._evaluate_quarterly_trigger(
                employee_id=emp_id,
                quarterly_scores=scores,
            )
            if result is not None:
                flagged.append(result)

        logger.info(
            "detect_quarterly_trigger: flagged %d employees in cycle %s",
            len(flagged),
            cycle_id,
        )
        return flagged

    def check_probation_milestones(self, org_id: UUID) -> list[dict[str, Any]]:
        """
        Find active employees approaching their 21-month probation milestone.

        Flags employees whose 21-month service anniversary falls within the
        next 30 days AND who have not yet been confirmed (confirmation_date is
        None).

        Args:
            org_id: Organisation to scope the query.

        Returns:
            List of dicts with keys:
              employee_id, date_of_joining, months_of_service, milestone_date
        """
        from app.models.people.hr.employee import Employee, EmployeeStatus

        today = date.today()
        # Milestone window: today to today + 30 days
        # milestone_date = date_of_joining + ~21 months
        # We query broadly (joining date range) and filter precisely in Python
        # to avoid calendar arithmetic edge cases in SQL.
        # Flag employees whose 21-month milestone is near "now" (not only upcoming).
        # This avoids missing end-of-month edge cases where join_date day clamps.
        window_start = today - timedelta(days=_PROBATION_WINDOW_DAYS)
        window_end = today + timedelta(days=_PROBATION_WINDOW_DAYS)

        # Approximate the join date range corresponding to the 21-month window
        # 21 months ≈ 630–640 days; use 620–650 to be safe
        lower_join = window_start - timedelta(days=21 * 31)
        upper_join = window_end - timedelta(days=21 * 28)

        stmt = select(Employee).where(
            and_(
                Employee.organization_id == org_id,
                Employee.status == EmployeeStatus.ACTIVE,
                Employee.confirmation_date.is_(None),
                Employee.date_of_joining >= lower_join,
                Employee.date_of_joining <= upper_join,
            )
        )
        candidates = list(self.db.scalars(stmt).all())
        logger.info(
            "check_probation_milestones: %d candidates for org %s",
            len(candidates),
            org_id,
        )

        milestones: list[dict[str, Any]] = []
        for emp in candidates:
            result = self._evaluate_probation_milestone(employee=emp)
            if result is not None:
                milestones.append(result)

        logger.info(
            "check_probation_milestones: %d employees due milestone for org %s",
            len(milestones),
            org_id,
        )
        return milestones

    def flag_for_pip(
        self,
        org_id: UUID,
        employee_id: UUID,
        *,
        trigger_type: str,
        triggering_appraisal_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Create a draft PIP and notify HR and supervisor."""
        from sqlalchemy import func as sa_func

        from app.models.people.hr.employee import Employee
        from app.models.people.perf.appraisal_outcome_action import (
            AppraisalOutcomeAction,
        )
        from app.models.people.perf.pip import PerformanceImprovementPlan
        from app.models.people.perf.pms_enums import (  # noqa: F401
            OutcomeActionStatus,
            OutcomeActionType,
            PIPCauseCategory,
            PIPStatus,
        )

        employee = self.db.scalar(
            select(Employee).where(
                Employee.employee_id == employee_id,
                Employee.organization_id == org_id,
            )
        )
        if not employee:
            raise UnderperformanceServiceError(f"Employee {employee_id} not found")

        # Generate PIP code
        count = (
            self.db.scalar(
                select(sa_func.count(PerformanceImprovementPlan.pip_id)).where(
                    PerformanceImprovementPlan.organization_id == org_id,
                )
            )
            or 0
        )
        pip_code = f"PIP-{date.today().year}-{count + 1:04d}"

        supervisor = OrgResolver(self.db).get_manager(employee.employee_id, org_id)
        pip = PerformanceImprovementPlan(
            organization_id=org_id,
            employee_id=employee_id,
            supervisor_id=supervisor.employee_id if supervisor else employee_id,
            hr_officer_id=employee_id,  # Placeholder — HR assigns later
            pip_code=pip_code,
            start_date=date.today(),
            end_date=date.today() + timedelta(days=183),
            reason=f"Auto-flagged: {trigger_type} trigger",
            cause_category=PIPCauseCategory.SKILLS,  # Default — supervisor refines
            improvement_areas=[
                {
                    "area": "To be defined by supervisor",
                    "current_level": "Below threshold",
                    "expected_level": "Good",
                }
            ],
            appraisal_id=triggering_appraisal_id,
        )
        self.db.add(pip)

        if triggering_appraisal_id is not None:
            self.db.add(
                AppraisalOutcomeAction(
                    organization_id=org_id,
                    appraisal_id=triggering_appraisal_id,
                    action_type=OutcomeActionType.PIP,
                    description=f"PIP initiated: {pip_code} ({trigger_type})",
                    actioned_date=date.today(),
                    reference_id=pip.pip_id,
                    reference_type="pip",
                    status=OutcomeActionStatus.PENDING,
                    notes="Auto-created from underperformance trigger",
                )
            )

        self.db.flush()

        logger.info(
            "Flagged employee %s for PIP: %s (trigger: %s)",
            employee_id,
            pip_code,
            trigger_type,
        )
        return {
            "status": "flagged",
            "pip_id": str(pip.pip_id),
            "pip_code": pip_code,
            "employee_id": str(employee_id),
            "trigger_type": trigger_type,
        }

    def ensure_pip_for_underperformance(
        self,
        org_id: UUID,
        *,
        appraisal_id: UUID,
        employee_id: UUID,
        final_score: Decimal | float | int,
        trigger_type: str = "score_below_50",
    ) -> dict[str, Any] | None:
        """
        Proactively ensure a PIP exists when appraisal score is below threshold.

        Returns:
            - None when score is not underperforming.
            - ``{"status": "exists", ...}`` when a linked PIP already exists.
            - ``{"status": "flagged", ...}`` when a new PIP is created.
        """
        from app.models.people.perf.pip import PerformanceImprovementPlan

        score = Decimal(str(final_score))
        score_pct = self._score_to_percentage(score)
        if score_pct >= _APPRAISAL_UNDERPERFORMANCE_TRIGGER:
            return None

        existing = self.db.scalar(
            select(PerformanceImprovementPlan).where(
                PerformanceImprovementPlan.organization_id == org_id,
                PerformanceImprovementPlan.appraisal_id == appraisal_id,
            )
        )
        if existing is not None:
            return {
                "status": "exists",
                "pip_id": str(existing.pip_id),
                "pip_code": existing.pip_code,
                "employee_id": str(employee_id),
                "trigger_type": trigger_type,
            }

        return self.flag_for_pip(
            org_id,
            employee_id,
            trigger_type=trigger_type,
            triggering_appraisal_id=appraisal_id,
        )

    # -------------------------------------------------------------------------
    # Internal logic helpers — pure functions, no DB access
    # -------------------------------------------------------------------------

    def _evaluate_annual_trigger(self, appraisal: Any) -> dict[str, Any] | None:
        """
        Evaluate a single year-end appraisal for the annual PIP trigger.

        Returns a result dict if the employee should be flagged, else None.
        """
        scored_kras = [
            s for s in appraisal.kra_scores if s.raw_score_percentage is not None
        ]
        total = len(scored_kras)
        if total == 0:
            return None

        fair_count = sum(
            1
            for s in scored_kras
            if Decimal(str(s.raw_score_percentage)) < _FAIR_SCORE_THRESHOLD
        )
        ratio = Decimal(str(fair_count)) / Decimal(str(total))

        if ratio < _FAIR_KPI_RATIO_THRESHOLD:
            return None

        percentage = float(ratio * 100)
        return {
            "employee_id": appraisal.employee_id,
            "appraisal_id": appraisal.appraisal_id,
            "fair_count": fair_count,
            "total_kpis": total,
            "percentage": percentage,
        }

    def _evaluate_quarterly_trigger(
        self,
        employee_id: UUID,
        quarterly_scores: list[float],
    ) -> dict[str, Any] | None:
        """
        Evaluate an employee's quarterly scores for the quarterly PIP trigger.

        Returns a result dict if the employee should be flagged, else None.
        """
        threshold = float(_FAIR_SCORE_THRESHOLD)
        quarters_below = sum(1 for s in quarterly_scores if s < threshold)

        if quarters_below < _QUARTERLY_BELOW_TRIGGER:
            return None

        return {
            "employee_id": employee_id,
            "quarters_below": quarters_below,
            "quarterly_scores": quarterly_scores,
        }

    def _evaluate_probation_milestone(self, employee: Any) -> dict[str, Any] | None:
        """
        Evaluate whether an employee is approaching their 21-month milestone.

        Returns a result dict if the milestone falls within the next 30 days
        AND the employee has not yet been confirmed, else None.
        """
        if employee.confirmation_date is not None:
            return None

        join_date: date = employee.date_of_joining
        today = date.today()

        # Calculate the 21-month milestone date using calendar arithmetic
        milestone_month = join_date.month + _PROBATION_MONTHS
        milestone_year = join_date.year + (milestone_month - 1) // 12
        milestone_month = ((milestone_month - 1) % 12) + 1

        # Handle end-of-month edge cases (e.g., Jan 31 + 1 month → Feb 28)
        import calendar

        max_day = calendar.monthrange(milestone_year, milestone_month)[1]
        milestone_day = min(join_date.day, max_day)
        milestone_date = date(milestone_year, milestone_month, milestone_day)

        # Flag milestones near "now" (not only upcoming) to avoid end-of-month
        # join-date clamping missing employees by 1 day.
        window_start = today - timedelta(days=_PROBATION_WINDOW_DAYS)
        window_end = today + timedelta(days=_PROBATION_WINDOW_DAYS)

        if not (window_start <= milestone_date <= window_end):
            return None

        # Calculate approximate months of service
        months_of_service = (today.year - join_date.year) * 12 + (
            today.month - join_date.month
        )

        return {
            "employee_id": employee.employee_id,
            "date_of_joining": join_date,
            "months_of_service": months_of_service,
            "milestone_date": milestone_date,
        }

    @staticmethod
    def _score_to_percentage(final_score: Decimal | float | int) -> Decimal:
        """
        Normalize appraisal score to a 0-100 scale.

        Legacy appraisal flow stores final_score on a 0-5 scale, while OHCSF
        uses 0-100. Treat scores <= 5 as 0-5 and convert to percentage.
        """
        score = Decimal(str(final_score))
        if score <= Decimal("5"):
            return score * Decimal("20")
        return score
