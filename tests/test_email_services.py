"""Tests for email service - failure handling and configuration."""

import hashlib
import json
import smtplib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.people.hr.employee import EmployeeStatus
from app.models.finance.platform.event_outbox import EventStatus
from app.models.person import PersonStatus
from app.services.email import (
    _env_bool,
    _env_int,
    _env_value,
    _get_smtp_config,
    employee_can_receive_email,
    enqueue_email,
    person_can_receive_email,
    purge_mature_email_delivery,
    send_email,
    send_password_reset_email,
    validate_email_attachments,
    validate_smtp_config,
)


def test_enqueue_email_records_attachment_in_caller_transaction() -> None:
    db = MagicMock()
    db.scalar.return_value = None
    organization_id = uuid4()
    with patch(
        "app.services.finance.platform.outbox_publisher.OutboxPublisher.publish_event"
    ) as publish:
        enqueue_email(
            db,
            delivery_id="purchase-order-approved:123:supplier@example.com",
            organization_id=organization_id,
            to_email="supplier@example.com",
            subject="Approved",
            body_html="<p>Approved</p>",
            attachments=[("po.pdf", b"%PDF", "application/pdf")],
        )

    private = db.add.call_args.args[0]
    assert private.attachments == [
        {"filename": "po.pdf", "data_b64": "JVBERg==", "mime_type": "application/pdf"}
    ]
    assert private.organization_id == organization_id
    assert publish.call_args.kwargs["payload"] == {
        "delivery_id": str(private.delivery_id)
    }
    assert publish.call_args.kwargs["headers"]["organization_id"] == str(
        organization_id
    )
    assert publish.call_args.kwargs["idempotency_key"].startswith("email:")
    db.commit.assert_not_called()


def test_enqueue_email_rejects_identity_reuse_with_changed_content() -> None:
    db = MagicMock()
    db.scalar.return_value = SimpleNamespace(
        event_name="email.delivery.requested",
        headers={"organization_id": str(uuid4())},
        payload={"delivery_id": str(uuid4())},
    )
    with pytest.raises(ValueError, match="reused with different content"):
        enqueue_email(
            db,
            delivery_id="same-business-event",
            organization_id=uuid4(),
            to_email="person@example.com",
            subject="Subject",
            body_html="<p>Message</p>",
        )
    db.commit.assert_not_called()


