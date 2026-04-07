"""Tests for audit Celery task resilience."""
from unittest.mock import MagicMock, patch

from sqlalchemy.exc import OperationalError, ProgrammingError


def _operational_error() -> OperationalError:
    return OperationalError("INSERT INTO audit_events", {}, Exception("db gone"))


def _programming_error() -> ProgrammingError:
    return ProgrammingError("can't change autocommit", {}, Exception("db intrans"))


class TestLogAuditEvent:
    """Tests for log_audit_event task."""

    def test_retries_on_operational_error(self) -> None:
        class RetryCalled(Exception):
            pass

        with (
            patch(
                "app.tasks.audit._write_audit_event",
                side_effect=_operational_error(),
            ),
            patch("app.tasks.audit.log_audit_event.retry") as mock_retry,
        ):
            from app.tasks.audit import log_audit_event

            mock_retry.side_effect = RetryCalled("retry")
            try:
                log_audit_event(
                    actor_type="system",
                    organization_id=None,
                    actor_person_id=None,
                    actor_id=None,
                    action="POST",
                    entity_type="/api/v1/sync/crm/bulk",
                    entity_id=None,
                    status_code=200,
                    is_success=True,
                    ip_address="172.18.0.1",
                    user_agent="DotMac-CRM/1.0",
                    request_id="req-123",
                    metadata_={"path": "/api/v1/sync/crm/bulk"},
                )
            except RetryCalled:
                pass

        mock_retry.assert_called_once()
        assert isinstance(mock_retry.call_args.kwargs["exc"], OperationalError)

    def test_retries_on_programming_error(self) -> None:
        class RetryCalled(Exception):
            pass

        with (
            patch(
                "app.tasks.audit._write_audit_event",
                side_effect=_programming_error(),
            ),
            patch("app.tasks.audit.log_audit_event.retry") as mock_retry,
        ):
            from app.tasks.audit import log_audit_event

            mock_retry.side_effect = RetryCalled("retry")
            try:
                log_audit_event(
                    actor_type="system",
                    organization_id=None,
                    actor_person_id=None,
                    actor_id=None,
                    action="POST",
                    entity_type="/auth/refresh",
                    entity_id=None,
                    status_code=200,
                    is_success=True,
                    ip_address="172.18.0.1",
                    user_agent="Mozilla/5.0",
                    request_id="req-124",
                    metadata_={"path": "/auth/refresh"},
                )
            except RetryCalled:
                pass

        mock_retry.assert_called_once()
        assert isinstance(mock_retry.call_args.kwargs["exc"], ProgrammingError)

    def test_returns_success_when_write_succeeds(self) -> None:
        with patch("app.tasks.audit._write_audit_event", return_value="event-123"):
            from app.tasks.audit import log_audit_event

            result = log_audit_event(
                actor_type="system",
                organization_id=None,
                actor_person_id=None,
                actor_id=None,
                action="POST",
                entity_type="/api/v1/sync/crm/bulk",
                entity_id=None,
                status_code=200,
                is_success=True,
                ip_address="172.18.0.1",
                user_agent="DotMac-CRM/1.0",
                request_id="req-123",
                metadata_={"path": "/api/v1/sync/crm/bulk"},
            )

        assert result == {
            "success": True,
            "event_id": "event-123",
            "error": None,
        }

    def test_returns_error_when_retry_also_fails(self) -> None:
        error = ValueError("permanent failure")

        with (
            patch("app.tasks.audit._write_audit_event", side_effect=error),
            patch("app.tasks.audit.logger") as mock_logger,
        ):
            from app.tasks.audit import log_audit_event

            result = log_audit_event(
                actor_type="system",
                organization_id=None,
                actor_person_id=None,
                actor_id=None,
                action="POST",
                entity_type="/api/v1/sync/crm/bulk",
                entity_id=None,
                status_code=200,
                is_success=True,
                ip_address="172.18.0.1",
                user_agent="DotMac-CRM/1.0",
                request_id="req-123",
                metadata_={"path": "/api/v1/sync/crm/bulk"},
            )

        assert result["success"] is False
        assert result["event_id"] is None
        assert "permanent failure" in result["error"]
        mock_logger.exception.assert_called_once_with("Failed to log audit event")

    def test_rolls_back_session_when_commit_fails(self) -> None:
        mock_db = MagicMock()
        mock_db.commit.side_effect = _operational_error()
        session_ctx = MagicMock()
        session_ctx.__enter__.return_value = mock_db
        session_ctx.__exit__.return_value = False

        with patch("app.tasks.audit.SessionLocal", return_value=session_ctx):
            from app.tasks.audit import _write_audit_event

            try:
                _write_audit_event(
                    actor_type="system",
                    organization_id=None,
                    actor_person_id=None,
                    actor_id=None,
                    action="POST",
                    entity_type="/api/v1/sync/crm/bulk",
                    entity_id=None,
                    status_code=200,
                    is_success=True,
                    ip_address="172.18.0.1",
                    user_agent="DotMac-CRM/1.0",
                    request_id="req-123",
                    metadata_={"path": "/api/v1/sync/crm/bulk"},
                )
            except OperationalError:
                pass

        mock_db.rollback.assert_called_once()
