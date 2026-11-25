from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.notification import Notification, NotificationChannel
from app.models.people.attendance import AttendanceRequest, AttendanceRequestStatus
from app.models.people.hr import (
    Department,
    Designation,
    Employee,
    EmployeeStatus,
    Position,
    PositionAssignment,
)
from app.models.person import Person
from app.models.rbac import PersonRole, Role
from app.services.common import ForbiddenError, ValidationError
from app.services.people.attendance import AttendanceService
from app.services.people.attendance.attendance_service import (
    AttendanceRequestNotFoundError,
    AttendanceRequestStatusError,
)

TODAY = date(2026, 11, 25)


@pytest.fixture
def review_setup(db_session, monkeypatch):
    connection = db_session.connection()
    schema_map = connection.get_execution_options()["schema_translate_map"]
    connection.execution_options(
        schema_translate_map={**schema_map, "attendance": None}
    )
    tables = (
        Department.__table__,
        Designation.__table__,
        Employee.__table__,
        Position.__table__,
        PositionAssignment.__table__,
        AttendanceRequest.__table__,
    )
    for table in tables:
        for column in table.columns:
            if "gen_random_uuid" in str(getattr(column.server_default, "arg", "")):
                monkeypatch.setattr(column, "server_default", None)
        table.create(connection, checkfirst=True)

    org_id = uuid4()
    employee = _employee(db_session, org_id, "Requester")
    manager = _employee(db_session, org_id, "Manager")
    hr = _employee(db_session, org_id, "HR")
    other_org_hr = _employee(db_session, uuid4(), "Other HR")

    role = db_session.scalar(select(Role).where(Role.name == "hr_manager"))
    if role is None:
        role = Role(name="hr_manager")
        db_session.add(role)
        db_session.flush()
    db_session.add_all(
        PersonRole(person_id=person_id, role_id=role.id)
        for person_id in (hr.person_id, other_org_hr.person_id)
    )
    monkeypatch.setattr(AttendanceService, "get_org_today", lambda *_: TODAY)
    db_session.flush()
    return org_id, employee, manager, hr


def _employee(db_session, org_id, name):
    person = Person(
        id=uuid4(),
        organization_id=org_id,
        first_name=name,
        last_name="Employee",
        email=f"{uuid4()}@example.com",
    )
    employee = Employee(
        organization_id=org_id,
        person_id=person.id,
        employee_code=uuid4().hex[:12],
        date_of_joining=TODAY,
        status=EmployeeStatus.ACTIVE,
    )
    db_session.add_all([person, employee])
    db_session.flush()
    return employee


def _set_reporting_line(db_session, org_id, employee, manager):
    parent = Position(
        position_id=uuid4(),
        organization_id=org_id,
        position_code=uuid4().hex[:12],
        position_name="Reporting Officer",
    )
    child = Position(
        position_id=uuid4(),
        organization_id=org_id,
        position_code=uuid4().hex[:12],
        position_name="Requester",
        parent_position_id=parent.position_id,
    )
    db_session.add_all([parent, child])
    db_session.flush()
    db_session.add_all(
        PositionAssignment(
            organization_id=org_id,
            employee_id=incumbent.employee_id,
            position_id=position.position_id,
            start_date=TODAY,
        )
        for incumbent, position in ((employee, child), (manager, parent))
    )
    db_session.flush()


def _submit(service, org_id, employee, reason="Late entry"):
    return service.create_attendance_exception(
        org_id,
        employee_id=employee.employee_id,
        requested_date=TODAY,
        reason=reason,
        explanation="Personal appointment",
        person_id=employee.person_id,
    )


def _notifications(db_session, org_id, request):
    return db_session.scalars(
        select(Notification).where(
            Notification.organization_id == org_id,
            Notification.entity_id == request.attendance_request_id,
        )
    ).all()


@pytest.mark.parametrize("reason", ["Early exit", "Late entry"])
def test_active_reporting_officer_decides_and_hr_gets_an_fyi(
    db_session, review_setup, reason
):
    org_id, employee, manager, hr = review_setup
    _set_reporting_line(db_session, org_id, employee, manager)
    service = AttendanceService(db_session)
    request = _submit(service, org_id, employee, reason)
    request_id = request.attendance_request_id
    notices = {
        notice.recipient_id: notice
        for notice in _notifications(db_session, org_id, request)
    }
    assert notices.keys() == {manager.person_id, hr.person_id}
    assert notices[manager.person_id].action_url.endswith(f"?request_id={request_id}")
    assert "Your review is needed." in notices[manager.person_id].message
    assert "awaiting the reporting officer's review" in notices[hr.person_id].message
    assert notices[hr.person_id].channel == NotificationChannel.IN_APP
    assert "Personal appointment" not in notices[hr.person_id].message

    routing = service.exception_routing(org_id)
    assert routing.manager_queue(manager.person_id) == [request]
    assert routing.manager_queue(hr.person_id) == []
    for person_id in (hr.person_id, employee.person_id):
        with pytest.raises(ForbiddenError):
            service.approve_attendance_request(
                org_id,
                request_id,
                person_id=person_id,
                has_hr_access=person_id == hr.person_id,
            )
    with pytest.raises(AttendanceRequestNotFoundError):
        service.approve_attendance_request(
            uuid4(), request_id, person_id=manager.person_id
        )

    service.approve_attendance_request(org_id, request_id, person_id=manager.person_id)
    assert request.status == AttendanceRequestStatus.APPROVED
    assert request.status_changed_by_id == manager.person_id
    with pytest.raises(AttendanceRequestStatusError):
        service.reject_attendance_request(
            org_id, request_id, person_id=manager.person_id
        )


