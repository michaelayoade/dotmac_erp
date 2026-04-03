"""
Appraisal Appeal Service — OHCSF Performance Management System.

Handles the full lifecycle of employee appraisal appeals:
filing, mediator assignment, mediation outcome, committee escalation,
committee decision, and communication of resolution.

Business rules:
- Appeals must be filed within 5 working days of appraisal completion.
- Only one open appeal is permitted per appraisal.
- All appeals must be resolved by 28 Feb of the appraisal cycle year.
- Unresolved mediation escalates to the committee.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.people.perf.appraisal import Appraisal
from app.models.people.perf.appraisal_appeal import AppraisalAppeal
from app.models.people.perf.pms_enums import AppealDecision, AppealStatus
from app.services.common import PaginatedResult, PaginationParams, paginate
from app.services.people.common import calculate_workdays
from app.services.people.perf.performance_policy import get_policy_profile
from app.services.people.perf.performance_mode_policy import enforce_pms_write_mode
from app.services.people.perf.scoring_engine import OHCSFScoringEngine

if TYPE_CHECKING:
    from app.web.deps import WebAuthContext

logger = logging.getLogger(__name__)

__all__ = [
    "AppealServiceError",
    "AppealNotFoundError",
    "AppealValidationError",
    "AppraisalAppealService",
]


# =============================================================================
# Error classes
# =============================================================================


class AppealServiceError(Exception):
    """Base error for AppraisalAppealService."""


class AppealNotFoundError(AppealServiceError):
    """Raised when an appeal cannot be found."""

    def __init__(self, appeal_id: UUID) -> None:
        self.appeal_id = appeal_id
        super().__init__(f"Appraisal appeal {appeal_id} not found")


class AppealValidationError(AppealServiceError):
    """Raised when appeal input validation fails."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


# =============================================================================
# Service
# =============================================================================


