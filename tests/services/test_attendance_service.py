from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.models.people.attendance import AttendanceStatus
from app.services.common import ValidationError
from app.services.people.attendance import AttendanceService, CheckInRequirementError
from app.services.people.attendance.attendance_service import AttendanceServiceError


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


def test_dob_requirement_off_does_not_load_employee(monkeypatch) -> None:
    service, db = _make_service()
    monkeypatch.setattr(
        "app.services.people.attendance.attendance_service.resolve_value",
        lambda *_args, **_kwargs: False,
    )

    service._validate_check_in_requirements(ORG_ID, EMPLOYEE_ID)

    db.scalar.assert_not_called()


def test_dob_requirement_on_accepts_employee_with_dob(monkeypatch) -> None:
    service, db = _make_service()
    monkeypatch.setattr(
        "app.services.people.attendance.attendance_service.resolve_value",
        lambda *_args, **_kwargs: True,
    )
    db.scalar.return_value = SimpleNamespace(
        person=SimpleNamespace(date_of_birth=date(1990, 1, 1))
    )

    service._validate_check_in_requirements(ORG_ID, EMPLOYEE_ID)

    db.scalar.assert_called_once()


def test_dob_requirement_on_rejects_before_attendance_mutation(monkeypatch) -> None:
    service, db = _make_service()
    monkeypatch.setattr(
        "app.services.people.attendance.attendance_service.resolve_value",
        lambda *_args, **_kwargs: True,
    )
    db.scalar.return_value = SimpleNamespace(person=SimpleNamespace(date_of_birth=None))

    with pytest.raises(CheckInRequirementError) as exc_info:
        service.check_in(ORG_ID, EMPLOYEE_ID)

    assert exc_info.value.code == "ERP_DOB_REQUIRED_FOR_CHECKIN"
    assert exc_info.value.action == "UPDATE_ERP_PROFILE"
    db.add.assert_not_called()
    db.flush.assert_not_called()


def test_dob_requirement_does_not_treat_missing_person_as_missing_dob(
    monkeypatch,
) -> None:
    service, db = _make_service()
    monkeypatch.setattr(
        "app.services.people.attendance.attendance_service.resolve_value",
        lambda *_args, **_kwargs: True,
    )
    db.scalar.return_value = SimpleNamespace(person=None)

    with pytest.raises(AttendanceServiceError, match="personal record is unavailable"):
        service._validate_check_in_requirements(ORG_ID, EMPLOYEE_ID)


def test_dob_requirement_does_not_treat_missing_employee_as_missing_dob(
    monkeypatch,
) -> None:
    service, db = _make_service()
    monkeypatch.setattr(
        "app.services.people.attendance.attendance_service.resolve_value",
        lambda *_args, **_kwargs: True,
    )
    db.scalar.return_value = None

    with pytest.raises(AttendanceServiceError, match="Employee record not found"):
        service._validate_check_in_requirements(ORG_ID, EMPLOYEE_ID)


def test_dob_requirement_reports_invalid_employee_data_separately(
    monkeypatch,
) -> None:
    service, db = _make_service()
    monkeypatch.setattr(
        "app.services.people.attendance.attendance_service.resolve_value",
        lambda *_args, **_kwargs: True,
    )
    db.scalar.return_value = SimpleNamespace(
        person=SimpleNamespace(date_of_birth="not-a-date")
    )

    with pytest.raises(ValidationError, match="Date of Birth is invalid") as exc_info:
        service._validate_check_in_requirements(ORG_ID, EMPLOYEE_ID)

    assert not isinstance(exc_info.value, CheckInRequirementError)


def test_dob_requirement_propagates_employee_lookup_failure(monkeypatch) -> None:
    service, db = _make_service()
    monkeypatch.setattr(
        "app.services.people.attendance.attendance_service.resolve_value",
        lambda *_args, **_kwargs: True,
    )
    db.scalar.side_effect = RuntimeError("database unavailable")

    with pytest.raises(RuntimeError, match="database unavailable"):
        service._validate_check_in_requirements(ORG_ID, EMPLOYEE_ID)


