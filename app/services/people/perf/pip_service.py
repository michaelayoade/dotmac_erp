"""
Performance Improvement Plan Service — OHCSF Performance Management System.

Handles creation, activation, extension, review recording, and outcome
completion for PerformanceImprovementPlan records within the PMS module.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.people.perf.pip import PerformanceImprovementPlan
from app.models.people.perf.pms_enums import PIPCauseCategory, PIPOutcome, PIPStatus
from app.services.common import PaginatedResult, PaginationParams, paginate
from app.services.people.perf.performance_mode_policy import enforce_pms_write_mode

if TYPE_CHECKING:
    from app.web.deps import WebAuthContext

logger = logging.getLogger(__name__)

__all__ = [
    "PIPServiceError",
    "PIPNotFoundError",
    "PIPValidationError",
    "PIPStatusError",
    "PIPService",
]

# Maximum PIP duration in days (approx. 6 months)
_MAX_DURATION_DAYS = 183
# Maximum extension length in days (approx. 3 months)
_MAX_EXTENSION_DAYS = 92


# =============================================================================
# Error classes
# =============================================================================


class PIPServiceError(Exception):
    """Base error for PIPService."""


class PIPNotFoundError(PIPServiceError):
    """Raised when a PIP cannot be found."""

    def __init__(self, pip_id: UUID) -> None:
        self.pip_id = pip_id
        super().__init__(f"Performance Improvement Plan {pip_id} not found")


class PIPValidationError(PIPServiceError):
    """Raised when PIP input validation fails."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class PIPStatusError(PIPServiceError):
    """Raised when a status transition is invalid."""

    def __init__(self, current: str, target: str) -> None:
        self.current = current
        self.target = target
        super().__init__(f"Cannot transition from {current} to {target}")


# =============================================================================
# Service
# =============================================================================


