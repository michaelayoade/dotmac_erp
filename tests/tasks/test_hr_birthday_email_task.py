from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from app.models.notification import EntityType, NotificationChannel, NotificationType
from app.tasks import hr as hr_tasks
from tests._helpers.session_mocks import org_session_context


def test_send_hr_birthday_morning_email_creates_hr_manager_notifications(
    monkeypatch,
) -> None:
    org_id = uuid4()
    employee_id = uuid4()
    recipient_id = uuid4()
    db = MagicMock()
    db.execute.return_value.all.return_value = [
        (employee_id, "Jane Doe"),
        (uuid4(), "John Smith"),
    ]
    recipient = SimpleNamespace(id=recipient_id)
    notification_service = MagicMock()
    notification_service.create_if_not_sent_since.return_value = SimpleNamespace(
        notification_id=uuid4()
    )

    monkeypatch.setattr(hr_tasks, "_list_organization_ids", lambda: [org_id])
    monkeypatch.setattr(hr_tasks, "session_for_org", org_session_context(db))
    monkeypatch.setattr(
        hr_tasks,
        "_get_hr_manager_recipients",
        lambda db_arg, org_id_arg: [recipient],
    )
    monkeypatch.setattr(
        hr_tasks,
        "NotificationService",
        MagicMock(return_value=notification_service),
    )

    result = hr_tasks.send_hr_birthday_morning_email()

    assert result == {
        "notifications_created": 1,
        "birthdays_found": 2,
        "recipients_notified": 1,
        "errors": [],
    }
    notification_service.create_if_not_sent_since.assert_called_once()
    kwargs = notification_service.create_if_not_sent_since.call_args.kwargs
    assert kwargs["organization_id"] == org_id
    assert kwargs["recipient_id"] == recipient_id
    assert kwargs["entity_type"] == EntityType.EMPLOYEE
    assert kwargs["entity_id"] == employee_id
    assert kwargs["notification_type"] == NotificationType.REMINDER
    assert kwargs["title"] == "Staff Birthday Reminder"
    assert kwargs["channel"] == NotificationChannel.BOTH
    assert kwargs["action_url"] == "/people"
    assert "Today is Jane Doe's birthday." in kwargs["message"]
    assert "Today is John Smith's birthday." in kwargs["message"]
    db.commit.assert_called_once()

    statement = str(db.execute.call_args.args[0])
    assert "people.date_of_birth" in statement
    assert "employee.status" in statement
    assert "employee.date_of_birth" not in statement


def test_send_hr_birthday_morning_email_skips_when_no_birthdays(monkeypatch) -> None:
    db = MagicMock()
    db.execute.return_value.all.return_value = []
    notification_service = MagicMock()

    monkeypatch.setattr(hr_tasks, "_list_organization_ids", lambda: [uuid4()])
    monkeypatch.setattr(hr_tasks, "session_for_org", org_session_context(db))
    monkeypatch.setattr(
        hr_tasks,
        "NotificationService",
        MagicMock(return_value=notification_service),
    )

    result = hr_tasks.send_hr_birthday_morning_email()

    assert result == {
        "notifications_created": 0,
        "birthdays_found": 0,
        "recipients_notified": 0,
        "errors": [],
    }
    notification_service.create_if_not_sent_since.assert_not_called()
    db.commit.assert_not_called()


def test_send_hr_birthday_morning_email_skips_when_no_hr_managers(
    monkeypatch,
) -> None:
    db = MagicMock()
    db.execute.return_value.all.return_value = [(uuid4(), "Jane Doe")]
    notification_service = MagicMock()

    monkeypatch.setattr(hr_tasks, "_list_organization_ids", lambda: [uuid4()])
    monkeypatch.setattr(hr_tasks, "session_for_org", org_session_context(db))
    monkeypatch.setattr(
        hr_tasks,
        "_get_hr_manager_recipients",
        lambda db_arg, org_id_arg: [],
    )
    monkeypatch.setattr(
        hr_tasks,
        "NotificationService",
        MagicMock(return_value=notification_service),
    )

    result = hr_tasks.send_hr_birthday_morning_email()

    assert result == {
        "notifications_created": 0,
        "birthdays_found": 1,
        "recipients_notified": 0,
        "errors": [],
    }
    notification_service.create_if_not_sent_since.assert_not_called()
    db.commit.assert_called_once()
