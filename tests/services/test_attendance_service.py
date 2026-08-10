from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.models.people.attendance import AttendanceStatus
from app.services.people.attendance import AttendanceService


ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
EMPLOYEE_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")


def _make_service() -> tuple[AttendanceService, MagicMock]:
    db = MagicMock()
    return AttendanceService(db), db


def _attendance_record(
    attendance_date: date,
    status: AttendanceStatus,
    *,
    working_hours: Decimal = Decimal("8.0"),
    late_entry: bool = False,
    early_exit: bool = False,
):
    return SimpleNamespace(
        attendance_date=attendance_date,
        status=status,
        working_hours=working_hours,
        late_entry=late_entry,
        early_exit=early_exit,
    )


def test_monthly_summary_excludes_leave_days_from_percentage() -> None:
    service, db = _make_service()
    db.scalars.return_value.all.return_value = [
        _attendance_record(date(2026, 4, 1), AttendanceStatus.PRESENT),
        _attendance_record(date(2026, 4, 2), AttendanceStatus.PRESENT),
        _attendance_record(date(2026, 4, 3), AttendanceStatus.HALF_DAY),
        _attendance_record(date(2026, 4, 4), AttendanceStatus.ON_LEAVE),
    ]

    summary = service.get_employee_monthly_summary(ORG_ID, EMPLOYEE_ID, 2026, 4)

    assert summary["on_leave"] == 1
    assert summary["attendance_percentage"] == Decimal("8.62")


def test_summary_report_excludes_leave_days_from_percentage() -> None:
    service, db = _make_service()
    db.get.return_value = SimpleNamespace(timezone="UTC")
    db.execute.return_value.one.return_value = SimpleNamespace(
        total_records=10,
        present=6,
        absent=1,
        half_day=2,
        on_leave=1,
        late_entries=0,
        early_exits=0,
        total_working_hours=Decimal("64"),
        total_overtime_hours=Decimal("0"),
    )

    report = service.get_attendance_summary_report(
        ORG_ID,
        start_date=date(2026, 4, 1),
        end_date=date(2026, 4, 30),
    )

    assert report["on_leave"] == 1
    assert report["attendance_percentage"] == Decimal("77.8")


def test_by_employee_report_excludes_leave_days_from_percentage() -> None:
    service, db = _make_service()
    db.get.return_value = SimpleNamespace(timezone="UTC")
    db.execute.return_value.all.return_value = [
        SimpleNamespace(
            employee_id=EMPLOYEE_ID,
            employee_name="Ada Lovelace",
            department_name="Engineering",
            total_days=10,
            present=6,
            absent=1,
            half_day=2,
            on_leave=1,
            late_entries=0,
            early_exits=0,
            total_hours=Decimal("64"),
            overtime_hours=Decimal("0"),
        )
    ]

    report = service.get_attendance_by_employee_report(
        ORG_ID,
        start_date=date(2026, 4, 1),
        end_date=date(2026, 4, 30),
    )

    assert report["employees"][0]["on_leave"] == 1
    assert report["employees"][0]["attendance_percentage"] == Decimal("77.8")


def test_trends_report_excludes_leave_days_from_monthly_and_average_percentages() -> (
    None
):
    service, db = _make_service()
    db.get.return_value = SimpleNamespace(timezone="UTC")
    service.get_org_today = lambda _org_id: date(2026, 5, 15)  # type: ignore[method-assign]
    db.execute.return_value.all.return_value = [
        SimpleNamespace(
            month=datetime(2026, 4, 1),
            total_records=10,
            present=6,
            absent=1,
            half_day=2,
            on_leave=1,
            late_entries=0,
            total_hours=Decimal("64"),
        )
    ]

    report = service.get_attendance_trends_report(ORG_ID, months=1)

    assert report["months"][0]["on_leave"] == 1
    assert report["months"][0]["attendance_percentage"] == Decimal("77.8")
    assert report["average_attendance_percentage"] == Decimal("77.8")


