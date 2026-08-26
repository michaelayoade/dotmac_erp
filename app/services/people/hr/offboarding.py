"""Employee email and access offboarding orchestration."""

from __future__ import annotations

import logging
import secrets
import string
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.people.hr.employee import Employee, EmployeeStatus
from app.models.person import Person
from app.services.application_lifecycle import ApplicationAccessLifecycle
from app.services.mailcow.client import MailcowClient
from app.services.mailcow.cleanup_queue import SogoCleanupQueueClient
from app.services.mailcow.config import (
    MailcowOffboardingConfig,
    get_mailcow_offboarding_config,
)
from app.services.mailcow.sieve import (
    ManageSieveClient,
    ManageSieveConfig,
    build_offboarding_sieve_script,
    remove_redirect_from_sieve,
)
from app.services.mailcow.sogo import SogoProfileService

logger = logging.getLogger(__name__)

OFFBOARDING_STATUSES = {EmployeeStatus.RESIGNED, EmployeeStatus.TERMINATED}


@dataclass
class EmployeeOffboardingResult:
    employee_id: str
    email: str | None = None
    status: str | None = None
    erp_credentials_disabled: int = 0
    erp_sessions_revoked: int = 0
    person_deactivated: bool = False
    mailcow_enabled: bool = False
    mailcow_mailbox_found: bool | None = None
    mailcow_password_reset: bool = False
    sieve_offboarding_script_updated: bool = False
    sogo_inactive_forward_updated: bool = False
    sogo_cleanup_request_queued: bool = False
    shared_profiles_cleaned: list[str] = field(default_factory=list)
    shared_sieve_scripts_cleaned: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def should_offboard_status(status: EmployeeStatus | None) -> bool:
    return status in OFFBOARDING_STATUSES


def generate_mailbox_password(length: int = 32) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*()-_=+"
    return "".join(secrets.choice(alphabet) for _ in range(length))


