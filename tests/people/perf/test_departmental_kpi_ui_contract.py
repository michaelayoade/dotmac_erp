"""Static UI and route contracts for the departmental KPI dashboard."""

from pathlib import Path


ROOT = Path(__file__).parents[3]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_dashboard_exposes_database_driven_filters_and_empty_states() -> None:
    template = _read("templates/people/perf/departmental_kpi/dashboard.html")
    assert 'name="department_id"' in template
    assert 'name="period_start"' in template
    assert 'name="period_end"' in template
    assert 'name="employee_id"' in template
    assert 'name="category"' in template
    assert 'name="status"' in template
    assert "No KPI definitions for this department" in template
    assert 'chart_canvas("trendLine"' in template
    assert "request.state.csrf_form | safe" in template


def test_management_form_has_configurable_scoring_and_assignment_fields() -> None:
    template = _read("templates/people/perf/departmental_kpi/configuration_form.html")
    for field in (
        "kpi_code",
        "department_id",
        "assignment_scope",
        "target_value",
        "weightage",
        "measurement_type",
        "direction",
        "green_threshold",
        "amber_threshold",
        "frequency",
        "metric_source_key",
    ):
        assert f'name="{field}"' in template
    assert "request.state.csrf_form | safe" in template


def test_routes_use_existing_permission_and_tenant_dependencies() -> None:
    routes = _read("app/web/people/departmental_kpi.py")
    for permission in (
        "performance:kpi:dashboard:view",
        "performance:kpi:dashboard:view_all_departments",
        "performance:kpi:manage",
        "performance:kpi:measure",
        "performance:kpi:approve",
        "performance:kpi:export",
    ):
        assert permission in routes
    assert "get_db_for_org" in routes
    assert "require_private_performance_mode" in routes
    assert '@router.get("/department"' in routes
    assert '@router.get("/measurements"' in routes
    assert '@router.get("/definitions"' in routes
    assert '@router.get("/assignments"' in routes
    assert '@router.get("/scorecards"' in routes
    assert '"/export.csv"' in routes


def test_workspace_uses_semantic_kpi_sections() -> None:
    landing = _read("templates/people/perf/index.html")
    definitions = _read("templates/people/perf/departmental_kpi/configurations.html")
    assert "KPI Definitions" in landing
    assert ">Assignments<" in landing
    assert "Measurements" in landing
    assert "Department Scorecards" in landing
    assert "health_by_template" in definitions


def test_measurement_queue_exposes_operational_states_and_actions() -> None:
    template = _read("templates/people/perf/departmental_kpi/measurements.html")
    for label in (
        "Missing actuals",
        "Draft measurements",
        "Awaiting approval",
        "Returned",
        "Automatic refreshes",
        "Record measurement",
    ):
        assert label in template
    assert 'name="period_start"' in template
    assert 'name="period_end"' in template
    assert 'name="state"' in template
    assert "request.state.csrf_form | safe" in template


def test_measurement_forms_are_csrf_protected() -> None:
    measurement = _read("templates/people/perf/departmental_kpi/measurement_form.html")
    detail = _read("templates/people/perf/departmental_kpi/configuration_detail.html")
    assert "request.state.csrf_form | safe" in measurement
    assert "request.state.csrf_form | safe" in detail
    assert "measurement-history" in detail
    assert 'chart_canvas("trendLine"' in detail
