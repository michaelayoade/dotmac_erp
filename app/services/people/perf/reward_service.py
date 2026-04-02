"""
PMS Rewards and Recognition Service.

Implements transparent nomination and approval workflow for appraisal-based
recognition decisions.
"""

from __future__ import annotations

import logging
from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.people.perf.appraisal import Appraisal, AppraisalStatus
from app.models.people.perf.appraisal_appeal import AppraisalAppeal
from app.models.people.perf.appraisal_outcome_action import AppraisalOutcomeAction
from app.models.people.perf.pip import PerformanceImprovementPlan
from app.models.people.perf.pms_enums import AppealStatus, PIPStatus
from app.models.people.perf.pms_enums import OutcomeActionStatus, OutcomeActionType
from app.services.common import PaginatedResult, PaginationParams, paginate

logger = logging.getLogger(__name__)

__all__ = [
    "RewardServiceError",
    "RewardNotFoundError",
    "RewardValidationError",
    "PMSRewardService",
]

_DEFAULT_MIN_REWARD_RATING = 4


class RewardServiceError(Exception):
    """Base error for reward workflow operations."""


class RewardNotFoundError(RewardServiceError):
    """Raised when reward action cannot be found."""

    def __init__(self, action_id: UUID) -> None:
        super().__init__(f"Reward action {action_id} not found")


class RewardValidationError(RewardServiceError):
    """Raised when reward workflow validation fails."""