class PIPService:
    """Service for managing OHCSF Performance Improvement Plans."""

    def __init__(self, db: Session, ctx: WebAuthContext | None = None) -> None:
        self.db = db
        self.ctx = ctx

    def _ensure_pms_write_mode(self, org_id: UUID) -> None:
        try:
            enforce_pms_write_mode(self.db, org_id)
        except ValueError as exc:
            raise PIPValidationError(str(exc)) from exc

    # ------------------------------------------------------------------
    # Private validation helpers
    # ------------------------------------------------------------------

    def _validate_duration(self, start_date: date, end_date: date) -> None:
        """Validate that the PIP duration does not exceed 183 days.

        Raises:
            PIPValidationError: if end_date is not strictly after start_date,
                or if the duration exceeds 183 days.
        """
        delta = (end_date - start_date).days
        if delta <= 0:
            raise PIPValidationError(
                f"end_date must be after start_date (got delta={delta} days)"
            )
        if delta > _MAX_DURATION_DAYS:
            raise PIPValidationError(
                f"PIP duration must not exceed {_MAX_DURATION_DAYS} days "
                f"(got {delta} days)"
            )

    # ------------------------------------------------------------------
    # Private query helpers
    # ------------------------------------------------------------------

    def _get_or_404(self, org_id: UUID, pip_id: UUID) -> PerformanceImprovementPlan:
        """Return a PIP scoped to the organisation or raise PIPNotFoundError."""
        stmt = select(PerformanceImprovementPlan).where(
            PerformanceImprovementPlan.organization_id == org_id,
            PerformanceImprovementPlan.pip_id == pip_id,
        )
        pip = self.db.scalar(stmt)
        if pip is None:
            raise PIPNotFoundError(pip_id)
        return pip

    # ------------------------------------------------------------------
    # Public query methods
    # ------------------------------------------------------------------

    def get_pip(self, org_id: UUID, pip_id: UUID) -> PerformanceImprovementPlan:
        """Return a PIP by ID scoped to the organisation.

        Raises:
            PIPNotFoundError: if not found.
        """
        return self._get_or_404(org_id, pip_id)

    def get_pip_for_appraisal(
        self, org_id: UUID, appraisal_id: UUID
    ) -> PerformanceImprovementPlan | None:
        """Return the most recent PIP linked to an appraisal, if any."""
        stmt = (
            select(PerformanceImprovementPlan)
            .where(
                PerformanceImprovementPlan.organization_id == org_id,
                PerformanceImprovementPlan.appraisal_id == appraisal_id,
            )
            .order_by(PerformanceImprovementPlan.created_at.desc())
        )
        return self.db.scalar(stmt)

    def list_pips(
        self,
        org_id: UUID,
        *,
        employee_id: UUID | None = None,
        status: PIPStatus | None = None,
        search: str | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[PerformanceImprovementPlan]:
        """List PIPs for an organisation with optional filters."""
        stmt = (
            select(PerformanceImprovementPlan)
            .where(PerformanceImprovementPlan.organization_id == org_id)
            .order_by(PerformanceImprovementPlan.created_at.desc())
        )

        if employee_id is not None:
            stmt = stmt.where(PerformanceImprovementPlan.employee_id == employee_id)
        if status is not None:
            stmt = stmt.where(PerformanceImprovementPlan.status == status)
        if search:
            pattern = f"%{search}%"
            stmt = stmt.where(PerformanceImprovementPlan.pip_code.ilike(pattern))

        return paginate(
            self.db,
            stmt,
            pagination,
            count_column=PerformanceImprovementPlan.pip_id,
        )

    # ------------------------------------------------------------------
    # Public mutation methods
    # ------------------------------------------------------------------

    def create_pip(
        self,
        org_id: UUID,
        *,
        employee_id: UUID,
        supervisor_id: UUID,
        hr_officer_id: UUID,
        pip_code: str,
        start_date: date,
        end_date: date,
        reason: str,
        cause_category: PIPCauseCategory | str,
        improvement_areas: list,
        support_measures: str | None = None,
        appraisal_id: UUID | None = None,
    ) -> PerformanceImprovementPlan:
        """Create a new PIP with status DRAFT.

        Raises:
            PIPValidationError: if duration validation fails.
        """
        self._ensure_pms_write_mode(org_id)
        self._validate_duration(start_date, end_date)

        pip = PerformanceImprovementPlan(
            organization_id=org_id,
            employee_id=employee_id,
            supervisor_id=supervisor_id,
            hr_officer_id=hr_officer_id,
            pip_code=pip_code,
            start_date=start_date,
            end_date=end_date,
            reason=reason,
            cause_category=cause_category,
            improvement_areas=improvement_areas,
            support_measures=support_measures,
            appraisal_id=appraisal_id,
            status=PIPStatus.DRAFT,
        )
        self.db.add(pip)
        self.db.flush()
        logger.info(
            "Created PerformanceImprovementPlan %s for employee %s",
            pip.pip_id,
            employee_id,
        )
        return pip

    def activate_pip(self, org_id: UUID, pip_id: UUID) -> PerformanceImprovementPlan:
        """Transition a PIP from DRAFT to ACTIVE.

        Raises:
            PIPNotFoundError: if PIP not found.
            PIPStatusError: if current status is not DRAFT.
        """
        self._ensure_pms_write_mode(org_id)
        pip = self._get_or_404(org_id, pip_id)
        if pip.status != PIPStatus.DRAFT:
            raise PIPStatusError(pip.status.value, PIPStatus.ACTIVE.value)
        pip.status = PIPStatus.ACTIVE
        self.db.flush()
        logger.info("PIP %s activated", pip_id)
        return pip

    def grant_extension(
        self,
        org_id: UUID,
        pip_id: UUID,
        *,
        new_end_date: date,
        reason: str,
    ) -> PerformanceImprovementPlan:
        """Grant a one-time extension to an active PIP.

        The new end date must be within 92 days of the current end date.
        Only one extension is permitted per PIP.

        Raises:
            PIPNotFoundError: if PIP not found.
            PIPValidationError: if already extended or extension exceeds 92 days.
        """
        self._ensure_pms_write_mode(org_id)
        pip = self._get_or_404(org_id, pip_id)

        if pip.extension_granted:
            raise PIPValidationError(
                "Extension already granted — only one extension is permitted per PIP"
            )

        extension_days = (new_end_date - pip.end_date).days
        if extension_days > _MAX_EXTENSION_DAYS:
            raise PIPValidationError(
                f"Extension must not exceed {_MAX_EXTENSION_DAYS} days from the "
                f"current end date (requested {extension_days} days)"
            )
        if extension_days <= 0:
            raise PIPValidationError("New end date must be after the current end date")

        pip.extension_granted = True
        pip.extension_end_date = new_end_date
        pip.extension_reason = reason
        pip.status = PIPStatus.EXTENDED
        self.db.flush()
        logger.info("PIP %s extended to %s", pip_id, new_end_date)
        return pip

    def record_review(
        self,
        org_id: UUID,
        pip_id: UUID,
        *,
        review_date: date,
        notes: str,
        progress_status: str,
    ) -> PerformanceImprovementPlan:
        """Append a review entry to the PIP's review_intervals JSON list.

        Raises:
            PIPNotFoundError: if PIP not found.
        """
        self._ensure_pms_write_mode(org_id)
        pip = self._get_or_404(org_id, pip_id)

        entry = {
            "review_date": review_date.isoformat(),
            "notes": notes,
            "progress_status": progress_status,
        }

        if pip.review_intervals is None:
            pip.review_intervals = [entry]
        else:
            pip.review_intervals = list(pip.review_intervals) + [entry]

        self.db.flush()
        logger.info(
            "Review recorded on PIP %s (progress_status=%s)", pip_id, progress_status
        )
        return pip

    def complete_pip(
        self,
        org_id: UUID,
        pip_id: UUID,
        *,
        outcome: PIPOutcome,
        notes: str,
    ) -> PerformanceImprovementPlan:
        """Record the outcome of a PIP and transition to the appropriate status.

        - SATISFACTORY outcome → status IMPROVED, outcome_date set to today
        - UNSATISFACTORY outcome → status ESCALATED, committee_referral_date set

        Raises:
            PIPNotFoundError: if PIP not found.
        """
        self._ensure_pms_write_mode(org_id)
        pip = self._get_or_404(org_id, pip_id)

        if pip.status not in (PIPStatus.ACTIVE, PIPStatus.EXTENDED):
            raise PIPStatusError(pip.status.value, "ACTIVE or EXTENDED")

        pip.outcome = outcome
        pip.outcome_notes = notes
        pip.outcome_date = date.today()

        if outcome == PIPOutcome.SATISFACTORY:
            pip.status = PIPStatus.IMPROVED
            logger.info("PIP %s completed with SATISFACTORY outcome", pip_id)
        else:
            pip.status = PIPStatus.ESCALATED
            pip.committee_referral_date = date.today()
            logger.info(
                "PIP %s completed with UNSATISFACTORY outcome — referred to committee",
                pip_id,
            )

        # Create audit trail
        from app.models.people.perf.appraisal_outcome_action import (
            AppraisalOutcomeAction,
        )
        from app.models.people.perf.pms_enums import (
            OutcomeActionStatus,
            OutcomeActionType,
        )

        if pip.appraisal_id:
            action = AppraisalOutcomeAction(
                organization_id=pip.organization_id,
                appraisal_id=pip.appraisal_id,
                action_type=OutcomeActionType.PIP,
                description=f"PIP {pip.pip_code}: {outcome.value}",
                actioned_date=date.today(),
                reference_id=pip.pip_id,
                reference_type="pip",
                status=OutcomeActionStatus.COMPLETED,
                notes=notes,
            )
            self.db.add(action)

        self.db.flush()
        return pip

    def issue_completion_letter(
        self, org_id: UUID, pip_id: UUID
    ) -> PerformanceImprovementPlan:
        """Mark a completion letter as issued for a satisfactory PIP.

        Only valid when the PIP outcome is SATISFACTORY.

        Raises:
            PIPNotFoundError: if PIP not found.
            PIPValidationError: if outcome is not SATISFACTORY or not yet set.
        """
        self._ensure_pms_write_mode(org_id)
        pip = self._get_or_404(org_id, pip_id)

        if pip.outcome != PIPOutcome.SATISFACTORY:
            raise PIPValidationError(
                "Completion letter can only be issued when the PIP outcome is SATISFACTORY"
            )

        pip.completion_letter_issued = True
        self.db.flush()
        logger.info("Completion letter issued for PIP %s", pip_id)
        return pip