class EmployeeOffboardingService:
    def __init__(
        self,
        db: Session,
        *,
        config: MailcowOffboardingConfig | None = None,
        mailcow_client: MailcowClient | None = None,
        sieve_client: ManageSieveClient | None = None,
        sogo_service: SogoProfileService | None = None,
        sogo_cleanup_client: SogoCleanupQueueClient | None = None,
    ) -> None:
        self.db = db
        self.config = config or get_mailcow_offboarding_config()
        self._mailcow_client = mailcow_client
        self._sieve_client = sieve_client
        self._sogo_service = sogo_service
        self._sogo_cleanup_client = sogo_cleanup_client

    def offboard_employee(
        self, organization_id: UUID, employee_id: UUID
    ) -> EmployeeOffboardingResult:
        employee = self.db.scalar(
            select(Employee)
            .options(joinedload(Employee.person))
            .where(
                Employee.organization_id == organization_id,
                Employee.employee_id == employee_id,
            )
        )
        if not employee:
            raise ValueError(f"Employee {employee_id} not found")

        result = EmployeeOffboardingResult(
            employee_id=str(employee.employee_id),
            status=employee.status.value if employee.status else None,
        )
        if not should_offboard_status(employee.status):
            result.skipped.append("employee status is not offboarding-eligible")
            return result

        person = employee.person or self.db.get(Person, employee.person_id)
        if not person:
            result.errors.append("employee has no linked person")
            return result

        access_change = ApplicationAccessLifecycle(self.db).deactivate(
            organization_id, person.id
        )
        result.erp_credentials_disabled = access_change.credentials_changed
        result.erp_sessions_revoked = access_change.sessions_revoked
        result.person_deactivated = access_change.changed

        email = (person.email or "").strip().lower()
        result.email = email
        if not email:
            result.errors.append("linked person has no email")
            return result

        if not self.config.enabled:
            result.skipped.append("mailcow offboarding integration disabled")
            return result

        result.mailcow_enabled = True
        self._run_mailcow_steps(employee, person, email, result)
        return result

    def _run_mailcow_steps(
        self,
        employee: Employee,
        person: Person,
        email: str,
        result: EmployeeOffboardingResult,
    ) -> None:
        try:
            mailcow = self._get_mailcow_client()
            mailbox = mailcow.get_mailbox(email)
            result.mailcow_mailbox_found = bool(mailbox)
            if mailbox:
                mailcow.update_mailbox_password(
                    email,
                    generate_mailbox_password(),
                    active=True,
                )
                result.mailcow_password_reset = True
        except Exception as exc:
            logger.exception("Mailcow mailbox password reset failed for %s", email)
            result.errors.append(f"mailcow password reset failed: {exc}")

        full_name = person.name or getattr(employee, "full_name", None) or email
        script = build_offboarding_sieve_script(
            full_name=full_name,
            email=email,
            forward_to=self.config.inactive_forward_to,
            subject=self.config.autoresponder_subject,
            message_template=self.config.autoresponder_template,
        )
        try:
            self._get_sieve_client().put_and_activate_script(
                email,
                self.config.sieve_script_name,
                script,
            )
            result.sieve_offboarding_script_updated = True
        except Exception as exc:
            logger.exception(
                "ManageSieve offboarding script update failed for %s", email
            )
            result.errors.append(f"sieve offboarding update failed: {exc}")

        self._request_sogo_cleanup(email, result)

        if not self.config.sogo_db_configured and self._sogo_service is None:
            result.skipped.append("Mailcow SOGo DB cleanup is not configured")
            return

        try:
            sogo = self._get_sogo_service()
            result.sogo_inactive_forward_updated = sogo.set_inactive_forward(
                email,
                self.config.inactive_forward_to,
            )
            result.shared_profiles_cleaned = sogo.cleanup_forwarding_references(email)
        except Exception as exc:
            logger.exception("SOGo profile cleanup failed for %s", email)
            result.errors.append(f"sogo cleanup failed: {exc}")
            return

        self._cleanup_shared_sieve_scripts(email, result)

    def _request_sogo_cleanup(
        self,
        email: str,
        result: EmployeeOffboardingResult,
    ) -> None:
        if (
            not self.config.sogo_cleanup_receiver_configured
            and self._sogo_cleanup_client is None
        ):
            result.skipped.append("Mailcow SOGo cleanup receiver is not configured")
            return

        try:
            result.sogo_cleanup_request_queued = (
                self._get_sogo_cleanup_client().enqueue(email)
            )
        except Exception as exc:
            logger.exception("SOGo cleanup request failed during employee offboarding")
            result.errors.append(f"sogo cleanup request failed: {exc}")

    def _cleanup_shared_sieve_scripts(
        self,
        email: str,
        result: EmployeeOffboardingResult,
    ) -> None:
        sieve = self._get_sieve_client()
        for mailbox in result.shared_profiles_cleaned:
            try:
                active_name, script = sieve.get_active_script(mailbox)
                if not active_name or script is None:
                    result.skipped.append(f"{mailbox}: no active sieve script")
                    continue
                updated, changed = remove_redirect_from_sieve(script, email)
                if not changed:
                    continue
                sieve.put_and_activate_script(mailbox, active_name, updated)
                result.shared_sieve_scripts_cleaned.append(mailbox)
            except Exception as exc:
                logger.warning(
                    "Skipping shared mailbox %s during offboarding cleanup: %s",
                    mailbox,
                    exc,
                )
                result.skipped.append(f"{mailbox}: {exc}")

    def _get_mailcow_client(self) -> MailcowClient:
        if self._mailcow_client:
            return self._mailcow_client
        if not self.config.mailcow_api_configured:
            raise RuntimeError("Mailcow API is not configured")
        self._mailcow_client = MailcowClient(
            base_url=self.config.base_url,
            api_key=self.config.api_key or "",
            timeout=self.config.request_timeout,
        )
        return self._mailcow_client

    def _get_sieve_client(self) -> ManageSieveClient:
        if self._sieve_client:
            return self._sieve_client
        if not self.config.sieve_configured:
            raise RuntimeError("Mailcow ManageSieve is not configured")
        self._sieve_client = ManageSieveClient(
            ManageSieveConfig(
                host=self.config.sieve_host,
                port=self.config.sieve_port,
                master_user=self.config.sieve_master_user or "",
                master_password=self.config.sieve_master_password or "",
                use_starttls=self.config.sieve_use_starttls,
                timeout=self.config.request_timeout,
            )
        )
        return self._sieve_client

    def _get_sogo_service(self) -> SogoProfileService:
        if self._sogo_service:
            return self._sogo_service
        if not self.config.sogo_db_configured:
            raise RuntimeError("Mailcow SOGo DB is not configured")
        self._sogo_service = SogoProfileService(
            host=self.config.sogo_db_host,
            port=self.config.sogo_db_port,
            database=self.config.sogo_db_name,
            user=self.config.sogo_db_user or "",
            password=self.config.sogo_db_password or "",
        )
        return self._sogo_service

    def _get_sogo_cleanup_client(self) -> SogoCleanupQueueClient:
        if self._sogo_cleanup_client:
            return self._sogo_cleanup_client
        if not self.config.sogo_cleanup_receiver_configured:
            raise RuntimeError("Mailcow SOGo cleanup receiver is not configured")
        self._sogo_cleanup_client = SogoCleanupQueueClient(
            url=self.config.sogo_cleanup_url,
            token=self.config.sogo_cleanup_token or "",
            timeout=self.config.request_timeout,
        )
        return self._sogo_cleanup_client


def queue_employee_mailcow_offboarding(
    employee_id: UUID,
    organization_id: UUID,
    status: EmployeeStatus,
) -> None:
    if not should_offboard_status(status):
        return
    try:
        from app.tasks.hr import run_employee_mailcow_offboarding

        run_employee_mailcow_offboarding.delay(
            str(employee_id),
            str(organization_id),
            status.value,
        )
    except Exception:
        logger.exception(
            "Failed to enqueue Mailcow offboarding for employee %s",
            employee_id,
        )
