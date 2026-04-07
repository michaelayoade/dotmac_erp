"""Tests for notification Celery tasks."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sqlalchemy.exc import OperationalError


def _operational_error() -> OperationalError:
    return OperationalError("SELECT", {}, Exception("db gone"))


def _build_notification(*, email: str | None = "user@example.com") -> SimpleNamespace:
    return SimpleNamespace(
        notification_id="notif-1",
        recipient=SimpleNamespace(email=email),
        title="Leave update",
        message="Your request",
        action_url="/self",
        organization_id="org-1",
        channel=None,
    )


def test_process_pending_notification_emails_retries_on_operational_error() -> None:
    class RetryCalled(Exception):
        pass

    with (
        patch("app.tasks.notifications.SessionLocal", side_effect=_operational_error()),
        patch("app.tasks.notifications.process_pending_notification_emails.retry") as mock_retry,
    ):
        from app.tasks.notifications import process_pending_notification_emails

        mock_retry.side_effect = RetryCalled(
            "retry"
        )

        try:
            process_pending_notification_emails()
        except RetryCalled:
            pass

    mock_retry.assert_called_once()
    assert isinstance(
        mock_retry.call_args.kwargs["exc"],
        OperationalError,
    )


def test_process_pending_notification_emails_sends_active_notification() -> None:
    with (
        patch("app.tasks.notifications.SessionLocal") as mock_session_local,
        patch("app.tasks.notifications.person_can_receive_email", return_value=True),
        patch("app.tasks.notifications.send_email", return_value=True),
    ):
        from app.tasks.notifications import process_pending_notification_emails

        db = MagicMock()
        mock_session_local.return_value = db
        execute_result = MagicMock()
        execute_result.scalars.return_value.all.side_effect = [[], [_build_notification()]]
        db.execute.return_value = execute_result

        result = process_pending_notification_emails(batch_size=1)

        assert result["processed"] == 1
        assert result["sent"] == 1
        assert result["skipped"] == 0
        assert result["failed"] == 0
        assert result["dead_letter"] == 0


def test_process_pending_notification_emails_skips_when_email_missing() -> None:
    with (
        patch("app.tasks.notifications.SessionLocal") as mock_session_local,
        patch("app.tasks.notifications.person_can_receive_email", return_value=True),
        patch("app.tasks.notifications.send_email") as mock_send_email,
    ):
        from app.tasks.notifications import process_pending_notification_emails

        db = MagicMock()
        mock_session_local.return_value = db
        execute_result = MagicMock()
        execute_result.scalars.return_value.all.side_effect = [
            [],
            [_build_notification(email=None)],
        ]
        db.execute.return_value = execute_result

        result = process_pending_notification_emails(batch_size=1)

        assert result["processed"] == 1
        assert result["sent"] == 0
        assert result["skipped"] == 1
        mock_send_email.assert_not_called()
