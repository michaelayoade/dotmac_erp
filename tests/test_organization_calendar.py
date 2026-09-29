"""Focused contracts for the ERP organization calendar slice."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.models.organization_calendar import (
    CalendarBusinessStatus,
    CalendarEventScope,
    OrganizationCalendarEvent,
    OrganizationCalendarReminder,
)
from app.models.notification import Notification, NotificationChannel
from app.models.people.hr.employee import EmployeeStatus
from app.models.person import PersonStatus
from app.services.organization_calendar import (
    CalendarEventData,
    OrganizationCalendarService,
    ParticipantCandidate,
    build_event_data,
)

ROOT = Path(__file__).resolve().parents[1]


def test_timed_event_converts_lagos_local_time_to_utc() -> None:
    result = build_event_data(
        title="Finance review",
        description=None,
        event_details=None,
        location=None,
        meeting_url=None,
        timezone_name="Africa/Lagos",
        all_day=False,
        start_date_value=date(2026, 9, 21),
        end_date_value=date(2026, 9, 21),
        start_time_value=time(9, 0),
        end_time_value=time(10, 30),
        color="#4F46E5",
    )

    assert result.start_at.isoformat() == "2026-09-21T08:00:00+00:00"
    assert result.end_at.isoformat() == "2026-09-21T09:30:00+00:00"
    assert result.start_at.tzinfo == UTC
    assert result.start_date is None


def test_all_day_event_uses_an_exclusive_date_end() -> None:
    result = build_event_data(
        title="Company day",
        description=None,
        event_details=None,
        location=None,
        meeting_url=None,
        timezone_name="Africa/Lagos",
        all_day=True,
        start_date_value=date(2026, 9, 21),
        end_date_value=date(2026, 9, 23),
        start_time_value=None,
        end_time_value=None,
        color="#4F46E5",
    )

    assert result.start_date == date(2026, 9, 21)
    assert result.end_date_exclusive == date(2026, 9, 24)
    assert result.start_at is None
    assert result.end_at is None


def test_all_day_reminder_uses_event_timezone() -> None:
    event = OrganizationCalendarEvent(
        event_id=uuid4(),
        organization_id=uuid4(),
        ical_uid="erp-event-test@dotmac.ng",
        title="Company day",
        timezone="Africa/Lagos",
        all_day=True,
        start_date=date(2026, 9, 21),
        end_date_exclusive=date(2026, 9, 22),
        color="#4F46E5",
        business_status=CalendarBusinessStatus.PUBLISHED.value,
        version=1,
        created_by_id=uuid4(),
        updated_by_id=uuid4(),
    )

    scheduled = OrganizationCalendarService._reminder_time(event, 60)

    assert scheduled.isoformat() == "2026-09-20T22:00:00+00:00"


def test_reminder_edit_does_not_repeat_fired_or_expired_periods() -> None:
    now = datetime.now(UTC)
    organization_id = uuid4()
    event = OrganizationCalendarEvent(
        event_id=uuid4(),
        organization_id=organization_id,
        ical_uid="reminder-edit@example.test",
        title="Upcoming event",
        timezone="UTC",
        all_day=False,
        start_at=now + timedelta(days=2),
        end_at=now + timedelta(days=2, hours=1),
        color="#4F46E5",
        business_status=CalendarBusinessStatus.PUBLISHED.value,
        version=1,
        created_by_id=uuid4(),
        updated_by_id=uuid4(),
    )
    fired_at = now - timedelta(days=1)
    event.reminders.append(
        OrganizationCalendarReminder(
            organization_id=organization_id,
            offset_minutes=10080,
            scheduled_for=event.start_at - timedelta(days=7),
            dispatched_at=fired_at,
        )
    )
    service = OrganizationCalendarService(_CalendarUnitOfWork(), organization_id)

    service._replace_reminders(event, [10080, 1440])

    by_offset = {item.offset_minutes: item for item in event.reminders}
    assert by_offset[10080].dispatched_at == fired_at
    assert by_offset[1440].dispatched_at is None

    event.reminders.clear()
    service._replace_reminders(event, [10080, 1440])
    assert (
        next(
            item for item in event.reminders if item.offset_minutes == 10080
        ).dispatched_at
        is not None
    )

    event.start_at += timedelta(days=10)
    event.end_at += timedelta(days=10)
    service._replace_reminders(event, [10080])
    assert event.reminders[0].dispatched_at is None


def test_talk_reminder_migration_is_the_erp_head() -> None:
    migration = (
        ROOT / "alembic" / "versions" / "20260920_calendar_talk_notifications.py"
    ).read_text(encoding="utf-8")
    descriptor = (ROOT / "deploy" / "product.toml").read_text(encoding="utf-8")
    assert 'down_revision = "20260919_self_calendar"' in migration
    assert 'revision = "20260920_calendar_talk"' in migration
    assert '"20260920_calendar_talk"' in descriptor
    assert "scheduled_for" in migration
    assert "dispatched_at" in migration


def test_calendar_permissions_and_roles_are_provisioned_by_migration() -> None:
    migration = (
        ROOT / "alembic" / "versions" / "20260919_organization_calendar.py"
    ).read_text(encoding="utf-8")
    for permission in (
        "calendar:events:access",
        "calendar:events:create",
        "calendar:events:update_own",
        "calendar:events:update_all",
        "calendar:events:cancel_own",
        "calendar:events:cancel_all",
        "calendar:participants:add_all",
    ):
        assert permission in migration
    assert "finance_manager" in migration
    assert "finance_director" in migration
    assert "ENABLE ROW LEVEL SECURITY" in migration
    assert "FORCE ROW LEVEL SECURITY" in migration


def test_calendar_ui_keeps_month_grid_detail_and_sidebar_actions() -> None:
    index = (ROOT / "templates" / "admin" / "calendar" / "index.html").read_text(
        encoding="utf-8"
    )
    form = (ROOT / "templates" / "admin" / "calendar" / "form.html").read_text(
        encoding="utf-8"
    )
    assert "grid-cols-7" in index
    assert "/people/calendar/events/{{ row.event.event_id }}" in index
    assert "}} Edit" in index
    assert "}} Delete" in index
    picker = (ROOT / "templates" / "components" / "calendar_participant_picker.html").read_text(
        encoding="utf-8"
    )
    assert 'calendar_participant_picker.html' in form
    assert "Add everyone" in picker
    assert 'item.kind === \'employee\' ? \'participant_ids\'' in picker
    assert 'name="reminder_offsets"' in form
    assert '{% extends "people/base_people.html" %}' in index


def test_calendar_participant_picker_is_shared_and_has_accessible_contract() -> None:
    picker = (
        ROOT / "templates" / "components" / "calendar_participant_picker.html"
    ).read_text(encoding="utf-8")
    admin = (ROOT / "templates" / "admin" / "calendar" / "form.html").read_text(
        encoding="utf-8"
    )
    self_service = (
        ROOT / "templates" / "people" / "self" / "calendar" / "form.html"
    ).read_text(encoding="utf-8")
    assert 'include "components/calendar_participant_picker.html"' in admin
    assert 'include "components/calendar_participant_picker.html"' in self_service
    for contract in (
        'role="combobox"',
        'aria-expanded',
        'aria-controls',
        'role="listbox"',
        'role="option"',
        "ArrowDown",
        "ArrowUp",
        "Escape",
        "No matching participants",
        "Unable to search participants",
        "@click.outside",
        "Remove \' + item.label",
        "item.kind === 'employee' ? 'participant_ids'",
        "item.kind === 'department' ? 'department_ids'",
        "'designation_ids'",
    ):
        assert contract in picker
    assert 'type="checkbox" name="participant_ids"' not in admin
    assert 'type="checkbox" name="department_ids"' not in admin
    assert 'type="checkbox" name="designation_ids"' not in admin
    assert 'type="checkbox" name="participant_ids"' not in self_service
    assert 'type="checkbox" name="department_ids"' not in self_service
    assert 'type="checkbox" name="designation_ids"' not in self_service
    assert 'name="reminder_offsets"' in admin
    assert 'name="reminder_offsets"' in self_service


def test_participant_search_service_contract_is_tenant_safe_and_bounded() -> None:
    service = (ROOT / "app" / "services" / "organization_calendar.py").read_text(
        encoding="utf-8"
    )
    method = service[service.index("def search_participant_options"):]
    assert "Employee.organization_id == self.organization_id" in method
    assert "Department.organization_id == self.organization_id" in method
    assert "Designation.organization_id == self.organization_id" in method
    assert "EmployeeStatus.ACTIVE" in method
    assert "Department.is_active.is_(True)" in method
    assert "Designation.is_active.is_(True)" in method
    assert "return items[:bounded_limit]" in method
    assert "seen: set[tuple[str, str]]" in method


class _Rows:
    def __init__(self, rows) -> None:
        self.rows = rows

    def all(self):
        return self.rows


@pytest.mark.parametrize(
    ("query", "expected_kind"),
    [
        ("Ada", "employee"),
        ("ada@example.com", "employee"),
        ("EMP-001", "employee"),
        ("Engineering", "employee"),
        ("Senior Manager", "employee"),
        ("engine", "department"),
        ("manager", "designation"),
    ],
)
def test_participant_search_matches_employee_and_group_fields(
    query: str, expected_kind: str
) -> None:
    organization_id = uuid4()
    employee_id = uuid4()
    department_id = uuid4()
    designation_id = uuid4()
    employee = SimpleNamespace(
        employee_id=employee_id,
        employee_code="EMP-001",
        status=EmployeeStatus.ACTIVE,
        date_of_leaving=None,
    )
    person = SimpleNamespace(
        id=uuid4(),
        name="Ada Lovelace",
        email="ada@example.com",
        is_active=True,
        status=PersonStatus.active,
        nextcloud_user_id=None,
    )
    department = SimpleNamespace(department_id=department_id, department_name="Engineering")
    designation = SimpleNamespace(
        designation_id=designation_id, designation_name="Senior Manager"
    )
    db = MagicMock()
    db.execute.return_value.all.return_value = [
        (employee, person, department.department_name, designation.designation_name)
    ]
    db.scalars.side_effect = [
        _Rows([department]),
        _Rows([designation]),
    ]

    items = OrganizationCalendarService(db, organization_id).search_participant_options(
        query
    )

    assert items
    assert expected_kind in {item["kind"] for item in items}
    assert len(items) <= 20


def test_participant_search_excludes_inactive_and_self_and_deduplicates() -> None:
    organization_id = uuid4()
    owner_id = uuid4()
    active = SimpleNamespace(
        employee_id=uuid4(),
        employee_code="EMP-001",
        status=EmployeeStatus.ACTIVE,
        date_of_leaving=None,
    )
    inactive = SimpleNamespace(
        employee_id=uuid4(),
        employee_code="EMP-002",
        status=EmployeeStatus.RESIGNED,
        date_of_leaving=None,
    )
    def person(person_id, name):
        return SimpleNamespace(
            id=person_id,
            name=name,
            email=f"{name.lower()}@example.com",
            is_active=True,
            status=PersonStatus.active,
            nextcloud_user_id=None,
        )
    owner = person(owner_id, "Owner")
    other = person(uuid4(), "Other")
    db = MagicMock()
    db.execute.return_value.all.return_value = [
        (active, owner, "Engineering", "Manager"),
        (active, owner, "Engineering", "Manager"),
        (inactive, other, "Engineering", "Manager"),
    ]
    db.scalars.side_effect = [_Rows([]), _Rows([])]
    items = OrganizationCalendarService(db, organization_id).search_participant_options(
        "engine", exclude_person_id=owner_id
    )
    assert items == []


def test_talk_notification_boundary_is_documented() -> None:
    plan = (ROOT / "docs" / "organization-calendar-implementation.md").read_text(
        encoding="utf-8"
    )
    assert "Recurrence is intentionally excluded from release 1" in plan
    assert "Nextcloud Calendar receives no copy" in plan
    assert "No deployment address, account, credential, or employee identity" in plan
    assert "APP_URL" in plan


class _CalendarUnitOfWork:
    def __init__(self) -> None:
        self.added = []

    def add(self, _value) -> None:
        self.added.append(_value)

    def add_all(self, values) -> None:
        self.added.extend(values)

    def flush(self) -> None:
        pass


class _PersonalCalendarService(OrganizationCalendarService):
    def __init__(self, organization_id, candidates, db=None):
        super().__init__(db or _CalendarUnitOfWork(), organization_id)
        self._candidates = candidates

    def eligible_participants(self):
        return self._candidates, []

    def _audit(self, *args, **kwargs) -> None:
        pass


def test_personal_event_is_private_scope_and_always_includes_creator() -> None:
    organization_id = uuid4()
    creator_id = uuid4()
    invitee_id = uuid4()
    candidates = [
        ParticipantCandidate(
            person_id=creator_id,
            employee_id=uuid4(),
            name="Event Owner",
            email="owner@example.com",
            nextcloud_user_id="owner",
            department_name="Operations",
        ),
        ParticipantCandidate(
            person_id=invitee_id,
            employee_id=uuid4(),
            name="Invited Employee",
            email="invitee@example.com",
            nextcloud_user_id="invitee",
            department_name="Operations",
        ),
    ]
    service = _PersonalCalendarService(organization_id, candidates)
    event = service.create_personal_event(
        CalendarEventData(
            title="Private appointment",
            description=None,
            event_details=None,
            location=None,
            meeting_url=None,
            timezone="Africa/Lagos",
            all_day=True,
            start_at=None,
            end_at=None,
            start_date=date(2026, 9, 25),
            end_date_exclusive=date(2026, 9, 26),
        ),
        actor_person_id=creator_id,
        participant_person_ids=[invitee_id, creator_id, invitee_id],
        reminder_offsets=[60],
    )

    assert event.event_scope == CalendarEventScope.PERSONAL.value
    assert event.business_status == CalendarBusinessStatus.PUBLISHED.value
    assert {participant.person_id for participant in event.participants} == {
        creator_id,
        invitee_id,
    }


def test_published_organizational_event_notifies_only_selected_participants() -> None:
    organization_id = uuid4()
    actor_id = uuid4()
    participant_id = uuid4()
    database = _CalendarUnitOfWork()
    service = _PersonalCalendarService(
        organization_id,
        [
            ParticipantCandidate(
                person_id=participant_id,
                employee_id=uuid4(),
                name="Selected Employee",
                email="selected@example.com",
                nextcloud_user_id="selected",
                department_name="Operations",
            )
        ],
        db=database,
    )
    event = service.create_event(
        CalendarEventData(
            title="Operations review",
            description=None,
            event_details=None,
            location=None,
            meeting_url=None,
            timezone="Africa/Lagos",
            all_day=True,
            start_at=None,
            end_at=None,
            start_date=date(2026, 9, 27),
            end_date_exclusive=date(2026, 9, 28),
        ),
        actor_person_id=actor_id,
        participant_person_ids=[participant_id],
        reminder_offsets=[],
        publish=True,
    )

    notifications = [item for item in database.added if isinstance(item, Notification)]
    assert len(notifications) == 2
    assert {item.channel for item in notifications} == {
        NotificationChannel.BOTH,
        NotificationChannel.NEXTCLOUD,
    }
    assert all(item.recipient_id == participant_id for item in notifications)
    assert all(
        item.action_url == (f"/people/self/calendar/events/{event.event_id}")
        for item in notifications
    )
    assert "Event: Operations review" in notifications[0].message
    assert "27 Sep 2026" in notifications[0].message


def test_overlapping_direct_department_and_designation_targets_get_one_email_each() -> (
    None
):
    organization_id = uuid4()
    department_id = uuid4()
    designation_id = uuid4()
    first_id, second_id = uuid4(), uuid4()
    candidates = [
        ParticipantCandidate(
            first_id,
            uuid4(),
            "First",
            "first@example.com",
            None,
            "Operations",
            department_id,
            designation_id,
            "Manager",
        ),
        ParticipantCandidate(
            second_id,
            uuid4(),
            "Second",
            "second@example.com",
            None,
            "Operations",
            department_id,
            None,
            None,
        ),
    ]
    database = _CalendarUnitOfWork()
    service = _PersonalCalendarService(organization_id, candidates, db=database)

    event = service.create_event(
        CalendarEventData(
            title="Operations review",
            description="Quarterly planning",
            event_details="Bring your plan",
            location="Board room",
            meeting_url=None,
            timezone="Africa/Lagos",
            all_day=True,
            start_at=None,
            end_at=None,
            start_date=date(2026, 10, 1),
            end_date_exclusive=date(2026, 10, 2),
        ),
        actor_person_id=uuid4(),
        participant_person_ids=[first_id],
        department_ids=[department_id],
        designation_ids=[designation_id],
        reminder_offsets=[],
        publish=True,
    )

    notifications = [item for item in database.added if isinstance(item, Notification)]
    assert len(notifications) == 2
    assert {item.recipient_id for item in notifications} == {first_id, second_id}
    assert all(item.channel == NotificationChannel.BOTH for item in notifications)
    assert all("Location: Board room" in item.message for item in notifications)
    assert all(
        "Additional details: Bring your plan" in item.message for item in notifications
    )
    assert event.recipient_targets == {
        "departments": [str(department_id)],
        "designations": [str(designation_id)],
    }


@pytest.mark.parametrize(
    "module_name",
    ["app.web.organization_calendar", "app.web.people.self_service_calendar"],
)
def test_edit_form_leaves_missing_meeting_link_blank(module_name: str) -> None:
    from importlib import import_module

    module = import_module(module_name)
    organization_id = uuid4()
    actor_id = uuid4()
    event = OrganizationCalendarEvent(
        event_id=uuid4(),
        organization_id=organization_id,
        ical_uid="calendar-optional-link@example.test",
        title="In-person event",
        timezone="Africa/Lagos",
        all_day=True,
        start_date=date(2026, 10, 1),
        end_date_exclusive=date(2026, 10, 2),
        color="#4F46E5",
        meeting_url=None,
        business_status=CalendarBusinessStatus.PUBLISHED.value,
        version=1,
        created_by_id=actor_id,
        updated_by_id=actor_id,
    )
    auth = SimpleNamespace(
        organization_id=organization_id,
        person_id=actor_id,
        has_permission=lambda _permission: True,
    )
    with (
        patch.object(module, "OrganizationCalendarService") as service_type,
        patch.object(module, "base_context", return_value={}),
    ):
        service_type.return_value.eligible_participants.return_value = ([], [])
        service_type.return_value.recipient_groups.return_value = {
            "departments": [],
            "designations": [],
        }
        service_type.return_value.participant_options_for_selection.return_value = []
        context = module._form_context(None, auth, MagicMock(), event=event)

    assert context["form_data"]["meeting_url"] == ""


def test_self_service_calendar_privacy_and_permissions_are_explicit() -> None:
    migration = (
        ROOT / "alembic" / "versions" / "20260919_self_service_calendar.py"
    ).read_text(encoding="utf-8")
    service = (ROOT / "app" / "services" / "organization_calendar.py").read_text(
        encoding="utf-8"
    )
    routes = (ROOT / "app" / "web" / "people" / "self_service_calendar.py").read_text(
        encoding="utf-8"
    )
    for permission in (
        "calendar:personal:access",
        "calendar:personal:create",
        "calendar:personal:invite",
    ):
        assert permission in migration
    assert "event_scope IN ('ORGANIZATIONAL', 'PERSONAL')" in migration
    assert "OrganizationCalendarEvent.created_by_id == person_id" in service
    assert "OrganizationCalendarParticipant.person_id == person_id" in service
    assert "Only the creator can manage this personal event" in routes
    assert "auth.is_admin" not in routes


def test_my_calendar_uses_self_service_layout_and_expected_actions() -> None:
    index = (
        ROOT / "templates" / "people" / "self" / "calendar" / "index.html"
    ).read_text(encoding="utf-8")
    form = (
        ROOT / "templates" / "people" / "self" / "calendar" / "form.html"
    ).read_text(encoding="utf-8")
    detail = (
        ROOT / "templates" / "people" / "self" / "calendar" / "detail.html"
    ).read_text(encoding="utf-8")
    assert '{% extends "people/base_people.html" %}' in index
    assert "grid-cols-7" in index
    assert "Personal" in index and "Organizational" in index
    assert "/people/self/calendar/events/{{ row.event.event_id }}/edit" in index
    assert "/people/self/calendar/events/{{ row.event.event_id }}/delete" in index
    picker = (ROOT / "templates" / "components" / "calendar_participant_picker.html").read_text(
        encoding="utf-8"
    )
    assert 'calendar_participant_picker.html' in form
    assert 'item.kind === \'employee\' ? \'participant_ids\'' in picker
    assert 'name="reminder_offsets"' in form
    assert "Only you and employees you explicitly invite" in form
    assert "is_personal and is_owner" in detail


def test_admin_calendar_is_named_organizational_and_remains_permission_gated() -> None:
    route = (ROOT / "app" / "web" / "organization_calendar.py").read_text(
        encoding="utf-8"
    )
    index = (ROOT / "templates" / "admin" / "calendar" / "index.html").read_text(
        encoding="utf-8"
    )
    assert "Organizational Calendar" in index
    assert 'prefix="/calendar"' in route
    assert 'require_web_permission("calendar:events:create")' in route
    assert "Finance Manager" not in route
    assert "Admin" not in route


def test_group_recipient_resolution_deduplicates_current_candidates() -> None:
    organization_id = uuid4()
    department_id = uuid4()
    designation_id = uuid4()
    first = uuid4()
    second = uuid4()
    candidates = [
        ParticipantCandidate(
            first,
            uuid4(),
            "First",
            "first@example.com",
            None,
            "Operations",
            department_id,
            designation_id,
            "Manager",
        ),
        ParticipantCandidate(
            second,
            uuid4(),
            "Second",
            "second@example.com",
            None,
            "Operations",
            department_id,
            None,
            None,
        ),
    ]
    service = _PersonalCalendarService(organization_id, candidates)

    resolved, targets = service._resolve_candidates(
        [first], False, [department_id], [designation_id]
    )

    assert {item.person_id for item in resolved} == {first, second}
    assert targets == {
        "departments": [str(department_id)],
        "designations": [str(designation_id)],
    }
