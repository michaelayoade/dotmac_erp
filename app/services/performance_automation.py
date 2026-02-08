"""
Performance Review Cycle Automation Service.

Handles automatic advancement of appraisal cycles and generation
of individual appraisals for eligible employees.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from dateutil.relativedelta import relativedelta
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.models.people.hr.employee import Employee, EmployeeStatus
from app.models.people.perf.appraisal import Appraisal, AppraisalStatus
from app.models.people.perf.appraisal_cycle import AppraisalCycle, AppraisalCycleStatus

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

__all__ = ["PerformanceAutomationService"]


class PerformanceAutomationService:
    """
    Service for automating performance review cycle workflows.

    Handles:
    - Cycle phase transitions based on deadlines
    - Appraisal generation for eligible employees
    - Progress tracking and status synchronization
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    # ─────────────────────────────────────────────────────────────────────────────
    # Cycle Phase Transitions
    # ─────────────────────────────────────────────────────────────────────────────

    def get_cycles_ready_for_transition(
        self,
    ) -> list[tuple[AppraisalCycle, AppraisalCycleStatus]]:
        """
        Find cycles that should transition to the next phase based on deadlines.

        Returns:
            List of (cycle, target_status) tuples
        """
        today = date.today()
        transitions: list[tuple[AppraisalCycle, AppraisalCycleStatus]] = []

        # Find ACTIVE cycles past self-assessment deadline → REVIEW
        active_cycles = self.db.scalars(
            select(AppraisalCycle).where(
                AppraisalCycle.status == AppraisalCycleStatus.ACTIVE,
                AppraisalCycle.self_assessment_deadline.isnot(None),
                AppraisalCycle.self_assessment_deadline < today,
            )
        ).all()

        for cycle in active_cycles:
            transitions.append((cycle, AppraisalCycleStatus.REVIEW))

        # Find REVIEW cycles past manager review deadline → CALIBRATION
        review_cycles = self.db.scalars(
            select(AppraisalCycle).where(
                AppraisalCycle.status == AppraisalCycleStatus.REVIEW,
                AppraisalCycle.manager_review_deadline.isnot(None),
                AppraisalCycle.manager_review_deadline < today,
            )
        ).all()

        for cycle in review_cycles:
            transitions.append((cycle, AppraisalCycleStatus.CALIBRATION))

        # Find CALIBRATION cycles past calibration deadline → COMPLETED
        calibration_cycles = self.db.scalars(
            select(AppraisalCycle).where(
                AppraisalCycle.status == AppraisalCycleStatus.CALIBRATION,
                AppraisalCycle.calibration_deadline.isnot(None),
                AppraisalCycle.calibration_deadline < today,
            )
        ).all()

        for cycle in calibration_cycles:
            transitions.append((cycle, AppraisalCycleStatus.COMPLETED))

        return transitions

    def advance_cycle_phase(
        self,
        cycle: AppraisalCycle,
        target_status: AppraisalCycleStatus,
    ) -> bool:
        """
        Advance a cycle to the next phase.

        Args:
            cycle: The cycle to advance
            target_status: The target status

        Returns:
            True if successful
        """
        old_status = cycle.status
        cycle.status = target_status
        self.db.flush()

        logger.info(
            "Advanced cycle %s from %s to %s",
            cycle.cycle_id,
            old_status.value,
            target_status.value,
        )

        # Advance individual appraisal statuses as needed
        self._sync_appraisal_statuses(cycle, target_status)

        return True

    def _sync_appraisal_statuses(
        self,
        cycle: AppraisalCycle,
        cycle_status: AppraisalCycleStatus,
    ) -> None:
        """
        Sync individual appraisal statuses when cycle advances.

        When cycle moves to REVIEW phase, move all SELF_ASSESSMENT appraisals
        to PENDING_REVIEW. When cycle moves to CALIBRATION, move UNDER_REVIEW
        to PENDING_CALIBRATION.
        """
        if cycle_status == AppraisalCycleStatus.REVIEW:
            # Move DRAFT and SELF_ASSESSMENT to PENDING_REVIEW
            appraisals = self.db.scalars(
                select(Appraisal).where(
                    Appraisal.cycle_id == cycle.cycle_id,
                    Appraisal.status.in_(
                        [
                            AppraisalStatus.DRAFT,
                            AppraisalStatus.SELF_ASSESSMENT,
                        ]
                    ),
                )
            ).all()

            for appraisal in appraisals:
                appraisal.status = AppraisalStatus.PENDING_REVIEW
                logger.debug(
                    "Advanced appraisal %s to PENDING_REVIEW",
                    appraisal.appraisal_id,
                )

        elif cycle_status == AppraisalCycleStatus.CALIBRATION:
            # Move PENDING_REVIEW and UNDER_REVIEW to PENDING_CALIBRATION
            appraisals = self.db.scalars(
                select(Appraisal).where(
                    Appraisal.cycle_id == cycle.cycle_id,
                    Appraisal.status.in_(
                        [
                            AppraisalStatus.PENDING_REVIEW,
                            AppraisalStatus.UNDER_REVIEW,
                        ]
                    ),
                )
            ).all()

            for appraisal in appraisals:
                appraisal.status = AppraisalStatus.PENDING_CALIBRATION
                logger.debug(
                    "Advanced appraisal %s to PENDING_CALIBRATION",
                    appraisal.appraisal_id,
                )

        elif cycle_status == AppraisalCycleStatus.COMPLETED:
            # Mark remaining non-completed appraisals as COMPLETED
            appraisals = self.db.scalars(
                select(Appraisal).where(
                    Appraisal.cycle_id == cycle.cycle_id,
                    Appraisal.status.notin_(
                        [
                            AppraisalStatus.COMPLETED,
                            AppraisalStatus.CANCELLED,
                        ]
                    ),
                )
            ).all()

            for appraisal in appraisals:
                appraisal.status = AppraisalStatus.COMPLETED
                logger.debug(
                    "Marked appraisal %s as COMPLETED",
                    appraisal.appraisal_id,
                )

        self.db.flush()

    # ─────────────────────────────────────────────────────────────────────────────
    # Appraisal Generation
    # ─────────────────────────────────────────────────────────────────────────────

    def get_eligible_employees(self, cycle: AppraisalCycle) -> list[Employee]:
        """
        Get employees eligible for a given appraisal cycle.

        Eligibility criteria:
        - Employee is active
        - Employee has met minimum tenure requirement
        - Employee is not on probation (unless cycle includes probation employees)
        - Employee doesn't already have an appraisal in this cycle

        Args:
            cycle: The appraisal cycle

        Returns:
            List of eligible employees
        """
        today = date.today()
        tenure_cutoff = today - relativedelta(months=cycle.min_tenure_months)

        # Base query for active employees
        query = (
            select(Employee)
            .options(joinedload(Employee.manager))
            .where(
                Employee.organization_id == cycle.organization_id,
                Employee.status == EmployeeStatus.ACTIVE,
                Employee.date_of_joining.isnot(None),
                Employee.date_of_joining <= tenure_cutoff,
            )
        )

        # Exclude probation employees if not included
        if not cycle.include_probation_employees:
            query = query.where(
                # Either no probation end date or probation already ended
                (Employee.probation_end_date.is_(None))
                | (Employee.probation_end_date < today)
            )

        # Exclude employees who already have an appraisal in this cycle
        existing_appraisal_ids = select(Appraisal.employee_id).where(
            Appraisal.cycle_id == cycle.cycle_id,
        )
        query = query.where(Employee.employee_id.notin_(existing_appraisal_ids))

        return list(self.db.scalars(query).all())

    def generate_appraisals_for_cycle(
        self,
        cycle: AppraisalCycle,
        *,
        template_id: UUID | None = None,
    ) -> list[Appraisal]:
        """
        Generate appraisals for all eligible employees in a cycle.

        Args:
            cycle: The appraisal cycle
            template_id: Optional template to use for all appraisals

        Returns:
            List of created appraisals
        """
        if cycle.status != AppraisalCycleStatus.ACTIVE:
            logger.warning(
                "Cannot generate appraisals for cycle %s - status is %s, not ACTIVE",
                cycle.cycle_id,
                cycle.status.value,
            )
            return []

        eligible_employees = self.get_eligible_employees(cycle)
        created_appraisals: list[Appraisal] = []

        for employee in eligible_employees:
            # Determine the reviewing manager
            manager_id = employee.reports_to_id

            if not manager_id:
                logger.warning(
                    "Skipping appraisal for employee %s - no manager assigned",
                    employee.employee_id,
                )
                continue

            appraisal = Appraisal(
                organization_id=cycle.organization_id,
                employee_id=employee.employee_id,
                cycle_id=cycle.cycle_id,
                manager_id=manager_id,
                template_id=template_id,
                status=AppraisalStatus.SELF_ASSESSMENT,
            )

            self.db.add(appraisal)
            created_appraisals.append(appraisal)

            logger.debug(
                "Created appraisal for employee %s in cycle %s",
                employee.employee_id,
                cycle.cycle_id,
            )

        self.db.flush()

        logger.info(
            "Generated %d appraisals for cycle %s",
            len(created_appraisals),
            cycle.cycle_id,
        )

        return created_appraisals

    # ─────────────────────────────────────────────────────────────────────────────
    # Progress Tracking
    # ─────────────────────────────────────────────────────────────────────────────

    def get_cycle_progress(self, cycle: AppraisalCycle) -> dict:
        """
        Get detailed progress statistics for a cycle.

        Returns:
            Dict with progress statistics
        """
        total = (
            self.db.scalar(
                select(func.count(Appraisal.appraisal_id)).where(
                    Appraisal.cycle_id == cycle.cycle_id,
                )
            )
            or 0
        )

        if total == 0:
            return {
                "cycle_id": str(cycle.cycle_id),
                "cycle_status": cycle.status.value,
                "total_appraisals": 0,
                "progress": {},
            }

        # Get counts by status
        status_results = self.db.execute(
            select(Appraisal.status, func.count(Appraisal.appraisal_id))
            .where(Appraisal.cycle_id == cycle.cycle_id)
            .group_by(Appraisal.status)
        ).all()

        status_counts = {status.value: count for status, count in status_results}

        # Calculate phase completion percentages
        completed_count = status_counts.get(AppraisalStatus.COMPLETED.value, 0)

        # Self-assessment phase: DRAFT/SELF_ASSESSMENT not yet submitted
        self_assessment_pending = status_counts.get(
            AppraisalStatus.DRAFT.value, 0
        ) + status_counts.get(AppraisalStatus.SELF_ASSESSMENT.value, 0)

        # Manager review phase: PENDING_REVIEW/UNDER_REVIEW
        manager_review_pending = status_counts.get(
            AppraisalStatus.PENDING_REVIEW.value, 0
        ) + status_counts.get(AppraisalStatus.UNDER_REVIEW.value, 0)

        # Calibration phase: PENDING_CALIBRATION/CALIBRATION
        calibration_pending = status_counts.get(
            AppraisalStatus.PENDING_CALIBRATION.value, 0
        ) + status_counts.get(AppraisalStatus.CALIBRATION.value, 0)

        return {
            "cycle_id": str(cycle.cycle_id),
            "cycle_status": cycle.status.value,
            "total_appraisals": total,
            "status_counts": status_counts,
            "progress": {
                "self_assessment_pending": self_assessment_pending,
                "self_assessment_completed_pct": round(
                    (total - self_assessment_pending) / total * 100, 1
                )
                if total > 0
                else 0,
                "manager_review_pending": manager_review_pending,
                "manager_review_completed_pct": round(
                    (total - self_assessment_pending - manager_review_pending)
                    / total
                    * 100,
                    1,
                )
                if total > 0
                else 0,
                "calibration_pending": calibration_pending,
                "completed_count": completed_count,
                "completed_pct": round(completed_count / total * 100, 1)
                if total > 0
                else 0,
            },
        }

    def check_cycle_completion_eligibility(self, cycle: AppraisalCycle) -> bool:
        """
        Check if a cycle can be marked as completed.

        A cycle can be completed when all appraisals are either
        COMPLETED or CANCELLED.

        Args:
            cycle: The appraisal cycle

        Returns:
            True if cycle can be completed
        """
        incomplete = (
            self.db.scalar(
                select(func.count(Appraisal.appraisal_id)).where(
                    Appraisal.cycle_id == cycle.cycle_id,
                    Appraisal.status.notin_(
                        [
                            AppraisalStatus.COMPLETED,
                            AppraisalStatus.CANCELLED,
                        ]
                    ),
                )
            )
            or 0
        )

        return incomplete == 0

    # ─────────────────────────────────────────────────────────────────────────────
    # Deadline Reminders
    # ─────────────────────────────────────────────────────────────────────────────

    def get_upcoming_deadlines(
        self,
        *,
        days_ahead: int = 7,
        org_id: UUID | None = None,
    ) -> list[dict]:
        """
        Get cycles with deadlines approaching in the next N days.

        Args:
            days_ahead: Number of days to look ahead
            org_id: Optional organization filter

        Returns:
            List of deadline info dicts
        """
        today = date.today()
        deadline_date = today + timedelta(days=days_ahead)
        deadlines: list[dict] = []

        query = select(AppraisalCycle).where(
            AppraisalCycle.status.in_(
                [
                    AppraisalCycleStatus.ACTIVE,
                    AppraisalCycleStatus.REVIEW,
                    AppraisalCycleStatus.CALIBRATION,
                ]
            ),
        )

        if org_id:
            query = query.where(AppraisalCycle.organization_id == org_id)

        cycles = self.db.scalars(query).all()

        for cycle in cycles:
            # Check each deadline
            if (
                cycle.status == AppraisalCycleStatus.ACTIVE
                and cycle.self_assessment_deadline
                and today <= cycle.self_assessment_deadline <= deadline_date
            ):
                days_remaining = (cycle.self_assessment_deadline - today).days
                deadlines.append(
                    {
                        "cycle_id": str(cycle.cycle_id),
                        "cycle_name": cycle.cycle_name,
                        "deadline_type": "self_assessment",
                        "deadline_date": str(cycle.self_assessment_deadline),
                        "days_remaining": days_remaining,
                    }
                )

            if (
                cycle.status == AppraisalCycleStatus.REVIEW
                and cycle.manager_review_deadline
                and today <= cycle.manager_review_deadline <= deadline_date
            ):
                days_remaining = (cycle.manager_review_deadline - today).days
                deadlines.append(
                    {
                        "cycle_id": str(cycle.cycle_id),
                        "cycle_name": cycle.cycle_name,
                        "deadline_type": "manager_review",
                        "deadline_date": str(cycle.manager_review_deadline),
                        "days_remaining": days_remaining,
                    }
                )

            if (
                cycle.status == AppraisalCycleStatus.CALIBRATION
                and cycle.calibration_deadline
                and today <= cycle.calibration_deadline <= deadline_date
            ):
                days_remaining = (cycle.calibration_deadline - today).days
                deadlines.append(
                    {
                        "cycle_id": str(cycle.cycle_id),
                        "cycle_name": cycle.cycle_name,
                        "deadline_type": "calibration",
                        "deadline_date": str(cycle.calibration_deadline),
                        "days_remaining": days_remaining,
                    }
                )

        return sorted(deadlines, key=lambda x: x["days_remaining"])