class PMSRewardService:
    """Service for PMS recognition nomination and approval."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def _get_action_or_raise(self, org_id: UUID, action_id: UUID) -> AppraisalOutcomeAction:
        action = self.db.scalar(
            select(AppraisalOutcomeAction).where(
                AppraisalOutcomeAction.organization_id == org_id,
                AppraisalOutcomeAction.action_id == action_id,
                AppraisalOutcomeAction.action_type == OutcomeActionType.REWARD,
            )
        )
        if action is None:
            raise RewardNotFoundError(action_id)
        return action

    def _has_unresolved_appeal(self, org_id: UUID, appraisal_id: UUID) -> bool:
        appeal = self.db.scalar(
            select(AppraisalAppeal.appeal_id).where(
                AppraisalAppeal.organization_id == org_id,
                AppraisalAppeal.appraisal_id == appraisal_id,
                AppraisalAppeal.status.notin_(
                    [AppealStatus.RESOLVED, AppealStatus.DISMISSED]
                ),
            )
        )
        return appeal is not None

    def _has_unresolved_pip(self, org_id: UUID, appraisal_id: UUID) -> bool:
        pip = self.db.scalar(
            select(PerformanceImprovementPlan.pip_id).where(
                PerformanceImprovementPlan.organization_id == org_id,
                PerformanceImprovementPlan.appraisal_id == appraisal_id,
                PerformanceImprovementPlan.status.notin_(
                    [PIPStatus.IMPROVED, PIPStatus.CLOSED]
                ),
            )
        )
        return pip is not None

    def _has_existing_reward_action(self, org_id: UUID, appraisal_id: UUID) -> bool:
        existing = self.db.scalar(
            select(AppraisalOutcomeAction.action_id).where(
                AppraisalOutcomeAction.organization_id == org_id,
                AppraisalOutcomeAction.appraisal_id == appraisal_id,
                AppraisalOutcomeAction.action_type == OutcomeActionType.REWARD,
                AppraisalOutcomeAction.status.in_(
                    [OutcomeActionStatus.PENDING, OutcomeActionStatus.COMPLETED]
                ),
            )
        )
        return existing is not None

    def _evaluate_reward_eligibility(
        self,
        appraisal: Appraisal,
        *,
        min_rating: int = _DEFAULT_MIN_REWARD_RATING,
    ) -> dict:
        reasons: list[str] = []
        if appraisal.status != AppraisalStatus.COMPLETED:
            reasons.append("Appraisal must be completed")
        if appraisal.final_rating is None:
            reasons.append("Final rating is required")
        elif appraisal.final_rating < min_rating:
            reasons.append(f"Final rating must be >= {min_rating}")
        if getattr(appraisal, "is_prior_year_carryover", False) is True:
            reasons.append("Prior-year carryover appraisals are not eligible")
        if self._has_unresolved_appeal(appraisal.organization_id, appraisal.appraisal_id):
            reasons.append("Unresolved appeal exists")
        if self._has_unresolved_pip(appraisal.organization_id, appraisal.appraisal_id):
            reasons.append("Unresolved PIP exists")
        if self._has_existing_reward_action(appraisal.organization_id, appraisal.appraisal_id):
            reasons.append("Reward action already exists")

        score = float(appraisal.final_score) if appraisal.final_score is not None else 0.0
        return {
            "eligible": len(reasons) == 0,
            "reasons": reasons,
            "final_rating": appraisal.final_rating,
            "final_score": score,
        }

    @staticmethod
    def _append_audit_entry(
        action: AppraisalOutcomeAction,
        *,
        event: str,
        actor_person_id: UUID | None,
        actor_employee_id: UUID | None = None,
        note: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        parts = [
            f"[{date.today().isoformat()}]",
            event,
            f"actor_person={actor_person_id}" if actor_person_id else None,
            f"actor_employee={actor_employee_id}" if actor_employee_id else None,
            f"note={note.strip()}" if note else None,
            f"meta={metadata}" if metadata else None,
        ]
        entry = " | ".join([part for part in parts if part])
        existing = (action.notes or "").strip()
        action.notes = f"{existing}\n{entry}".strip() if existing else entry

    def list_reward_candidates(
        self,
        org_id: UUID,
        *,
        cycle_id: UUID | None = None,
        min_rating: int = _DEFAULT_MIN_REWARD_RATING,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[Appraisal]:
        """List completed appraisals eligible for nomination."""
        stmt = (
            select(Appraisal)
            .where(
                Appraisal.organization_id == org_id,
                Appraisal.status == AppraisalStatus.COMPLETED,
                Appraisal.final_rating.isnot(None),
                Appraisal.final_rating >= min_rating,
            )
            .order_by(Appraisal.final_score.desc().nullslast(), Appraisal.completed_on.desc())
        )
        if cycle_id is not None:
            stmt = stmt.where(Appraisal.cycle_id == cycle_id)
        raw = paginate(
            self.db,
            stmt,
            pagination,
            count_column=Appraisal.appraisal_id,
        )
        eligible_items = [
            appraisal
            for appraisal in raw.items
            if self._evaluate_reward_eligibility(
                appraisal, min_rating=min_rating
            )["eligible"]
        ]
        return PaginatedResult(
            items=eligible_items,
            total=len(eligible_items),
            offset=0,
            limit=len(eligible_items),
        )

    def list_reward_candidates_with_eligibility(
        self,
        org_id: UUID,
        *,
        cycle_id: UUID | None = None,
        min_rating: int = _DEFAULT_MIN_REWARD_RATING,
        limit: int = 200,
    ) -> list[dict]:
        """List reward candidates with transparent eligibility reasons."""
        stmt = (
            select(Appraisal)
            .where(
                Appraisal.organization_id == org_id,
                Appraisal.status == AppraisalStatus.COMPLETED,
            )
            .order_by(Appraisal.final_score.desc().nullslast(), Appraisal.completed_on.desc())
            .limit(limit)
        )
        if cycle_id is not None:
            stmt = stmt.where(Appraisal.cycle_id == cycle_id)
        appraisals = list(self.db.scalars(stmt).all())
        rows: list[dict] = []
        for appraisal in appraisals:
            evaluation = self._evaluate_reward_eligibility(appraisal, min_rating=min_rating)
            rows.append(
                {
                    "appraisal": appraisal,
                    "eligible": evaluation["eligible"],
                    "reasons": evaluation["reasons"],
                    "final_rating": evaluation["final_rating"],
                    "final_score": evaluation["final_score"],
                }
            )
        return rows

    def list_reward_actions(
        self,
        org_id: UUID,
        *,
        status: OutcomeActionStatus | None = None,
        cycle_id: UUID | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[AppraisalOutcomeAction]:
        """List reward actions with optional status/cycle filters."""
        stmt = (
            select(AppraisalOutcomeAction)
            .join(
                Appraisal,
                Appraisal.appraisal_id == AppraisalOutcomeAction.appraisal_id,
            )
            .where(
                AppraisalOutcomeAction.organization_id == org_id,
                AppraisalOutcomeAction.action_type == OutcomeActionType.REWARD,
            )
            .order_by(AppraisalOutcomeAction.created_at.desc())
        )
        if status is not None:
            stmt = stmt.where(AppraisalOutcomeAction.status == status)
        if cycle_id is not None:
            stmt = stmt.where(Appraisal.cycle_id == cycle_id)
        return paginate(
            self.db,
            stmt,
            pagination,
            count_column=AppraisalOutcomeAction.action_id,
        )

    def nominate_reward(
        self,
        org_id: UUID,
        *,
        appraisal_id: UUID,
        reward_type: str,
        nomination_notes: str | None,
        nominated_by_person_id: UUID | None = None,
    ) -> AppraisalOutcomeAction:
        """Create a pending reward action for a completed appraisal."""
        appraisal = self.db.scalar(
            select(Appraisal).where(
                Appraisal.organization_id == org_id,
                Appraisal.appraisal_id == appraisal_id,
            )
        )
        if appraisal is None:
            raise RewardValidationError("Appraisal not found")
        evaluation = self._evaluate_reward_eligibility(appraisal)
        if not evaluation["eligible"]:
            raise RewardValidationError(
                "Appraisal is not eligible for reward nomination: "
                + "; ".join(evaluation["reasons"])
            )

        notes = nomination_notes.strip() if nomination_notes else None
        reward_type_clean = reward_type.strip().upper()
        action = AppraisalOutcomeAction(
            organization_id=org_id,
            appraisal_id=appraisal_id,
            action_type=OutcomeActionType.REWARD,
            description=f"Reward nomination: {reward_type_clean}",
            reference_type="reward_nomination",
            status=OutcomeActionStatus.PENDING,
            notes=None,
            created_by_id=nominated_by_person_id,
            updated_by_id=nominated_by_person_id,
        )
        self._append_audit_entry(
            action,
            event="NOMINATED",
            actor_person_id=nominated_by_person_id,
            note=notes,
            metadata={
                "reward_type": reward_type_clean,
                "min_rating": _DEFAULT_MIN_REWARD_RATING,
                "final_rating": evaluation["final_rating"],
                "final_score": evaluation["final_score"],
            },
        )
        self.db.add(action)

        appraisal.reward_nominated = True
        appraisal.reward_type = reward_type_clean
        appraisal.reward_notes = notes
        self.db.flush()

        logger.info(
            "Created reward nomination action %s for appraisal %s",
            action.action_id,
            appraisal_id,
        )
        return action

    def approve_reward(
        self,
        org_id: UUID,
        action_id: UUID,
        *,
        approved_by_employee_id: UUID | None = None,
        approved_by_person_id: UUID | None = None,
        approval_notes: str | None = None,
    ) -> AppraisalOutcomeAction:
        """Approve a pending reward action."""
        action = self._get_action_or_raise(org_id, action_id)
        if action.status != OutcomeActionStatus.PENDING:
            raise RewardValidationError(
                f"Only pending reward actions can be approved (current: {action.status.value})"
            )

        action.status = OutcomeActionStatus.COMPLETED
        action.actioned_by_id = approved_by_employee_id
        action.actioned_date = date.today()
        action.updated_by_id = approved_by_person_id
        self._append_audit_entry(
            action,
            event="APPROVED",
            actor_person_id=approved_by_person_id,
            actor_employee_id=approved_by_employee_id,
            note=approval_notes,
            metadata={"status": action.status.value},
        )
        self.db.flush()

        logger.info("Approved reward action %s", action_id)
        return action

    def cancel_reward(
        self,
        org_id: UUID,
        action_id: UUID,
        *,
        cancelled_by_person_id: UUID | None = None,
        cancellation_notes: str | None = None,
    ) -> AppraisalOutcomeAction:
        """Cancel a pending reward action and clear nomination flag on appraisal."""
        action = self._get_action_or_raise(org_id, action_id)
        if action.status != OutcomeActionStatus.PENDING:
            raise RewardValidationError(
                f"Only pending reward actions can be cancelled (current: {action.status.value})"
            )

        appraisal = self.db.scalar(
            select(Appraisal).where(
                Appraisal.organization_id == org_id,
                Appraisal.appraisal_id == action.appraisal_id,
            )
        )
        if appraisal is not None:
            appraisal.reward_nominated = False
            appraisal.reward_type = None
            appraisal.reward_notes = None

        action.status = OutcomeActionStatus.CANCELLED
        action.updated_by_id = cancelled_by_person_id
        self._append_audit_entry(
            action,
            event="CANCELLED",
            actor_person_id=cancelled_by_person_id,
            note=cancellation_notes,
            metadata={"status": action.status.value},
        )
        self.db.flush()

        logger.info("Cancelled reward action %s", action_id)
        return action
