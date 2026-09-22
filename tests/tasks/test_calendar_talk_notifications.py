"""Calendar-to-Talk notification delivery contracts."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.config import settings
from app.models.notification import NotificationChannel
from app.tasks.notifications import _erp_action_url, process_due_calendar_reminders


def test_talk_action_uses_configured_erp_origin(monkeypatch) -> None:
    monkeypatch.setattr(settings, "app_url", "https://erp.example.test")

    assert _erp_action_url("/people/self/calendar/events/event-1") == (
        "https://erp.example.test/people/self/calendar/events/event-1"
    )


@pytest.mark.parametrize(
    "unsafe_url",
    ["https://untrusted.example/event", "//untrusted.example/event", "relative"],
)
def test_talk_action_rejects_non_erp_links(monkeypatch, unsafe_url) -> None:
    monkeypatch.setattr(settings, "app_url", "https://erp.example.test")

    with pytest.raises(ValueError, match="ERP-relative"):
        _erp_action_url(unsafe_url)


def test_each_due_reminder_queues_email_and_talk_once() -> None:
    organization_id = uuid4()
    event_id = uuid4()
    recipient_ids = [uuid4(), uuid4()]
    week = SimpleNamespace(event_id=event_id, offset_minutes=10080, dispatched_at=None)
    day = SimpleNamespace(event_id=event_id, offset_minutes=1440, dispatched_at=None)
    event = SimpleNamespace(
        organization_id=organization_id,
        event_id=event_id,
        title="Operations review",
        all_day=False,
        timezone="Africa/Lagos",
        start_at=datetime(2026, 10, 1, 8, 0, tzinfo=UTC),
        end_at=datetime(2026, 10, 1, 9, 0, tzinfo=UTC),
        location="Board room",
        description="Quarterly planning",
        event_details="Bring your plan",
        meeting_url=None,
    )
    db = MagicMock()
    db.scalars.side_effect = [
        SimpleNamespace(all=lambda: [week, day]),
        SimpleNamespace(all=lambda: recipient_ids),
        SimpleNamespace(all=lambda: recipient_ids),
        SimpleNamespace(all=lambda: recipient_ids),
        SimpleNamespace(all=lambda: recipient_ids),
    ]
    db.scalar.return_value = event
    with (
        patch(
            "app.tasks.notifications.active_organization_ids",
            return_value=[organization_id],
        ),
        patch("app.tasks.notifications.session_for_org") as session_factory,
        patch("app.tasks.notifications.NotificationService") as service_type,
    ):
        session_factory.return_value.__enter__.return_value = db
        service_type.return_value.create_many.side_effect = lambda **_kwargs: [
            SimpleNamespace(),
            SimpleNamespace(),
        ]

        result = process_due_calendar_reminders.run()

    assert result == {"processed": 2, "notifications_queued": 8}
    assert week.dispatched_at is not None
    assert day.dispatched_at is not None
    session_factory.assert_called_once_with(organization_id)
    db.commit.assert_called_once_with()
    assert service_type.return_value.create_many.call_count == 4
    calls = service_type.return_value.create_many.call_args_list
    assert all(call.kwargs["organization_id"] == organization_id for call in calls)
    assert calls[0].kwargs["recipient_ids"] == recipient_ids
    assert calls[0].kwargs["channel"] == NotificationChannel.BOTH
    assert calls[1].kwargs["recipient_ids"] == recipient_ids
    assert calls[1].kwargs["channel"] == NotificationChannel.NEXTCLOUD
    assert calls[2].kwargs["channel"] == NotificationChannel.BOTH
    assert calls[3].kwargs["channel"] == NotificationChannel.NEXTCLOUD
    assert "1 week" in calls[0].kwargs["title"]
    assert "1 day" in calls[2].kwargs["title"]
    assert "Event: Operations review" in calls[0].kwargs["message"]
    assert "01 Oct 2026, 09:00" in calls[0].kwargs["message"]
    assert "Location: Board room" in calls[0].kwargs["message"]
    assert all(
        call.kwargs["action_url"] == f"/people/self/calendar/events/{event_id}"
        for call in calls
    )


def test_due_reminder_batch_budget_is_global_across_organizations() -> None:
    first_org = uuid4()
    second_org = uuid4()
    first_db = MagicMock()
    second_db = MagicMock()
    first_db.scalars.return_value.all.return_value = [
        SimpleNamespace(event_id=uuid4(), dispatched_at=None)
    ]
    first_db.scalar.return_value = None

    def _session(org_id):
        manager = MagicMock()
        manager.__enter__.return_value = first_db if org_id == first_org else second_db
        manager.__exit__.return_value = False
        return manager

    with (
        patch(
            "app.tasks.notifications.active_organization_ids",
            return_value=[first_org, second_org],
        ),
        patch(
            "app.tasks.notifications.session_for_org", side_effect=_session
        ) as sessions,
    ):
        result = process_due_calendar_reminders.run(batch_size=1)

    assert result == {"processed": 0, "notifications_queued": 0}
    sessions.assert_called_once_with(first_org)
    first_db.commit.assert_called_once_with()
    second_db.scalars.assert_not_called()
