"""
Email Module Background Tasks - Celery tasks for email sending.

Handles:
- Async email sending (non-blocking for HTTP requests)
- Email with attachments (base64 encoded)
- Smart retry logic for transient vs permanent failures
"""

import base64
import logging
import smtplib
import socket
import uuid
from typing import Any

import httpx
from celery import shared_task

from app.db.session_context import cross_org_session, session_for_org
from app.services.common import coerce_uuid

logger = logging.getLogger(__name__)


# SMTP error codes that indicate permanent failures (don't retry)
# 5xx errors are generally permanent, but some are transient
PERMANENT_SMTP_CODES = frozenset(
    {
        550,  # Mailbox unavailable, user unknown
        551,  # User not local
        552,  # Exceeded storage allocation
        553,  # Mailbox name not allowed
        554,  # Transaction failed
        530,  # Authentication required (config issue)
        535,  # Authentication credentials invalid
        556,  # Domain does not accept mail
    }
)

# Transient codes that should be retried (4xx and some 5xx)
TRANSIENT_SMTP_CODES = frozenset(
    {
        421,  # Service not available, closing connection
        450,  # Mailbox temporarily unavailable
        451,  # Local error in processing
        452,  # Insufficient system storage
    }
)


class PermanentEmailError(Exception):
    """Email error that should not be retried."""

    pass


class TransientEmailError(Exception):
    """Email error that may succeed on retry."""

    pass


def classify_email_error(exc: Exception) -> type[Exception]:
    """
    Classify an email exception as permanent or transient.

    Args:
        exc: The exception that occurred

    Returns:
        PermanentEmailError or TransientEmailError class
    """
    # SMTP authentication errors are permanent (bad config)
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return PermanentEmailError

    # Check SMTP response codes
    if isinstance(exc, smtplib.SMTPResponseException):
        code = exc.smtp_code
        if code in PERMANENT_SMTP_CODES:
            return PermanentEmailError
        if code in TRANSIENT_SMTP_CODES or (400 <= code < 500):
            return TransientEmailError
        # Unknown 5xx codes default to permanent
        if code >= 500:
            return PermanentEmailError

    # Recipient refused - check the error codes
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        # All recipients permanently rejected
        for _addr, (code, _msg) in exc.recipients.items():
            if code in PERMANENT_SMTP_CODES:
                return PermanentEmailError
        return TransientEmailError

    # Sender refused is usually permanent (bad config)
    if isinstance(exc, smtplib.SMTPSenderRefused):
        return PermanentEmailError

    # Connection errors are transient (network issues)
    if isinstance(
        exc,
        (
            smtplib.SMTPConnectError,
            smtplib.SMTPServerDisconnected,
            socket.timeout,
            socket.gaierror,  # DNS resolution failure
            ConnectionRefusedError,
            ConnectionResetError,
            OSError,  # General network errors
        ),
    ):
        return TransientEmailError

    # Default: treat unknown errors as transient (safer to retry)
    return TransientEmailError


@shared_task(
    name="app.tasks.hr.run_employee_mailcow_provisioning",
    bind=True,
    max_retries=5,
    autoretry_for=(httpx.TransportError, RuntimeError),
    retry_backoff=True,
    retry_backoff_max=900,
    retry_jitter=True,
)
def run_employee_mailcow_provisioning(
    self,
    employee_id: str,
    organization_id: str,
) -> dict[str, Any]:
    """Create an employee work mailbox and deliver its activation link."""
    from app.models.people.hr.employee import Employee
    from app.services.email import send_mailbox_activation_email
    from app.services.people.hr.mailbox_provisioning import (
        EmployeeMailboxProvisioningService,
    )

    org_uuid = uuid.UUID(organization_id)
    employee_uuid = uuid.UUID(employee_id)
    with session_for_org(org_uuid) as db:
        provisioning_service = EmployeeMailboxProvisioningService(db)
        result = provisioning_service.ensure_mailbox(org_uuid, employee_uuid)
        db.commit()
        if result.activation_token:
            employee = db.get(Employee, employee_uuid)
            try:
                send_mailbox_activation_email(
                    db,
                    result.personal_email or "",
                    result.email or "",
                    result.activation_token,
                    employee.person.name if employee and employee.person else None,
                    org_uuid,
                    raise_on_error=True,
                )
            except Exception as exc:
                raise RuntimeError("Could not send mailbox activation email") from exc
            provisioning_service.mark_activation_sent(
                employee_uuid,
                result.activation_token,
            )
            db.commit()
        return {
            "employee_id": result.employee_id,
            "email": result.email,
            "created": result.created,
            "already_exists": result.already_exists,
            "skipped": result.skipped,
        }


