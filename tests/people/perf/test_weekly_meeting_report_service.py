from __future__ import annotations

from datetime import date, time
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

from app.models.people.perf.weekly_meeting_report import (
    MeetingAttendanceStatus,
    MeetingParticipantSource,
    ReportEmailStatus,
    WeeklyMeetingParticipant,
    WeeklyMeetingReport,
    WeeklyMeetingReportStatus,
)
from app.services.people.perf.weekly_meeting_report_service import (
    DEFAULT_HR_REPORT_EMAIL,
    ParticipantInput,
    WeeklyMeetingReportError,
    WeeklyMeetingReportService,
)


def _report(*, organization_id: UUID | None = None) -> WeeklyMeetingReport:
    org_id = organization_id or uuid4()
    return WeeklyMeetingReport(
        report_id=uuid4(),
        organization_id=org_id,
        report_number="WMR-20260821-OPS",
        department_id=uuid4(),
        division_name_snapshot="Operations",
        week_ending=date(2026, 8, 21),
        meeting_date=date(2026, 8, 20),
        meeting_time=time(9, 0),
        prepared_by_person_id=uuid4(),
        prepared_by_name_snapshot="Report Owner",
        status=WeeklyMeetingReportStatus.DRAFT,
        notification_status=ReportEmailStatus.NOT_QUEUED,
        version=1,
    )


def _participant(
    report: WeeklyMeetingReport,
    *,
    employee_id: UUID | None,
    name: str,
    role: str,
    source: MeetingParticipantSource = MeetingParticipantSource.EMPLOYEE,
    role_overridden: bool = False,
) -> WeeklyMeetingParticipant:
    return WeeklyMeetingParticipant(
        participant_id=uuid4(),
        organization_id=report.organization_id,
        report_id=report.report_id,
        employee_id=employee_id,
        name_snapshot=name,
        role_snapshot=role,
        attendance_status=MeetingAttendanceStatus.PRESENT,
        source=source,
        role_overridden=role_overridden,
        sequence=len(report.participants),
    )


def test_refresh_merges_live_hr_data_without_losing_manual_changes() -> None:
    report = _report()
    report.division_head_employee_id = uuid4()
    report.division_head_name_snapshot = "Former Division Head"
    existing_employee_id = uuid4()
    new_employee_id = uuid4()
    overridden = _participant(
        report,
        employee_id=existing_employee_id,
        name="Old Employee Name",
        role="Meeting-specific role",
        role_overridden=True,
    )
    external = _participant(
        report,
        employee_id=None,
        name="External Adviser",
        role="Consultant",
        source=MeetingParticipantSource.EXTERNAL,
    )
    report.participants.extend([overridden, external])

    db = MagicMock()
    service = WeeklyMeetingReportService(db)
    service.get_report = MagicMock(return_value=report)  # type: ignore[method-assign]
    service.department_roster = MagicMock(  # type: ignore[method-assign]
        return_value={
            "head": None,
            "participants": [
                {
                    "employee_id": str(existing_employee_id),
                    "name": "Current Employee Name",
                    "role": "Current HR Role",
                },
                {
                    "employee_id": str(new_employee_id),
                    "name": "New Employee",
                    "role": "Network Engineer",
                },
            ],
        }
    )

    service.refresh_from_hr(report.organization_id, report.report_id, uuid4())

    assert len(report.participants) == 3
    assert overridden.name_snapshot == "Current Employee Name"
    assert overridden.role_snapshot == "Meeting-specific role"
    assert overridden.attendance_status == MeetingAttendanceStatus.PRESENT
    assert external in report.participants
    added = next(
        item for item in report.participants if item.employee_id == new_employee_id
    )
    assert added.name_snapshot == "New Employee"
    assert added.role_snapshot == "Network Engineer"
    assert added.attendance_status == MeetingAttendanceStatus.INVITED
    assert report.division_head_employee_id is None
    assert report.division_head_name_snapshot is None
    assert report.hr_refreshed_at is not None
    db.flush.assert_called_once()