def test_turning_dob_requirement_off_immediately_removes_restriction(
    monkeypatch,
) -> None:
    service, db = _make_service()
    enabled = iter((True, False))
    monkeypatch.setattr(
        "app.services.people.attendance.attendance_service.resolve_value",
        lambda *_args, **_kwargs: next(enabled),
    )
    db.scalar.return_value = SimpleNamespace(person=SimpleNamespace(date_of_birth=None))

    with pytest.raises(CheckInRequirementError):
        service._validate_check_in_requirements(ORG_ID, EMPLOYEE_ID)

    service._validate_check_in_requirements(ORG_ID, EMPLOYEE_ID)
    db.scalar.assert_called_once()


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


def test_check_in_resolves_employee_shift_and_marks_late_arrival(monkeypatch) -> None:
    service, db = _make_service()
    monkeypatch.setattr(
        "app.services.people.attendance.attendance_service.resolve_value",
        lambda *_args, **_kwargs: True,
    )
    db.scalar.return_value = SimpleNamespace(
        person=SimpleNamespace(date_of_birth=date(1990, 1, 1))
    )
    monkeypatch.setattr(
        "app.services.people.attendance.attendance_service.ScheduleResolver",
        lambda db: SimpleNamespace(resolve_employee_shift=lambda *_args: None),
    )
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


def test_check_in_by_attendance_id_resolves_missing_shift(monkeypatch) -> None:
    service, db = _make_service()
    monkeypatch.setattr(
        "app.services.people.attendance.attendance_service.resolve_value",
        lambda *_args, **_kwargs: False,
    )
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


def test_check_in_by_attendance_id_cannot_bypass_dob_requirement(
    monkeypatch,
) -> None:
    service, db = _make_service()
    attendance = SimpleNamespace(
        employee_id=EMPLOYEE_ID,
        check_in=None,
    )
    service.get_attendance = MagicMock(return_value=attendance)  # type: ignore[method-assign]
    monkeypatch.setattr(
        "app.services.people.attendance.attendance_service.resolve_value",
        lambda *_args, **_kwargs: True,
    )
    db.scalar.return_value = SimpleNamespace(person=SimpleNamespace(date_of_birth=None))

    with pytest.raises(CheckInRequirementError):
        service.check_in_by_attendance_id(
            ORG_ID,
            uuid.UUID("00000000-0000-0000-0000-000000000004"),
        )

    assert attendance.check_in is None
    db.flush.assert_not_called()


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


def test_check_in_links_published_schedule_assignment(monkeypatch) -> None:
    service, db = _make_service()
    shift_id = uuid.UUID("00000000-0000-0000-0000-000000000003")
    shift_schedule_id = uuid.UUID("00000000-0000-0000-0000-000000000004")
    work_schedule_id = uuid.UUID("00000000-0000-0000-0000-000000000005")
    shift = SimpleNamespace(
        shift_type_id=shift_id,
        start_time=time(22, 0),
        late_entry_grace_period=0,
    )
    assignment = SimpleNamespace(
        shift_schedule_id=shift_schedule_id,
        shift_type_id=shift_id,
        shift_type=shift,
        shift_date=date(2026, 8, 3),
    )
    schedule = SimpleNamespace(work_schedule_id=work_schedule_id)
    resolved = SimpleNamespace(assignment=assignment, schedule=schedule)
    monkeypatch.setattr(
        "app.services.people.attendance.attendance_service.ScheduleResolver",
        lambda db: SimpleNamespace(resolve_employee_shift=lambda *_args: resolved),
    )
    service.get_attendance_by_date = MagicMock(return_value=None)  # type: ignore[method-assign]
    service.get_employee_shift = MagicMock()  # type: ignore[method-assign]
    service._validate_geofence = MagicMock()  # type: ignore[method-assign]
    service._normalize_in_org_tz = lambda _org_id, value: value  # type: ignore[method-assign]

    attendance = service.check_in(
        ORG_ID,
        EMPLOYEE_ID,
        check_in_time=datetime(2026, 8, 3, 22, 5, tzinfo=UTC),
    )

    service.get_attendance_by_date.assert_called_once_with(
        ORG_ID,
        EMPLOYEE_ID,
        date(2026, 8, 3),
    )
    service.get_employee_shift.assert_not_called()
    assert attendance.shift_type_id == shift_id
    assert attendance.shift_schedule_id == shift_schedule_id
    assert attendance.work_schedule_id == work_schedule_id
    db.add.assert_called_once_with(attendance)
