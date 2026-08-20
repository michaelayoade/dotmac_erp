from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

from app.models.notification import EntityType, NotificationChannel, NotificationType
from app.models.person import Person
from app.services.people.leave.leave_service import LeaveService


def test_notify_leave_submitted_creates_both_channel_notifications_for_hr_managers():
    db = MagicMock()
    auth = SimpleNamespace(
        person_id=UUID("00000000-0000-0000-0000-000000000099"),
    )
    service = LeaveService(db, auth)
    org_id = uuid4()
    application_id = uuid4()
    employee_id = uuid4()
    hr_manager_1 = SimpleNamespace(id=uuid4())
    hr_manager_2 = SimpleNamespace(id=uuid4())
    application = SimpleNamespace(
        application_id=application_id,
        application_number="LVE-0001",
        employee_id=employee_id,
        from_date=date(2026, 4, 28),
        to_date=date(2026, 4, 30),
    )
    employee = SimpleNamespace(full_name="Jane Doe")

    with (
        patch.object(
            service,
            "_get_hr_manager_recipients",
            return_value=[hr_manager_1, hr_manager_2],
        ),
        patch(
            "app.services.people.leave.leave_service.NotificationService.create"
        ) as create,
    ):
        db.get.return_value = employee

        service._notify_leave_submitted(org_id, application)

    assert create.call_count == 2
    first_call = create.call_args_list[0]
    assert first_call.args[0] is db
    assert first_call.kwargs["organization_id"] == org_id
    assert first_call.kwargs["recipient_id"] == hr_manager_1.id
    assert first_call.kwargs["entity_type"] == EntityType.LEAVE
    assert first_call.kwargs["entity_id"] == application_id
    assert first_call.kwargs["notification_type"] == NotificationType.SUBMITTED
    assert first_call.kwargs["title"] == "Leave application submitted"
    assert "Jane Doe submitted leave request LVE-0001" in first_call.kwargs["message"]
    assert first_call.kwargs["channel"] == NotificationChannel.BOTH
    assert (
        first_call.kwargs["action_url"]
        == f"/people/leave/applications/{application_id}"
    )
    assert first_call.kwargs["actor_id"] == auth.person_id


def test_notify_leave_submitted_skips_when_no_hr_managers():
    db = MagicMock()
    service = LeaveService(db)
    application = SimpleNamespace(
        application_id=uuid4(),
        application_number="LVE-0002",
        employee_id=uuid4(),
        from_date=date(2026, 5, 1),
        to_date=date(2026, 5, 2),
    )

    with (
        patch.object(service, "_get_hr_manager_recipients", return_value=[]),
        patch(
            "app.services.people.leave.leave_service.NotificationService.create"
        ) as create,
    ):
        service._notify_leave_submitted(uuid4(), application)

    create.assert_not_called()


def test_get_hr_manager_recipients_returns_unique_people():
    db = MagicMock()
    service = LeaveService(db)
    org_id = uuid4()
    person_id = uuid4()
    person = Person(
        id=person_id,
        organization_id=org_id,
    )

    db.scalars.side_effect = [
        SimpleNamespace(all=lambda: [person_id, person_id]),
        SimpleNamespace(all=lambda: [person]),
    ]

    recipients = service._get_hr_manager_recipients(org_id)

    assert recipients == [person]
    assert db.scalars.call_count == 2


def test_get_hr_manager_recipients_skips_person_distinct_on_json_rows():
    db = MagicMock()
    service = LeaveService(db)
    org_id = uuid4()
    person_id = uuid4()
    person = Person(
        id=person_id,
        organization_id=org_id,
    )

    db.scalars.side_effect = [
        SimpleNamespace(all=lambda: [person_id]),
        SimpleNamespace(all=lambda: [person]),
    ]

    recipients = service._get_hr_manager_recipients(org_id)

    assert recipients == [person]
    first_stmt = db.scalars.call_args_list[0].args[0]
    second_stmt = db.scalars.call_args_list[1].args[0]
    first_sql = str(first_stmt)
    second_sql = str(second_stmt)

    assert "SELECT DISTINCT people.id" in first_sql
    assert "SELECT people.id, people.organization_id" in second_sql


def test_create_application_triggers_hr_manager_notifications():
    db = MagicMock()
    service = LeaveService(db)
    org_id = uuid4()
    employee_id = uuid4()
    leave_type_id = uuid4()
    application_id = uuid4()
    application_numbers = iter(["LVE-0003"])

    leave_type = SimpleNamespace(include_holidays=False, is_lwp=False)
    created_application = {}

    def add_side_effect(obj):
        created_application["application"] = obj

    def flush_side_effect():
        application = created_application.get("application")
        if (
            application is not None
            and getattr(application, "application_id", None) is None
        ):
            application.application_id = application_id

    db.add.side_effect = add_side_effect
    db.flush.side_effect = flush_side_effect
    db.scalar.return_value = None

    with (
        patch.object(service, "get_leave_type", return_value=leave_type),
        patch.object(service, "calculate_leave_days", return_value=Decimal("3")),
        patch.object(service, "_validate_service_eligibility"),
        patch.object(service, "get_employee_balance", return_value=Decimal("10")),
        patch("app.services.people.discipline.DisciplineService") as discipline_service,
        patch.object(
            service,
            "_next_application_number",
            side_effect=lambda _: next(application_numbers),
        ),
        patch.object(service, "_notify_leave_submitted") as notify_leave_submitted,
        patch("app.services.people.leave.leave_service.fire_audit_event"),
    ):
        discipline_service.return_value.has_active_investigation.return_value = False

        application = service.create_application(
            org_id,
            employee_id=employee_id,
            leave_type_id=leave_type_id,
            from_date=date(2026, 5, 4),
            to_date=date(2026, 5, 6),
            reason="Travel",
        )

    assert application is created_application["application"]
    notify_leave_submitted.assert_called_once_with(org_id, application)
    assert application.status.value == "SUBMITTED"
