"""
Tests for staff committee reconciliation workflow in PerformanceService.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from app.models.people.perf import Appraisal, AppraisalStatus
from app.services.people.perf.perf_service import PerformanceService, PerformanceServiceError


def _make_appraisal(*, cycle_id: uuid.UUID, rating: int) -> Appraisal:
    appraisal = Appraisal(
        organization_id=uuid.uuid4(),
        employee_id=uuid.uuid4(),
        cycle_id=cycle_id,
        manager_id=uuid.uuid4(),
        status=AppraisalStatus.COMPLETED,
    )
    appraisal.appraisal_id = uuid.uuid4()
    appraisal.final_rating = rating
    appraisal.calibrated_rating = rating
    appraisal.rating_label = "Good"
    return appraisal


def test_reconcile_department_ratings_adjusts_and_endorses() -> None:
    db = MagicMock()
    svc = PerformanceService(db)

    cycle_id = uuid.uuid4()
    app_a = _make_appraisal(cycle_id=cycle_id, rating=3)
    app_b = _make_appraisal(cycle_id=cycle_id, rating=4)
    db.scalar.side_effect = [app_a, app_b]

    result = svc.reconcile_department_ratings(
        app_a.organization_id,
        cycle_id=cycle_id,
        committee_level="SENIOR",
        reconciled_by_id=uuid.uuid4(),
        notes="Distribution balancing",
        entries=[
            {"appraisal_id": str(app_a.appraisal_id), "final_rating": 4},
            {"appraisal_id": str(app_b.appraisal_id), "final_rating": 4},
        ],
    )

    assert result["processed_count"] == 2
    assert result["adjusted_count"] == 1
    assert result["endorsed_count"] == 1
    assert app_a.final_rating == 4
    assert app_a.committee_decision == "ADJUSTED"
    assert app_b.committee_decision == "ENDORSED"
    assert "SENIOR STAFF COMMITTEE" in (app_a.committee_notes or "")


def test_reconcile_department_ratings_rejects_non_completed_appraisal() -> None:
    db = MagicMock()
    svc = PerformanceService(db)

    cycle_id = uuid.uuid4()
    appraisal = _make_appraisal(cycle_id=cycle_id, rating=3)
    appraisal.status = AppraisalStatus.CALIBRATION
    db.scalar.return_value = appraisal

    with pytest.raises(PerformanceServiceError, match="must be COMPLETED"):
        svc.reconcile_department_ratings(
            appraisal.organization_id,
            cycle_id=cycle_id,
            committee_level="JUNIOR",
            reconciled_by_id=uuid.uuid4(),
            entries=[{"appraisal_id": str(appraisal.appraisal_id), "final_rating": 3}],
        )


def test_reconcile_department_ratings_rejects_invalid_level() -> None:
    db = MagicMock()
    svc = PerformanceService(db)

    with pytest.raises(PerformanceServiceError, match="committee_level"):
        svc.reconcile_department_ratings(
            uuid.uuid4(),
            cycle_id=uuid.uuid4(),
            committee_level="LOCAL",
            reconciled_by_id=uuid.uuid4(),
            entries=[{"appraisal_id": str(uuid.uuid4()), "final_rating": 3}],
        )

