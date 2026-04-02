"""
Tests for approved-absence appraisal behavior in PerformanceService.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.models.people.perf import Appraisal, AppraisalStatus
from app.services.people.perf.perf_service import PerformanceService, PerformanceServiceError


def _make_cycle() -> MagicMock:
    cycle = MagicMock()
    cycle.cycle_id = uuid.uuid4()
    return cycle


def test_create_appraisal_rejects_absence_over_6_without_documentation() -> None:
    db = MagicMock()
    svc = PerformanceService(db)
    db.scalar.return_value = _make_cycle()  # get_cycle lookup

    with pytest.raises(PerformanceServiceError, match="documentation"):
        svc.create_appraisal(
            uuid.uuid4(),
            employee_id=uuid.uuid4(),
            cycle_id=uuid.uuid4(),
            manager_id=uuid.uuid4(),
            absence_months=7,
            approved_absence_evidence=None,
        )


def test_create_appraisal_creates_prior_year_carryover_when_absence_over_6() -> None:
    db = MagicMock()
    svc = PerformanceService(db)
    cycle = _make_cycle()

    prior = Appraisal(
        organization_id=uuid.uuid4(),
        employee_id=uuid.uuid4(),
        cycle_id=uuid.uuid4(),
        manager_id=uuid.uuid4(),
        status=AppraisalStatus.COMPLETED,
    )
    prior.appraisal_id = uuid.uuid4()
    prior.final_score = Decimal("83.20")
    prior.final_rating = 4
    prior.rating_label = "Excellent"
    prior.completed_on = date.today()

    db.scalar.side_effect = [cycle, prior]

    created = svc.create_appraisal(
        uuid.uuid4(),
        employee_id=prior.employee_id,
        cycle_id=uuid.uuid4(),
        manager_id=uuid.uuid4(),
        absence_months=8,
        approved_absence_evidence={
            "document_type": "Medical Leave",
            "document_reference": "MED-2026-0043",
            "approval_reference": "PS/HR/ABS/223",
            "validation_reference": "HR-VAL-993",
            "audit_reference": "AUD-ABS-19",
        },
    )

    assert created.is_prior_year_carryover is True
    assert created.status == AppraisalStatus.COMPLETED
    assert created.carryover_source_id == prior.appraisal_id
    assert created.final_rating == prior.final_rating
    assert created.rating_label == prior.rating_label


def test_create_appraisal_normal_flow_when_absence_not_over_6() -> None:
    db = MagicMock()
    svc = PerformanceService(db)
    db.scalar.return_value = _make_cycle()

    created = svc.create_appraisal(
        uuid.uuid4(),
        employee_id=uuid.uuid4(),
        cycle_id=uuid.uuid4(),
        manager_id=uuid.uuid4(),
        absence_months=3,
        approved_absence_evidence=None,
    )

    assert created.is_prior_year_carryover is False
    assert created.status == AppraisalStatus.DRAFT


def test_create_appraisal_rejects_negative_absence_months() -> None:
    db = MagicMock()
    svc = PerformanceService(db)
    db.scalar.return_value = _make_cycle()

    with pytest.raises(PerformanceServiceError, match="cannot be negative"):
        svc.create_appraisal(
            uuid.uuid4(),
            employee_id=uuid.uuid4(),
            cycle_id=uuid.uuid4(),
            manager_id=uuid.uuid4(),
            absence_months=-1,
        )


def test_create_appraisal_rejects_absence_evidence_missing_required_fields() -> None:
    db = MagicMock()
    svc = PerformanceService(db)
    db.scalar.return_value = _make_cycle()

    with pytest.raises(PerformanceServiceError, match="missing required fields"):
        svc.create_appraisal(
            uuid.uuid4(),
            employee_id=uuid.uuid4(),
            cycle_id=uuid.uuid4(),
            manager_id=uuid.uuid4(),
            absence_months=7,
            approved_absence_evidence={
                "document_type": "Leave Approval",
                "document_reference": "ABS-001",
            },
        )


def test_update_appraisal_blocks_prior_year_carryover() -> None:
    db = MagicMock()
    svc = PerformanceService(db)
    appraisal = Appraisal(
        organization_id=uuid.uuid4(),
        employee_id=uuid.uuid4(),
        cycle_id=uuid.uuid4(),
        manager_id=uuid.uuid4(),
        status=AppraisalStatus.COMPLETED,
        is_prior_year_carryover=True,
    )
    appraisal.appraisal_id = uuid.uuid4()
    db.scalar.return_value = appraisal

    with pytest.raises(PerformanceServiceError, match="prior-year carryover"):
        svc.update_appraisal(
            appraisal.organization_id,
            appraisal.appraisal_id,
            manager_id=uuid.uuid4(),
        )


def test_update_appraisal_rejects_absence_over_6_without_evidence() -> None:
    db = MagicMock()
    svc = PerformanceService(db)
    appraisal = Appraisal(
        organization_id=uuid.uuid4(),
        employee_id=uuid.uuid4(),
        cycle_id=uuid.uuid4(),
        manager_id=uuid.uuid4(),
        status=AppraisalStatus.DRAFT,
        is_prior_year_carryover=False,
    )
    appraisal.appraisal_id = uuid.uuid4()
    db.scalar.return_value = appraisal

    with pytest.raises(PerformanceServiceError, match="evidence is required"):
        svc.update_appraisal(
            appraisal.organization_id,
            appraisal.appraisal_id,
            absence_months=8,
        )


def test_submit_self_assessment_blocks_prior_year_carryover() -> None:
    db = MagicMock()
    svc = PerformanceService(db)
    appraisal = Appraisal(
        organization_id=uuid.uuid4(),
        employee_id=uuid.uuid4(),
        cycle_id=uuid.uuid4(),
        manager_id=uuid.uuid4(),
        status=AppraisalStatus.DRAFT,
        is_prior_year_carryover=True,
    )
    appraisal.appraisal_id = uuid.uuid4()
    db.scalar.return_value = appraisal

    with pytest.raises(PerformanceServiceError, match="prior-year carryover"):
        svc.submit_self_assessment(
            appraisal.organization_id,
            appraisal.appraisal_id,
            self_overall_rating=3,
        )


def test_update_appraisal_rejects_invalid_status_transition() -> None:
    db = MagicMock()
    svc = PerformanceService(db)
    appraisal = Appraisal(
        organization_id=uuid.uuid4(),
        employee_id=uuid.uuid4(),
        cycle_id=uuid.uuid4(),
        manager_id=uuid.uuid4(),
        status=AppraisalStatus.DRAFT,
        is_prior_year_carryover=False,
    )
    appraisal.appraisal_id = uuid.uuid4()
    db.scalar.return_value = appraisal

    with pytest.raises(PerformanceServiceError, match="Cannot transition"):
        svc.update_appraisal(
            appraisal.organization_id,
            appraisal.appraisal_id,
            status=AppraisalStatus.CALIBRATION,
        )
