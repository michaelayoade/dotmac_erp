"""
OHCSF Appraisal Service.

Implements the OHCSF-specific multi-stage appraisal workflow:
  DRAFT → SELF_ASSESSMENT → PENDING_REVIEW → UNDER_REVIEW →
  PENDING_COUNTERSIGN → COUNTERSIGNED → PENDING_COMMITTEE → COMPLETED

Cascade-up rule: a supervisor cannot complete their appraisal until
all their direct reports have completed theirs in the same cycle.

Quarterly support: quarterly sub-cycles produce quarterly Appraisal
records that feed into the annual rating calculation.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.people.perf.appraisal import (
    Appraisal,
    AppraisalKRAScore,
    AppraisalStatus,
)
from app.models.people.perf.appraisal_cycle import AppraisalCycle
from app.models.people.perf.competency_assessment import CompetencyAssessment
from app.models.people.perf.performance_contract import PerformanceContract
from app.models.people.perf.pip import PerformanceImprovementPlan
from app.models.people.perf.pms_enums import ContractStatus
from app.models.people.perf.pms_enums import CommitteeDecision
from app.models.people.perf.pms_enums import PIPStatus
from app.services.people.perf.performance_policy import (
    GOVERNMENT_PMS_POLICY,
    get_policy_profile,
)
from app.services.people.perf.performance_mode_policy import enforce_pms_write_mode
from app.services.people.hr.org_resolver import OrgResolver
from app.services.people.perf.scoring_engine import OHCSFScoringEngine

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

TWO_DP = Decimal("0.01")
UNDERPERFORMANCE_SCORE_THRESHOLD = Decimal("50.00")

# ---------------------------------------------------------------------------
# Status transition map
# ---------------------------------------------------------------------------

OHCSF_STATUS_TRANSITIONS: dict[AppraisalStatus, set[AppraisalStatus]] = {
    status: set(next_states)
    for status, next_states in GOVERNMENT_PMS_POLICY.ohcsf_status_transitions.items()
}

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class OHCSFAppraisalError(Exception):
    """Base error for OHCSF appraisal workflow."""


class OHCSFAppraisalNotFoundError(OHCSFAppraisalError):
    """Appraisal record not found."""

    def __init__(self, appraisal_id: UUID) -> None:
        self.appraisal_id = appraisal_id
        super().__init__(f"Appraisal {appraisal_id} not found")


class OHCSFAppraisalStatusError(OHCSFAppraisalError):
    """Invalid status transition."""

    def __init__(self, current: AppraisalStatus, target: AppraisalStatus) -> None:
        self.current = current
        self.target = target
        super().__init__(
            f"Cannot transition appraisal from {current.value} to {target.value}"
        )


class CascadeUpViolation(OHCSFAppraisalError):
    """Supervisor cannot proceed until all direct reports are completed."""

    def __init__(self, incomplete_count: int) -> None:
        self.incomplete_count = incomplete_count
        super().__init__(
            f"Supervisor cannot be appraised until all subordinates complete "
            f"their appraisals ({incomplete_count} still incomplete)"
        )


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class OHCSFAppraisalService:
    """Service for managing OHCSF appraisal workflow operations."""

    def __init__(
        self, db: Session, policy_profile_name: str = "GOVERNMENT_PMS"
    ) -> None:
        self.db = db
        self._policy = get_policy_profile(policy_profile_name)
        self._scoring = OHCSFScoringEngine(policy_profile_name=policy_profile_name)

    def _ensure_pms_write_mode(self, org_id: UUID) -> None:
        try:
            enforce_pms_write_mode(self.db, org_id)
        except ValueError as exc:
            raise OHCSFAppraisalError(str(exc)) from exc

    def _progress_error_message(self, detail: str) -> str:
        prefix = self._policy.ui_messages.get(
            "appraisal_progress_prefix",
            "Cannot progress appraisal",
        )
        return f"{prefix}: {detail}"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_or_404(self, org_id: UUID, appraisal_id: UUID) -> Appraisal:
        """Fetch appraisal by PK with org check, or raise OHCSFAppraisalNotFoundError."""
        appraisal = self.db.get(Appraisal, appraisal_id)
        if appraisal is None or appraisal.organization_id != org_id:
            raise OHCSFAppraisalNotFoundError(appraisal_id)
        return appraisal

    def _validate_transition(
        self, current: AppraisalStatus, target: AppraisalStatus
    ) -> None:
        """Raise OHCSFAppraisalStatusError if transition is not permitted."""
        allowed = OHCSF_STATUS_TRANSITIONS.get(current, set())
        if target not in allowed:
            raise OHCSFAppraisalStatusError(current, target)

    def _enforce_phase_deadline(
        self,
        appraisal: Appraisal,
        *,
        deadline_field: str,
        phase_label: str,
    ) -> None:
        cycle = getattr(appraisal, "cycle", None)
        if cycle is None:
            return
        deadline = getattr(cycle, deadline_field, None)
        if not isinstance(deadline, date):
            return
        if date.today() > deadline:
            raise OHCSFAppraisalError(
                f"Cannot submit {phase_label}: cycle deadline was {deadline.isoformat()}"
            )

    def _check_cascade_up(
        self, org_id: UUID, cycle_id: UUID, employee_id: UUID
    ) -> None:
        """Raise CascadeUpViolation if any direct report has an incomplete appraisal.

        An appraisal is considered incomplete if its status is not COMPLETED or CANCELLED.
        """
        direct_report_ids = [
            employee.employee_id
            for employee in OrgResolver(self.db).get_direct_reports(
                employee_id,
                org_id,
            )
            if employee.employee_id != employee_id
        ]
        legacy_direct_report_ids_stmt = select(Appraisal.employee_id).where(
            Appraisal.organization_id == org_id,
            Appraisal.cycle_id == cycle_id,
            Appraisal.manager_id == employee_id,
        )
        direct_report_ids.extend(
            employee_id
            for employee_id in self.db.scalars(legacy_direct_report_ids_stmt).all()
            if employee_id not in direct_report_ids
        )

        if not direct_report_ids:
            return  # No direct reports — no cascade-up constraint

        # Count their incomplete appraisals in this cycle
        terminal_statuses = {AppraisalStatus.COMPLETED, AppraisalStatus.CANCELLED}
        incomplete_count = (
            self.db.scalar(
                select(func.count(Appraisal.appraisal_id)).where(
                    Appraisal.organization_id == org_id,
                    Appraisal.cycle_id == cycle_id,
                    Appraisal.employee_id.in_(direct_report_ids),
                    Appraisal.status.not_in(terminal_statuses),
                )
            )
            or 0
        )

        if incomplete_count > 0:
            raise CascadeUpViolation(incomplete_count)

    def _apply_kra_ratings(
        self,
        appraisal_id: UUID,
        org_id: UUID,
        kra_ratings: list[dict],
        *,
        rating_field: str,
        comments_field: str,
    ) -> None:
        """Upsert KRA score rows for self or manager ratings.

        Each entry in kra_ratings must have:
            kra_id (UUID | str), rating (int), comments (str, optional)
        """
        for entry in kra_ratings:
            kra_id = UUID(str(entry["kra_id"]))
            stmt = select(AppraisalKRAScore).where(
                AppraisalKRAScore.appraisal_id == appraisal_id,
                AppraisalKRAScore.kra_id == kra_id,
                AppraisalKRAScore.organization_id == org_id,
            )
            score_row = self.db.scalar(stmt)
            if score_row is None:
                score_row = AppraisalKRAScore(
                    organization_id=org_id,
                    appraisal_id=appraisal_id,
                    kra_id=kra_id,
                    weightage=Decimal(str(entry.get("weightage", "0"))),
                )
                self.db.add(score_row)

            setattr(score_row, rating_field, entry.get("rating"))
            if comments_field:
                setattr(score_row, comments_field, entry.get("comments"))

            # For manager ratings, also apply OHCSF threshold-based raw score
            if rating_field == "manager_rating":
                actual = entry.get("actual_achievement")
                if actual is not None:
                    score_row.actual_achievement = Decimal(str(actual))
                    thresholds: dict[str, Decimal] = {}
                    for band in ("outstanding", "excellent", "good", "fair", "poor"):
                        val = entry.get(f"{band}_threshold")
                        if val is not None:
                            thresholds[band] = Decimal(str(val))
                            setattr(score_row, f"{band}_threshold", thresholds[band])
                    if len(thresholds) < 5 and actual is not None:
                        logger.warning(
                            "Incomplete thresholds (%d/5) for KRA %s in appraisal %s"
                            " — skipping score calculation",
                            len(thresholds),
                            kra_id,
                            appraisal_id,
                        )
                    if len(thresholds) == 5:
                        raw_pct = self._scoring.calculate_raw_score(
                            score_row.actual_achievement, thresholds
                        )
                        score_row.raw_score_percentage = raw_pct
                        weighted = self._scoring.calculate_weighted_score(
                            raw_pct, score_row.weightage / Decimal("100")
                        )
                        score_row.weighted_score = weighted
                        score_row.final_rating = entry.get("rating")

    def _compute_objective_score(self, appraisal_id: UUID, org_id: UUID) -> Decimal:
        """Sum weighted scores of all KRA score rows for this appraisal."""
        stmt = select(AppraisalKRAScore).where(
            AppraisalKRAScore.appraisal_id == appraisal_id,
            AppraisalKRAScore.organization_id == org_id,
        )
        rows = list(self.db.scalars(stmt).all())
        weighted_scores = [
            r.weighted_score for r in rows if r.weighted_score is not None
        ]
        return self._scoring.calculate_composite(weighted_scores)

    def _compute_competency_score(self, appraisal_id: UUID, org_id: UUID) -> Decimal:
        """Compute average final_rating across all competency assessments (scaled 0-100)."""
        stmt = select(CompetencyAssessment).where(
            CompetencyAssessment.appraisal_id == appraisal_id,
            CompetencyAssessment.organization_id == org_id,
        )
        rows = list(self.db.scalars(stmt).all())
        rated = [r.final_rating for r in rows if r.final_rating is not None]
        if not rated:
            return Decimal("0.00")
        # Scale: ratings are 1-5; map to 0-100 as (rating / 5) * 100
        avg = Decimal(sum(rated)) / Decimal(len(rated))
        return (avg / Decimal("5") * Decimal("100")).quantize(
            TWO_DP, rounding=ROUND_HALF_UP
        )

    def _apply_competency_ratings(
        self,
        appraisal_id: UUID,
        org_id: UUID,
        competency_ratings: list[dict],
        *,
        development_focus_by_competency_id: dict[str, bool],
    ) -> None:
        """Upsert manager final_rating on CompetencyAssessment rows."""
        for entry in competency_ratings:
            comp_id = UUID(str(entry["competency_id"]))
            stmt = select(CompetencyAssessment).where(
                CompetencyAssessment.organization_id == org_id,
                CompetencyAssessment.appraisal_id == appraisal_id,
                CompetencyAssessment.competency_id == comp_id,
            )
            ca = self.db.scalar(stmt)
            if ca is None:
                ca = CompetencyAssessment(
                    organization_id=org_id,
                    appraisal_id=appraisal_id,
                    competency_id=comp_id,
                )
                self.db.add(ca)
            manager_raw = entry.get("manager_rating", entry.get("final_rating"))
            if manager_raw is None:
                raise OHCSFAppraisalError(
                    "Missing manager_rating/final_rating for competency rating entry"
                )
            final_raw = entry.get("final_rating", manager_raw)
            if final_raw is None:
                raise OHCSFAppraisalError(
                    "Missing final_rating for competency rating entry"
                )
            manager_rating = int(manager_raw)
            final_rating = int(final_raw)
            ca.manager_rating = manager_rating
            ca.final_rating = final_rating
            ca.is_priority = True
            ca.is_development_focus = development_focus_by_competency_id.get(
                str(comp_id), False
            )
            ca.evidence = str(entry.get("evidence") or "").strip()

    def _validate_competency_ratings_for_contract(
        self,
        contract: PerformanceContract,
        competency_ratings: list[dict] | None,
    ) -> dict[str, bool]:
        """Validate competency rating payload against selected contract competencies."""
        selected = contract.competency_ids or []
        selected_map: dict[str, bool] = {}
        for row in selected:
            selected_map[str(row.get("competency_id") or "").strip()] = bool(
                row.get("is_development_focus")
            )
        selected_map = {cid: is_focus for cid, is_focus in selected_map.items() if cid}

        if competency_ratings is None:
            required_count = self._policy.competency.required_count
            raise OHCSFAppraisalError(
                self._progress_error_message(
                    "manager review must include ratings for all "
                    f"{required_count} selected competencies with evidence"
                )
            )
        if len(competency_ratings) != len(selected_map):
            required_count = self._policy.competency.required_count
            raise OHCSFAppraisalError(
                self._progress_error_message(
                    "competency ratings must cover exactly the "
                    f"{required_count} selected contract competencies"
                )
            )

        seen: set[str] = set()
        for idx, entry in enumerate(competency_ratings, start=1):
            comp_id = str(entry.get("competency_id") or "").strip()
            if not comp_id:
                raise OHCSFAppraisalError(
                    self._progress_error_message(
                        f"competency rating {idx} is missing competency_id"
                    )
                )
            if comp_id not in selected_map:
                raise OHCSFAppraisalError(
                    self._progress_error_message(
                        "competency ratings must match selected contract competencies"
                    )
                )
            if comp_id in seen:
                raise OHCSFAppraisalError(
                    self._progress_error_message(
                        "duplicate competency rating entries are not allowed"
                    )
                )
            seen.add(comp_id)

            manager_rating_raw = entry.get("manager_rating", entry.get("final_rating"))
            if manager_rating_raw is None:
                raise OHCSFAppraisalError(
                    self._progress_error_message(
                        f"competency {idx} manager_rating is required"
                    )
                )
            manager_rating = int(manager_rating_raw)
            rating_min = self._policy.competency.rating_min
            rating_max = self._policy.competency.rating_max
            if manager_rating < rating_min or manager_rating > rating_max:
                raise OHCSFAppraisalError(
                    self._progress_error_message(
                        "competency manager_rating must be "
                        f"between {rating_min} and {rating_max}"
                    )
                )

            final_rating_raw = entry.get("final_rating")
            if final_rating_raw is not None:
                final_rating = int(final_rating_raw)
                rating_min = self._policy.competency.rating_min
                rating_max = self._policy.competency.rating_max
                if final_rating < rating_min or final_rating > rating_max:
                    raise OHCSFAppraisalError(
                        self._progress_error_message(
                            "competency final_rating must be "
                            f"between {rating_min} and {rating_max}"
                        )
                    )

            evidence = str(entry.get("evidence") or "").strip()
            if self._policy.competency.evidence_required and not evidence:
                raise OHCSFAppraisalError(
                    self._progress_error_message(
                        "competency evidence is required for each rating"
                    )
                )

        if seen != set(selected_map.keys()):
            raise OHCSFAppraisalError(
                self._progress_error_message(
                    "missing ratings for one or more selected competencies"
                )
            )
        return selected_map

    def _ensure_contract_planning_compliance(
        self, org_id: UUID, appraisal: Appraisal
    ) -> PerformanceContract:
        """Ensure appraisal has a compliant performance contract before progression."""
        contract = self.db.scalar(
            select(PerformanceContract).where(
                PerformanceContract.organization_id == org_id,
                PerformanceContract.employee_id == appraisal.employee_id,
                PerformanceContract.cycle_id == appraisal.cycle_id,
                PerformanceContract.status.in_(
                    [
                        ContractStatus.ACTIVE,
                        ContractStatus.PENDING_SIGNATURE,
                        ContractStatus.DRAFT,
                    ]
                ),
            )
        )
        if contract is None:
            raise OHCSFAppraisalError(
                self._progress_error_message(
                    "no planning contract found for employee in cycle"
                )
            )

        objectives = contract.objectives or []
        min_count = self._policy.objective.min_count
        max_count = self._policy.objective.max_count
        if len(objectives) < min_count or len(objectives) > max_count:
            raise OHCSFAppraisalError(
                self._progress_error_message(
                    f"contract must have {min_count}-{max_count} objectives"
                )
            )

        total_weight = 0
        for idx, objective in enumerate(objectives, start=1):
            objective_text = str(
                objective.get("objective")
                or objective.get("kra")
                or objective.get("title")
                or ""
            ).strip()
            if not objective_text:
                raise OHCSFAppraisalError(
                    self._progress_error_message(
                        f"objective {idx} description is missing"
                    )
                )
            if not str(objective.get("kpi") or "").strip():
                raise OHCSFAppraisalError(
                    self._progress_error_message(f"objective {idx} KPI is missing")
                )
            if not str(objective.get("target") or "").strip():
                raise OHCSFAppraisalError(
                    self._progress_error_message(f"objective {idx} target is missing")
                )
            total_weight += int(objective.get("weight", 0))

        required_total_weight = self._policy.objective.required_total_weight
        if total_weight != required_total_weight:
            raise OHCSFAppraisalError(
                self._progress_error_message(
                    "objective weights must sum to "
                    f"{required_total_weight} (got {total_weight})"
                )
            )

        competencies = contract.competency_ids or []
        required_count = self._policy.competency.required_count
        if len(competencies) != required_count:
            raise OHCSFAppraisalError(
                self._progress_error_message(
                    f"exactly {required_count} competencies are required"
                )
            )
        if not isinstance(competencies[0], dict):
            raise OHCSFAppraisalError(
                self._progress_error_message(
                    "competencies must include development focus flags"
                )
            )

        competency_ids = [
            str(c.get("competency_id") or "").strip() for c in competencies
        ]
        if any(not cid for cid in competency_ids):
            raise OHCSFAppraisalError(
                self._progress_error_message(
                    "competency_id missing in competency selection"
                )
            )
        if len(set(competency_ids)) != required_count:
            raise OHCSFAppraisalError(
                self._progress_error_message("selected competencies must be unique")
            )

        dev_focus = [c for c in competencies if c.get("is_development_focus")]
        required_focus = self._policy.competency.required_development_focus_count
        if len(dev_focus) != required_focus:
            raise OHCSFAppraisalError(
                self._progress_error_message(
                    f"exactly {required_focus} competencies must be development focus"
                )
            )
        return contract

    def _ensure_underperformance_pip_resolution(
        self, org_id: UUID, appraisal: Appraisal
    ) -> None:
        """
        Enforce PIP gate for underperformance before appraisal completion.

        Rule:
        - If final_score < 50, a PIP must exist and be resolved before COMPLETED.
        - Resolved statuses: IMPROVED, ESCALATED, CLOSED.
        """
        if appraisal.final_score is None:
            return

        final_score = Decimal(str(appraisal.final_score)).quantize(
            TWO_DP, rounding=ROUND_HALF_UP
        )
        if final_score <= Decimal("5"):
            final_score = (final_score * Decimal("20")).quantize(
                TWO_DP, rounding=ROUND_HALF_UP
            )
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
            raise OHCSFAppraisalError(
                "Cannot complete appraisal: underperformance detected (score below 50). "
                "A PIP has been created and must be resolved first."
            )

        if pip.status not in {
            PIPStatus.IMPROVED,
            PIPStatus.ESCALATED,
            PIPStatus.CLOSED,
        }:
            raise OHCSFAppraisalError(
                "Cannot complete appraisal: linked PIP is not resolved "
                f"(current status: {pip.status.value})."
            )

    def _trigger_proactive_underperformance_pip(
        self, org_id: UUID, appraisal: Appraisal
    ) -> None:
        """Create PIP early when score already indicates underperformance."""
        if appraisal.final_score is None:
            return
        try:
            from app.services.people.perf.underperformance_service import (
                UnderperformanceService,
            )

            UnderperformanceService(self.db).ensure_pip_for_underperformance(
                org_id,
                appraisal_id=appraisal.appraisal_id,
                employee_id=appraisal.employee_id,
                final_score=Decimal(str(appraisal.final_score)),
                trigger_type="score_below_50",
            )
        except Exception:
            logger.exception(
                "Proactive PIP trigger failed for appraisal %s", appraisal.appraisal_id
            )

    # ------------------------------------------------------------------
    # Workflow actions
    # ------------------------------------------------------------------

    def submit_self_assessment_ohcsf(
        self,
        org_id: UUID,
        appraisal_id: UUID,
        *,
        self_overall_rating: int,
        self_summary: str,
        achievements: str | None = None,
        challenges: str | None = None,
        development_needs: str | None = None,
        kra_ratings: list[dict] | None = None,
    ) -> Appraisal:
        """Employee submits self-assessment; transitions to PENDING_REVIEW.

        Valid starting statuses: DRAFT or SELF_ASSESSMENT.
        Enforces cascade-up rule: all direct-report appraisals must be complete.
        """
        self._ensure_pms_write_mode(org_id)
        appraisal = self._get_or_404(org_id, appraisal_id)

        # Both DRAFT and SELF_ASSESSMENT can move to PENDING_REVIEW
        if appraisal.status not in (
            AppraisalStatus.DRAFT,
            AppraisalStatus.SELF_ASSESSMENT,
        ):
            raise OHCSFAppraisalStatusError(
                appraisal.status, AppraisalStatus.PENDING_REVIEW
            )
        self._enforce_phase_deadline(
            appraisal,
            deadline_field="self_assessment_deadline",
            phase_label="self-assessment",
        )

        self._check_cascade_up(org_id, appraisal.cycle_id, appraisal.employee_id)

        appraisal.self_assessment_date = date.today()
        appraisal.self_overall_rating = self_overall_rating
        appraisal.self_summary = self_summary
        if achievements is not None:
            appraisal.achievements = achievements
        if challenges is not None:
            appraisal.challenges = challenges
        if development_needs is not None:
            appraisal.development_needs = development_needs

        if kra_ratings:
            self._apply_kra_ratings(
                appraisal_id,
                org_id,
                kra_ratings,
                rating_field="self_rating",
                comments_field="self_comments",
            )

        appraisal.status = AppraisalStatus.PENDING_REVIEW
        self.db.flush()
        logger.info(
            "OHCSF self-assessment submitted: appraisal=%s employee=%s",
            appraisal_id,
            appraisal.employee_id,
        )
        return appraisal

    def submit_manager_review_ohcsf(
        self,
        org_id: UUID,
        appraisal_id: UUID,
        *,
        manager_overall_rating: int,
        manager_summary: str,
        manager_recommendations: str | None = None,
        kra_ratings: list[dict] | None = None,
        competency_ratings: list[dict] | None = None,
        process_rating: int | None = None,
    ) -> Appraisal:
        """Manager submits review; calculates composite scores; transitions to PENDING_COUNTERSIGN."""
        self._ensure_pms_write_mode(org_id)
        appraisal = self._get_or_404(org_id, appraisal_id)
        self._validate_transition(appraisal.status, AppraisalStatus.PENDING_COUNTERSIGN)
        self._enforce_phase_deadline(
            appraisal,
            deadline_field="manager_review_deadline",
            phase_label="manager review",
        )
        contract = self._ensure_contract_planning_compliance(org_id, appraisal)
        development_focus_by_competency_id = (
            self._validate_competency_ratings_for_contract(contract, competency_ratings)
        )

        appraisal.manager_review_date = date.today()
        appraisal.manager_overall_rating = manager_overall_rating
        appraisal.manager_summary = manager_summary
        if manager_recommendations is not None:
            appraisal.manager_recommendations = manager_recommendations

        if kra_ratings:
            self._apply_kra_ratings(
                appraisal_id,
                org_id,
                kra_ratings,
                rating_field="manager_rating",
                comments_field="manager_comments",
            )

        if competency_ratings:
            self._apply_competency_ratings(
                appraisal_id,
                org_id,
                competency_ratings,
                development_focus_by_competency_id=development_focus_by_competency_id,
            )

        # Process scoring (10% bucket) — scale 1-5 rating to 0-100
        if process_rating is not None:
            appraisal.process_manager_rating = process_rating
            appraisal.process_final_rating = process_rating

        # Flush so KRA/competency rows are visible for scoring
        self.db.flush()

        # Calculate composite scores
        objective_score = self._compute_objective_score(appraisal_id, org_id)
        competency_score = self._compute_competency_score(appraisal_id, org_id)
        process_score = (
            (
                Decimal(str(appraisal.process_final_rating))
                / Decimal("5")
                * Decimal("100")
            ).quantize(TWO_DP, rounding=ROUND_HALF_UP)
            if appraisal.process_final_rating
            else Decimal("0.00")
        )

        appraisal.objective_weighted_score = objective_score
        appraisal.competency_weighted_score = competency_score
        appraisal.process_weighted_score = process_score

        final = self._scoring.calculate_appraisal_final(
            objective_score, competency_score, process_score
        )
        appraisal.final_score = final
        rating_int, label = self._scoring.score_to_rating(final)
        appraisal.final_rating = rating_int
        appraisal.rating_label = label

        self._trigger_proactive_underperformance_pip(org_id, appraisal)
        appraisal.status = AppraisalStatus.PENDING_COUNTERSIGN
        self.db.flush()
        logger.info(
            "OHCSF manager review submitted: appraisal=%s final_score=%s rating=%s",
            appraisal_id,
            final,
            label,
        )
        return appraisal

    def submit_countersign(
        self,
        org_id: UUID,
        appraisal_id: UUID,
        *,
        counter_signer_id: UUID,
        comments: str | None = None,
    ) -> Appraisal:
        """Countersigner endorses the appraisal; transitions to COUNTERSIGNED."""
        self._ensure_pms_write_mode(org_id)
        appraisal = self._get_or_404(org_id, appraisal_id)
        self._validate_transition(appraisal.status, AppraisalStatus.COUNTERSIGNED)

        appraisal.counter_signer_id = counter_signer_id
        appraisal.counter_signer_date = date.today()
        if comments is not None:
            appraisal.counter_signer_comments = comments

        appraisal.status = AppraisalStatus.COUNTERSIGNED
        self.db.flush()
        logger.info(
            "OHCSF countersigned: appraisal=%s counter_signer=%s",
            appraisal_id,
            counter_signer_id,
        )
        return appraisal

    def submit_committee_review(
        self,
        org_id: UUID,
        appraisal_id: UUID,
        *,
        decision: CommitteeDecision,
        notes: str | None = None,
        adjusted_rating: int | None = None,
    ) -> Appraisal:
        """Committee reviews countersigned appraisal; transitions to COMPLETED."""
        self._ensure_pms_write_mode(org_id)
        appraisal = self._get_or_404(org_id, appraisal_id)
        self._enforce_phase_deadline(
            appraisal,
            deadline_field="calibration_deadline",
            phase_label="committee review",
        )

        # Accept from COUNTERSIGNED (auto-advance) or PENDING_COMMITTEE
        if appraisal.status == AppraisalStatus.COUNTERSIGNED:
            # Validate COUNTERSIGNED → PENDING_COMMITTEE first
            self._validate_transition(
                appraisal.status, AppraisalStatus.PENDING_COMMITTEE
            )
        elif appraisal.status == AppraisalStatus.PENDING_COMMITTEE:
            self._validate_transition(appraisal.status, AppraisalStatus.COMPLETED)
        else:
            # Any other status is invalid — raise via the PENDING_COMMITTEE path
            # so the error reflects the actual current status
            self._validate_transition(appraisal.status, AppraisalStatus.COMPLETED)

        appraisal.committee_review_date = date.today()
        appraisal.committee_decision = decision.value
        if notes is not None:
            appraisal.committee_notes = notes

        adjusted_required = (
            decision.value in self._policy.committee_decisions_requiring_adjusted_rating
        )
        if adjusted_required and adjusted_rating is None:
            raise OHCSFAppraisalError(
                "Adjusted rating is required when committee decision is ADJUSTED"
            )

        if adjusted_required and adjusted_rating is not None:
            appraisal.final_rating = adjusted_rating
            _, label = self._scoring.score_to_rating(
                Decimal(str(adjusted_rating)) / Decimal("5") * Decimal("100")
            )
            appraisal.rating_label = label

        self._ensure_underperformance_pip_resolution(org_id, appraisal)
        appraisal.status = AppraisalStatus.COMPLETED
        appraisal.completed_on = date.today()
        self.db.flush()
        logger.info(
            "OHCSF committee review completed: appraisal=%s decision=%s",
            appraisal_id,
            decision.value,
        )
        return appraisal

    # ------------------------------------------------------------------
    # Quarterly appraisal creation
    # ------------------------------------------------------------------

    def create_quarterly_appraisals(
        self, org_id: UUID, cycle_id: UUID, quarter: int
    ) -> list[Appraisal]:
        """Create quarterly Appraisal records for all employees with active contracts.

        Finds the quarterly sub-cycle matching the given parent cycle and quarter,
        then creates one Appraisal per employee with an ACTIVE PerformanceContract
        for that cycle.

        Returns:
            List of newly created Appraisal records.
        """
        self._ensure_pms_write_mode(org_id)
        from app.models.people.perf.performance_contract import PerformanceContract
        from app.models.people.perf.pms_enums import ContractStatus

        # Find the quarterly sub-cycle
        sub_cycle = self.db.scalar(
            select(AppraisalCycle).where(
                AppraisalCycle.organization_id == org_id,
                AppraisalCycle.parent_cycle_id == cycle_id,
                AppraisalCycle.quarter == quarter,
            )
        )
        if sub_cycle is None:
            logger.warning(
                "No quarterly sub-cycle found for cycle=%s quarter=%s",
                cycle_id,
                quarter,
            )
            return []

        # Find employees with ACTIVE contracts for the parent cycle
        active_contracts = list(
            self.db.scalars(
                select(PerformanceContract).where(
                    PerformanceContract.organization_id == org_id,
                    PerformanceContract.cycle_id == cycle_id,
                    PerformanceContract.status == ContractStatus.ACTIVE,
                )
            ).all()
        )

        created: list[Appraisal] = []
        for contract in active_contracts:
            # Check if appraisal already exists for this sub-cycle
            existing = self.db.scalar(
                select(Appraisal).where(
                    Appraisal.organization_id == org_id,
                    Appraisal.cycle_id == sub_cycle.cycle_id,
                    Appraisal.employee_id == contract.employee_id,
                )
            )
            if existing is not None:
                continue  # Skip duplicates

            appraisal = Appraisal(
                organization_id=org_id,
                employee_id=contract.employee_id,
                manager_id=contract.supervisor_id,
                cycle_id=sub_cycle.cycle_id,
                status=AppraisalStatus.DRAFT,
                is_quarterly=True,
            )
            self.db.add(appraisal)
            created.append(appraisal)

        self.db.flush()
        logger.info(
            "Created %d quarterly appraisals: cycle=%s quarter=%s",
            len(created),
            cycle_id,
            quarter,
        )
        return created

    # ------------------------------------------------------------------
    # Annual rating calculation
    # ------------------------------------------------------------------

    def calculate_annual_rating(
        self, org_id: UUID, cycle_id: UUID, employee_id: UUID
    ) -> dict:
        """Average quarterly ratings to produce an annual score.

        Fetches all quarterly Appraisal records for the employee in sub-cycles
        of the given annual cycle, averages the quarterly_rating values, and
        returns a structured result dict.

        Returns:
            {
                "employee_id": UUID,
                "quarterly_scores": [{"quarter": int, "score": Decimal}, ...],
                "annual_score": Decimal,
                "rating": int,
                "label": str,
            }
        """
        # Find quarterly sub-cycles under this annual cycle
        sub_cycle_ids_stmt = select(AppraisalCycle.cycle_id).where(
            AppraisalCycle.organization_id == org_id,
            AppraisalCycle.parent_cycle_id == cycle_id,
        )
        sub_cycle_id_list = list(self.db.scalars(sub_cycle_ids_stmt).all())

        if not sub_cycle_id_list:
            return {
                "employee_id": employee_id,
                "quarterly_scores": [],
                "annual_score": Decimal("0.00"),
                "rating": 1,
                "label": "Poor",
                "has_data": False,
            }

        appraisals = list(
            self.db.scalars(
                select(Appraisal).where(
                    Appraisal.organization_id == org_id,
                    Appraisal.cycle_id.in_(sub_cycle_id_list),
                    Appraisal.employee_id == employee_id,
                    Appraisal.is_quarterly.is_(True),
                    Appraisal.status == AppraisalStatus.COMPLETED,
                )
            ).all()
        )

        quarterly_scores: list[dict] = []
        for ap in appraisals:
            score = (
                ap.quarterly_rating
                if ap.quarterly_rating is not None
                else ap.final_score
            )
            if score is not None:
                quarterly_scores.append(
                    {
                        "appraisal_id": ap.appraisal_id,
                        "cycle_id": ap.cycle_id,
                        "score": score,
                    }
                )

        if not quarterly_scores:
            return {
                "employee_id": employee_id,
                "quarterly_scores": [],
                "annual_score": Decimal("0.00"),
                "rating": 1,
                "label": "Poor",
                "has_data": False,
            }

        total = sum(entry["score"] for entry in quarterly_scores)
        annual_score = (total / Decimal(str(len(quarterly_scores)))).quantize(
            TWO_DP, rounding=ROUND_HALF_UP
        )
        rating_int, label = self._scoring.score_to_rating(annual_score)

        return {
            "employee_id": employee_id,
            "quarterly_scores": quarterly_scores,
            "annual_score": annual_score,
            "rating": rating_int,
            "label": label,
            "has_data": True,
        }
