"""Pure contract tests: no application startup, database or private modules."""

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from app.services.people.perf.kpi_dashboard_contract import (
    DEFAULT_STATUSES,
    DEFAULT_WIDGETS,
    DashboardAccessError,
    DashboardConfig,
    DashboardValidationError,
    authorize_departments,
    has_organization_access,
    organization_today,
    reporting_percentage,
    view_name,
)


@pytest.mark.parametrize(
    ("period", "today", "start", "end"),
    [
        ("this_week", "2026-09-15", "2026-09-14", "2026-09-20"),
        ("this_week", "2027-01-01", "2026-12-28", "2027-01-03"),
        ("this_month", "2024-02-15", "2024-02-01", "2024-02-29"),
        ("this_month", "2026-12-31", "2026-12-01", "2026-12-31"),
        ("this_quarter", "2026-12-31", "2026-10-01", "2026-12-31"),
        ("last_month", "2027-01-10", "2026-12-01", "2026-12-31"),
    ],
)
def test_period_boundaries(period, today, start, end):
    assert DashboardConfig.parse({"period": period}).window(
        date.fromisoformat(today)
    ) == (
        date.fromisoformat(start),
        date.fromisoformat(end),
    )


def test_defaults_and_round_trip():
    config = DashboardConfig.parse({})
    assert config.widgets == DEFAULT_WIDGETS
    assert config.statuses == DEFAULT_STATUSES
    assert DashboardConfig.parse(config.to_dict()) == config
    assert not {"DRAFT", "DEFERRED", "CANCELLED"} & set(config.statuses)


def test_relative_view_does_not_freeze_dates():
    config = DashboardConfig.parse({"period": "this_month", "start_date": "bad"})
    assert config.start_date == ""
    assert config.window(date(2026, 10, 5))[0] == date(2026, 10, 1)


@pytest.mark.parametrize(
    "payload",
    [
        {"period": "all_time"},
        {"widgets": []},
        {"widgets": ["tracked", "tracked"]},
        {"widgets": ["sql"]},
        {"statuses": []},
        {"statuses": ["INVENTED"]},
        {"statuses": ["ACTIVE", "ACTIVE"]},
        {"statuses": "ACTIVE"},
        {"search": "a" * 101},
        {"search": 42},
        {"department_ids": ["not-a-uuid"]},
        {"department_ids": [str(uuid4())] * 101},
        {"department_ids": [None]},
        {"organization_id": str(uuid4())},
        {"sql": "SELECT * FROM people"},
        {"period": "custom", "start_date": "20260101", "end_date": "2026-01-02"},
        {"period": "custom", "start_date": "2026-02-30", "end_date": "2026-03-01"},
        {"period": "custom", "start_date": "2026-02-01", "end_date": "2026-01-01"},
        {"period": "custom", "start_date": "2024-01-01", "end_date": "2025-01-01"},
    ],
)
def test_invalid_configuration_rejected(payload):
    with pytest.raises(DashboardValidationError):
        DashboardConfig.parse(payload)


def test_custom_range_inclusive_limit_and_widget_order():
    config = DashboardConfig.parse(
        {
            "period": "custom",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "widgets": ["coverage", "", "tracked"],
        }
    )
    assert config.widgets == ("coverage", "tracked")
    assert config.window(date(2026, 1, 1)) == (date(2024, 1, 1), date(2024, 12, 31))


def test_uuid_alias_does_not_bypass_duplicate_check():
    department = uuid4()
    with pytest.raises(DashboardValidationError):
        DashboardConfig.parse({"department_ids": [str(department), department.hex]})


def test_restricted_empty_selection_means_all_permitted_not_all_company():
    department = uuid4()
    assert authorize_departments([], [str(department)], organization_wide=False) == (
        department,
    )
    assert authorize_departments([], [str(department)], organization_wide=True) is None


def test_no_department_never_falls_through_to_unrestricted():
    with pytest.raises(DashboardAccessError):
        authorize_departments([], [], organization_wide=False)


@pytest.mark.parametrize("wide", [False, True])
def test_foreign_department_rejected_even_for_org_admin(wide):
    with pytest.raises(DashboardAccessError):
        authorize_departments([str(uuid4())], [str(uuid4())], organization_wide=wide)


def test_explicit_multi_department_scope():
    departments = [str(uuid4()), str(uuid4())]
    assert {
        str(value)
        for value in authorize_departments(
            departments, departments, organization_wide=False
        )
    } == set(departments)


@pytest.mark.parametrize("roles", [["ADMIN"], [" HR_Manager "], ["hr_director"]])
def test_explicit_organization_roles(roles):
    assert has_organization_access(roles)


@pytest.mark.parametrize(
    "roles", [[], ["payroll_admin"], ["manager"], ["employee"], ["superadmin"]]
)
def test_other_roles_do_not_receive_company_access(roles):
    assert not has_organization_access(roles)


def test_business_date_uses_organization_timezone():
    moment = datetime(2026, 9, 14, 23, 30, tzinfo=timezone.utc)
    assert organization_today("Africa/Lagos", moment) == date(2026, 9, 15)
    assert organization_today(None, moment) == date(2026, 9, 14)
    with pytest.raises(DashboardValidationError):
        organization_today("Invalid/Timezone", moment)
    with pytest.raises(DashboardValidationError):
        organization_today("UTC", datetime(2026, 9, 15))


def test_coverage_is_ratio_of_counts_not_average_of_percentages():
    assert reporting_percentage(1, 3) == Decimal("33.3")
    assert reporting_percentage(0, 10) == Decimal("0.0")
    assert reporting_percentage(0, 0) is None
    assert reporting_percentage(100, 200) == Decimal("50.0")


@pytest.mark.parametrize("reported,total", [(1, 0), (-1, 2), (3, 2), (0, -1)])
def test_impossible_coverage_rejected(reported, total):
    with pytest.raises(DashboardValidationError):
        reporting_percentage(reported, total)


@pytest.mark.parametrize("name", ["", "   ", "a" * 81, None])
def test_invalid_saved_view_names(name):
    with pytest.raises(DashboardValidationError):
        view_name(name)


def test_view_name_trimmed():
    assert view_name("  Engineering monthly  ") == "Engineering monthly"
