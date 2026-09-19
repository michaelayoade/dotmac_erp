"""Focused contracts for the ERP organization calendar slice."""

from __future__ import annotations

from datetime import UTC, date, time
from pathlib import Path
from uuid import uuid4

from app.models.organization_calendar import (
    CalendarBusinessStatus,
    CalendarEventScope,
    OrganizationCalendarEvent,
    OrganizationCalendarParticipant,
    OrganizationCalendarReminder,
    ParticipantMembershipStatus,
)
from app.models.notification import Notification
from app.services.organization_calendar import (
    CALENDAR_CANCELLED,
    CALENDAR_UPSERTED,
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


def test_integrator_payload_carries_stable_identity_version_and_desired_membership() -> (
    None
):
    org_id = uuid4()
    event_id = uuid4()
    person_id = uuid4()
    event = OrganizationCalendarEvent(
        event_id=event_id,
        organization_id=org_id,
        ical_uid=f"erp-event-{org_id}-{event_id}@dotmac.ng",
        title="Review",
        timezone="Africa/Lagos",
        all_day=True,
        start_date=date(2026, 9, 21),
        end_date_exclusive=date(2026, 9, 22),
        start_at=None,
        end_at=None,
        color="#4F46E5",
        event_scope=CalendarEventScope.ORGANIZATIONAL.value,
        business_status=CalendarBusinessStatus.PUBLISHED.value,
        version=7,
        created_by_id=uuid4(),
        updated_by_id=uuid4(),
    )
    event.participants = [
        OrganizationCalendarParticipant(
            organization_id=org_id,
            person_id=person_id,
            employee_id=uuid4(),
            participant_name="Ada Employee",
            participant_email="ada@example.com",
            nextcloud_user_id="ada@example.com",
            membership_status=ParticipantMembershipStatus.REMOVED.value,
        )
    ]
    event.reminders = [
        OrganizationCalendarReminder(
            organization_id=org_id,
            offset_minutes=60,
        )
    ]

    payload = OrganizationCalendarService(None, org_id)._contract_payload(
        event,
        CALENDAR_UPSERTED,
        correlation_id="calendar-correlation",
        idempotency_key="calendar-delivery-v7",
    )

    assert payload["event_version"] == 7
    assert payload["event_scope"] == CalendarEventScope.ORGANIZATIONAL.value
    assert payload["uid"] == event.ical_uid
    assert payload["action"] == "UPSERT_EVENT"
    assert payload["participants"][0]["membership_status"] == "REMOVED"
    assert payload["reminders"] == [60]
    assert payload["correlation_id"] == "calendar-correlation"
    assert payload["idempotency_key"] == "calendar-delivery-v7"


def test_cancel_contract_is_a_distinct_action() -> None:
    event = OrganizationCalendarEvent(
        event_id=uuid4(),
        organization_id=uuid4(),
        ical_uid="erp-event-test@dotmac.ng",
        title="Review",
        timezone="Africa/Lagos",
        all_day=True,
        start_date=date(2026, 9, 21),
        end_date_exclusive=date(2026, 9, 22),
        color="#4F46E5",
        business_status=CalendarBusinessStatus.CANCELLED.value,
        version=2,
        created_by_id=uuid4(),
        updated_by_id=uuid4(),
    )
    payload = OrganizationCalendarService(
        None, event.organization_id
    )._contract_payload(event, CALENDAR_CANCELLED)
    assert payload["action"] == "CANCEL_EVENT"


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
        "calendar:sync:retry",
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
    assert "/admin/calendar/events/{{ row.event.event_id }}" in index
    assert "}} Edit" in index
    assert "}} Delete" in index
    assert "Add everyone" in form
    assert 'name="participant_ids"' in form
    assert 'name="reminder_offsets"' in form
    assert "/admin/calendar/sync-issues" in index
    assert (ROOT / "templates" / "admin" / "calendar" / "sync_issues.html").exists()


def test_release_one_recurrence_boundary_is_documented() -> None:
    plan = (ROOT / "docs" / "organization-calendar-implementation.md").read_text(
        encoding="utf-8"
    )
    assert "Recurrence is intentionally excluded from release 1" in plan
    assert "HTTP 412" in plan
    assert "30 seconds, 2 minutes, 10 minutes, 1 hour" in plan
    assert "automatic attendee appearance" in plan.lower()


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

    def _publish_command(self, *args, **kwargs) -> None:
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
    assert len(notifications) == 1
    assert notifications[0].recipient_id == participant_id
    assert notifications[0].action_url == (
        f"/people/self/calendar/events/{event.event_id}"
    )


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
    assert 'name="participant_ids"' in form
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
    assert 'require_web_permission("calendar:events:create")' in route
    assert "Finance Manager" not in route
    assert "Admin" not in route
