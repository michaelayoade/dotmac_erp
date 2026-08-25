from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_performance_page_and_sidebar_expose_weekly_report() -> None:
    perf_index = _read("templates/people/perf/index.html")
    people_nav = _read("templates/people/base_people.html")

    assert 'href="/people/perf/weekly-meeting-reports"' in perf_index
    assert "Weekly Meeting Report" in perf_index
    assert 'href="/people/perf/weekly-meeting-reports"' in people_nav
    assert "perf-weekly-reports" in people_nav


def test_mutating_weekly_report_forms_include_csrf() -> None:
    form = _read("templates/people/perf/weekly_meeting_reports/form.html")
    detail = _read("templates/people/perf/weekly_meeting_reports/detail.html")

    assert "request.state.csrf_form" in form
    assert form.count("request.state.csrf_form") >= 1
    assert detail.count("request.state.csrf_form") >= 3


def test_dynamic_form_supports_hr_refresh_and_manual_participants() -> None:
    form = _read("templates/people/perf/weekly_meeting_reports/form.html")

    assert "/department/${this.departmentId}/roster" in form
    assert "Add Employee" in form
    assert "Add External" in form
    assert "role_overridden" in form
    assert "{% for status in attendance_statuses %}" in form
    assert 'x-model="row.attendance_status"' in form


def test_long_text_fields_fill_their_available_cards_and_table_cells() -> None:
    form = _read("templates/people/perf/weekly_meeting_reports/form.html")

    assert form.count('class="form-textarea w-full"') == 3
    assert 'rows="4" class="form-textarea w-full"' in form
    assert 'class="form-textarea w-full" rows="2"' in form


def test_weekly_report_router_is_mode_neutral_and_permission_guarded() -> None:
    people_router = _read("app/web/people/__init__.py")
    feature_router = _read("app/web/people/weekly_meeting_reports.py")

    assert people_router.index("include_router(weekly_meeting_reports_router)") < (
        people_router.index("include_router(perf_router)")
    )
    assert "require_private_performance_mode" not in feature_router
    assert "performance:weekly_reports:read" in feature_router
    assert "performance:weekly_reports:write" in feature_router
    assert "performance:weekly_reports:submit" in feature_router
    assert "performance:weekly_reports:reopen" in feature_router


def test_migration_adds_tenant_policies_and_all_report_permissions() -> None:
    migration = _read("alembic/versions/20260825_weekly_meeting_reports.py")

    assert 'down_revision = "20260824_outbox_relay"' in migration
    assert "ENABLE ROW LEVEL SECURITY" in migration
    assert "FORCE ROW LEVEL SECURITY" in migration
    assert "WITH CHECK" in migration
    assert "current_setting('app.current_organization_id')::uuid" in migration
    assert "hr_weekly_report_email" in migration
    for action in ("read", "write", "submit", "reopen"):
        assert f"performance:weekly_reports:{action}" in migration
