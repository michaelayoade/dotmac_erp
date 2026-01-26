import logging
import os
import smtplib
import socket
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from sqlalchemy.orm import Session

from app.models.domain_settings import SettingDomain

logger = logging.getLogger(__name__)


def _env_value(name: str) -> str | None:
    value = os.getenv(name)
    if value is None or value == "":
        return None
    return value


def _env_int(name: str, default: int) -> int:
    raw = _env_value(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = _env_value(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _get_db_setting(db: Session | None, key: str) -> object | None:
    """Get a setting value from the database."""
    if db is None:
        return None
    try:
        from app.services.settings_spec import resolve_value
        return resolve_value(db, SettingDomain.email, key)
    except (ImportError, AttributeError, KeyError) as exc:
        logger.debug("Could not resolve email setting %s: %s", key, exc)
        return None


def _get_smtp_config(db: Session | None = None) -> dict:
    """Get SMTP config from database first, then fall back to environment variables."""
    # Try DB settings first, then env vars, then defaults
    host = _get_db_setting(db, "smtp_host") or _env_value("SMTP_HOST") or "localhost"
    port = _get_db_setting(db, "smtp_port") or _env_int("SMTP_PORT", 587)
    username = _get_db_setting(db, "smtp_username") or _env_value("SMTP_USERNAME")
    password = _get_db_setting(db, "smtp_password") or _env_value("SMTP_PASSWORD")

    # Boolean settings
    use_tls_db = _get_db_setting(db, "smtp_use_tls")
    use_tls = use_tls_db if use_tls_db is not None else _env_bool("SMTP_USE_TLS", True)

    use_ssl_db = _get_db_setting(db, "smtp_use_ssl")
    use_ssl = use_ssl_db if use_ssl_db is not None else _env_bool("SMTP_USE_SSL", False)

    from_email = _get_db_setting(db, "smtp_from_email") or _env_value("SMTP_FROM_EMAIL") or "noreply@example.com"
    from_name = _get_db_setting(db, "smtp_from_name") or _env_value("SMTP_FROM_NAME") or "Dotmac ERP"
    reply_to = _get_db_setting(db, "email_reply_to") or _env_value("EMAIL_REPLY_TO")

    port_int = 587
    if port is not None:
        try:
            port_int = int(str(port))
        except (TypeError, ValueError):
            port_int = 587

    return {
        "host": host,
        "port": port_int,
        "username": username,
        "password": password,
        "use_tls": bool(use_tls),
        "use_ssl": bool(use_ssl),
        "from_email": from_email,
        "from_name": from_name,
        "reply_to": reply_to,
    }


def validate_smtp_config(config: dict, timeout_seconds: int = 10) -> tuple[bool, str | None]:
    """Validate SMTP settings by attempting a connection and (optional) auth."""
    host = str(config.get("host") or "").strip()
    if not host:
        return False, "SMTP host is required."

    try:
        port = int(config.get("port") or 0)
    except (TypeError, ValueError):
        return False, "SMTP port must be a valid integer."
    if port <= 0:
        return False, "SMTP port must be a positive integer."

    use_tls = bool(config.get("use_tls"))
    use_ssl = bool(config.get("use_ssl"))
    if use_tls and use_ssl:
        return False, "SMTP TLS and SSL cannot both be enabled."

    username = config.get("username")
    password = config.get("password")
    if (username and not password) or (password and not username):
        return False, "SMTP username and password must both be set."

    server: smtplib.SMTP | smtplib.SMTP_SSL | None = None
    try:
        if use_ssl:
            server = smtplib.SMTP_SSL(host, port, timeout=timeout_seconds)
        else:
            server = smtplib.SMTP(host, port, timeout=timeout_seconds)

        server.ehlo()
        if use_tls and not use_ssl:
            server.starttls()
            server.ehlo()

        if username and password:
            server.login(username, password)

        # NOOP ensures server accepts commands after connect/auth
        server.noop()
        return True, None
    except smtplib.SMTPAuthenticationError:
        logger.error("SMTP validation failed for host %s:%s: authentication failed", host, port)
        return False, "SMTP authentication failed. Check username and password."
    except smtplib.SMTPConnectError as exc:
        logger.error("SMTP validation failed for host %s:%s: %s", host, port, exc)
        return False, "SMTP connection failed. Check host/port and firewall."
    except (socket.gaierror, ssl.SSLError, OSError) as exc:
        logger.error("SMTP validation failed for host %s:%s: %s", host, port, exc)
        if isinstance(exc, OSError) and getattr(exc, "errno", None) == 111:
            return False, "SMTP connection refused. Check host/port and firewall."
        return False, "Unable to connect to the SMTP server with the provided settings."
    except Exception as exc:
        logger.error("SMTP validation failed for host %s:%s: %s", host, port, exc)
        return False, "Unable to connect to the SMTP server with the provided settings."
    finally:
        if server:
            try:
                server.quit()
            except (smtplib.SMTPException, OSError) as quit_exc:
                logger.debug("SMTP quit failed during validation: %s", quit_exc)
                try:
                    server.close()
                except (smtplib.SMTPException, OSError) as close_exc:
                    logger.debug("SMTP close also failed during validation: %s", close_exc)


def send_email(
    db: Session | None,
    to_email: str,
    subject: str,
    body_html: str,
    body_text: str | None = None,
) -> bool:
    """Send an email using SMTP settings from database or environment."""
    config = _get_smtp_config(db)
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{config['from_name']} <{config['from_email']}>"
    msg["To"] = to_email

    # Add Reply-To header if configured
    if config.get("reply_to"):
        msg["Reply-To"] = config["reply_to"]

    if body_text:
        msg.attach(MIMEText(body_text, "plain"))
    msg.attach(MIMEText(body_html, "html"))

    server: smtplib.SMTP | smtplib.SMTP_SSL | None = None
    timeout_seconds = 10
    try:
        if config["use_ssl"]:
            server = smtplib.SMTP_SSL(config["host"], config["port"], timeout=timeout_seconds)
        else:
            server = smtplib.SMTP(config["host"], config["port"], timeout=timeout_seconds)

        if config["use_tls"] and not config["use_ssl"]:
            server.starttls()

        if config["username"] and config["password"]:
            server.login(config["username"], config["password"])

        server.sendmail(config["from_email"], to_email, msg.as_string())

        logger.info("Email sent to %s", to_email)
        return True
    except Exception as exc:
        logger.error("Failed to send email to %s: %s", to_email, exc)
        return False
    finally:
        if server:
            try:
                server.quit()
            except (smtplib.SMTPException, OSError) as quit_exc:
                logger.debug("SMTP quit failed, trying close: %s", quit_exc)
                try:
                    server.close()
                except (smtplib.SMTPException, OSError) as close_exc:
                    logger.debug("SMTP close also failed: %s", close_exc)


def send_password_reset_email(
    db: Session | None,
    to_email: str,
    reset_token: str,
    person_name: str | None = None,
    app_url: str | None = None,
) -> bool:
    name = person_name or "there"
    env_app_url = _env_value("APP_URL")
    resolved_app_url = env_app_url or app_url or "http://localhost:8000"
    reset_link = f"{resolved_app_url.rstrip('/')}/reset-password?token={reset_token}"
    subject = "Reset your password"
    body_html = (
        f"<p>Hi {name},</p>"
        "<p>Use the link below to reset your password:</p>"
        f'<p><a href="{reset_link}">Reset password</a></p>'
    )
    body_text = f"Hi {name}, use this link to reset your password: {reset_link}"
    return send_email(db, to_email, subject, body_html, body_text)