def test_enqueue_email_replay_returns_existing_delivery() -> None:
    db = MagicMock()
    organization_id = uuid4()
    private_id = uuid4()
    existing = SimpleNamespace(
        event_name="email.delivery.requested",
        headers={"organization_id": str(organization_id)},
        payload={"delivery_id": str(private_id)},
    )
    db.scalar.return_value = existing
    content = {
        "to_email": "person@example.com",
        "subject": "Subject",
        "body_html": "<p>Message</p>",
        "body_text": None,
        "module": "ADMIN",
        "attachments": [],
    }
    digest = hashlib.sha256(
        json.dumps(content, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    private = SimpleNamespace(organization_id=organization_id, content_digest=digest)
    db.get.return_value = private
    with patch(
        "app.services.finance.platform.outbox_publisher.OutboxPublisher.publish_event"
    ) as publish:
        result = enqueue_email(
            db,
            delivery_id="same-business-event",
            organization_id=organization_id,
            to_email="person@example.com",
            subject="Subject",
            body_html="<p>Message</p>",
        )
    assert result is existing
    db.get.assert_called_once()
    publish.assert_not_called()


def test_enqueue_email_concurrent_unique_conflict_reloads_matching_delivery() -> None:
    db = MagicMock()
    organization_id = uuid4()
    private_id = uuid4()
    existing = SimpleNamespace(
        event_name="email.delivery.requested",
        headers={"organization_id": str(organization_id)},
        payload={"delivery_id": str(private_id)},
    )
    db.scalar.side_effect = [None, existing]
    content = {
        "to_email": "person@example.com",
        "subject": "Subject",
        "body_html": "<p>Message</p>",
        "body_text": None,
        "module": "ADMIN",
        "attachments": [],
    }
    digest = hashlib.sha256(
        json.dumps(content, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    db.get.return_value = SimpleNamespace(
        organization_id=organization_id, content_digest=digest
    )
    with patch(
        "app.services.finance.platform.outbox_publisher.OutboxPublisher.publish_event",
        side_effect=IntegrityError("insert", {}, Exception("unique conflict")),
    ):
        result = enqueue_email(
            db,
            delivery_id="same-business-event",
            organization_id=organization_id,
            to_email="person@example.com",
            subject="Subject",
            body_html="<p>Message</p>",
        )
    assert result is existing
    assert db.scalar.call_count == 2
    db.commit.assert_not_called()


@pytest.mark.parametrize(
    "attachments",
    [
        [("../escape.pdf", b"%PDF", "application/pdf")],
        [("po.pdf", b"%PDF", "application/octet-stream")],
        [("po.pdf", b"a" * (5 * 1024 * 1024 + 1), "application/pdf")],
        [(f"po-{index}.pdf", b"%PDF", "application/pdf") for index in range(6)],
    ],
)
def test_email_attachment_boundary_rejects_unsafe_content(attachments) -> None:
    with pytest.raises(ValueError):
        validate_email_attachments(attachments)


@pytest.mark.parametrize("status", [EventStatus.PUBLISHED, EventStatus.DEAD])
def test_mature_terminal_email_content_can_be_purged(status) -> None:
    organization_id = uuid4()
    private_id = uuid4()
    event = SimpleNamespace(
        event_name="email.delivery.requested",
        status=status,
        terminal_at=datetime.now(UTC) - timedelta(days=31),
        headers={"organization_id": str(organization_id)},
        payload={"delivery_id": str(private_id)},
    )
    stored = SimpleNamespace(organization_id=organization_id)
    db = MagicMock()
    db.get.return_value = stored

    purge_mature_email_delivery(
        db,
        event,
        organization_id=organization_id,
        cutoff=datetime.now(UTC) - timedelta(days=30),
    )

    db.delete.assert_called_once_with(stored)
    db.flush.assert_called_once()
    db.commit.assert_not_called()


@pytest.mark.parametrize(
    ("status", "age_days"),
    [(EventStatus.PENDING, 31), (EventStatus.FAILED, 31), (EventStatus.DEAD, 29)],
)
def test_nonterminal_or_young_email_content_is_not_purged(status, age_days) -> None:
    organization_id = uuid4()
    event = SimpleNamespace(
        event_name="email.delivery.requested",
        status=status,
        terminal_at=datetime.now(UTC) - timedelta(days=age_days),
        headers={"organization_id": str(organization_id)},
        payload={"delivery_id": str(uuid4())},
    )
    db = MagicMock()

    with pytest.raises(ValueError):
        purge_mature_email_delivery(
            db,
            event,
            organization_id=organization_id,
            cutoff=datetime.now(UTC) - timedelta(days=30),
        )
    db.get.assert_not_called()
    db.delete.assert_not_called()


class TestEnvHelpers:
    """Tests for environment variable helper functions."""

    def test_env_value_returns_value(self, monkeypatch):
        """Test _env_value returns environment variable value."""
        monkeypatch.setenv("TEST_VAR", "test_value")
        assert _env_value("TEST_VAR") == "test_value"

    def test_env_value_returns_none_for_missing(self, monkeypatch):
        """Test _env_value returns None for missing variable."""
        monkeypatch.delenv("MISSING_VAR", raising=False)
        assert _env_value("MISSING_VAR") is None

    def test_env_value_returns_none_for_empty(self, monkeypatch):
        """Test _env_value returns None for empty string."""
        monkeypatch.setenv("EMPTY_VAR", "")
        assert _env_value("EMPTY_VAR") is None

    def test_env_int_returns_integer(self, monkeypatch):
        """Test _env_int returns parsed integer."""
        monkeypatch.setenv("INT_VAR", "42")
        assert _env_int("INT_VAR", 0) == 42

    def test_env_int_returns_default_for_missing(self, monkeypatch):
        """Test _env_int returns default for missing variable."""
        monkeypatch.delenv("MISSING_INT", raising=False)
        assert _env_int("MISSING_INT", 100) == 100

    def test_env_int_returns_default_for_invalid(self, monkeypatch):
        """Test _env_int returns default for invalid integer."""
        monkeypatch.setenv("INVALID_INT", "not_a_number")
        assert _env_int("INVALID_INT", 50) == 50

    def test_env_bool_true_values(self, monkeypatch):
        """Test _env_bool with various true values."""
        for value in ["1", "true", "yes", "on"]:
            monkeypatch.setenv("BOOL_VAR", value)
            assert _env_bool("BOOL_VAR", False) is True

    def test_env_bool_false_values(self, monkeypatch):
        """Test _env_bool with various false values."""
        for value in ["0", "false", "no", "off"]:
            monkeypatch.setenv("BOOL_VAR", value)
            assert _env_bool("BOOL_VAR", True) is False

    def test_env_bool_default(self, monkeypatch):
        """Test _env_bool returns default for missing."""
        monkeypatch.delenv("MISSING_BOOL", raising=False)
        assert _env_bool("MISSING_BOOL", True) is True
        assert _env_bool("MISSING_BOOL", False) is False


class TestRecipientEligibility:
    """Tests for recipient email eligibility helpers."""

    def test_person_can_receive_email_for_active_person(self):
        person = SimpleNamespace(
            email="active@example.com",
            is_active=True,
            status=PersonStatus.active,
        )

        assert person_can_receive_email(person) is True

    def test_person_can_receive_email_rejects_inactive_person(self):
        person = SimpleNamespace(
            email="inactive@example.com",
            is_active=False,
            status=PersonStatus.active,
        )

        assert person_can_receive_email(person) is False

    def test_employee_can_receive_email_rejects_separated_employee(self):
        person = SimpleNamespace(
            email="employee@example.com",
            is_active=True,
            status=PersonStatus.active,
        )
        employee = SimpleNamespace(
            status=EmployeeStatus.TERMINATED,
            person=person,
            work_email="employee@example.com",
            personal_email=None,
        )

        assert employee_can_receive_email(employee) is False

    def test_employee_can_receive_email_allows_draft_employee(self):
        person = SimpleNamespace(
            email="newhire@example.com",
            is_active=True,
            status=PersonStatus.active,
        )
        employee = SimpleNamespace(
            status=EmployeeStatus.DRAFT,
            person=person,
            work_email="newhire@example.com",
            personal_email=None,
        )

        assert employee_can_receive_email(employee) is True


class TestSmtpConfig:
    """Tests for SMTP configuration loading."""

    def test_get_smtp_config_defaults(self, monkeypatch):
        """Test SMTP config with defaults."""
        for var in [
            "SMTP_HOST",
            "SMTP_PORT",
            "SMTP_USERNAME",
            "SMTP_PASSWORD",
            "SMTP_USE_TLS",
            "SMTP_USE_SSL",
            "SMTP_FROM_EMAIL",
            "SMTP_FROM_NAME",
        ]:
            monkeypatch.delenv(var, raising=False)

        config = _get_smtp_config()

        assert config["host"] == "localhost"
        assert config["port"] == 587
        assert config["username"] is None
        assert config["password"] is None
        assert config["use_tls"] is True
        assert config["use_ssl"] is False
        assert config["from_email"] == "noreply@example.com"
        assert config["from_name"] == "Dotmac ERP"

    def test_get_smtp_config_custom(self, monkeypatch):
        """Test SMTP config with custom values."""
        monkeypatch.setenv("SMTP_HOST", "mail.example.com")
        monkeypatch.setenv("SMTP_PORT", "465")
        monkeypatch.setenv("SMTP_USERNAME", "user@example.com")
        monkeypatch.setenv("SMTP_PASSWORD", "secret123")
        monkeypatch.setenv("SMTP_USE_TLS", "false")
        monkeypatch.setenv("SMTP_USE_SSL", "true")
        monkeypatch.setenv("SMTP_FROM_EMAIL", "app@example.com")
        monkeypatch.setenv("SMTP_FROM_NAME", "My App")

        config = _get_smtp_config()

        assert config["host"] == "mail.example.com"
        assert config["port"] == 465
        assert config["username"] == "user@example.com"
        assert config["password"] == "secret123"
        assert config["use_tls"] is False
        assert config["use_ssl"] is True
        assert config["from_email"] == "app@example.com"
        assert config["from_name"] == "My App"


class TestValidateSmtpConfig:
    """Tests for SMTP validation."""

    def test_validate_smtp_config_requires_host(self):
        """Test validation fails when host is missing."""
        ok, error = validate_smtp_config({"host": "", "port": 587})
        assert ok is False
        assert error is not None

    def test_validate_smtp_config_rejects_tls_and_ssl(self):
        """Test validation fails when TLS and SSL are both enabled."""
        ok, error = validate_smtp_config(
            {"host": "smtp.example.com", "port": 587, "use_tls": True, "use_ssl": True}
        )
        assert ok is False
        assert error is not None

    def test_validate_smtp_config_success_with_tls_and_auth(self):
        """Test validation succeeds with TLS and authentication."""
        mock_smtp = MagicMock()
        with patch("app.services.email.smtplib.SMTP", return_value=mock_smtp):
            ok, error = validate_smtp_config(
                {
                    "host": "smtp.example.com",
                    "port": 587,
                    "use_tls": True,
                    "use_ssl": False,
                    "username": "user",
                    "password": "pass",
                }
            )

        assert ok is True
        assert error is None
        mock_smtp.starttls.assert_called_once()
        mock_smtp.login.assert_called_once_with("user", "pass")


class TestSendEmail:
    """Tests for send_email function."""

    def test_module_fallback_keeps_explicit_organization_scope(self):
        organization_id = uuid4()
        db = MagicMock()
        config = {
            "host": "localhost",
            "port": 587,
            "username": None,
            "password": None,
            "use_tls": False,
            "use_ssl": False,
            "from_email": "noreply@example.com",
            "from_name": "Dotmac ERP",
            "reply_to": None,
        }
        smtp = MagicMock()

        with (
            patch(
                "app.services.email._get_module_smtp_config",
                return_value=None,
            ),
            patch(
                "app.services.email._get_smtp_config",
                return_value=config,
            ) as fallback,
            patch("app.services.email.smtplib.SMTP", return_value=smtp),
        ):
            assert send_email(
                db,
                "test@example.com",
                "Test Subject",
                "<p>Test Body</p>",
                organization_id=organization_id,
            )

        fallback.assert_called_once_with(db, organization_id=organization_id)

    def test_send_email_success(self, monkeypatch):
        """Test successful email sending."""
        monkeypatch.setenv("SMTP_HOST", "localhost")
        monkeypatch.setenv("SMTP_PORT", "587")
        monkeypatch.setenv("SMTP_USE_TLS", "true")
        monkeypatch.setenv("SMTP_USE_SSL", "false")

        mock_smtp = MagicMock()
        with patch("app.services.email.smtplib.SMTP", return_value=mock_smtp):
            result = send_email(
                None,
                "test@example.com",
                "Test Subject",
                "<p>Test Body</p>",
                "Test Body",
            )

        assert result is True
        mock_smtp.starttls.assert_called_once()
        mock_smtp.sendmail.assert_called_once()
        mock_smtp.quit.assert_called_once()

    def test_send_email_with_ssl(self, monkeypatch):
        """Test email sending with SSL."""
        monkeypatch.setenv("SMTP_HOST", "localhost")
        monkeypatch.setenv("SMTP_PORT", "465")
        monkeypatch.setenv("SMTP_USE_SSL", "true")

        mock_smtp = MagicMock()
        with patch("app.services.email.smtplib.SMTP_SSL", return_value=mock_smtp):
            result = send_email(
                None,
                "test@example.com",
                "Test Subject",
                "<p>Test Body</p>",
            )

        assert result is True
        mock_smtp.sendmail.assert_called_once()

    def test_send_email_with_auth(self, monkeypatch):
        """Test email sending with authentication."""
        monkeypatch.setenv("SMTP_HOST", "localhost")
        monkeypatch.setenv("SMTP_USERNAME", "user")
        monkeypatch.setenv("SMTP_PASSWORD", "pass")
        monkeypatch.setenv("SMTP_USE_SSL", "false")

        mock_smtp = MagicMock()
        with patch("app.services.email.smtplib.SMTP", return_value=mock_smtp):
            result = send_email(
                None,
                "test@example.com",
                "Test Subject",
                "<p>Test Body</p>",
            )

        assert result is True
        mock_smtp.login.assert_called_once_with("user", "pass")

    def test_send_email_without_auth(self, monkeypatch):
        """Test email sending without authentication."""
        monkeypatch.setenv("SMTP_HOST", "localhost")
        monkeypatch.delenv("SMTP_USERNAME", raising=False)
        monkeypatch.delenv("SMTP_PASSWORD", raising=False)
        monkeypatch.setenv("SMTP_USE_SSL", "false")

        mock_smtp = MagicMock()
        with patch("app.services.email.smtplib.SMTP", return_value=mock_smtp):
            result = send_email(
                None,
                "test@example.com",
                "Test Subject",
                "<p>Test Body</p>",
            )

        assert result is True
        mock_smtp.login.assert_not_called()

    def test_send_email_connection_failure(self, monkeypatch):
        """Test email sending with connection failure."""
        monkeypatch.setenv("SMTP_HOST", "nonexistent.example.com")
        monkeypatch.setenv("SMTP_USE_SSL", "false")

        with patch(
            "app.services.email.smtplib.SMTP",
            side_effect=smtplib.SMTPConnectError(421, "Connection refused"),
        ):
            result = send_email(
                None,
                "test@example.com",
                "Test Subject",
                "<p>Test Body</p>",
            )

        assert result is False

    def test_send_email_auth_failure(self, monkeypatch):
        """Test email sending with authentication failure."""
        monkeypatch.setenv("SMTP_HOST", "localhost")
        monkeypatch.setenv("SMTP_USERNAME", "user")
        monkeypatch.setenv("SMTP_PASSWORD", "wrong_pass")
        monkeypatch.setenv("SMTP_USE_SSL", "false")

        mock_smtp = MagicMock()
        mock_smtp.login.side_effect = smtplib.SMTPAuthenticationError(
            535, "Authentication failed"
        )
        with patch("app.services.email.smtplib.SMTP", return_value=mock_smtp):
            result = send_email(
                None,
                "test@example.com",
                "Test Subject",
                "<p>Test Body</p>",
            )

        assert result is False

    def test_send_email_recipient_refused(self, monkeypatch):
        """Test email sending with recipient refused."""
        monkeypatch.setenv("SMTP_HOST", "localhost")
        monkeypatch.setenv("SMTP_USE_SSL", "false")

        mock_smtp = MagicMock()
        mock_smtp.sendmail.side_effect = smtplib.SMTPRecipientsRefused(
            {"bad@example.com": (550, "User unknown")}
        )
        with patch("app.services.email.smtplib.SMTP", return_value=mock_smtp):
            result = send_email(
                None,
                "bad@example.com",
                "Test Subject",
                "<p>Test Body</p>",
            )

        assert result is False

    def test_send_email_server_disconnected(self, monkeypatch):
        """Test email sending with server disconnect."""
        monkeypatch.setenv("SMTP_HOST", "localhost")
        monkeypatch.setenv("SMTP_USE_SSL", "false")

        mock_smtp = MagicMock()
        mock_smtp.sendmail.side_effect = smtplib.SMTPServerDisconnected(
            "Connection lost"
        )
        with patch("app.services.email.smtplib.SMTP", return_value=mock_smtp):
            result = send_email(
                None,
                "test@example.com",
                "Test Subject",
                "<p>Test Body</p>",
            )

        assert result is False

    def test_send_email_generic_exception(self, monkeypatch):
        """Test email sending handles generic exceptions."""
        monkeypatch.setenv("SMTP_HOST", "localhost")
        monkeypatch.setenv("SMTP_USE_SSL", "false")

        with patch(
            "app.services.email.smtplib.SMTP",
            side_effect=Exception("Unexpected error"),
        ):
            result = send_email(
                None,
                "test@example.com",
                "Test Subject",
                "<p>Test Body</p>",
            )

        assert result is False


class TestSendPasswordResetEmail:
    """Tests for password reset email function."""

    def test_send_password_reset_email_success(self, monkeypatch):
        """Test successful password reset email."""
        monkeypatch.setenv("APP_URL", "https://app.example.com")
        monkeypatch.setenv("SMTP_USE_SSL", "false")

        mock_smtp = MagicMock()
        with patch("app.services.email.smtplib.SMTP", return_value=mock_smtp):
            result = send_password_reset_email(
                None,
                "user@example.com",
                "reset_token_123",
                "John Doe",
            )

        assert result is True
        # Verify sendmail was called with the recipient
        call_args = mock_smtp.sendmail.call_args
        assert "user@example.com" in call_args[0]

    def test_send_password_reset_email_with_default_name(self, monkeypatch):
        """Test password reset email with no person name."""
        monkeypatch.setenv("APP_URL", "https://app.example.com")
        monkeypatch.setenv("SMTP_USE_SSL", "false")

        mock_smtp = MagicMock()
        with patch("app.services.email.smtplib.SMTP", return_value=mock_smtp):
            result = send_password_reset_email(
                None,
                "user@example.com",
                "reset_token_123",
                None,  # No name provided
            )

        assert result is True

    def test_send_password_reset_email_default_app_url(self, monkeypatch):
        """Test password reset email with default APP_URL."""
        monkeypatch.delenv("APP_URL", raising=False)
        monkeypatch.setenv("SMTP_USE_SSL", "false")

        mock_smtp = MagicMock()
        with patch("app.services.email.smtplib.SMTP", return_value=mock_smtp):
            result = send_password_reset_email(
                None,
                "user@example.com",
                "reset_token_123",
                "Jane",
            )

        assert result is True

    def test_send_password_reset_email_failure(self, monkeypatch):
        """Test password reset email failure handling."""
        monkeypatch.setenv("APP_URL", "https://app.example.com")
        monkeypatch.setenv("SMTP_USE_SSL", "false")

        with patch(
            "app.services.email.smtplib.SMTP",
            side_effect=Exception("SMTP error"),
        ):
            result = send_password_reset_email(
                None,
                "user@example.com",
                "reset_token_123",
                "John",
            )

        assert result is False

    def test_send_password_reset_email_content(self, monkeypatch):
        """Test password reset email contains correct content."""
        monkeypatch.setenv("APP_URL", "https://app.example.com")
        monkeypatch.setenv("SMTP_USE_SSL", "false")

        mock_smtp = MagicMock()
        captured_message = None

        def capture_sendmail(from_email, to_email, message):
            nonlocal captured_message
            captured_message = message

        mock_smtp.sendmail.side_effect = capture_sendmail

        with patch("app.services.email.smtplib.SMTP", return_value=mock_smtp):
            send_password_reset_email(
                None,
                "user@example.com",
                "my_reset_token",
                "John",
            )

        assert captured_message is not None
        assert "my_reset_token" in captured_message
        assert "Reset" in captured_message or "reset" in captured_message

    def test_send_password_reset_email_includes_next_url(self, monkeypatch):
        """Test password reset email can include a post-login destination."""
        monkeypatch.setenv("APP_URL", "https://app.example.com")
        monkeypatch.setenv("SMTP_USE_SSL", "false")

        mock_smtp = MagicMock()
        captured_message = None

        def capture_sendmail(from_email, to_email, message):
            nonlocal captured_message
            captured_message = message

        mock_smtp.sendmail.side_effect = capture_sendmail

        with patch("app.services.email.smtplib.SMTP", return_value=mock_smtp):
            send_password_reset_email(
                None,
                "user@example.com",
                "my_reset_token",
                "John",
                next_url="/people/self/tax-info",
            )

        assert captured_message is not None
        assert "next=/people/self/tax-info" in captured_message

    def test_send_password_reset_email_includes_attachment(self, monkeypatch):
        """Test password reset email can include an attachment."""
        monkeypatch.setenv("APP_URL", "https://app.example.com")
        monkeypatch.setenv("SMTP_USE_SSL", "false")

        mock_smtp = MagicMock()
        captured_message = None

        def capture_sendmail(from_email, to_email, message):
            nonlocal captured_message
            captured_message = message

        mock_smtp.sendmail.side_effect = capture_sendmail

        with patch("app.services.email.smtplib.SMTP", return_value=mock_smtp):
            send_password_reset_email(
                None,
                "user@example.com",
                "my_reset_token",
                "John",
                attachments=[("welcome.pdf", b"%PDF-1.4", "application/pdf")],
            )

        assert captured_message is not None
        assert "welcome.pdf" in captured_message
        assert "application/pdf" in captured_message


class TestEmailLogging:
    """Tests for email logging behavior."""

    def test_send_email_logs_success(self, monkeypatch, caplog):
        """Test that successful email is logged."""
        monkeypatch.setenv("SMTP_USE_SSL", "false")

        mock_smtp = MagicMock()
        with patch("app.services.email.smtplib.SMTP", return_value=mock_smtp):
            import logging

            with caplog.at_level(logging.INFO):
                send_email(
                    None,
                    "test@example.com",
                    "Test",
                    "<p>Test</p>",
                )

        assert "Email sent to test@example.com" in caplog.text

    def test_send_email_logs_failure(self, monkeypatch, caplog):
        """Test that failed email is logged."""
        monkeypatch.setenv("SMTP_USE_SSL", "false")

        with patch(
            "app.services.email.smtplib.SMTP",
            side_effect=Exception("Test error"),
        ):
            import logging

            with caplog.at_level(logging.ERROR):
                send_email(
                    None,
                    "test@example.com",
                    "Test",
                    "<p>Test</p>",
                )

        assert "Failed to send email" in caplog.text

    def test_outbox_send_log_excludes_recipient_and_raw_smtp_error(self, caplog):
        import logging

        event_id = uuid4()
        with (
            patch(
                "app.services.email._smtp_pool.get_connection",
                side_effect=RuntimeError("private SMTP detail"),
            ),
            caplog.at_level(logging.ERROR),
        ):
            assert not send_email(
                None,
                "private@example.com",
                "Test",
                "<p>Test</p>",
                outbox_event_id=event_id,
            )

        assert str(event_id) in caplog.text
        assert "RuntimeError" in caplog.text
        assert "private@example.com" not in caplog.text
        assert "private SMTP detail" not in caplog.text


class TestAdminEmailSettingsTest:
    """Tests for non-persisting SMTP connection tests from the admin UI."""

    def test_global_test_uses_unsaved_form_values(self, db_session, monkeypatch):
        captured = {}

        def fake_validate(config):
            captured.update(config)
            return True, None

        monkeypatch.setattr("app.services.email.validate_smtp_config", fake_validate)

        from app.services.finance.settings_web import settings_web_service

        ok, message = settings_web_service.test_email_settings(
            db_session,
            uuid4(),
            {
                "smtp_host": "smtp.example.com",
                "smtp_port": "465",
                "smtp_username": "ops@example.com",
                "smtp_password": "secret",
                "smtp_use_tls": "false",
                "smtp_use_ssl": "true",
                "smtp_from_email": "ops@example.com",
                "smtp_from_name": "Ops",
                "email_reply_to": "help@example.com",
            },
            "global",
        )

        assert ok is True
        assert message == "Global SMTP connection succeeded."
        assert captured["host"] == "smtp.example.com"
        assert captured["port"] == 465
        assert captured["username"] == "ops@example.com"
        assert captured["password"] == "secret"
        assert captured["use_tls"] is False
        assert captured["use_ssl"] is True

    def test_module_test_uses_unsaved_module_values(self, db_session, monkeypatch):
        captured = {}

        def fake_validate(config):
            captured.update(config)
            return True, None

        monkeypatch.setattr("app.services.email.validate_smtp_config", fake_validate)

        from app.services.finance.settings_web import settings_web_service

        ok, message = settings_web_service.test_email_settings(
            db_session,
            uuid4(),
            {
                "module_finance_use_default": "false",
                "module_finance_smtp_host": "smtp.finance.example.com",
                "module_finance_smtp_port": "587",
                "module_finance_smtp_username": "finance@example.com",
                "module_finance_smtp_password": "finance-secret",
                "module_finance_smtp_use_tls": "true",
                "module_finance_smtp_use_ssl": "false",
                "module_finance_smtp_from_email": "finance@example.com",
                "module_finance_smtp_from_name": "Finance",
                "module_finance_email_reply_to": "finance-help@example.com",
            },
            "module:finance",
        )

        assert ok is True
        assert message == "Finance SMTP connection succeeded."
        assert captured["host"] == "smtp.finance.example.com"
        assert captured["port"] == 587
        assert captured["username"] == "finance@example.com"
        assert captured["password"] == "finance-secret"
        assert captured["use_tls"] is True
        assert captured["use_ssl"] is False
