from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from fastapi import Request
from fastapi.responses import HTMLResponse

from app.models.people.attendance import Attendance, AttendanceStatus
from app.models.people.leave import LeaveApplication
from app.services.people import self_service_web
from app.services.people.attendance import AttendanceService
from app.services.people.scheduling.resolver import ScheduleResolver
from app.web.deps import WebAuthContext


@pytest.mark.parametrize(
    ("roster_rows", "eligible_days", "monday_status"),
    [
        (
            [
                (date(2026, 4, 6), True),
                (date(2026, 4, 7), False),
                (date(2026, 4, 8), True),
                (date(2026, 4, 9), True),
            ],
            2,
            "Missed",
        ),
        ([(date(2026, 4, 7), False)], 0, "No scheduled shift"),
        ([], None, "No scheduled shift"),
    ],
)
def test_attendance_page_shares_published_dates_between_calendar_and_card(
    monkeypatch: pytest.MonkeyPatch,
    roster_rows: list[tuple[date, bool]],
    eligible_days: int | None,
    monday_status: str,
) -> None:
    organization_id = UUID(int=1)
    employee_id = UUID(int=2)
    auth = WebAuthContext(
        is_authenticated=True, organization_id=organization_id, person_id=UUID(int=3)
    )
    request = Request(
        {"type": "http", "path": "/people/self/attendance", "query_string": b""}
    )
    db = MagicMock()
    db.execute.return_value.all.return_value = roster_rows
    db.scalars.side_effect = [
        [Attendance(attendance_date=date(2026, 4, 8), status=AttendanceStatus.PRESENT)],
        [LeaveApplication(from_date=date(2026, 4, 9), to_date=date(2026, 4, 9))],
    ]
    service = self_service_web.SelfServiceWebService()
    monkeypatch.setattr(service, "_get_employee_id", lambda *_: employee_id)
    monkeypatch.setattr(service, "_get_holiday_map", lambda *_: {})
    monkeypatch.setattr(service, "_has_team_approvals", lambda *_, **__: False)
    monkeypatch.setattr(service, "_has_team_expense_approvals", lambda *_, **__: False)
    monkeypatch.setattr(self_service_web, "base_context", lambda *_, **__: {})
    monkeypatch.setattr(ScheduleResolver, "resolve_employee_shift", lambda *_: None)
    monkeypatch.setattr(
        AttendanceService, "get_org_now", lambda *_: datetime(2026, 4, 10, tzinfo=UTC)
    )
    monkeypatch.setattr(AttendanceService, "get_attendance_by_date", lambda *_: None)
    monkeypatch.setattr(
        AttendanceService, "list_attendance", lambda *_, **__: SimpleNamespace(items=[])
    )
    fallback = {
        "attendance_credit_days": Decimal("1.0"),
        "eligible_days": 30,
        "attendance_percentage": Decimal("3.33"),
    }
    monkeypatch.setattr(
        AttendanceService, "get_employee_monthly_summary", lambda *_: fallback
    )
    render = MagicMock(return_value=HTMLResponse())
    monkeypatch.setattr(self_service_web.templates, "TemplateResponse", render)

    response = service.attendance_response(request, auth, db, month="2026-04")

    assert response is render.return_value
    db.execute.assert_called_once()
    assert db.scalars.call_count == 2
    _, template_name, context = render.call_args.args
    assert template_name == "people/self/attendance.html"
    days = {
        day["date"]: day["status_label"]
        for week in context["calendar_weeks"]
        for day in week
    }
    assert days[date(2026, 4, 6)] == monday_status
    assert days[date(2026, 4, 7)] == "No scheduled shift"
    assert days[date(2026, 4, 8)] == "Present"
    assert days[date(2026, 4, 9)] == "On Leave"
    assert context["summary"] is fallback

    card = context["attendance_card"]
    if eligible_days is None:
        assert card is None
    else:
        assert card.eligible_days == eligible_days
        assert card.attendance_credit_days == Decimal("1.0" if eligible_days else "0.0")
        assert card.attendance_percentage == Decimal(
            "50.00" if eligible_days else "0.00"
        )
