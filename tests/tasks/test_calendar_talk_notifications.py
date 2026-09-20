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
    ]
    db.get.return_value = event
    queued = [SimpleNamespace(), SimpleNamespace()]

    with (
        patch("app.tasks.notifications._task_db_session") as session_factory,
        patch("app.tasks.notifications.NotificationService") as service_type,
    ):
        session_factory.return_value.__enter__.return_value = db
        service_type.return_value.create_many.return_value = queued

        result = process_due_calendar_reminders.run()

    assert result == {"processed": 1, "notifications_queued": 2}
    assert reminder.dispatched_at is not None
    db.commit.assert_called_once_with()
    service_type.return_value.create_many.assert_called_once()
    call = service_type.return_value.create_many.call_args
    assert call.kwargs["organization_id"] == organization_id
    assert call.kwargs["recipient_ids"] == recipient_ids
    assert call.kwargs["channel"] == NotificationChannel.NEXTCLOUD
    assert call.kwargs["action_url"] == f"/people/self/calendar/events/{event_id}"
