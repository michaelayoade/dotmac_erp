"""Idempotent Mailcow provisioning and one-time mailbox activation."""

from __future__ import annotations

import hashlib
import secrets
import string
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.people.hr.employee import Employee, EmployeeStatus
from app.services.mailcow.client import MailcowClient
from app.services.mailcow.config import (
    MailcowProvisioningConfig,
    get_mailcow_provisioning_config,
)


@dataclass
class EmployeeMailboxProvisioningResult:
    employee_id: str
    email: str | None = None
    personal_email: str | None = None
    created: bool = False
    already_exists: bool = False
    activation_token: str | None = field(default=None, repr=False)
    activation_expires_at: datetime | None = None
    skipped: list[str] = field(default_factory=list)


def generate_initial_mailbox_password(length: int = 32) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*()-_=+"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def hash_mailbox_activation_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def parse_mailbox_activation_organization(token: str) -> UUID:
    organization_text, separator, secret = token.partition(".")
    if not separator or not secret:
        raise ValueError("Invalid or expired mailbox activation link")
    try:
        return UUID(organization_text)
    except ValueError as exc:
        raise ValueError("Invalid or expired mailbox activation link") from exc


class EmployeeMailboxProvisioningService:
    def __init__(
        self,
        db: Session,
        *,
        config: MailcowProvisioningConfig | None = None,
        mailcow_client: MailcowClient | None = None,
    ) -> None:
        self.db = db
        self.config = config or get_mailcow_provisioning_config()
        self._mailcow_client = mailcow_client

    def ensure_mailbox(
        self,
        organization_id: UUID,
        employee_id: UUID,
    ) -> EmployeeMailboxProvisioningResult:
        employee = self.db.scalar(
            select(Employee)
            .options(joinedload(Employee.person))
            .where(
                Employee.organization_id == organization_id,
                Employee.employee_id == employee_id,
            )
        )
        if not employee:
            raise RuntimeError(f"Employee {employee_id} is not committed yet")

        result = EmployeeMailboxProvisioningResult(employee_id=str(employee_id))
        if not self.config.enabled:
            result.skipped.append("mailcow provisioning integration disabled")
            return result
        if not self.config.mailcow_api_configured:
            raise RuntimeError("Mailcow provisioning API is not configured")
        if employee.status in {
            EmployeeStatus.RESIGNED,
            EmployeeStatus.TERMINATED,
            EmployeeStatus.RETIRED,
        }:
            result.skipped.append("employee is no longer provisionable")
            return result

        person = employee.person
        email = ((person.email if person else "") or "").strip().lower()
        personal_email = (employee.personal_email or "").strip().lower()
        result.email = email or None
        result.personal_email = personal_email or None
        local_part, separator, domain = email.partition("@")
        if not separator or not local_part or not domain:
            result.skipped.append("employee has no valid work email")
            return result
        if domain != self.config.domain:
            result.skipped.append(
                f"work email domain {domain} is not managed by Mailcow"
            )
            return result
        if not personal_email or "@" not in personal_email:
            raise RuntimeError(
                "A valid personal email is required for mailbox activation"
            )
        if personal_email == email:
            raise RuntimeError("Personal email must be different from the work email")

        mailcow = self._get_mailcow_client()
        if mailcow.get_mailbox(email):
            result.already_exists = True
            self._issue_activation_if_needed(employee, result)
            return result

        mailcow.create_mailbox(
            email,
            name=person.name or employee.employee_code,
            password=generate_initial_mailbox_password(),
            quota_mb=self.config.quota_mb,
            force_password_update=True,
        )
        if not mailcow.get_mailbox(email):
            raise RuntimeError(
                f"Mailcow reported success but mailbox {email} was not readable"
            )
        result.created = True
        employee.mailcow_mailbox_provisioned_at = datetime.now(timezone.utc)
        self._issue_activation_if_needed(employee, result)
        return result

    def _issue_activation_if_needed(
        self,
        employee: Employee,
        result: EmployeeMailboxProvisioningResult,
    ) -> None:
        if employee.mailcow_activated_at is not None:
            return
        if employee.mailcow_provisioning_requested_at is None:
            result.skipped.append("existing mailbox was not provisioned by ERP")
            return
        now = datetime.now(timezone.utc)
        expires_at = employee.mailcow_activation_expires_at
        if (
            employee.mailcow_activation_sent_at is not None
            and expires_at is not None
            and expires_at > now
        ):
            result.skipped.append("mailbox activation email already sent")
            return
        token = f"{employee.organization_id}.{secrets.token_urlsafe(32)}"
        employee.mailcow_activation_token_hash = hash_mailbox_activation_token(token)
        employee.mailcow_activation_expires_at = now + timedelta(
            hours=self.config.activation_ttl_hours
        )
        employee.mailcow_activation_sent_at = None
        result.activation_token = token
        result.activation_expires_at = employee.mailcow_activation_expires_at

    def mark_activation_sent(self, employee_id: UUID, token: str) -> None:
        organization_id = parse_mailbox_activation_organization(token)
        employee = self.db.scalar(
            select(Employee).where(
                Employee.employee_id == employee_id,
                Employee.organization_id == organization_id,
                Employee.mailcow_activation_token_hash
                == hash_mailbox_activation_token(token),
            )
        )
        if employee:
            employee.mailcow_activation_sent_at = datetime.now(timezone.utc)

    def _get_mailcow_client(self) -> MailcowClient:
        if self._mailcow_client:
            return self._mailcow_client
        self._mailcow_client = MailcowClient(
            base_url=self.config.base_url,
            api_key=self.config.api_key or "",
            timeout=self.config.request_timeout,
        )
        return self._mailcow_client


def activate_employee_mailbox(token: str, new_password: str) -> tuple[str, datetime]:
    from app.db.session_context import session_for_org
    from app.schemas.auth_flow import validate_password_strength

    organization_id = parse_mailbox_activation_organization(token)
    validate_password_strength(new_password)
    token_hash = hash_mailbox_activation_token(token)
    now = datetime.now(timezone.utc)
    with session_for_org(organization_id) as db:
        employee = db.scalar(
            select(Employee)
            .options(joinedload(Employee.person))
            .where(
                Employee.organization_id == organization_id,
                Employee.mailcow_activation_token_hash == token_hash,
            )
            .with_for_update()
        )
        expires_at = employee.mailcow_activation_expires_at if employee else None
        if (
            not employee
            or employee.mailcow_activated_at is not None
            or expires_at is None
            or expires_at <= now
        ):
            raise ValueError("Invalid or expired mailbox activation link")
        email = (
            ((employee.person.email if employee.person else "") or "").strip().lower()
        )
        if not email:
            raise ValueError("Invalid or expired mailbox activation link")
        config = get_mailcow_provisioning_config()
        if not config.enabled or not config.mailcow_api_configured:
            raise RuntimeError("Mailbox activation is temporarily unavailable")
        MailcowClient(
            base_url=config.base_url,
            api_key=config.api_key or "",
            timeout=config.request_timeout,
        ).update_mailbox_password(email, new_password, active=True)
        employee.mailcow_activated_at = now
        employee.mailcow_activation_token_hash = None
        employee.mailcow_activation_expires_at = None
        db.commit()
        return email, now
