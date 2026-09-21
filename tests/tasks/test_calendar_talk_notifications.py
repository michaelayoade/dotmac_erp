"""Calendar-to-Talk notification delivery contracts."""

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


def test_due_reminder_queues_talk_notifications_and_is_marked_dispatched() -> None:
    organization_id = uuid4()
    event_id = uuid4()
    recipient_ids = [uuid4(), uuid4()]
    reminder = SimpleNamespace(event_id=event_id, dispatched_at=None)
    event = SimpleNamespace(
        organization_id=organization_id,
        event_id=event_id,
        title="Operations review",
    )
    db = MagicMock()
    db.scalars.side_effect = [
        SimpleNamespace(all=lambda: [reminder]),
        SimpleNamespace(all=lambda: recipient_ids),
        SimpleNamespace(all=lambda: recipient_ids),
    ]
    db.scalar.return_value = event
    queued = [SimpleNamespace(), SimpleNamespace()]

    with (
        patch(
            "app.tasks.notifications.active_organization_ids",
            return_value=[organization_id],
        ),
        patch("app.tasks.notifications.session_for_org") as session_factory,
        patch("app.tasks.notifications.NotificationService") as service_type,
    ):
        session_factory.return_value.__enter__.return_value = db
        service_type.return_value.create_many.return_value = queued

        result = process_due_calendar_reminders.run()

    assert result == {"processed": 1, "notifications_queued": 4}
    assert reminder.dispatched_at is not None
    session_factory.assert_called_once_with(organization_id)
    db.commit.assert_called_once_with()
    assert service_type.return_value.create_many.call_count == 2
    calls = service_type.return_value.create_many.call_args_list
    assert all(call.kwargs["organization_id"] == organization_id for call in calls)
    assert calls[0].kwargs["recipient_ids"] == recipient_ids
    assert calls[0].kwargs["channel"] == NotificationChannel.IN_APP
    assert calls[1].kwargs["recipient_ids"] == recipient_ids
    assert calls[1].kwargs["channel"] == NotificationChannel.NEXTCLOUD
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
