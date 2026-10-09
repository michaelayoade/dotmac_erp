from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from uuid import UUID

from app.services.people.attendance import web as attendance_web
from app.services.people.attendance.web import AttendanceWebService


ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
DEPARTMENT_ID = UUID("00000000-0000-0000-0000-000000000003")


class FakeOrganizationService:
    def __init__(self, db, org_id) -> None:
        assert org_id == ORG_ID

    def list_departments(self, *_args, **_kwargs):
        department = SimpleNamespace(
            department_id=DEPARTMENT_ID,
            department_name="Engineering",
        )
        return SimpleNamespace(items=[department])


def _capture_template_context(monkeypatch) -> dict:
    captured: dict = {}

    def render(_request, _template_name, context):
        captured["context"] = context
        return SimpleNamespace()

    monkeypatch.setattr(
        "app.services.people.hr.OrganizationService", FakeOrganizationService
    )
    monkeypatch.setattr(attendance_web, "base_context", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(attendance_web.templates, "TemplateResponse", render)
    return captured


def test_summary_report_builds_filter_preserving_drilldown_urls(monkeypatch) -> None:
    report = {
        "start_date": date(2026, 8, 1),
        "end_date": date(2026, 8, 31),
        "total_records": 10,
        "eligible_days": 9,
        "present": 6,
        "absent": 1,
        "half_day": 2,
        "on_leave": 1,
        "attendance_credit_days": Decimal("7"),
        "late_entries": 2,
        "early_exits": 1,
        "total_working_hours": Decimal("64"),
        "total_overtime_hours": Decimal("4"),
        "attendance_percentage": Decimal("77.8"),
    }

    class FakeAttendanceService:
        def __init__(self, db) -> None:
            self.db = db

        def get_attendance_summary_report(self, org_id, **kwargs):
            assert org_id == ORG_ID
            assert kwargs["department_id"] == DEPARTMENT_ID
            return report

    captured = _capture_template_context(monkeypatch)
    monkeypatch.setattr(attendance_web, "AttendanceService", FakeAttendanceService)

    AttendanceWebService.attendance_summary_report_response(
        request=SimpleNamespace(),
        auth=SimpleNamespace(organization_id=ORG_ID),
        db=SimpleNamespace(),
        start_date="2026-08-01",
        end_date="2026-08-31",
        department_id=str(DEPARTMENT_ID),
    )

    context = captured["context"]
    present_url = urlparse(context["status_drilldown_urls"]["PRESENT"])
    assert present_url.path == "/people/attendance/records"
    assert parse_qs(present_url.query) == {
        "start_date": ["2026-08-01"],
        "end_date": ["2026-08-31"],
        "department_id": [str(DEPARTMENT_ID)],
        "status": ["PRESENT"],
    }
    assert urlparse(context["working_hours_url"]).path == (
        "/people/attendance/reports/by-employee"
    )
    assert urlparse(context["punctuality_url"]).path == (
        "/people/attendance/reports/late-early"
    )


def test_by_employee_report_builds_search_and_employee_detail_url(monkeypatch) -> None:
    employee_id = UUID("00000000-0000-0000-0000-000000000002")
    report = {
        "start_date": date(2026, 8, 1),
        "end_date": date(2026, 8, 31),
        "employees": [
            {
                "employee_id": str(employee_id),
                "employee_name": "Ada Lovelace",
            }
        ],
        "total_employees": 1,
    }

    class FakeAttendanceService:
        def __init__(self, db) -> None:
            self.db = db

        def get_attendance_by_employee_report(self, org_id, **kwargs):
            assert org_id == ORG_ID
            assert kwargs["employee_search"] == "Ada"
            return report

    captured = _capture_template_context(monkeypatch)
    monkeypatch.setattr(attendance_web, "AttendanceService", FakeAttendanceService)

    AttendanceWebService.attendance_by_employee_report_response(
        request=SimpleNamespace(),
        auth=SimpleNamespace(organization_id=ORG_ID),
        db=SimpleNamespace(),
        start_date="2026-08-01",
        end_date="2026-08-31",
        department_id=str(DEPARTMENT_ID),
        employee_search="Ada",
    )

    context = captured["context"]
    assert context["employee_search"] == "Ada"
    detail_url = urlparse(context["report"]["employees"][0]["detail_url"])
    assert detail_url.path == "/people/attendance/records"
    assert parse_qs(detail_url.query) == {
        "start_date": ["2026-08-01"],
        "end_date": ["2026-08-31"],
        "department_id": [str(DEPARTMENT_ID)],
        "employee_id": [str(employee_id)],
    }
