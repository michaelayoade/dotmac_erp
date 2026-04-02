"""
Tests for legacy (non-OHCSF) appraisal completion PIP gate in PerformanceService.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.models.people.perf import AppraisalStatus
from app.models.people.perf.pms_enums import PIPStatus
from app.services.people.perf.perf_service import PerformanceService, PerformanceServiceError


def _make_service_and_appraisal() -> tuple[PerformanceService, MagicMock]:
    db = MagicMock()
    svc = PerformanceService(db)

    appraisal = MagicMock()
    appraisal.appraisal_id = uuid.uuid4()
    appraisal.organization_id = uuid.uuid4()
    appraisal.employee_id = uuid.uuid4()
    appraisal.status = AppraisalStatus.CALIBRATION
    appraisal.kra_scores = [
        SimpleNamespace(
            manager_rating=1,
            self_rating=None,
            final_rating=None,
            weightage=Decimal("100"),
            weighted_score=None,
        )
    ]

    db.scalar.side_effect = [appraisal]
    return svc, appraisal


def test_submit_calibration_blocks_when_linked_pip_unresolved() -> None:
    svc, appraisal = _make_service_and_appraisal()
    org_id = appraisal.organization_id
    pip = SimpleNamespace(status=PIPStatus.ACTIVE, pip_code="PIP-2026-0001")
    svc.db.scalar.side_effect = [appraisal, pip]

    with patch(
        "app.services.people.perf.underperformance_service.UnderperformanceService.ensure_pip_for_underperformance"
    ) as proactive:
        proactive.return_value = {"status": "exists", "pip_code": pip.pip_code}
        with pytest.raises(PerformanceServiceError, match="not resolved"):
            svc.submit_calibration(
                org_id,
                appraisal.appraisal_id,
                calibrated_rating=1,
                calibration_notes="below threshold",
            )

    assert appraisal.status == AppraisalStatus.CALIBRATION


def test_submit_calibration_completes_when_linked_pip_resolved() -> None:
    svc, appraisal = _make_service_and_appraisal()
    org_id = appraisal.organization_id
    pip = SimpleNamespace(status=PIPStatus.IMPROVED, pip_code="PIP-2026-0002")
    svc.db.scalar.side_effect = [appraisal, pip]

    with patch(
        "app.services.people.perf.underperformance_service.UnderperformanceService.ensure_pip_for_underperformance"
    ) as proactive:
        proactive.return_value = {"status": "exists", "pip_code": pip.pip_code}
        result = svc.submit_calibration(
            org_id,
            appraisal.appraisal_id,
            calibrated_rating=1,
            calibration_notes="resolved pip",
        )

    proactive.assert_called_once()
    assert result is appraisal
    assert appraisal.status == AppraisalStatus.COMPLETED
    assert appraisal.completed_on is not None


def test_submit_calibration_uses_percentage_normalization_for_legacy_score() -> None:
    svc, appraisal = _make_service_and_appraisal()
    org_id = appraisal.organization_id
    appraisal.kra_scores = [
        SimpleNamespace(
            manager_rating=5,
            self_rating=None,
            final_rating=None,
            weightage=Decimal("100"),
            weighted_score=None,
        )
    ]

    with patch(
        "app.services.people.perf.underperformance_service.UnderperformanceService.ensure_pip_for_underperformance"
    ) as proactive:
        proactive.return_value = None
        result = svc.submit_calibration(
            org_id,
            appraisal.appraisal_id,
            calibrated_rating=5,
            calibration_notes="high score",
        )

    assert result is appraisal
    # legacy flow score is 5.00, normalized to 100 for gate checks, so it should complete
    assert appraisal.final_score == Decimal("5.00")
    assert appraisal.status == AppraisalStatus.COMPLETED
