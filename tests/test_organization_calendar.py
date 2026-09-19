"""Focused contracts for the ERP organization calendar slice."""

from __future__ import annotations

from datetime import UTC, date, time
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.organization_calendar import (
    CalendarBusinessStatus,
    CalendarSyncStatus,
    OrganizationCalendarEvent,
    OrganizationCalendarParticipant,
    OrganizationCalendarReminder,
    ParticipantMembershipStatus,
    ParticipantSyncStatus,
)
from app.schemas.organization_calendar import CalendarSyncResultRequest
from app.services.organization_calendar import (
    CALENDAR_CANCELLED,
    CALENDAR_UPSERTED,
    OrganizationCalendarService,
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
        business_status=CalendarBusinessStatus.PUBLISHED.value,
        sync_status=CalendarSyncStatus.PENDING.value,
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
            sync_status=ParticipantSyncStatus.PENDING.value,
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
        sync_status=CalendarSyncStatus.PENDING.value,
        version=2,
        created_by_id=uuid4(),
        updated_by_id=uuid4(),
    )
    payload = OrganizationCalendarService(
        None, event.organization_id
    )._contract_payload(event, CALENDAR_CANCELLED)
    assert payload["action"] == "CANCEL_EVENT"


def test_integrator_result_schema_fails_closed_on_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        CalendarSyncResultRequest.model_validate(
            {
                "event_version": 1,
                "result": "SYNCED",
                "participant_results": [],
                "service_account_password": "must-never-be-accepted",
            }
        )


def test_integrator_result_schema_accepts_correlation_id() -> None:
    result = CalendarSyncResultRequest.model_validate(
        {
            "event_version": 1,
            "correlation_id": "calendar-correlation",
            "result": "SYNCED",
            "participant_results": [],
        }
    )
    assert result.correlation_id == "calendar-correlation"


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
