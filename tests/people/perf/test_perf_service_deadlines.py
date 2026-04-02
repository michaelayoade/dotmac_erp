"""
Deadline enforcement tests for PerformanceService workflow submissions.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

from app.models.people.perf import Appraisal, AppraisalStatus
from app.services.people.perf.perf_service import PerformanceService, PerformanceServiceError


def _make_appraisal(status: AppraisalStatus) -> Appraisal:
    appraisal = Appraisal(
        organization_id=uuid.uuid4(),
        employee_id=uuid.uuid4(),
        cycle_id=uuid.uuid4(),
        manager_id=uuid.uuid4(),
        status=status,
    )
    appraisal.appraisal_id = uuid.uuid4()
    appraisal.kra_scores = []
    return appraisal


def test_submit_self_assessment_rejects_after_deadline() -> None:
    db = MagicMock()
    svc = PerformanceService(db)
    appraisal = _make_appraisal(AppraisalStatus.DRAFT)
    appraisal.cycle = MagicMock()
    appraisal.cycle.self_assessment_deadline = date.today() - timedelta(days=1)
    db.scalar.return_value = appraisal

    with pytest.raises(PerformanceServiceError, match="self-assessment"):
        svc.submit_self_assessment(
            appraisal.organization_id,
            appraisal.appraisal_id,
            self_overall_rating=3,
        )


def test_submit_manager_review_rejects_after_deadline() -> None:
    db = MagicMock()
    svc = PerformanceService(db)
    appraisal = _make_appraisal(AppraisalStatus.UNDER_REVIEW)
    appraisal.cycle = MagicMock()
    appraisal.cycle.manager_review_deadline = date.today() - timedelta(days=1)
    db.scalar.return_value = appraisal

    with pytest.raises(PerformanceServiceError, match="manager review"):
        svc.submit_manager_review(
            appraisal.organization_id,
            appraisal.appraisal_id,
            manager_overall_rating=3,
        )


def test_submit_calibration_rejects_after_deadline() -> None:
    db = MagicMock()
    svc = PerformanceService(db)
    appraisal = _make_appraisal(AppraisalStatus.CALIBRATION)
    appraisal.cycle = MagicMock()
    appraisal.cycle.calibration_deadline = date.today() - timedelta(days=1)
    db.scalar.return_value = appraisal

    with pytest.raises(PerformanceServiceError, match="calibration"):
        svc.submit_calibration(
            appraisal.organization_id,
            appraisal.appraisal_id,
            calibrated_rating=3,
        )

