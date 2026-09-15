"""Employee insight and scoring integration regressions against the real models."""

from datetime import date
from decimal import Decimal as D
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.models.people.perf.kpi import KPI, KPIStatus
from app.services.people.perf.kpi_dashboard_contract import (
    DashboardConfig,
    DashboardValidationError,
)
from app.services.people.perf.kpi_dashboard_service import (
    DashboardScope,
    KPIDashboardService,
)
from app.services.people.perf.perf_service import (
    PerformanceService,
    DEPARTMENT_TEMPLATE_LIBRARY,
)


def kpi(**kwargs):
    values = dict(
        kpi_name="Test KPI",
        target_value=D("20"),
        actual_value=D("20"),
        status=KPIStatus.ACHIEVED,
        lower_is_better=False,
        organization_id=uuid4(),
        employee_id=uuid4(),
        period_start=date(2026, 9, 1),
        period_end=date(2026, 9, 30),
    )
    values.update(kwargs)
    return KPI(**values)


def test_manual_zero_replaces_previous_achieved_status():
    item = kpi()
    svc = PerformanceService(MagicMock())
    svc.get_kpi = MagicMock(return_value=item)
    svc.update_kpi_progress(item.organization_id, uuid4(), actual_value=D("0"))
    assert item.achievement_percentage == D("0")
    assert item.status == KPIStatus.AT_RISK
    svc.db.commit.assert_not_called()


@pytest.mark.parametrize("explicit", [True, None])
def test_manual_lower_direction_matches_system_application(explicit):
    item = kpi(
        kpi_name="support.avg_resolution_days",
        lower_is_better=explicit,
        target_value=D("2"),
    )
    svc = PerformanceService(MagicMock())
    svc.get_kpi = MagicMock(return_value=item)
    svc.update_kpi_progress(item.organization_id, uuid4(), actual_value=D("4"))
    assert item.achievement_percentage == D("50")
    assert not item.is_achieved
    svc._apply_kpi_actual_value(
        item, D("4"), lower_is_better=item.effective_lower_is_better
    )
    assert item.achievement_percentage == D("50")


def test_missing_measurement_clears_old_score_without_reopening_final_outcomes():
    item = kpi()
    PerformanceService._apply_kpi_actual_value(item, None)
    assert item.actual_value is None and item.achievement_percentage is None
    assert item.status == KPIStatus.PENDING
    item.status = KPIStatus.COMPLETED
    PerformanceService._apply_kpi_actual_value(item, None)
    assert item.status == KPIStatus.COMPLETED


def test_target_edit_recalculates_and_draft_edit_stays_draft():
    item = kpi()
    svc = PerformanceService(MagicMock())
    svc.get_kpi = MagicMock(return_value=item)
    svc.update_kpi(item.organization_id, uuid4(), target_value=D("40"))
    assert item.achievement_percentage == D("50")
    item.status, item.actual_value = KPIStatus.DRAFT, None
    svc.update_kpi(item.organization_id, uuid4(), notes="Draft planning")
    assert item.status == KPIStatus.DRAFT


@pytest.mark.parametrize(
    "metric", ["support.resolution_rate", "support.avg_resolution_days"]
)
def test_empty_support_cohort_is_not_a_zero_measurement(metric):
    db = MagicMock()
    db.scalar.return_value = 0
    db.scalars.return_value.all.return_value = []
    assert (
        PerformanceService(db)._calculate_support_ticket_metric(
            uuid4(),
            employee_id=uuid4(),
            metric_key=metric,
            period_start=date(2026, 9, 1),
            period_end=date(2026, 9, 30),
        )
        is None
    )


def test_missing_scorecard_observation_clears_stale_derived_values():
    item = MagicMock(
        actual_value=None,
        target_value=D("20"),
        score=D("100"),
        weighted_score=D("50"),
        weightage=D("50"),
    )
    PerformanceService._apply_scorecard_item_score(item)
    assert item.score is None and item.weighted_score is None


