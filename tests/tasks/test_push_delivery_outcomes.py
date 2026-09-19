"""Regression: a dispatch attempt is not a successful push send."""

from contextlib import nullcontext
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.tasks import notifications as tasks


def record():
    return SimpleNamespace(
        notification_id=uuid4(),
        recipient_id=uuid4(),
        action_url=None,
        title="Synthetic notification",
        message="Test only",
        push_sent=False,
        push_sent_at=None,
        push_status="pending",
        push_retry_count=0,
        push_next_retry_at=None,
    )


def counters():
    return dict.fromkeys(
        ["processed", "sent", "swept", "failed", "skipped", "partial", "dead_letter"], 0
    )


def test_no_device_is_skipped_not_sent():
    notification, service, results = record(), MagicMock(), counters()
    service.active_tokens_for_person.return_value = []
    tasks._dispatch_notification_push(
        notification, service, datetime.now(timezone.utc), results
    )
    assert notification.push_status == "no_device"
    assert notification.push_sent is False
    assert notification.push_sent_at is None
    assert results["skipped"] == 1
    service.send_to_person.assert_not_called()


@pytest.mark.parametrize("exception", [False, True])
def test_zero_send_or_exception_gets_bounded_retry_without_false_success(exception):
    notification, service, results = record(), MagicMock(), counters()
    service.active_tokens_for_person.return_value = [object()]
    service.send_to_person.return_value = 0
    if exception:
        service.send_to_person.side_effect = RuntimeError("synthetic transport failure")
    now = datetime.now(timezone.utc)
    for _ in range(3):
        tasks._dispatch_notification_push(notification, service, now, results)
        assert notification.push_status == "retry"
        assert notification.push_next_retry_at > now
        assert notification.push_sent is False
        assert notification.push_sent_at is None
    tasks._dispatch_notification_push(notification, service, now, results)
    assert notification.push_status == "failed"
    assert notification.push_next_retry_at is None
    assert notification.push_retry_count == 4
    assert results["dead_letter"] == 1
    assert results["sent"] == 0


@pytest.mark.parametrize(("sent", "status"), [(1, "partial"), (2, "sent")])
def test_provider_acceptance_sets_sent_flag_and_partial_is_not_retried(sent, status):
    notification, service, results = record(), MagicMock(), counters()
    service.active_tokens_for_person.return_value = [object(), object()]
    service.send_to_person.return_value = sent
    now = datetime.now(timezone.utc)
    tasks._dispatch_notification_push(notification, service, now, results)
    assert notification.push_status == status
    assert notification.push_sent is True
    assert notification.push_sent_at == now
    assert notification.push_next_retry_at is None
    assert results["sent"] == sent


def test_task_queries_are_org_scoped_and_expiry_is_not_a_send(monkeypatch):
    from app.services.push import PushService

    org = uuid4()
    db = MagicMock()
    db.execute.return_value.rowcount = 3
    db.execute.return_value.scalars.return_value.all.return_value = []
    monkeypatch.setattr(PushService, "is_configured", staticmethod(lambda: True))
    monkeypatch.setattr(tasks, "active_organization_ids", lambda: [org])
    monkeypatch.setattr(tasks, "session_for_org", lambda value: nullcontext(db))
    results = tasks.process_pending_push_notifications.run()
    statements = [call.args[0] for call in db.execute.call_args_list]
    assert len(statements) == 3
    assert all(
        "notification.organization_id" in str(statement) for statement in statements
    )
    assert "push_status" in str(statements[-1])
    assert "push_next_retry_at" in str(statements[-1])
    assert "SKIP LOCKED" in str(statements[-1].compile(dialect=postgresql.dialect()))
    expired_values = statements[1].compile().params
    assert expired_values["push_status"] == "expired"
    assert "push_sent" not in expired_values
    assert results["swept"] == 3
    assert results["sent"] == 0
    db.commit.assert_called_once_with()


def test_unconfigured_push_does_not_open_sessions(monkeypatch):
    from app.services.push import PushService

    discover = MagicMock()
    monkeypatch.setattr(PushService, "is_configured", staticmethod(lambda: False))
    monkeypatch.setattr(tasks, "active_organization_ids", discover)
    assert tasks.process_pending_push_notifications.run()["processed"] == 0
    discover.assert_not_called()