def test_replacing_participants_deletes_old_rows_before_reusing_employee() -> None:
    report = _report()
    employee_id = uuid4()
    old = _participant(
        report,
        employee_id=employee_id,
        name="Previous Name",
        role="Previous Role",
    )
    report.participants.append(old)
    employee = SimpleNamespace(employee_id=employee_id, full_name="Current Name")

    db = MagicMock()
    service = WeeklyMeetingReportService(db)
    service._get_employee = MagicMock(return_value=employee)  # type: ignore[method-assign]
    service._employee_option = MagicMock(  # type: ignore[method-assign]
        return_value={"role": "Current Role"}
    )

    service._replace_participants(
        report.organization_id,
        report,
        [
            ParticipantInput(
                employee_id=employee_id,
                name="Ignored stale name",
                role="Ignored stale role",
                attendance_status=MeetingAttendanceStatus.EXCUSED,
                source=MeetingParticipantSource.EMPLOYEE,
                role_overridden=False,
            )
        ],
        uuid4(),
    )

    db.delete.assert_called_once_with(old)
    assert db.flush.call_count == 1
    assert len(report.participants) == 1
    assert report.participants[0].name_snapshot == "Current Name"
    assert report.participants[0].role_snapshot == "Current Role"
    assert report.participants[0].attendance_status == MeetingAttendanceStatus.EXCUSED


def test_submit_locks_snapshot_and_uses_configured_hr_recipient() -> None:
    report = _report()
    report.participants.append(
        _participant(report, employee_id=uuid4(), name="Ada Employee", role="Engineer")
    )
    submitter_id = uuid4()
    db = MagicMock()
    db.scalar.return_value = SimpleNamespace(
        hr_weekly_report_email=" people-ops@example.com "
    )
    service = WeeklyMeetingReportService(db)
    service.get_report = MagicMock(return_value=report)  # type: ignore[method-assign]

    submitted = service.submit(report.organization_id, report.report_id, submitter_id)

    assert submitted.status == WeeklyMeetingReportStatus.SUBMITTED
    assert submitted.submitted_by_id == submitter_id
    assert submitted.submitted_at is not None
    assert submitted.notification_recipient == "people-ops@example.com"
    assert submitted.notification_status == ReportEmailStatus.PENDING
    assert submitted.version == 2
    db.flush.assert_called_once()


def test_submit_uses_hr_default_and_rejects_invalid_override() -> None:
    report = _report()
    report.participants.append(
        _participant(report, employee_id=uuid4(), name="Ada Employee", role="Engineer")
    )
    db = MagicMock()
    service = WeeklyMeetingReportService(db)
    service.get_report = MagicMock(return_value=report)  # type: ignore[method-assign]

    db.scalar.return_value = SimpleNamespace(hr_weekly_report_email=None)
    service.submit(report.organization_id, report.report_id, uuid4())
    assert report.notification_recipient == DEFAULT_HR_REPORT_EMAIL

    invalid_report = _report(organization_id=report.organization_id)
    invalid_report.participants.append(
        _participant(
            invalid_report,
            employee_id=uuid4(),
            name="Another Employee",
            role="Engineer",
        )
    )
    service.get_report = MagicMock(  # type: ignore[method-assign]
        return_value=invalid_report
    )
    db.scalar.return_value = SimpleNamespace(hr_weekly_report_email="invalid address")

    with pytest.raises(WeeklyMeetingReportError, match="valid weekly report recipient"):
        service.submit(
            invalid_report.organization_id, invalid_report.report_id, uuid4()
        )

    assert invalid_report.status == WeeklyMeetingReportStatus.DRAFT
    assert invalid_report.notification_status == ReportEmailStatus.NOT_QUEUED


def test_notification_email_validation_is_deliberately_single_recipient() -> None:
    assert WeeklyMeetingReportService._is_valid_notification_email("hr@dotmac.ng")
    assert not WeeklyMeetingReportService._is_valid_notification_email("hr@dotmac")
    assert not WeeklyMeetingReportService._is_valid_notification_email(
        "hr@dotmac.ng,manager@dotmac.ng"
    )


def test_reopen_requires_a_submitted_report() -> None:
    report = _report()
    service = WeeklyMeetingReportService(MagicMock())
    service.get_report = MagicMock(return_value=report)  # type: ignore[method-assign]

    with pytest.raises(WeeklyMeetingReportError, match="Only submitted reports"):
        service.reopen(report.organization_id, report.report_id, uuid4())
