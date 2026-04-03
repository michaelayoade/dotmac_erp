"""
Institutional Performance Service — OHCSF Performance Management System.

Handles creation, scoring, and reconciliation of InstitutionalPerformance
records. Each record captures a ministry/department's appraisal within
an appraisal cycle, including per-criterion scores and a reconciliation
workflow that aligns institutional scores with individual employee ratings.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.people.perf.institutional_performance import (
    InstitutionalCriteriaTemplate,
    InstitutionalPerformance,
)
from app.models.people.perf.pms_enums import InstitutionalPerfStatus, InstitutionType
from app.services.common import PaginatedResult, PaginationParams, paginate
from app.services.people.perf.performance_mode_policy import enforce_pms_write_mode
from app.services.people.perf.scoring_engine import OHCSFScoringEngine

if TYPE_CHECKING:
    from app.web.deps import WebAuthContext

logger = logging.getLogger(__name__)

__all__ = [
    "InstitutionalServiceError",
    "InstitutionalPerfNotFoundError",
    "InstitutionalValidationError",
    "InstitutionalPerformanceService",
]


# =============================================================================
# Error classes
# =============================================================================


class InstitutionalServiceError(Exception):
    """Base error for InstitutionalPerformanceService."""


class InstitutionalPerfNotFoundError(InstitutionalServiceError):
    """Raised when an InstitutionalPerformance record cannot be found."""

    def __init__(self, inst_perf_id: UUID) -> None:
        self.inst_perf_id = inst_perf_id
        super().__init__(f"Institutional performance record {inst_perf_id} not found")


class InstitutionalValidationError(InstitutionalServiceError):
    """Raised when input validation fails."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


# =============================================================================
# Service
# =============================================================================


