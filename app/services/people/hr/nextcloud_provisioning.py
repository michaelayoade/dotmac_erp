"""Forward-only Nextcloud provisioning for ERP-created employees."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.people.hr.employee import Employee, EmployeeStatus
from app.services.nextcloud.client import (
    NextcloudProvisioningConfig,
    NextcloudTalkClient,
)

_EXITED_STATUSES = frozenset(
    {
        EmployeeStatus.RESIGNED,
        EmployeeStatus.TERMINATED,
        EmployeeStatus.RETIRED,
    }
)


class NextcloudIdentityConflictError(ValueError):
    """The desired Nextcloud identity exists without an ERP binding."""


@dataclass
class EmployeeNextcloudProvisioningResult:
    employee_id: str
    user_id: str | None = None
    created: bool = False
    already_exists: bool = False
    enabled: bool = False
    skipped: list[str] = field(default_factory=list)


class EmployeeNextcloudProvisioningService:
    """Create and reconcile an employee's ERP-owned Nextcloud identity."""

    def __init__(
        self,
        db: Session,
        *,
        config: NextcloudProvisioningConfig | None = None,
        nextcloud_client: NextcloudTalkClient | None = None,
    ) -> None:
        self.db = db
        self.config = config or NextcloudProvisioningConfig.from_settings()
        self._nextcloud_client = nextcloud_client

    def ensure_account(
        self,
        organization_id: UUID,
        employee_id: UUID,
    ) -> EmployeeNextcloudProvisioningResult:
        employee = self.db.scalar(
            select(Employee)
            .options(joinedload(Employee.person))
            .where(
                Employee.organization_id == organization_id,
                Employee.employee_id == employee_id,
            )
            .with_for_update()
        )
        if not employee:
            raise RuntimeError(f"Employee {employee_id} is not committed yet")

        result = EmployeeNextcloudProvisioningResult(employee_id=str(employee_id))
        if not self.config.enabled:
            result.skipped.append("nextcloud provisioning integration disabled")
            return result
        if not self.config.configured:
            raise RuntimeError("Nextcloud provisioning API is not configured")
        if employee.mailcow_provisioning_requested_at is None:
            result.skipped.append("employee has no forward provisioning request")
            return result
        if employee.status in _EXITED_STATUSES:
            result.skipped.append("employee is no longer provisionable")
            return result
        if employee.mailcow_mailbox_provisioned_at is None:
            result.skipped.append("employee Mailcow mailbox is not provisioned")
            return result

        person = employee.person
        if not person:
            raise RuntimeError("Employee has no linked person")
        email = (person.email or "").strip().lower()
        if not email or "@" not in email:
            raise RuntimeError("Employee has no valid work email")

        nextcloud = self._get_client()
        bound_user_id = (person.nextcloud_user_id or "").strip()
        desired_user_id = bound_user_id or email
        existing = nextcloud.get_user(desired_user_id)

        if existing is not None and not bound_user_id:
            raise NextcloudIdentityConflictError(
                "Nextcloud user already exists without an ERP identity binding"
            )
        if existing is not None:
            existing_email = str(existing.get("email") or "").strip().lower()
            if existing_email and existing_email != email:
                raise NextcloudIdentityConflictError(
                    "ERP-bound Nextcloud user has a different email address"
                )
            result.already_exists = True
            if existing.get("enabled") is False:
                nextcloud.enable_user(desired_user_id)
                result.enabled = True
            groups = existing.get("groups")
            if not isinstance(groups, list) or self.config.group not in groups:
                nextcloud.add_user_to_group(desired_user_id, self.config.group)
        else:
            nextcloud.create_user(
                desired_user_id,
                email=email,
                display_name=person.name or employee.employee_code,
                group=self.config.group,
                quota=self.config.quota,
            )
            created_user = nextcloud.get_user(desired_user_id)
            if created_user is None:
                raise RuntimeError(
                    "Nextcloud reported success but the employee account was not readable"
                )
            result.created = True

        person.nextcloud_user_id = desired_user_id
        result.user_id = desired_user_id
        return result

    def _get_client(self) -> NextcloudTalkClient:
        if self._nextcloud_client is None:
            self._nextcloud_client = NextcloudTalkClient(self.config)
        return self._nextcloud_client