class AppraisalAppealService:
    """Service for managing OHCSF appraisal appeals."""

    def __init__(
        self,
        db: Session,
        ctx: WebAuthContext | None = None,
        policy_profile_name: str = "GOVERNMENT_PMS",
    ) -> None:
        self.db = db
        self.ctx = ctx
        self._policy = get_policy_profile(policy_profile_name)
        self._scoring = OHCSFScoringEngine(policy_profile_name=policy_profile_name)

    def _ensure_pms_write_mode(self, org_id: UUID) -> None:
        try:
            enforce_pms_write_mode(self.db, org_id)
        except ValueError as exc:
            raise AppealValidationError(str(exc)) from exc

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_or_raise(self, org_id: UUID, appeal_id: UUID) -> AppraisalAppeal:
        """Fetch an appeal scoped to the org, raise AppealNotFoundError if missing."""
        stmt = select(AppraisalAppeal).where(
            AppraisalAppeal.organization_id == org_id,
            AppraisalAppeal.appeal_id == appeal_id,
        )
        appeal = self.db.scalar(stmt)
        if appeal is None:
            raise AppealNotFoundError(appeal_id)
        return appeal

    def _resolution_deadline(self, cycle_year: int) -> date:
        """Return the Feb-28 deadline for a given cycle year."""
        return date(
            cycle_year,
            self._policy.resolution_deadline_month,
            self._policy.resolution_deadline_day,
        )

    # ------------------------------------------------------------------
    # Public query methods
    # ------------------------------------------------------------------

    def get_appeal(self, org_id: UUID, appeal_id: UUID) -> AppraisalAppeal:
        """Return a single appeal scoped to the organisation.

        Raises:
            AppealNotFoundError: if not found.
        """
        return self._get_or_raise(org_id, appeal_id)

    def list_appeals(
        self,
        org_id: UUID,
        *,
        status: AppealStatus | None = None,
        employee_id: UUID | None = None,
        search: str | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[AppraisalAppeal]:
        """List appeals for an organisation with optional filters.

        Args:
            org_id: Organisation scope.
            status: Filter by appeal status.
            employee_id: Filter by employee.
            search: Text search on appeal reason.
            pagination: Pagination parameters.

        Returns:
            PaginatedResult containing appeals and total count.
        """
        stmt = (
            select(AppraisalAppeal)
            .where(AppraisalAppeal.organization_id == org_id)
            .order_by(AppraisalAppeal.filed_date.desc())
        )

        if status is not None:
            stmt = stmt.where(AppraisalAppeal.status == status)
        if employee_id is not None:
            stmt = stmt.where(AppraisalAppeal.employee_id == employee_id)
        if search:
            pattern = f"%{search}%"
            stmt = stmt.where(AppraisalAppeal.reason.ilike(pattern))

        return paginate(
            self.db,
            stmt,
            pagination,
            count_column=AppraisalAppeal.appeal_id,
        )

    def get_overdue_appeals(self, org_id: UUID) -> list[AppraisalAppeal]:
        """Return appeals that are unresolved and past the Feb-28 deadline.

        An appeal is overdue when today is after Feb 28 of the current
        calendar year and the appeal is not yet RESOLVED or DISMISSED.

        Returns:
            List of AppraisalAppeal instances that are overdue.
        """
        today = date.today()
        deadline = self._resolution_deadline(today.year)

        if today <= deadline:
            return []

        stmt = select(AppraisalAppeal).where(
            AppraisalAppeal.organization_id == org_id,
            AppraisalAppeal.status.notin_(
                [AppealStatus.RESOLVED, AppealStatus.DISMISSED]
            ),
        )
        return list(self.db.scalars(stmt).all())

    # ------------------------------------------------------------------
    # Public mutation methods
    # ------------------------------------------------------------------

    def file_appeal(
        self,
        org_id: UUID,
        *,
        appraisal_id: UUID,
        employee_id: UUID,
        reason: str,
        requested_outcome: str | None = None,
        filed_date: date | None = None,
    ) -> AppraisalAppeal:
        """File a new appraisal appeal.

        Validates:
        - No existing open appeal for this appraisal.
        - Filed within 5 working days of appraisal completion.

        Args:
            org_id: Organisation scope.
            appraisal_id: The appraisal being appealed.
            employee_id: The employee filing the appeal.
            reason: Detailed reason for the appeal.
            requested_outcome: What outcome the employee is requesting.
            filed_date: Date of filing; defaults to today.

        Returns:
            Newly created AppraisalAppeal with FILED status.

        Raises:
            AppealValidationError: if a duplicate appeal exists or the
                filing window has expired.
        """
        self._ensure_pms_write_mode(org_id)
        filed_date = filed_date or date.today()

        # Check for existing open appeal on this appraisal
        existing_stmt = select(AppraisalAppeal).where(
            AppraisalAppeal.organization_id == org_id,
            AppraisalAppeal.appraisal_id == appraisal_id,
            AppraisalAppeal.status.notin_(
                [AppealStatus.RESOLVED, AppealStatus.DISMISSED]
            ),
        )
        existing = self.db.scalar(existing_stmt)
        if existing is not None:
            raise AppealValidationError(
                f"An open appeal already exists for appraisal {appraisal_id} "
                f"(appeal {existing.appeal_id})"
            )

        # Validate filing window: must be within 5 working days of completion
        appraisal = self.db.scalar(
            select(Appraisal).where(
                Appraisal.organization_id == org_id,
                Appraisal.appraisal_id == appraisal_id,
            )
        )
        if appraisal is not None and appraisal.completed_on is not None:
            working_days_elapsed = calculate_workdays(
                appraisal.completed_on, filed_date
            )
            if working_days_elapsed > self._policy.appeal_filing_window_workdays:
                raise AppealValidationError(
                    "Appeal must be filed within "
                    f"{self._policy.appeal_filing_window_workdays} "
                    f"working days of appraisal completion "
                    f"(completed {appraisal.completed_on}, filed {filed_date}, "
                    f"{working_days_elapsed} working days elapsed)"
                )

        appeal = AppraisalAppeal(
            organization_id=org_id,
            appraisal_id=appraisal_id,
            employee_id=employee_id,
            reason=reason,
            requested_outcome=requested_outcome,
            filed_date=filed_date,
            status=AppealStatus.FILED,
        )
        self.db.add(appeal)
        self.db.flush()
        logger.info(
            "Filed appeal %s for appraisal %s by employee %s",
            appeal.appeal_id,
            appraisal_id,
            employee_id,
        )
        return appeal

    def assign_mediator(
        self,
        org_id: UUID,
        appeal_id: UUID,
        *,
        mediator_id: UUID,
    ) -> AppraisalAppeal:
        """Assign a mediator to an appeal and transition to UNDER_MEDIATION.

        Args:
            org_id: Organisation scope.
            appeal_id: The appeal to update.
            mediator_id: Employee ID of the assigned mediator.

        Returns:
            Updated AppraisalAppeal.

        Raises:
            AppealNotFoundError: if the appeal does not exist.
        """
        self._ensure_pms_write_mode(org_id)
        appeal = self._get_or_raise(org_id, appeal_id)
        appeal.mediator_id = mediator_id
        appeal.status = AppealStatus.UNDER_MEDIATION
        self.db.flush()
        logger.info(
            "Appeal %s assigned to mediator %s — status UNDER_MEDIATION",
            appeal_id,
            mediator_id,
        )
        return appeal

    def record_mediation_outcome(
        self,
        org_id: UUID,
        appeal_id: UUID,
        *,
        outcome: str,
        resolved: bool,
    ) -> AppraisalAppeal:
        """Record the result of mediation.

        If resolved, transitions to RESOLVED with today as the resolution date.
        If not resolved, escalates to REFERRED_TO_COMMITTEE.

        Args:
            org_id: Organisation scope.
            appeal_id: The appeal to update.
            outcome: Narrative description of the mediation outcome.
            resolved: True if mediation resolved the appeal; False to escalate.

        Returns:
            Updated AppraisalAppeal.

        Raises:
            AppealNotFoundError: if the appeal does not exist.
        """
        self._ensure_pms_write_mode(org_id)
        today = date.today()
        appeal = self._get_or_raise(org_id, appeal_id)
        appeal.mediation_date = today
        appeal.mediation_outcome = outcome
        appeal.mediation_resolved = resolved

        if resolved:
            appeal.status = AppealStatus.RESOLVED
            appeal.resolution_date = today
            logger.info("Appeal %s resolved at mediation stage", appeal_id)
        else:
            appeal.status = AppealStatus.REFERRED_TO_COMMITTEE
            appeal.committee_referral_date = today
            logger.info(
                "Appeal %s unresolved at mediation — referred to committee", appeal_id
            )

        self.db.flush()
        return appeal

    def record_committee_decision(
        self,
        org_id: UUID,
        appeal_id: UUID,
        *,
        decision: AppealDecision,
        notes: str,
        adjusted_rating: int | None = None,
    ) -> AppraisalAppeal:
        """Record the committee's decision and resolve the appeal.

        Sets hearing date to today, records decision and notes.
        If decision is UPHELD or PARTIALLY_UPHELD, records adjusted_rating.
        Transitions status to RESOLVED.

        Args:
            org_id: Organisation scope.
            appeal_id: The appeal to update.
            decision: The committee's decision (UPHELD/PARTIALLY_UPHELD/DISMISSED).
            notes: Committee's deliberation notes.
            adjusted_rating: New rating granted by committee (required when
                decision is UPHELD or PARTIALLY_UPHELD).

        Returns:
            Updated AppraisalAppeal.

        Raises:
            AppealNotFoundError: if the appeal does not exist.
            AppealValidationError: if adjusted_rating is missing when decision
                requires it.
        """
        self._ensure_pms_write_mode(org_id)
        decisions_requiring_adjusted_rating = (
            self._policy.appeal_decisions_requiring_adjusted_rating
        )
        if decision.value in decisions_requiring_adjusted_rating:
            if adjusted_rating is None:
                raise AppealValidationError(
                    f"adjusted_rating is required when decision is {decision.value}"
                )

        today = date.today()
        appeal = self._get_or_raise(org_id, appeal_id)
        appeal.committee_hearing_date = today
        appeal.committee_decision = decision
        appeal.committee_notes = notes
        appeal.status = AppealStatus.RESOLVED
        appeal.resolution_date = today

        if decision.value in decisions_requiring_adjusted_rating:
            appeal.adjusted_rating = adjusted_rating

        # Update the original appraisal if rating was adjusted
        if adjusted_rating is not None:
            from app.models.people.perf.appraisal import Appraisal

            appraisal = self.db.scalar(
                select(Appraisal).where(
                    Appraisal.appraisal_id == appeal.appraisal_id,
                    Appraisal.organization_id == org_id,
                )
            )
            if appraisal:
                _, label = self._scoring.score_to_rating(
                    Decimal(str(adjusted_rating)) / Decimal("5") * Decimal("100")
                )
                appraisal.final_rating = adjusted_rating
                appraisal.rating_label = label

        self.db.flush()
        logger.info(
            "Appeal %s decided by committee: %s — status RESOLVED",
            appeal_id,
            decision.value,
        )
        return appeal

    def communicate_decision(
        self,
        org_id: UUID,
        appeal_id: UUID,
    ) -> AppraisalAppeal:
        """Record that the appeal outcome has been formally communicated to the employee.

        Sets communicated_date to today.

        Args:
            org_id: Organisation scope.
            appeal_id: The appeal to update.

        Returns:
            Updated AppraisalAppeal.

        Raises:
            AppealNotFoundError: if the appeal does not exist.
        """
        self._ensure_pms_write_mode(org_id)
        appeal = self._get_or_raise(org_id, appeal_id)
        appeal.communicated_date = date.today()
        self.db.flush()
        logger.info(
            "Appeal %s outcome communicated on %s", appeal_id, appeal.communicated_date
        )
        return appeal