@shared_task(
    bind=True,
    max_retries=3,
    autoretry_for=(TransientEmailError,),  # Only auto-retry transient errors
    retry_backoff=True,  # Exponential backoff: 1s, 2s, 4s...
    retry_backoff_max=300,  # Max 5 minutes between retries
    retry_jitter=True,  # Add randomness to avoid thundering herd
)
def send_email_async(
    self,
    to_email: str,
    subject: str,
    body_html: str,
    body_text: str | None = None,
    attachments_b64: list[dict[str, str]] | None = None,
    module: str | None = None,
    organization_id: str | None = None,
) -> dict[str, Any]:
    """
    Asynchronously send an email.

    Called via `.delay()` to avoid blocking HTTP requests.
    Uses smart retry logic:
    - Transient failures (network, temporary server issues): retry with backoff
    - Permanent failures (invalid address, auth errors): fail immediately

    Args:
        to_email: Recipient email address
        subject: Email subject
        body_html: HTML body content
        body_text: Plain text body content (optional)
        attachments_b64: List of attachments as dicts with:
            - filename: str
            - data_b64: base64-encoded file data
            - mime_type: str (e.g., "application/pdf")

    Returns:
        Dict with success status and error details if any
    """
    from app.models.email_profile import EmailModule
    from app.services.email import send_email

    logger.info(
        "Sending email to %s: %s (attempt %d)",
        to_email,
        subject,
        self.request.retries + 1,
    )

    result: dict[str, Any] = {
        "success": False,
        "to_email": to_email,
        "subject": subject,
        "error": None,
        "retry_count": self.request.retries,
        "is_permanent_failure": False,
    }

    try:
        # Decode attachments from base64
        attachments: list[tuple[str, bytes, str]] | None = None
        if attachments_b64:
            attachments = []
            for att in attachments_b64:
                filename = att["filename"]
                data = base64.b64decode(att["data_b64"])
                mime_type = att["mime_type"]
                attachments.append((filename, data, mime_type))

        # Use a tenant-aware task session to get SMTP settings from DB
        # Use raise_on_error=True to get the actual exception for classification
        org_uuid = coerce_uuid(organization_id) if organization_id else None
        session_context = session_for_org(org_uuid) if org_uuid else cross_org_session()
        with session_context as db:
            module_enum = EmailModule(module) if module else None
            send_email(
                db=db,
                to_email=to_email,
                subject=subject,
                body_html=body_html,
                body_text=body_text,
                attachments=attachments,
                raise_on_error=True,
                module=module_enum,
                organization_id=org_uuid,
            )

        result["success"] = True
        logger.info("Email sent successfully to %s", to_email)

    except (PermanentEmailError, TransientEmailError):
        # Re-raise our classified errors for Celery to handle
        raise

    except Exception as e:
        # Classify the exception and raise appropriate error type
        error_class = classify_email_error(e)
        result["error"] = str(e)

        if error_class is PermanentEmailError:
            result["is_permanent_failure"] = True
            logger.error(
                "Permanent email failure to %s: %s (not retrying)", to_email, e
            )
            # Don't retry - return result with failure info
            return result
        else:
            logger.warning(
                "Transient email failure to %s: %s (will retry)", to_email, e
            )
            raise TransientEmailError(str(e)) from e

    return result


def queue_email(
    to_email: str,
    subject: str,
    body_html: str,
    body_text: str | None = None,
    attachments: list[tuple[str, bytes, str]] | None = None,
    module: str | None = None,
    organization_id: str | None = None,
) -> None:
    """
    Queue an email for async delivery via Celery.

    This is a convenience wrapper that handles attachment encoding.

    Args:
        to_email: Recipient email address
        subject: Email subject
        body_html: HTML body content
        body_text: Plain text body content (optional)
        attachments: List of attachments as (filename, data, mime_type) tuples
    """
    # Encode attachments to base64 for JSON serialization
    attachments_b64: list[dict[str, str]] | None = None
    if attachments:
        attachments_b64 = []
        for filename, data, mime_type in attachments:
            attachments_b64.append(
                {
                    "filename": filename,
                    "data_b64": base64.b64encode(data).decode("ascii"),
                    "mime_type": mime_type,
                }
            )

    send_email_async.delay(
        to_email=to_email,
        subject=subject,
        body_html=body_html,
        body_text=body_text,
        attachments_b64=attachments_b64,
        module=module,
        organization_id=organization_id,
    )
