"""
Monthly Review Service — OHCSF Performance Management System.

Handles creation, submission, acknowledgement, and querying of
MonthlyReview records within the PMS module.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.people.perf.monthly_review import MonthlyReview
from app.models.people.perf.pms_enums import MonthlyReviewStatus
from app.services.common import PaginatedResult, PaginationParams, paginate
from app.services.people.perf.performance_mode_policy import enforce_pms_write_mode

if TYPE_CHECKING:
    from app.web.deps import WebAuthContext

logger = logging.getLogger(__name__)

__all__ = [
    "MonthlyReviewServiceError",
    "MonthlyReviewNotFoundError",
    "MonthlyReviewValidationError",
    "MonthlyReviewService",
]


# =============================================================================
# Error classes
# =============================================================================


class MonthlyReviewServiceError(Exception):
    """Base error for MonthlyReviewService."""


class MonthlyReviewNotFoundError(MonthlyReviewServiceError):
    """Raised when a monthly review cannot be found."""

    def __init__(self, review_id: UUID) -> None:
        self.review_id = review_id
        super().__init__(f"Monthly review {review_id} not found")


class MonthlyReviewValidationError(MonthlyReviewServiceError):
    """Raised when monthly review input validation fails."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


# =============================================================================
# Service
# =============================================================================