def test_support_sync_preserves_existing_perspective_notes():
    item = kpi(
        kpi_name="Support rate",
        notes="Metric key: support.resolution_rate\nScorecard perspective: CUSTOMER",
    )
    svc = PerformanceService(MagicMock())
    svc._calculate_support_ticket_metric = MagicMock(return_value=None)
    svc._sync_kpi_actual_from_system_metric(item.organization_id, item)
    assert "Scorecard perspective: CUSTOMER" in item.notes
    assert item.actual_value is None
    assert item.status == KPIStatus.PENDING


def test_new_resolution_rate_default_does_not_claim_sla_compliance():
    metric = next(
        item
        for item in DEPARTMENT_TEMPLATE_LIBRARY["customer_experience"]
        if item["metric_source_key"] == "support.resolution_rate"
    )
    assert "SLA" not in metric["kpi_name"]


@pytest.mark.parametrize(
    ("actual", "target", "lower", "deadline", "expected"),
    [
        (None, "20", False, date(2026, 9, 1), "Missing measurement"),
        ("0", "20", False, date(2026, 9, 1), "Below target after deadline"),
        ("10", "20", False, date(2026, 9, 30), "In progress — period not ended"),
        ("4", "2", True, date(2026, 9, 1), "Below target after deadline"),
        ("1", "2", True, date(2026, 9, 1), "Target met"),
        ("1", "0", False, date(2026, 9, 1), "Review measurement setup"),
    ],
)
def test_record_assessment_does_not_grade_unfinished_or_missing_results(
    actual, target, lower, deadline, expected
):
    row = dict(
        actual_value=D(actual) if actual is not None else None,
        target_value=D(target),
        lower_is_better=lower,
        period_end=deadline,
        status=KPIStatus.ACTIVE,
        employee_name=None,
        employee_code="EMP-1",
    )
    output = KPIDashboardService._record(row, date(2026, 9, 15))
    assert output["assessment"] == expected
    assert output["employee_name"] == "EMP-1"


@pytest.mark.parametrize(
    "field,value",
    [
        ("employee_search", "x" * 101),
        ("employee_search", []),
        ("cohort", "future"),
        ("cohort", []),
        ("attention", "lowest_rank"),
        ("show_charts", "true"),
    ],
)
def test_invalid_insight_filters_are_rejected(field, value):
    with pytest.raises(DashboardValidationError):
        DashboardConfig.parse({field: value})


def test_old_saved_view_gets_safe_defaults_and_new_settings_round_trip():
    old = DashboardConfig.parse({"widgets": ["tracked"]})
    assert old.show_charts and old.cohort == "due" and old.attention == "all"
    new = DashboardConfig.parse(
        {
            "employee_search": "EMP-7",
            "cohort": "active",
            "attention": "needs_attention",
            "show_charts": False,
        }
    )
    assert DashboardConfig.parse(new.to_dict()) == new


@pytest.mark.parametrize(
    "cohort,sql_fragment",
    [
        ("due", "kpi.period_end >="),
        ("active", "kpi.period_start <="),
        ("overdue", "kpi.period_end <"),
    ],
)
def test_date_scopes_and_literal_employee_search(cohort, sql_fragment):
    org = uuid4()
    scope = DashboardScope(
        org,
        uuid4(),
        False,
        ({"id": str(uuid4()), "name": "Test", "active": True},),
        "Africa/Lagos",
    )
    query = (
        KPIDashboardService(MagicMock())
        ._base_query(
            scope,
            DashboardConfig(cohort=cohort, employee_search="a%b_c"),
            date(2026, 9, 15),
        )
        .compile(dialect=postgresql.dialect())
    )
    assert sql_fragment in str(query)
    assert "people.organization_id =" in str(query)
    assert "employee.department_id IN" in str(query)
    assert "a/%b/_c" in query.params.values()
    if cohort == "overdue":
        assert date(2026, 9, 15) in query.params.values()
        assert date(2026, 9, 1) not in query.params.values()
