"""Tests for PMSRewardService."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from app.services.people.perf.reward_service import (
    PMSRewardService,
    RewardValidationError,
)


def make_service() -> tuple[PMSRewardService, MagicMock]:
    db = MagicMock()
    return PMSRewardService(db), db


def test_nominate_reward_rejects_non_completed_appraisal() -> None:
    from app.models.people.perf.appraisal import AppraisalStatus

    svc, db = make_service()
    org_id = uuid.uuid4()
    appraisal = MagicMock()
    appraisal.status = AppraisalStatus.DRAFT
    appraisal.final_rating = 5
    appraisal.is_prior_year_carryover = False
    db.scalar.side_effect = [appraisal, None, None, None]

    with pytest.raises(RewardValidationError, match="Appraisal must be completed"):
        svc.nominate_reward(
            org_id,
            appraisal_id=uuid.uuid4(),
            reward_type="MERIT_AWARD",
            nomination_notes="Strong result",
        )


def test_nominate_reward_rejects_low_rating() -> None:
    from app.models.people.perf.appraisal import AppraisalStatus

    svc, db = make_service()
    org_id = uuid.uuid4()
    appraisal = MagicMock()
    appraisal.status = AppraisalStatus.COMPLETED
    appraisal.final_rating = 3
    appraisal.is_prior_year_carryover = False
    db.scalar.side_effect = [appraisal, None, None, None]

    with pytest.raises(RewardValidationError, match="Final rating must be >= 4"):
        svc.nominate_reward(
            org_id,
            appraisal_id=uuid.uuid4(),
            reward_type="MERIT_AWARD",
            nomination_notes=None,
        )


def test_nominate_reward_creates_pending_action_and_marks_appraisal() -> None:
    from app.models.people.perf.appraisal import AppraisalStatus
    from app.models.people.perf.pms_enums import OutcomeActionStatus

    svc, db = make_service()
    org_id = uuid.uuid4()
    appraisal = MagicMock()
    appraisal.status = AppraisalStatus.COMPLETED
    appraisal.final_rating = 5
    appraisal.is_prior_year_carryover = False

    db.scalar.side_effect = [appraisal, None, None, None]
    action = svc.nominate_reward(
        org_id,
        appraisal_id=uuid.uuid4(),
        reward_type="merit_award",
        nomination_notes="Exceeded all targets",
        nominated_by_person_id=uuid.uuid4(),
    )

    assert appraisal.reward_nominated is True
    assert appraisal.reward_type == "MERIT_AWARD"
    assert action.status == OutcomeActionStatus.PENDING
    assert "NOMINATED" in (action.notes or "")
    db.add.assert_called_once()
    db.flush.assert_called_once()


def test_approve_reward_transitions_pending_to_completed() -> None:
    from app.models.people.perf.pms_enums import OutcomeActionStatus

    svc, db = make_service()
    org_id = uuid.uuid4()
    action = MagicMock()
    action.status = OutcomeActionStatus.PENDING
    db.scalar.return_value = action

    result = svc.approve_reward(
        org_id,
        uuid.uuid4(),
        approved_by_employee_id=uuid.uuid4(),
        approved_by_person_id=uuid.uuid4(),
        approval_notes="Approved by committee",
    )

    assert result.status == OutcomeActionStatus.COMPLETED
    assert "APPROVED" in (result.notes or "")
    assert "Approved by committee" in (result.notes or "")
    db.flush.assert_called_once()


def test_cancel_reward_clears_nomination_fields() -> None:
    from app.models.people.perf.pms_enums import OutcomeActionStatus

    svc, db = make_service()
    org_id = uuid.uuid4()
    action = MagicMock()
    action.status = OutcomeActionStatus.PENDING
    appraisal = MagicMock()
    db.scalar.side_effect = [action, appraisal]

    result = svc.cancel_reward(
        org_id,
        uuid.uuid4(),
        cancelled_by_person_id=uuid.uuid4(),
        cancellation_notes="Insufficient evidence",
    )

    assert result.status == OutcomeActionStatus.CANCELLED
    assert appraisal.reward_nominated is False
    assert appraisal.reward_type is None
    assert appraisal.reward_notes is None
    assert "CANCELLED" in (result.notes or "")
    assert "Insufficient evidence" in (result.notes or "")
    db.flush.assert_called_once()


def test_nominate_reward_rejects_unresolved_appeal() -> None:
    from app.models.people.perf.appraisal import AppraisalStatus

    svc, db = make_service()
    org_id = uuid.uuid4()
    appraisal = MagicMock()
    appraisal.status = AppraisalStatus.COMPLETED
    appraisal.final_rating = 5
    appraisal.is_prior_year_carryover = False
    appraisal.organization_id = org_id
    appraisal.appraisal_id = uuid.uuid4()
    db.scalar.side_effect = [appraisal, uuid.uuid4(), None, None]

    with pytest.raises(RewardValidationError, match="Unresolved appeal exists"):
        svc.nominate_reward(
            org_id,
            appraisal_id=uuid.uuid4(),
            reward_type="MERIT_AWARD",
            nomination_notes="Great year",
        )


def test_nominate_reward_rejects_carryover_appraisal() -> None:
    from app.models.people.perf.appraisal import AppraisalStatus

    svc, db = make_service()
    org_id = uuid.uuid4()
    appraisal = MagicMock()
    appraisal.status = AppraisalStatus.COMPLETED
    appraisal.final_rating = 5
    appraisal.is_prior_year_carryover = True
    appraisal.organization_id = org_id
    appraisal.appraisal_id = uuid.uuid4()
    db.scalar.side_effect = [appraisal, None, None, None]

    with pytest.raises(RewardValidationError, match="carryover"):
        svc.nominate_reward(
            org_id,
            appraisal_id=uuid.uuid4(),
            reward_type="MERIT_AWARD",
            nomination_notes="Carryover should be blocked",
        )
