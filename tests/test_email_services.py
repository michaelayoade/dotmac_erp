"""Tests for email service - failure handling and configuration."""

import smtplib
from unittest.mock import MagicMock, patch

import pytest

from app.services.email import (
    _env_bool,
    _env_int,
    _env_value,
    _get_smtp_config,
    send_email,
    send_password_reset_email,
    validate_smtp_config,
)


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
        mock_smtp.sendmail.side_effect = smtplib.SMTPServerDisconnected("Connection lost")
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