class MonthlyReviewService:
    """Service for managing OHCSF monthly performance reviews."""

    def __init__(self, db: Session, ctx: WebAuthContext | None = None) -> None:
        self.db = db
        self.ctx = ctx

    def _ensure_pms_write_mode(self, org_id: UUID) -> None:
        try:
            enforce_pms_write_mode(self.db, org_id)
        except ValueError as exc:
            raise MonthlyReviewValidationError(str(exc)) from exc

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def get_review(self, org_id: UUID, review_id: UUID) -> MonthlyReview:
        """Return a monthly review by ID scoped to the organisation.

        Raises:
            MonthlyReviewNotFoundError: if not found.
        """
        stmt = select(MonthlyReview).where(
            MonthlyReview.organization_id == org_id,
            MonthlyReview.review_id == review_id,
        )
        review = self.db.scalar(stmt)
        if review is None:
            raise MonthlyReviewNotFoundError(review_id)
        return review

    def list_reviews(
        self,
        org_id: UUID,
        *,
        employee_id: UUID | None = None,
        contract_id: UUID | None = None,
        status: MonthlyReviewStatus | None = None,
        review_month: date | None = None,
        search: str | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[MonthlyReview]:
        """List monthly reviews for an organisation with optional filters.

        Results are ordered by review_month descending (most recent first).
        """
        stmt = (
            select(MonthlyReview)
            .where(MonthlyReview.organization_id == org_id)
            .order_by(MonthlyReview.review_month.desc())
        )

        if employee_id is not None:
            stmt = stmt.where(MonthlyReview.employee_id == employee_id)
        if contract_id is not None:
            stmt = stmt.where(MonthlyReview.contract_id == contract_id)
        if status is not None:
            stmt = stmt.where(MonthlyReview.status == status)
        if review_month is not None:
            stmt = stmt.where(MonthlyReview.review_month == review_month)
        if search:
            pattern = f"%{search}%"
            stmt = stmt.where(MonthlyReview.challenges.ilike(pattern))

        return paginate(
            self.db,
            stmt,
            pagination,
            count_column=MonthlyReview.review_id,
        )

    # ------------------------------------------------------------------
    # Mutation methods
    # ------------------------------------------------------------------

    def create_review(
        self,
        org_id: UUID,
        *,
        employee_id: UUID,
        reviewer_id: UUID,
        contract_id: UUID,
        review_month: date,
        objective_progress: dict | list | None = None,
        challenges: str | None = None,
        support_required: str | None = None,
    ) -> MonthlyReview:
        """Create a new monthly review in DRAFT status.

        Raises:
            MonthlyReviewValidationError: if review_month is not the first of
                the month (non-critical normalisation check).
        """
        self._ensure_pms_write_mode(org_id)
        # Normalise review_month to the first of the month
        if review_month.day != 1:
            raise MonthlyReviewValidationError(
                f"review_month must be the first day of the month "
                f"(got {review_month}). Use date(year, month, 1)."
            )

        # Check one per employee per month
        existing = self.db.scalar(
            select(MonthlyReview).where(
                MonthlyReview.organization_id == org_id,
                MonthlyReview.employee_id == employee_id,
                MonthlyReview.review_month == review_month,
            )
        )
        if existing:
            raise MonthlyReviewValidationError(
                f"A review already exists for this employee for {review_month.strftime('%B %Y')}"
            )

        review = MonthlyReview(
            organization_id=org_id,
            employee_id=employee_id,
            reviewer_id=reviewer_id,
            contract_id=contract_id,
            review_month=review_month,
            status=MonthlyReviewStatus.DRAFT,
            objective_progress=objective_progress,
            challenges=challenges,
            support_required=support_required,
        )
        self.db.add(review)
        self.db.flush()
        logger.info(
            "Created MonthlyReview %s for employee %s (%s)",
            review.review_id,
            employee_id,
            review_month,
        )
        return review

    def submit_review(
        self,
        org_id: UUID,
        review_id: UUID,
        *,
        objective_progress: dict | list,
        challenges: str | None = None,
        support_required: str | None = None,
        reviewer_feedback: str | None = None,
        agreed_actions: str | None = None,
    ) -> MonthlyReview:
        """Submit a monthly review.

        Updates review content, records reviewer sign-off date (today),
        and transitions status to SUBMITTED.

        Raises:
            MonthlyReviewNotFoundError: if review does not exist.
        """
        self._ensure_pms_write_mode(org_id)
        review = self.get_review(org_id, review_id)

        if review.status != MonthlyReviewStatus.DRAFT:
            raise MonthlyReviewValidationError(
                f"Cannot submit review in {review.status.value} status, must be DRAFT"
            )

        review.objective_progress = objective_progress
        review.challenges = challenges
        review.support_required = support_required
        review.reviewer_feedback = reviewer_feedback
        review.agreed_actions = agreed_actions
        review.reviewer_signed_date = date.today()
        review.status = MonthlyReviewStatus.SUBMITTED

        self.db.flush()
        logger.info(
            "Submitted MonthlyReview %s (employee %s, month %s)",
            review_id,
            review.employee_id,
            review.review_month,
        )
        return review

    def acknowledge_review(
        self,
        org_id: UUID,
        review_id: UUID,
    ) -> MonthlyReview:
        """Acknowledge a monthly review by the employee.

        Records the employee sign-off date (today) and transitions
        status to ACKNOWLEDGED.

        Raises:
            MonthlyReviewNotFoundError: if review does not exist.
        """
        self._ensure_pms_write_mode(org_id)
        review = self.get_review(org_id, review_id)

        if review.status != MonthlyReviewStatus.SUBMITTED:
            raise MonthlyReviewValidationError(
                f"Cannot acknowledge review in {review.status.value} status, must be SUBMITTED"
            )

        review.employee_signed_date = date.today()
        review.status = MonthlyReviewStatus.ACKNOWLEDGED

        self.db.flush()
        logger.info(
            "Acknowledged MonthlyReview %s by employee %s",
            review_id,
            review.employee_id,
        )
        return review

    def get_missing_reviews(
        self,
        org_id: UUID,
        cycle_id: UUID,
        month: date,
    ) -> list:
        """Return employees with active contracts who have no review for the given month.

        Finds employees that have an active PerformanceContract in the given
        cycle but no MonthlyReview for the specified month.

        Args:
            org_id: Organisation ID.
            cycle_id: Appraisal cycle ID to scope the contract search.
            month: First day of the target review month.

        Returns:
            List of employee UUIDs (as a simple list of dicts with
            ``employee_id`` and ``contract_id``) that are missing reviews.
        """
        from app.models.people.perf.performance_contract import PerformanceContract
        from app.models.people.perf.pms_enums import ContractStatus

        # Subquery: employees who already have a review this month
        has_review_subq = (
            select(MonthlyReview.employee_id)
            .where(
                MonthlyReview.organization_id == org_id,
                MonthlyReview.review_month == month,
            )
            .scalar_subquery()
        )

        # Query: active contracts where employee has no review this month
        stmt = select(PerformanceContract).where(
            PerformanceContract.organization_id == org_id,
            PerformanceContract.cycle_id == cycle_id,
            PerformanceContract.status == ContractStatus.ACTIVE,
            PerformanceContract.employee_id.not_in(has_review_subq),
        )

        contracts = list(self.db.scalars(stmt).all())
        logger.info(
            "Found %d employees missing monthly reviews for %s in cycle %s",
            len(contracts),
            month,
            cycle_id,
        )
        return contracts
