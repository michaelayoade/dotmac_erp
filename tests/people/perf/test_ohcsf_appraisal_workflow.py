"""
Tests for OHCSFAppraisalService — OHCSF workflow and business logic.

Uses MagicMock for the DB session to test pure logic without a database.
Integration tests (actual DB queries) would be separate.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.models.people.perf.appraisal import (
    Appraisal,
    AppraisalStatus,
)
from app.models.people.perf.pms_enums import CommitteeDecision
from app.models.people.perf.pms_enums import PIPStatus
from app.services.people.perf.ohcsf_appraisal_service import (
    OHCSF_STATUS_TRANSITIONS,
    CascadeUpViolation,
    OHCSFAppraisalError,
    OHCSFAppraisalNotFoundError,
    OHCSFAppraisalService,
    OHCSFAppraisalStatusError,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ORG_ID = uuid.uuid4()
CYCLE_ID = uuid.uuid4()
EMP_ID = uuid.uuid4()
MGR_ID = uuid.uuid4()


def make_appraisal(
    status: AppraisalStatus = AppraisalStatus.DRAFT,
    *,
    appraisal_id: uuid.UUID | None = None,
    employee_id: uuid.UUID | None = None,
    cycle_id: uuid.UUID | None = None,
) -> Appraisal:
    """Build a minimal Appraisal-like object with plain attribute access."""
    ap = MagicMock(spec=Appraisal)
    ap.appraisal_id = appraisal_id or uuid.uuid4()
    ap.organization_id = ORG_ID
    ap.employee_id = employee_id or EMP_ID
    ap.manager_id = MGR_ID
    ap.cycle_id = cycle_id or CYCLE_ID
    ap.status = status
    ap.is_quarterly = False
    ap.quarterly_rating = None
    ap.final_score = None
    ap.final_rating = None
    ap.rating_label = None
    ap.process_final_rating = None
    ap.process_manager_rating = None
    ap.objective_weighted_score = None
    ap.competency_weighted_score = None
    ap.process_weighted_score = None
    ap.cycle = None
    return ap


def make_service() -> OHCSFAppraisalService:
    """Create service with a minimal stub DB."""
    db = MagicMock()
    db.scalars.return_value.all.return_value = []
    db.scalar.return_value = None
    db.get.return_value = None
    return OHCSFAppraisalService(db)


# ---------------------------------------------------------------------------
# Error hierarchy
# ---------------------------------------------------------------------------


class TestErrorHierarchy:
    def test_not_found_is_base_error(self) -> None:
        err = OHCSFAppraisalNotFoundError(uuid.uuid4())
        assert isinstance(err, OHCSFAppraisalError)

    def test_status_error_is_base_error(self) -> None:
        err = OHCSFAppraisalStatusError(
            AppraisalStatus.DRAFT, AppraisalStatus.COMPLETED
        )
        assert isinstance(err, OHCSFAppraisalError)

    def test_cascade_up_is_base_error(self) -> None:
        err = CascadeUpViolation(3)
        assert isinstance(err, OHCSFAppraisalError)

    def test_status_error_stores_statuses(self) -> None:
        err = OHCSFAppraisalStatusError(
            AppraisalStatus.DRAFT, AppraisalStatus.COMPLETED
        )
        assert err.current == AppraisalStatus.DRAFT
        assert err.target == AppraisalStatus.COMPLETED

    def test_cascade_up_stores_count(self) -> None:
        err = CascadeUpViolation(5)
        assert err.incomplete_count == 5


# ---------------------------------------------------------------------------
# Status transitions — structure
# ---------------------------------------------------------------------------


class TestStatusTransitions:
    """Verify the OHCSF_STATUS_TRANSITIONS map is correctly structured."""

    def test_all_appraisal_statuses_are_keys(self) -> None:
        """Every AppraisalStatus used in OHCSF workflow must appear as a key."""
        expected = {
            AppraisalStatus.DRAFT,
            AppraisalStatus.SELF_ASSESSMENT,
            AppraisalStatus.PENDING_REVIEW,
            AppraisalStatus.UNDER_REVIEW,
            AppraisalStatus.PENDING_COUNTERSIGN,
            AppraisalStatus.COUNTERSIGNED,
            AppraisalStatus.PENDING_COMMITTEE,
            AppraisalStatus.COMPLETED,
            AppraisalStatus.CANCELLED,
        }
        assert expected.issubset(set(OHCSF_STATUS_TRANSITIONS.keys()))

    def test_draft_can_move_to_self_assessment(self) -> None:
        assert (
            AppraisalStatus.SELF_ASSESSMENT
            in OHCSF_STATUS_TRANSITIONS[AppraisalStatus.DRAFT]
        )

    def test_draft_can_be_cancelled(self) -> None:
        assert (
            AppraisalStatus.CANCELLED in OHCSF_STATUS_TRANSITIONS[AppraisalStatus.DRAFT]
        )

    def test_self_assessment_to_pending_review(self) -> None:
        assert (
            AppraisalStatus.PENDING_REVIEW
            in OHCSF_STATUS_TRANSITIONS[AppraisalStatus.SELF_ASSESSMENT]
        )

    def test_self_assessment_can_return_to_draft(self) -> None:
        assert (
            AppraisalStatus.DRAFT
            in OHCSF_STATUS_TRANSITIONS[AppraisalStatus.SELF_ASSESSMENT]
        )

    def test_pending_review_to_under_review(self) -> None:
        assert (
            AppraisalStatus.UNDER_REVIEW
            in OHCSF_STATUS_TRANSITIONS[AppraisalStatus.PENDING_REVIEW]
        )

    def test_under_review_to_pending_countersign(self) -> None:
        assert (
            AppraisalStatus.PENDING_COUNTERSIGN
            in OHCSF_STATUS_TRANSITIONS[AppraisalStatus.UNDER_REVIEW]
        )

    def test_under_review_can_return_to_self_assessment(self) -> None:
        assert (
            AppraisalStatus.SELF_ASSESSMENT
            in OHCSF_STATUS_TRANSITIONS[AppraisalStatus.UNDER_REVIEW]
        )

    def test_completed_has_no_transitions(self) -> None:
        assert OHCSF_STATUS_TRANSITIONS[AppraisalStatus.COMPLETED] == set()

    def test_cancelled_has_no_transitions(self) -> None:
        assert OHCSF_STATUS_TRANSITIONS[AppraisalStatus.CANCELLED] == set()

    def test_full_happy_path_chain(self) -> None:
        """Verify every step in the happy-path chain is valid."""
        chain = [
            (AppraisalStatus.DRAFT, AppraisalStatus.SELF_ASSESSMENT),
            (AppraisalStatus.SELF_ASSESSMENT, AppraisalStatus.PENDING_REVIEW),
            (AppraisalStatus.PENDING_REVIEW, AppraisalStatus.UNDER_REVIEW),
            (AppraisalStatus.UNDER_REVIEW, AppraisalStatus.PENDING_COUNTERSIGN),
            (AppraisalStatus.PENDING_COUNTERSIGN, AppraisalStatus.COUNTERSIGNED),
            (AppraisalStatus.COUNTERSIGNED, AppraisalStatus.PENDING_COMMITTEE),
            (AppraisalStatus.PENDING_COMMITTEE, AppraisalStatus.COMPLETED),
        ]
        for current, target in chain:
            assert target in OHCSF_STATUS_TRANSITIONS[current], (
                f"{current} → {target} should be valid"
            )


# ---------------------------------------------------------------------------
# _validate_transition
# ---------------------------------------------------------------------------


class TestValidateTransition:
    def test_valid_transition_does_not_raise(self) -> None:
        svc = make_service()
        svc._validate_transition(AppraisalStatus.DRAFT, AppraisalStatus.SELF_ASSESSMENT)

    def test_invalid_transition_raises(self) -> None:
        svc = make_service()
        with pytest.raises(OHCSFAppraisalStatusError) as exc_info:
            svc._validate_transition(AppraisalStatus.COMPLETED, AppraisalStatus.DRAFT)
        assert exc_info.value.current == AppraisalStatus.COMPLETED
        assert exc_info.value.target == AppraisalStatus.DRAFT

    def test_completed_to_anything_raises(self) -> None:
        svc = make_service()
        for target in AppraisalStatus:
            if target != AppraisalStatus.COMPLETED:
                with pytest.raises(OHCSFAppraisalStatusError):
                    svc._validate_transition(AppraisalStatus.COMPLETED, target)

    def test_cancelled_to_anything_raises(self) -> None:
        svc = make_service()
        for target in AppraisalStatus:
            if target != AppraisalStatus.CANCELLED:
                with pytest.raises(OHCSFAppraisalStatusError):
                    svc._validate_transition(AppraisalStatus.CANCELLED, target)


# ---------------------------------------------------------------------------
# Cascade-up rule
# ---------------------------------------------------------------------------


class TestCascadeUpRule:
    def test_no_direct_reports_passes(self) -> None:
        """When an employee has no direct reports, cascade-up is satisfied."""
        svc = make_service()
        # scalars for direct_report_ids query returns []
        svc.db.scalars.return_value.all.return_value = []
        # Should not raise
        svc._check_cascade_up(ORG_ID, CYCLE_ID, EMP_ID)

    def test_all_subordinates_complete_passes(self) -> None:
        """When all direct reports are completed, cascade-up is satisfied."""
        sub_emp_id = uuid.uuid4()
        svc = make_service()
        # First scalars call returns direct report IDs
        svc.db.scalars.return_value.all.return_value = [sub_emp_id]
        # scalar for count query returns 0 (no incomplete)
        svc.db.scalar.return_value = 0
        svc._check_cascade_up(ORG_ID, CYCLE_ID, EMP_ID)

    def test_blocks_when_subordinates_incomplete(self) -> None:
        """When some direct reports are still in progress, raise CascadeUpViolation."""
        sub_emp_id = uuid.uuid4()
        svc = make_service()
        svc.db.scalars.return_value.all.return_value = [sub_emp_id]
        # 2 subordinates still incomplete
        svc.db.scalar.return_value = 2
        with pytest.raises(CascadeUpViolation) as exc_info:
            svc._check_cascade_up(ORG_ID, CYCLE_ID, EMP_ID)
        assert exc_info.value.incomplete_count == 2


# ---------------------------------------------------------------------------
# _get_or_404
# ---------------------------------------------------------------------------


class TestGetOr404:
    def test_not_found_raises(self) -> None:
        svc = make_service()
        svc.db.get.return_value = None
        with pytest.raises(OHCSFAppraisalNotFoundError):
            svc._get_or_404(ORG_ID, uuid.uuid4())

    def test_wrong_org_raises(self) -> None:
        svc = make_service()
        ap = make_appraisal()
        ap.organization_id = uuid.uuid4()  # different org
        svc.db.get.return_value = ap
        with pytest.raises(OHCSFAppraisalNotFoundError):
            svc._get_or_404(ORG_ID, ap.appraisal_id)

    def test_returns_appraisal_when_found(self) -> None:
        svc = make_service()
        ap = make_appraisal()
        svc.db.get.return_value = ap
        result = svc._get_or_404(ORG_ID, ap.appraisal_id)
        assert result is ap


# ---------------------------------------------------------------------------
# submit_self_assessment_ohcsf
# ---------------------------------------------------------------------------


class TestSubmitSelfAssessment:
    def _setup(self, status: AppraisalStatus = AppraisalStatus.DRAFT) -> tuple:
        svc = make_service()
        ap = make_appraisal(status)
        svc.db.get.return_value = ap
        # cascade-up: no direct reports
        svc.db.scalars.return_value.all.return_value = []
        svc.db.scalar.return_value = 0
        return svc, ap

    def test_from_draft_transitions_to_pending_review(self) -> None:
        svc, ap = self._setup(AppraisalStatus.DRAFT)
        svc.submit_self_assessment_ohcsf(
            ORG_ID,
            ap.appraisal_id,
            self_overall_rating=4,
            self_summary="Good year",
        )
        assert ap.status == AppraisalStatus.PENDING_REVIEW

    def test_from_self_assessment_transitions_to_pending_review(self) -> None:
        svc, ap = self._setup(AppraisalStatus.SELF_ASSESSMENT)
        svc.submit_self_assessment_ohcsf(
            ORG_ID,
            ap.appraisal_id,
            self_overall_rating=3,
            self_summary="Decent",
        )
        assert ap.status == AppraisalStatus.PENDING_REVIEW

    def test_sets_self_assessment_date(self) -> None:
        svc, ap = self._setup()
        svc.submit_self_assessment_ohcsf(
            ORG_ID, ap.appraisal_id, self_overall_rating=3, self_summary="OK"
        )
        assert ap.self_assessment_date == date.today()

    def test_sets_optional_fields(self) -> None:
        svc, ap = self._setup()
        svc.submit_self_assessment_ohcsf(
            ORG_ID,
            ap.appraisal_id,
            self_overall_rating=4,
            self_summary="Summary",
            achievements="Launched project",
            challenges="Budget constraints",
            development_needs="Leadership training",
        )
        assert ap.achievements == "Launched project"
        assert ap.challenges == "Budget constraints"
        assert ap.development_needs == "Leadership training"

    def test_invalid_status_raises(self) -> None:
        svc, ap = self._setup(AppraisalStatus.COMPLETED)
        with pytest.raises(OHCSFAppraisalStatusError):
            svc.submit_self_assessment_ohcsf(
                ORG_ID, ap.appraisal_id, self_overall_rating=3, self_summary="Late"
            )

    def test_cascade_up_violation_raises(self) -> None:
        svc, ap = self._setup(AppraisalStatus.DRAFT)
        # Override: direct reports exist and are incomplete
        svc.db.scalars.return_value.all.return_value = [uuid.uuid4()]
        svc.db.scalar.return_value = 1
        with pytest.raises(CascadeUpViolation):
            svc.submit_self_assessment_ohcsf(
                ORG_ID, ap.appraisal_id, self_overall_rating=3, self_summary="Blocked"
            )

    def test_calls_flush(self) -> None:
        svc, ap = self._setup()
        svc.submit_self_assessment_ohcsf(
            ORG_ID, ap.appraisal_id, self_overall_rating=3, self_summary="OK"
        )
        svc.db.flush.assert_called()

    def test_rejects_submission_after_self_assessment_deadline(self) -> None:
        svc, ap = self._setup()
        ap.cycle = MagicMock()
        ap.cycle.self_assessment_deadline = date.today() - timedelta(days=1)

        with pytest.raises(OHCSFAppraisalError, match="self-assessment"):
            svc.submit_self_assessment_ohcsf(
                ORG_ID,
                ap.appraisal_id,
                self_overall_rating=3,
                self_summary="Late submission",
            )


# ---------------------------------------------------------------------------
# submit_manager_review_ohcsf
# ---------------------------------------------------------------------------


class TestSubmitManagerReview:
    @staticmethod
    def _build_competency_ratings(contract: object) -> list[dict]:
        rows = getattr(contract, "competency_ids", [])
        return [
            {
                "competency_id": row["competency_id"],
                "manager_rating": 4,
                "evidence": "Observed behavior and delivery outcomes.",
            }
            for row in rows
        ]

    def _setup(self, status: AppraisalStatus = AppraisalStatus.UNDER_REVIEW) -> tuple:
        from types import SimpleNamespace

        svc = make_service()
        ap = make_appraisal(status)
        svc.db.get.return_value = ap
        svc.db.scalars.return_value.all.return_value = []
        contract = SimpleNamespace(
            objectives=[
                {"objective": "Obj A", "kpi": "KPI A", "target": "Target A", "weight": 20},
                {"objective": "Obj B", "kpi": "KPI B", "target": "Target B", "weight": 20},
                {"objective": "Obj C", "kpi": "KPI C", "target": "Target C", "weight": 30},
            ],
            competency_ids=[
                {"competency_id": str(uuid.uuid4()), "is_development_focus": True},
                {"competency_id": str(uuid.uuid4()), "is_development_focus": True},
                {"competency_id": str(uuid.uuid4()), "is_development_focus": True},
                {"competency_id": str(uuid.uuid4()), "is_development_focus": False},
                {"competency_id": str(uuid.uuid4()), "is_development_focus": False},
            ],
        )
        svc.db.scalar.side_effect = [contract, None, None, None, None, None]
        return svc, ap, contract

    def test_transitions_to_pending_countersign(self) -> None:
        svc, ap, contract = self._setup()
        svc.submit_manager_review_ohcsf(
            ORG_ID,
            ap.appraisal_id,
            manager_overall_rating=4,
            manager_summary="Great performance",
            competency_ratings=self._build_competency_ratings(contract),
        )
        assert ap.status == AppraisalStatus.PENDING_COUNTERSIGN

    def test_sets_manager_review_date(self) -> None:
        svc, ap, contract = self._setup()
        svc.submit_manager_review_ohcsf(
            ORG_ID,
            ap.appraisal_id,
            manager_overall_rating=3,
            manager_summary="OK",
            competency_ratings=self._build_competency_ratings(contract),
        )
        assert ap.manager_review_date == date.today()

    def test_sets_final_score_and_rating(self) -> None:
        svc, ap, contract = self._setup()
        svc.submit_manager_review_ohcsf(
            ORG_ID,
            ap.appraisal_id,
            manager_overall_rating=4,
            manager_summary="Good",
            competency_ratings=self._build_competency_ratings(contract),
        )
        # With no KRAs or competencies, all zeros → final_score should be set
        assert ap.final_score is not None
        assert ap.final_rating is not None
        assert ap.rating_label is not None

    def test_invalid_status_raises(self) -> None:
        svc, ap, _contract = self._setup(AppraisalStatus.DRAFT)
        with pytest.raises(OHCSFAppraisalStatusError):
            svc.submit_manager_review_ohcsf(
                ORG_ID,
                ap.appraisal_id,
                manager_overall_rating=3,
                manager_summary="Bad flow",
            )

    def test_sets_process_rating(self) -> None:
        svc, ap, contract = self._setup()
        svc.submit_manager_review_ohcsf(
            ORG_ID,
            ap.appraisal_id,
            manager_overall_rating=4,
            manager_summary="OK",
            process_rating=4,
            competency_ratings=self._build_competency_ratings(contract),
        )
        assert ap.process_manager_rating == 4
        assert ap.process_final_rating == 4

    def test_calls_flush(self) -> None:
        svc, ap, contract = self._setup()
        svc.submit_manager_review_ohcsf(
            ORG_ID,
            ap.appraisal_id,
            manager_overall_rating=3,
            manager_summary="OK",
            competency_ratings=self._build_competency_ratings(contract),
        )
        svc.db.flush.assert_called()

    def test_triggers_proactive_pip_check_after_scoring(self) -> None:
        from unittest.mock import patch

        svc, ap, contract = self._setup()
        with patch(
            "app.services.people.perf.underperformance_service.UnderperformanceService.ensure_pip_for_underperformance"
        ) as proactive:
            proactive.return_value = None
            svc.submit_manager_review_ohcsf(
                ORG_ID,
                ap.appraisal_id,
                manager_overall_rating=3,
                manager_summary="Proactive check",
                competency_ratings=self._build_competency_ratings(contract),
            )
        proactive.assert_called_once()

    def test_blocks_when_contract_planning_missing(self) -> None:
        svc, ap, _contract = self._setup()
        svc.db.scalar.side_effect = [None]
        with pytest.raises(OHCSFAppraisalError, match="planning contract"):
            svc.submit_manager_review_ohcsf(
                ORG_ID,
                ap.appraisal_id,
                manager_overall_rating=3,
                manager_summary="Blocked",
            )

    def test_requires_competency_evidence_for_each_rating(self) -> None:
        svc, ap, contract = self._setup()
        ratings = self._build_competency_ratings(contract)
        ratings[0]["evidence"] = ""

        with pytest.raises(OHCSFAppraisalError, match="evidence is required"):
            svc.submit_manager_review_ohcsf(
                ORG_ID,
                ap.appraisal_id,
                manager_overall_rating=3,
                manager_summary="Missing evidence",
                competency_ratings=ratings,
            )

    def test_rejects_competency_not_in_contract_selection(self) -> None:
        svc, ap, contract = self._setup()
        ratings = self._build_competency_ratings(contract)
        ratings[0]["competency_id"] = str(uuid.uuid4())

        with pytest.raises(OHCSFAppraisalError, match="must match selected"):
            svc.submit_manager_review_ohcsf(
                ORG_ID,
                ap.appraisal_id,
                manager_overall_rating=3,
                manager_summary="Unexpected competency",
                competency_ratings=ratings,
            )

    def test_rejects_submission_after_manager_review_deadline(self) -> None:
        svc, ap, contract = self._setup()
        ap.cycle = MagicMock()
        ap.cycle.manager_review_deadline = date.today() - timedelta(days=1)

        with pytest.raises(OHCSFAppraisalError, match="manager review"):
            svc.submit_manager_review_ohcsf(
                ORG_ID,
                ap.appraisal_id,
                manager_overall_rating=3,
                manager_summary="Late manager review",
                competency_ratings=self._build_competency_ratings(contract),
            )


# ---------------------------------------------------------------------------
# submit_countersign
# ---------------------------------------------------------------------------


class TestSubmitCountersign:
    def _setup(
        self, status: AppraisalStatus = AppraisalStatus.PENDING_COUNTERSIGN
    ) -> tuple:
        svc = make_service()
        ap = make_appraisal(status)
        svc.db.get.return_value = ap
        return svc, ap

    def test_transitions_to_countersigned(self) -> None:
        svc, ap = self._setup()
        svc.submit_countersign(ORG_ID, ap.appraisal_id, counter_signer_id=uuid.uuid4())
        assert ap.status == AppraisalStatus.COUNTERSIGNED

    def test_sets_counter_signer_date(self) -> None:
        svc, ap = self._setup()
        svc.submit_countersign(ORG_ID, ap.appraisal_id, counter_signer_id=uuid.uuid4())
        assert ap.counter_signer_date == date.today()

    def test_sets_counter_signer_id(self) -> None:
        svc, ap = self._setup()
        signer_id = uuid.uuid4()
        svc.submit_countersign(ORG_ID, ap.appraisal_id, counter_signer_id=signer_id)
        assert ap.counter_signer_id == signer_id

    def test_sets_comments_when_provided(self) -> None:
        svc, ap = self._setup()
        svc.submit_countersign(
            ORG_ID,
            ap.appraisal_id,
            counter_signer_id=uuid.uuid4(),
            comments="Looks good",
        )
        assert ap.counter_signer_comments == "Looks good"

    def test_invalid_status_raises(self) -> None:
        svc, ap = self._setup(AppraisalStatus.DRAFT)
        with pytest.raises(OHCSFAppraisalStatusError):
            svc.submit_countersign(
                ORG_ID, ap.appraisal_id, counter_signer_id=uuid.uuid4()
            )

    def test_calls_flush(self) -> None:
        svc, ap = self._setup()
        svc.submit_countersign(ORG_ID, ap.appraisal_id, counter_signer_id=uuid.uuid4())
        svc.db.flush.assert_called()


# ---------------------------------------------------------------------------
# submit_committee_review
# ---------------------------------------------------------------------------


class TestSubmitCommitteeReview:
    def _setup(
        self, status: AppraisalStatus = AppraisalStatus.PENDING_COMMITTEE
    ) -> tuple:
        svc = make_service()
        ap = make_appraisal(status)
        ap.final_rating = 4
        svc.db.get.return_value = ap
        return svc, ap

    def test_transitions_to_completed(self) -> None:
        svc, ap = self._setup()
        svc.submit_committee_review(
            ORG_ID, ap.appraisal_id, decision=CommitteeDecision.ENDORSED
        )
        assert ap.status == AppraisalStatus.COMPLETED

    def test_sets_committee_review_date(self) -> None:
        svc, ap = self._setup()
        svc.submit_committee_review(
            ORG_ID, ap.appraisal_id, decision=CommitteeDecision.ENDORSED
        )
        assert ap.committee_review_date == date.today()

    def test_sets_completed_on(self) -> None:
        svc, ap = self._setup()
        svc.submit_committee_review(
            ORG_ID, ap.appraisal_id, decision=CommitteeDecision.ENDORSED
        )
        assert ap.completed_on == date.today()

    def test_stores_decision_value(self) -> None:
        svc, ap = self._setup()
        svc.submit_committee_review(
            ORG_ID, ap.appraisal_id, decision=CommitteeDecision.DISPUTED
        )
        assert ap.committee_decision == CommitteeDecision.DISPUTED.value

    def test_adjusted_decision_updates_rating(self) -> None:
        svc, ap = self._setup()
        svc.submit_committee_review(
            ORG_ID,
            ap.appraisal_id,
            decision=CommitteeDecision.ADJUSTED,
            adjusted_rating=3,
        )
        assert ap.final_rating == 3

    def test_endorsed_does_not_override_rating(self) -> None:
        svc, ap = self._setup()
        original_rating = ap.final_rating
        svc.submit_committee_review(
            ORG_ID,
            ap.appraisal_id,
            decision=CommitteeDecision.ENDORSED,
            adjusted_rating=2,  # should be ignored for ENDORSED
        )
        assert ap.final_rating == original_rating

    def test_from_countersigned_status_works(self) -> None:
        """COUNTERSIGNED is auto-advanced through PENDING_COMMITTEE to COMPLETED."""
        svc, ap = self._setup(AppraisalStatus.COUNTERSIGNED)
        svc.submit_committee_review(
            ORG_ID, ap.appraisal_id, decision=CommitteeDecision.ENDORSED
        )
        assert ap.status == AppraisalStatus.COMPLETED

    def test_invalid_status_raises(self) -> None:
        svc, ap = self._setup(AppraisalStatus.DRAFT)
        with pytest.raises(OHCSFAppraisalStatusError):
            svc.submit_committee_review(
                ORG_ID, ap.appraisal_id, decision=CommitteeDecision.ENDORSED
            )

    def test_sets_notes(self) -> None:
        svc, ap = self._setup()
        svc.submit_committee_review(
            ORG_ID,
            ap.appraisal_id,
            decision=CommitteeDecision.ENDORSED,
            notes="Well done",
        )
        assert ap.committee_notes == "Well done"

    def test_rejects_submission_after_calibration_deadline(self) -> None:
        svc, ap = self._setup()
        ap.cycle = MagicMock()
        ap.cycle.calibration_deadline = date.today() - timedelta(days=1)

        with pytest.raises(OHCSFAppraisalError, match="committee review"):
            svc.submit_committee_review(
                ORG_ID,
                ap.appraisal_id,
                decision=CommitteeDecision.ENDORSED,
            )

    def test_underperformance_creates_pip_and_blocks_completion(self) -> None:
        from unittest.mock import patch

        svc, ap = self._setup()
        ap.final_score = Decimal("49.50")
        svc.db.scalar.return_value = None

        with patch(
            "app.services.people.perf.underperformance_service.UnderperformanceService.flag_for_pip"
        ) as flag_for_pip:
            flag_for_pip.return_value = {"status": "flagged", "pip_code": "PIP-2026-0001"}
            with pytest.raises(OHCSFAppraisalError, match="PIP has been created"):
                svc.submit_committee_review(
                    ORG_ID,
                    ap.appraisal_id,
                    decision=CommitteeDecision.ENDORSED,
                )
        assert ap.status != AppraisalStatus.COMPLETED

    def test_underperformance_with_unresolved_pip_blocks_completion(self) -> None:
        svc, ap = self._setup()
        ap.final_score = Decimal("45.00")
        svc.db.scalar.return_value = MagicMock(status=PIPStatus.ACTIVE, pip_code="PIP-2026-0002")

        with pytest.raises(OHCSFAppraisalError, match="not resolved"):
            svc.submit_committee_review(
                ORG_ID,
                ap.appraisal_id,
                decision=CommitteeDecision.ENDORSED,
            )
        assert ap.status != AppraisalStatus.COMPLETED

    def test_underperformance_with_resolved_pip_allows_completion(self) -> None:
        svc, ap = self._setup()
        ap.final_score = Decimal("48.00")
        svc.db.scalar.return_value = MagicMock(status=PIPStatus.IMPROVED, pip_code="PIP-2026-0003")

        svc.submit_committee_review(
            ORG_ID,
            ap.appraisal_id,
            decision=CommitteeDecision.ENDORSED,
        )
        assert ap.status == AppraisalStatus.COMPLETED


# ---------------------------------------------------------------------------
# Annual rating calculation
# ---------------------------------------------------------------------------


class TestAnnualRatingCalculation:
    def _make_completed_appraisal(
        self, cycle_id: uuid.UUID, score: Decimal
    ) -> Appraisal:
        ap = make_appraisal(AppraisalStatus.COMPLETED)
        ap.cycle_id = cycle_id
        ap.is_quarterly = True
        ap.quarterly_rating = score
        ap.final_score = score
        return ap

    def test_averages_four_quarterly_scores(self) -> None:
        """4 quarters: 80, 75, 85, 90 → avg 82.50"""
        svc = make_service()

        q_cycle_ids = [uuid.uuid4() for _ in range(4)]
        scores = [Decimal("80"), Decimal("75"), Decimal("85"), Decimal("90")]

        # First scalars call: sub_cycle_ids
        # Second scalars call: appraisals
        appraisals = [
            self._make_completed_appraisal(q_cycle_ids[i], scores[i]) for i in range(4)
        ]

        scalars_mock = MagicMock()
        scalars_mock.all.side_effect = [q_cycle_ids, appraisals]
        svc.db.scalars.return_value = scalars_mock

        result = svc.calculate_annual_rating(ORG_ID, CYCLE_ID, EMP_ID)

        assert result["employee_id"] == EMP_ID
        assert result["annual_score"] == Decimal("82.50")
        assert len(result["quarterly_scores"]) == 4

    def test_all_scores_equal(self) -> None:
        """4 quarters all at 80 → avg 80.00"""
        svc = make_service()

        q_cycle_ids = [uuid.uuid4() for _ in range(4)]
        appraisals = [
            self._make_completed_appraisal(q_cycle_ids[i], Decimal("80"))
            for i in range(4)
        ]

        scalars_mock = MagicMock()
        scalars_mock.all.side_effect = [q_cycle_ids, appraisals]
        svc.db.scalars.return_value = scalars_mock

        result = svc.calculate_annual_rating(ORG_ID, CYCLE_ID, EMP_ID)
        assert result["annual_score"] == Decimal("80.00")

    def test_no_sub_cycles_returns_zero(self) -> None:
        """When no quarterly sub-cycles exist, return score 0 / rating Poor."""
        svc = make_service()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = []
        svc.db.scalars.return_value = scalars_mock

        result = svc.calculate_annual_rating(ORG_ID, CYCLE_ID, EMP_ID)
        assert result["annual_score"] == Decimal("0.00")
        assert result["rating"] == 1
        assert result["label"] == "Poor"

    def test_no_completed_appraisals_returns_zero(self) -> None:
        """Sub-cycles exist but no completed quarterly appraisals → zero score."""
        svc = make_service()
        q_cycle_ids = [uuid.uuid4()]

        scalars_mock = MagicMock()
        scalars_mock.all.side_effect = [q_cycle_ids, []]
        svc.db.scalars.return_value = scalars_mock

        result = svc.calculate_annual_rating(ORG_ID, CYCLE_ID, EMP_ID)
        assert result["annual_score"] == Decimal("0.00")

    def test_outstanding_annual_score(self) -> None:
        """Score >= 90 maps to Outstanding (rating 5)."""
        svc = make_service()
        q_cycle_ids = [uuid.uuid4()]
        appraisals = [self._make_completed_appraisal(q_cycle_ids[0], Decimal("95"))]

        scalars_mock = MagicMock()
        scalars_mock.all.side_effect = [q_cycle_ids, appraisals]
        svc.db.scalars.return_value = scalars_mock

        result = svc.calculate_annual_rating(ORG_ID, CYCLE_ID, EMP_ID)
        assert result["rating"] == 5
        assert result["label"] == "Outstanding"

    def test_poor_annual_score(self) -> None:
        """Score < 60 maps to Poor (rating 1)."""
        svc = make_service()
        q_cycle_ids = [uuid.uuid4()]
        appraisals = [self._make_completed_appraisal(q_cycle_ids[0], Decimal("45"))]

        scalars_mock = MagicMock()
        scalars_mock.all.side_effect = [q_cycle_ids, appraisals]
        svc.db.scalars.return_value = scalars_mock

        result = svc.calculate_annual_rating(ORG_ID, CYCLE_ID, EMP_ID)
        assert result["rating"] == 1
        assert result["label"] == "Poor"

    def test_result_contains_required_keys(self) -> None:
        svc = make_service()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = []
        svc.db.scalars.return_value = scalars_mock

        result = svc.calculate_annual_rating(ORG_ID, CYCLE_ID, EMP_ID)
        assert "employee_id" in result
        assert "quarterly_scores" in result
        assert "annual_score" in result
        assert "rating" in result
        assert "label" in result


# ---------------------------------------------------------------------------
# Instantiation
# ---------------------------------------------------------------------------


def test_service_instantiation() -> None:
    svc = make_service()
    assert isinstance(svc, OHCSFAppraisalService)