def test_pending_review_moves_between_hr_and_reporting_officer(
    db_session, review_setup
):
    org_id, employee, manager, hr = review_setup
    service = AttendanceService(db_session)
    request = _submit(service, org_id, employee)
    request_id = request.attendance_request_id
    assert [
        note.recipient_id for note in _notifications(db_session, org_id, request)
    ] == [hr.person_id]
    assert service.exception_routing(org_id).can_decide(
        request, hr.person_id, has_hr_access=True
    )

    _set_reporting_line(db_session, org_id, employee, manager)
    with pytest.raises(ForbiddenError):
        service.reject_attendance_request(
            org_id, request_id, person_id=hr.person_id, has_hr_access=True
        )
    assert service.refresh_attendance_exception_reviews(org_id) == 1
    db_session.flush()
    assert service.refresh_attendance_exception_reviews(org_id) == 0

    manager.status = EmployeeStatus.SUSPENDED
    db_session.flush()
    with pytest.raises(ForbiddenError):
        service.approve_attendance_request(
            org_id, request_id, person_id=manager.person_id
        )
    assert service.refresh_attendance_exception_reviews(org_id) == 1
    db_session.flush()
    assert [
        note.recipient_id for note in _notifications(db_session, org_id, request)
    ].count(hr.person_id) == 2
    service.reject_attendance_request(
        org_id, request_id, person_id=hr.person_id, has_hr_access=True
    )
    assert request.status == AttendanceRequestStatus.REJECTED
    assert request.status_changed_by_id == hr.person_id

    manager.status = EmployeeStatus.ACTIVE
    db_session.flush()
    assert service.refresh_attendance_exception_reviews(org_id) == 0


def test_existing_pending_request_routes_around_disabled_manager_account(
    db_session, review_setup
):
    org_id, employee, manager, hr = review_setup
    _set_reporting_line(db_session, org_id, employee, manager)
    manager.person.is_active = False
    request = AttendanceRequest(
        organization_id=org_id,
        employee_id=employee.employee_id,
        from_date=TODAY,
        to_date=TODAY,
        reason="Attendance exception: Early exit",
        status=AttendanceRequestStatus.PENDING,
    )
    db_session.add(request)
    db_session.flush()

    service = AttendanceService(db_session)
    assert service.exception_routing(org_id).can_decide(
        request, hr.person_id, has_hr_access=True
    )
    assert service.refresh_attendance_exception_reviews(org_id) == 1
    assert {
        notice.recipient_id for notice in _notifications(db_session, org_id, request)
    } == {hr.person_id}

    manager.person.is_active = True
    db_session.flush()
    routing = service.exception_routing(org_id)
    assert not routing.can_decide(request, hr.person_id, has_hr_access=True)
    assert routing.manager_queue(manager.person_id) == [request]
    assert service.refresh_attendance_exception_reviews(org_id) == 1
    assert {
        notice.recipient_id for notice in _notifications(db_session, org_id, request)
    } == {
        hr.person_id,
        manager.person_id,
    }


def test_hr_module_access_is_enough_for_fallback_approval(db_session, review_setup):
    org_id, employee, _, hr = review_setup
    reviewer = _employee(db_session, org_id, "HR staff")
    service = AttendanceService(db_session)
    request = _submit(service, org_id, employee)
    request_id = request.attendance_request_id

    assert (
        reviewer.person_id
        not in service.exception_routing(org_id).hr_notification_recipient_ids
    )
    with pytest.raises(ForbiddenError):
        service.approve_attendance_request(
            org_id, request_id, person_id=reviewer.person_id
        )
    assert service.bulk_approve_attendance_requests(
        org_id, [request_id], person_id=reviewer.person_id, has_hr_access=True
    ) == {"updated": 1, "requested": 1}
    assert request.status_changed_by_id == reviewer.person_id
    assert [
        note.recipient_id for note in _notifications(db_session, org_id, request)
    ] == [hr.person_id]


def test_generic_request_mutations_cannot_bypass_exception_review(
    db_session, review_setup, monkeypatch
):
    org_id, employee, manager, hr = review_setup
    _set_reporting_line(db_session, org_id, employee, manager)
    service = AttendanceService(db_session)
    request = _submit(service, org_id, employee)
    request_id = request.attendance_request_id
    with pytest.raises(ForbiddenError):
        service.update_attendance_request(
            org_id, request_id, status=AttendanceRequestStatus.APPROVED
        )
    with pytest.raises(ForbiddenError):
        service.delete_attendance_request(org_id, request_id)
    with pytest.raises(ValidationError):
        service.create_attendance_request(
            org_id,
            employee_id=employee.employee_id,
            from_date=TODAY,
            to_date=TODAY,
            reason=request.reason,
        )
    correction = service.create_attendance_request(
        org_id,
        employee_id=employee.employee_id,
        from_date=TODAY,
        to_date=TODAY,
        reason="Missed punch",
    )
    with pytest.raises(ForbiddenError):
        service.update_attendance_request(
            org_id, correction.attendance_request_id, reason=request.reason
        )
    processed = []
    monkeypatch.setattr(
        service, "_process_attendance_request", lambda _, item: processed.append(item)
    )
    assert service.bulk_approve_attendance_requests(
        org_id,
        [request_id, correction.attendance_request_id],
        person_id=hr.person_id,
        has_hr_access=True,
    ) == {"updated": 1, "requested": 2}
    assert processed == [correction]
    assert request.status == AttendanceRequestStatus.PENDING