class InstitutionalPerformanceService:
    """Service for managing OHCSF institutional performance appraisals."""

    def __init__(self, db: Session, ctx: WebAuthContext | None = None) -> None:
        self.db = db
        self.ctx = ctx
        self._scoring_engine = OHCSFScoringEngine()

    def _ensure_pms_write_mode(self, org_id: UUID) -> None:
        try:
            enforce_pms_write_mode(self.db, org_id)
        except ValueError as exc:
            raise InstitutionalValidationError(str(exc)) from exc

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_or_404(self, org_id: UUID, inst_perf_id: UUID) -> InstitutionalPerformance:
        """Fetch an InstitutionalPerformance scoped to org or raise NotFound."""
        stmt = select(InstitutionalPerformance).where(
            InstitutionalPerformance.organization_id == org_id,
            InstitutionalPerformance.inst_perf_id == inst_perf_id,
        )
        record = self.db.scalar(stmt)
        if record is None:
            raise InstitutionalPerfNotFoundError(inst_perf_id)
        return record

    def _score_criterion(
        self,
        entry: dict,
    ) -> dict:
        """Score a single criterion entry in-place.

        Requires ``target`` and ``achievement`` to be populated.
        Uses a simplified direct-ratio approach: raw_score = (achievement / target) * 100,
        then weighted_score = raw_score * (weight / 100).

        Returns:
            Updated entry dict with raw_score and weighted_score set.
        """
        target = entry.get("target")
        achievement = entry.get("achievement")
        weight = entry.get("weight", 0)

        if target is None or achievement is None:
            return entry

        try:
            target_d = Decimal(str(target))
            achievement_d = Decimal(str(achievement))
            weight_d = Decimal(str(weight))
        except (ValueError, TypeError, ArithmeticError):
            logger.warning(
                "Could not convert criterion values to Decimal for %r",
                entry.get("criteria"),
            )
            return entry

        if target_d == Decimal("0"):
            raw_score = Decimal("0")
        else:
            raw_score = (achievement_d / target_d) * Decimal("100")
            # Clamp to [0, 100]
            raw_score = max(Decimal("0"), min(Decimal("100"), raw_score))

        raw_score = raw_score.quantize(Decimal("0.01"))
        # weight is stored as a whole number percentage (e.g. 30 for 30%)
        weighted_score = (raw_score * weight_d / Decimal("100")).quantize(
            Decimal("0.01")
        )

        entry = dict(entry)
        entry["raw_score"] = str(raw_score)
        entry["weighted_score"] = str(weighted_score)
        return entry

    # ------------------------------------------------------------------
    # Public query methods
    # ------------------------------------------------------------------

    def get_institutional_perf(
        self, org_id: UUID, inst_perf_id: UUID
    ) -> InstitutionalPerformance:
        """Return an institutional performance record scoped to the organisation.

        Raises:
            InstitutionalPerfNotFoundError: if not found.
        """
        return self._get_or_404(org_id, inst_perf_id)

    def list_institutional_perfs(
        self,
        org_id: UUID,
        *,
        cycle_id: UUID | None = None,
        department_id: UUID | None = None,
        status: InstitutionalPerfStatus | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[InstitutionalPerformance]:
        """List institutional performance records for an organisation.

        Optional filters:
            cycle_id: Limit to a specific appraisal cycle.
            department_id: Limit to a specific department.
            status: Limit to a specific lifecycle status.
            pagination: Offset/limit parameters.
        """
        stmt = (
            select(InstitutionalPerformance)
            .where(InstitutionalPerformance.organization_id == org_id)
            .order_by(InstitutionalPerformance.created_at.desc())
        )

        if cycle_id is not None:
            stmt = stmt.where(InstitutionalPerformance.cycle_id == cycle_id)
        if department_id is not None:
            stmt = stmt.where(InstitutionalPerformance.department_id == department_id)
        if status is not None:
            stmt = stmt.where(InstitutionalPerformance.status == status)

        return paginate(
            self.db,
            stmt,
            pagination,
            count_column=InstitutionalPerformance.inst_perf_id,
        )

    # ------------------------------------------------------------------
    # Public mutation methods
    # ------------------------------------------------------------------

    def create_for_cycle(
        self,
        org_id: UUID,
        *,
        cycle_id: UUID,
        institution_type: InstitutionType,
        department_id: UUID | None = None,
    ) -> InstitutionalPerformance:
        """Create an institutional performance record for a cycle.

        Loads the active criteria templates for the given institution_type and
        uses them to initialise ``criteria_scores`` as a list of criterion
        entries.  Each entry has the keys:
            - criteria: name from the template
            - weight: default_weight from the template
            - target: None (to be filled in later)
            - achievement: None (to be filled in later)
            - raw_score: None
            - weighted_score: None

        The record is created in DRAFT status.

        Returns:
            Newly created InstitutionalPerformance (flushed, not committed).
        """
        self._ensure_pms_write_mode(org_id)
        tmpl_stmt = (
            select(InstitutionalCriteriaTemplate)
            .where(
                InstitutionalCriteriaTemplate.organization_id == org_id,
                InstitutionalCriteriaTemplate.institution_type == institution_type,
                InstitutionalCriteriaTemplate.is_active.is_(True),
            )
            .order_by(InstitutionalCriteriaTemplate.sequence)
        )
        templates = list(self.db.scalars(tmpl_stmt).all())

        criteria_scores = [
            {
                "criteria": tmpl.criteria_name,
                "weight": tmpl.default_weight,
                "target": None,
                "achievement": None,
                "raw_score": None,
                "weighted_score": None,
            }
            for tmpl in templates
        ]

        record = InstitutionalPerformance(
            organization_id=org_id,
            cycle_id=cycle_id,
            department_id=department_id,
            institution_type=institution_type,
            status=InstitutionalPerfStatus.DRAFT,
            criteria_scores=criteria_scores,
        )

        self.db.add(record)
        self.db.flush()
        logger.info(
            "Created InstitutionalPerformance for org=%s cycle=%s type=%s",
            org_id,
            cycle_id,
            institution_type,
        )
        return record

    def score_criteria(
        self,
        org_id: UUID,
        inst_perf_id: UUID,
        *,
        criteria_scores: list[dict],
    ) -> InstitutionalPerformance:
        """Score institutional criteria and compute the composite score.

        For each criterion entry that has both ``target`` and ``achievement``
        values, calculates ``raw_score`` and ``weighted_score``.  Then sums
        all weighted scores to produce ``composite_score`` and derives
        ``rating_label`` via the OHCSF rating scale.

        Sets status to APPRAISED.

        Args:
            org_id: Organisation scope.
            inst_perf_id: Primary key of the InstitutionalPerformance record.
            criteria_scores: List of criterion dicts with target/achievement populated.

        Returns:
            Updated InstitutionalPerformance (flushed, not committed).

        Raises:
            InstitutionalPerfNotFoundError: if the record does not exist.
        """
        self._ensure_pms_write_mode(org_id)
        record = self._get_or_404(org_id, inst_perf_id)

        scored = [self._score_criterion(entry) for entry in criteria_scores]

        weighted_scores: list[Decimal] = []
        for entry in scored:
            ws = entry.get("weighted_score")
            if ws is not None:
                try:
                    weighted_scores.append(Decimal(str(ws)))
                except (ValueError, TypeError, ArithmeticError) as exc:
                    logger.warning("Skipping bad weighted_score %r: %s", ws, exc)

        composite = self._scoring_engine.calculate_composite(weighted_scores)
        _rating_int, rating_label = self._scoring_engine.score_to_rating(composite)

        record.criteria_scores = cast(dict[str, Any] | None, scored)
        record.composite_score = composite
        record.rating_label = rating_label
        record.status = InstitutionalPerfStatus.APPRAISED

        self.db.flush()
        logger.info(
            "Scored InstitutionalPerformance %s: composite=%.2f rating=%s",
            inst_perf_id,
            composite,
            rating_label,
        )
        return record

    def reconcile_with_employee_ratings(
        self,
        org_id: UUID,
        inst_perf_id: UUID,
        *,
        reconciled_by_id: UUID,
        notes: str,
        adjusted_composite: Decimal | None = None,
    ) -> InstitutionalPerformance:
        """Reconcile institutional performance with individual employee ratings.

        Saves the current composite score as ``pre_reconciliation_composite``
        before any adjustments.  If ``adjusted_composite`` is provided,
        updates ``composite_score`` and re-derives ``rating_label``.

        Sets ``is_reconciled=True``, ``reconciled_by_id``,
        ``reconciliation_date``, ``reconciliation_notes``, and transitions
        status to RECONCILED.

        Args:
            org_id: Organisation scope.
            inst_perf_id: Primary key of the InstitutionalPerformance record.
            reconciled_by_id: Employee ID of the person performing reconciliation.
            notes: Free-text reconciliation notes.
            adjusted_composite: Optional adjusted composite score (0–100).

        Returns:
            Updated InstitutionalPerformance (flushed, not committed).

        Raises:
            InstitutionalPerfNotFoundError: if the record does not exist.
        """
        self._ensure_pms_write_mode(org_id)
        record = self._get_or_404(org_id, inst_perf_id)

        # Snapshot current composite before any adjustment
        record.pre_reconciliation_composite = record.composite_score

        if adjusted_composite is not None:
            record.composite_score = adjusted_composite
            _rating_int, rating_label = self._scoring_engine.score_to_rating(
                adjusted_composite
            )
            record.rating_label = rating_label

        record.is_reconciled = True
        record.reconciled_by_id = reconciled_by_id
        record.reconciliation_date = date.today()
        record.reconciliation_notes = notes
        record.status = InstitutionalPerfStatus.RECONCILED

        self.db.flush()
        logger.info(
            "Reconciled InstitutionalPerformance %s by %s; pre=%.2f adjusted=%s",
            inst_perf_id,
            reconciled_by_id,
            record.pre_reconciliation_composite or 0,
            adjusted_composite,
        )
        return record
