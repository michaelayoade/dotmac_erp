"""Regression tests for performance row-level delete actions."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.models.people.perf import Appraisal, AppraisalStatus, KPI, KPIStatus
from app.services.people.perf.perf_service import (
    AppraisalNotFoundError,
    PerformanceService,
    PerformanceServiceError,
)
from app.services.people.perf.web.perf_web import PerfWebService


def _draft_appraisal() -> Appraisal:
    appraisal = Appraisal(
        organization_id=uuid.uuid4(),
        employee_id=uuid.uuid4(),
        cycle_id=uuid.uuid4(),
        manager_id=uuid.uuid4(),
        status=AppraisalStatus.DRAFT,
        is_prior_year_carryover=False,
    )
    appraisal.appraisal_id = uuid.uuid4()
    return appraisal


def _draft_kpi() -> KPI:
    kpi = KPI(
        organization_id=uuid.uuid4(),
        employee_id=uuid.uuid4(),
        kpi_name="Timely Delivery",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 12, 31),
        target_value=Decimal("100.00"),
        status=KPIStatus.DRAFT,
    )
    kpi.kpi_id = uuid.uuid4()
    return kpi


def _request(path: str, query_params: dict[str, str] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        url=SimpleNamespace(path=path), query_params=query_params or {}
    )


def _auth(org_id: uuid.UUID) -> SimpleNamespace:
    return SimpleNamespace(organization_id=org_id, employee_id=uuid.uuid4())


def test_delete_appraisal_removes_draft_and_child_rows() -> None:
    db = MagicMock()
    svc = PerformanceService(db)
    appraisal = _draft_appraisal()
    db.scalar.side_effect = [appraisal, 0, 0, 0, 0, 0]

    svc.delete_appraisal(appraisal.organization_id, appraisal.appraisal_id)

    assert db.execute.call_count == 2
    db.delete.assert_called_once_with(appraisal)
    db.flush.assert_called_once()


def test_delete_appraisal_blocks_non_draft() -> None:
    db = MagicMock()
    svc = PerformanceService(db)
    appraisal = _draft_appraisal()
    appraisal.status = AppraisalStatus.UNDER_REVIEW
    db.scalar.return_value = appraisal

    with pytest.raises(PerformanceServiceError, match="Only draft appraisals"):
        svc.delete_appraisal(appraisal.organization_id, appraisal.appraisal_id)

    db.delete.assert_not_called()


def test_delete_appraisal_blocks_when_feedback_exists() -> None:
    db = MagicMock()
    svc = PerformanceService(db)
    appraisal = _draft_appraisal()
    db.scalar.side_effect = [appraisal, 1]

    with pytest.raises(PerformanceServiceError, match="feedback has been recorded"):
        svc.delete_appraisal(appraisal.organization_id, appraisal.appraisal_id)

    db.delete.assert_not_called()


def test_delete_appraisal_cross_tenant_blocked() -> None:
    db = MagicMock()
    svc = PerformanceService(db)
    db.scalar.return_value = None

    with pytest.raises(AppraisalNotFoundError):
        svc.delete_appraisal(uuid.uuid4(), uuid.uuid4())


def test_delete_kpi_removes_draft_unstarted_kpi() -> None:
    db = MagicMock()
    svc = PerformanceService(db)
    kpi = _draft_kpi()
    db.scalar.return_value = kpi

    svc.delete_kpi(kpi.organization_id, kpi.kpi_id)

    db.delete.assert_called_once_with(kpi)
    db.flush.assert_called_once()


def test_delete_kpi_blocks_progressed_record() -> None:
    db = MagicMock()
    svc = PerformanceService(db)
    kpi = _draft_kpi()
    kpi.actual_value = Decimal("40.00")
    db.scalar.return_value = kpi

    with pytest.raises(PerformanceServiceError, match="progress has been recorded"):
        svc.delete_kpi(kpi.organization_id, kpi.kpi_id)

    db.delete.assert_not_called()


def test_list_appraisals_response_marks_only_deletable_rows() -> None:
    org_id = uuid.uuid4()
    deletable = _draft_appraisal()
    blocked = _draft_appraisal()
    blocked.status = AppraisalStatus.SELF_ASSESSMENT
    svc_instance = MagicMock()
    svc_instance.list_appraisals.return_value = SimpleNamespace(
        items=[deletable, blocked],
        page=1,
        total_pages=1,
        total=2,
        has_prev=False,
        has_next=False,
    )
    svc_instance.can_delete_appraisal.side_effect = [True, False]
    request = _request(
        "/people/perf/appraisals",
        {"success": "Appraisal deleted successfully", "error": "blocked"},
    )
    auth = _auth(org_id)

    with (
        patch(
            "app.services.people.perf.web.perf_web.PerformanceService",
            return_value=svc_instance,
        ),
        patch("app.services.people.perf.web.perf_web.base_context", return_value={}),
        patch(
            "app.services.people.perf.web.perf_web.templates.TemplateResponse",
            side_effect=lambda _request, _template, context: context,
        ),
    ):
        context = PerfWebService().list_appraisals_response(
            request, auth, MagicMock(), page=1
        )

    assert deletable.appraisal_id in context["deletable_appraisal_ids"]
    assert blocked.appraisal_id not in context["deletable_appraisal_ids"]
    assert context["success"] == "Appraisal deleted successfully"
    assert context["error"] == "blocked"


def test_list_goals_response_marks_only_deletable_rows() -> None:
    org_id = uuid.uuid4()
    deletable = _draft_kpi()
    blocked = _draft_kpi()
    blocked.status = KPIStatus.ACTIVE
    svc_instance = MagicMock()
    svc_instance.list_kpis.return_value = SimpleNamespace(
        items=[deletable, blocked],
        page=1,
        total_pages=1,
        total=2,
        has_prev=False,
        has_next=False,
    )
    svc_instance.can_delete_kpi.side_effect = [True, False]
    request = _request(
        "/people/perf/goals",
        {"success": "KPI deleted successfully", "error": "blocked"},
    )
    auth = _auth(org_id)

    with (
        patch(
            "app.services.people.perf.web.perf_web.PerformanceService",
            return_value=svc_instance,
        ),
        patch("app.services.people.perf.web.perf_web.base_context", return_value={}),
        patch(
            "app.services.people.perf.web.perf_web.templates.TemplateResponse",
            side_effect=lambda _request, _template, context: context,
        ),
    ):
        context = PerfWebService().list_goals_response(
            request, auth, MagicMock(), page=1
        )

    assert deletable.kpi_id in context["deletable_kpi_ids"]
    assert blocked.kpi_id not in context["deletable_kpi_ids"]
    assert context["success"] == "KPI deleted successfully"
    assert context["error"] == "blocked"
