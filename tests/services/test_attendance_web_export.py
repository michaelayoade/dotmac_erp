from __future__ import annotations

import csv
import io
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID
from zoneinfo import ZoneInfo

from app.models.people.attendance import AttendanceStatus
from app.services.common import PaginatedResult
from app.services.people.attendance import web as attendance_web
from app.services.people.attendance.web import AttendanceWebService


ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
EMPLOYEE_ID = UUID("00000000-0000-0000-0000-000000000002")


def test_export_attendance_csv_uses_filters_and_exports_all_rows(monkeypatch) -> None:
    record = SimpleNamespace(
        attendance_date=date(2026, 8, 3),
        employee=SimpleNamespace(
            full_name="Ada Lovelace",
            employee_code="EMP-001",
        ),
        shift_type=SimpleNamespace(shift_name="Day Shift"),
        check_in=datetime(2026, 8, 3, 7, 30, tzinfo=UTC),
        check_out=datetime(2026, 8, 3, 16, 45, tzinfo=UTC),
        working_hours=Decimal("9.25"),
        overtime_hours=Decimal("1.25"),
        status=AttendanceStatus.PRESENT,
    )

    class FakeAttendanceService:
        def __init__(self, db) -> None:
            self.db = db

        def list_attendance(self, org_id, **kwargs):
            assert org_id == ORG_ID
            assert kwargs == {
                "employee_id": EMPLOYEE_ID,
                "from_date": date(2026, 8, 1),
                "to_date": date(2026, 8, 3),
                "status": AttendanceStatus.PRESENT,
                "pagination": None,
            }
            return PaginatedResult(items=[record], total=1)

        def get_org_tzinfo(self, org_id):
            assert org_id == ORG_ID
            return ZoneInfo("Africa/Lagos")

        def get_org_today(self, org_id):
            assert org_id == ORG_ID
            return date(2026, 8, 3)

    monkeypatch.setattr(attendance_web, "AttendanceService", FakeAttendanceService)

    response = AttendanceWebService.export_attendance_csv_response(
        auth=SimpleNamespace(organization_id=ORG_ID),
        db=SimpleNamespace(),
        status="PRESENT",
        start_date="2026-08-01",
        end_date="2026-08-03",
        employee_id=str(EMPLOYEE_ID),
    )

    rows = list(csv.reader(io.StringIO(response.body.decode())))
    assert rows == [
        [
            "Date",
            "Employee",
            "Employee Code",
            "Shift",
            "Check In",
            "Check Out",
            "Hours",
            "Overtime",
            "Status",
        ],
        [
            "2026-08-03",
            "Ada Lovelace",
            "EMP-001",
            "Day Shift",
            "08:30",
            "17:45",
            "9.25",
            "1.25",
            "PRESENT",
        ],
    ]
    assert response.media_type == "text/csv; charset=utf-8"
    assert (
        response.headers["Content-Disposition"]
        == 'attachment; filename="attendance_records_20260803.csv"'
    )


def test_export_attendance_csv_escapes_spreadsheet_formulas() -> None:
    assert AttendanceWebService._csv_safe_cell("=cmd|calc") == "'=cmd|calc"
    assert AttendanceWebService._csv_safe_cell("+SUM(A1:A2)") == "'+SUM(A1:A2)"
    assert AttendanceWebService._csv_safe_cell("Ada Lovelace") == "Ada Lovelace"