def test_check_in_resolves_employee_shift_and_marks_late_arrival() -> None:
    service, db = _make_service()
    shift_id = uuid.UUID("00000000-0000-0000-0000-000000000003")
    shift = SimpleNamespace(
        shift_type_id=shift_id,
        start_time=time(8, 0),
        late_entry_grace_period=15,
    )
    service.get_attendance_by_date = MagicMock(  # type: ignore[method-assign]
        side_effect=[None, None]
    )
    service.get_employee_shift = MagicMock(  # type: ignore[method-assign]
        return_value=shift
    )
    service._validate_geofence = MagicMock()  # type: ignore[method-assign]
    service._normalize_in_org_tz = (  # type: ignore[method-assign]
        lambda _org_id, value: value
    )
    service._validate_geofence = MagicMock()  # type: ignore[method-assign]

    attendance = service.check_in(
        ORG_ID,
        EMPLOYEE_ID,
        check_in_time=datetime(2026, 8, 3, 8, 16, tzinfo=UTC),
    )

    service.get_employee_shift.assert_called_once_with(
        ORG_ID, EMPLOYEE_ID, date(2026, 8, 3)
    )
    assert attendance.shift_type_id == shift_id
    assert attendance.late_entry is True
    assert attendance.late_entry_minutes == 1
    db.add.assert_called_once_with(attendance)


def test_check_in_by_attendance_id_resolves_missing_shift() -> None:
    service, db = _make_service()
    attendance_id = uuid.UUID("00000000-0000-0000-0000-000000000004")
    shift_id = uuid.UUID("00000000-0000-0000-0000-000000000003")
    attendance = SimpleNamespace(
        attendance_id=attendance_id,
        employee_id=EMPLOYEE_ID,
        attendance_date=date(2026, 8, 3),
        shift_type_id=None,
        check_in=None,
        late_entry=False,
        late_entry_minutes=0,
        status=AttendanceStatus.ABSENT,
        remarks=None,
    )
    shift = SimpleNamespace(
        shift_type_id=shift_id,
        start_time=time(8, 0),
        late_entry_grace_period=10,
    )
    service.get_attendance = MagicMock(return_value=attendance)  # type: ignore[method-assign]
    service.get_employee_shift = MagicMock(return_value=shift)  # type: ignore[method-assign]

    result = service.check_in_by_attendance_id(
        ORG_ID,
        attendance_id,
        check_in_time=datetime(2026, 8, 3, 8, 25, tzinfo=UTC),
    )

    assert result.shift_type_id == shift_id
    assert result.late_entry is True
    assert result.late_entry_minutes == 15
    assert result.status == AttendanceStatus.PRESENT
    db.flush.assert_called_once()


def test_duplicate_checkout_preserves_original_checkout_and_hours() -> None:
    service, db = _make_service()
    original_checkout = datetime(2026, 8, 3, 17, 0, tzinfo=UTC)
    attendance = SimpleNamespace(
        check_in=datetime(2026, 8, 3, 8, 0, tzinfo=UTC),
        check_out=original_checkout,
        working_hours=Decimal("9.0"),
        shift_type_id=None,
        early_exit=False,
        remarks=None,
    )
    service.get_attendance_by_date = MagicMock(  # type: ignore[method-assign]
        return_value=attendance
    )
    service._normalize_in_org_tz = (  # type: ignore[method-assign]
        lambda _org_id, value: value
    )
    service._validate_geofence = MagicMock()  # type: ignore[method-assign]

    result = service.check_out(
        ORG_ID,
        EMPLOYEE_ID,
        check_out_time=datetime(2026, 8, 3, 17, 15, tzinfo=UTC),
    )

    service.get_attendance_by_date.assert_called_once_with(
        ORG_ID,
        EMPLOYEE_ID,
        date(2026, 8, 3),
        for_update=True,
    )
    assert result.check_out == original_checkout
    assert result.working_hours == Decimal("9.0")
    service._validate_geofence.assert_not_called()
    db.flush.assert_not_called()
