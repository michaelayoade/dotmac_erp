"""
Staff sync: mirror employee lifecycle into dotmac_sub staff accounts.

ERP is the HR system of record. On hire (ACTIVE) the employee gets a
dotmac_sub SystemUser (created + invited, or re-enabled); on exit
(TERMINATED / RESIGNED / RETIRED / SUSPENDED) the account is disabled —
dotmac_sub revokes live sessions on deactivation.

``sync_employee`` is a one-employee idempotent push, safe to call from
lifecycle events and from the nightly reconcile sweep alike.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.config import settings
from app.models.people.hr.employee import Employee, EmployeeStatus
from app.services.dotmac_sub.client import DotmacSubClient, DotmacSubConfig

logger = logging.getLogger(__name__)

# Statuses whose dotmac_sub account must be enabled / disabled.
_ENABLED_STATUSES = {EmployeeStatus.ACTIVE}
_DISABLED_STATUSES = {
    EmployeeStatus.SUSPENDED,
    EmployeeStatus.RESIGNED,
    EmployeeStatus.TERMINATED,
    EmployeeStatus.RETIRED,
}


def _staff_email(employee: Employee) -> str | None:
    return employee.work_email or employee.personal_email


def sync_employee(
    db: Session,
    employee: Employee,
    *,
    client: DotmacSubClient | None = None,
) -> dict[str, Any]:
    """Push one employee's lifecycle state to dotmac_sub. Idempotent.

    Returns a result dict: {action: created|enabled|disabled|skipped|noop, ...}.
    Never raises on business-state gaps (missing email, draft status) — those
    are 'skipped' with a reason; transport/auth errors do raise so callers
    (Celery retry / reconcile error counters) see them.
    """
    if not settings.dotmac_sub_staff_sync_enabled:
        return {"action": "skipped", "reason": "staff sync disabled"}

    status = employee.status
    if status not in _ENABLED_STATUSES | _DISABLED_STATUSES:
        return {"action": "skipped", "reason": f"status {status.value} not synced"}

    email = _staff_email(employee)
    if not email:
        return {"action": "skipped", "reason": "employee has no email"}

    owns_client = client is None
    if client is None:
        config = DotmacSubConfig.for_org(db, employee.organization_id)
        if not config.is_configured():
            return {"action": "skipped", "reason": "dotmac_sub not configured"}
        client = DotmacSubClient(config)

    try:
        account_id = employee.dotmac_sub_account_id
        account: dict[str, Any] | None = None
        if not account_id:
            account = client.get_staff_account(email)
            if account:
                account_id = str(account.get("id"))

        if status in _ENABLED_STATUSES:
            if not account_id:
                created = client.create_staff_account(
                    email=email,
                    first_name=employee.first_name or "",
                    last_name=employee.last_name or "",
                    role=settings.dotmac_sub_staff_default_role,
                    send_invite=True,
                )
                employee.dotmac_sub_account_id = str(created.get("id"))
                _mark_synced(employee)
                return {
                    "action": "created",
                    "account_id": employee.dotmac_sub_account_id,
                }
            if account is None:
                account = client.get_staff_account(email)
            employee.dotmac_sub_account_id = account_id
            if account and not account.get("is_active", True):
                client.set_staff_account_active(account_id, is_active=True)
                _mark_synced(employee)
                return {"action": "enabled", "account_id": account_id}
            _mark_synced(employee)
            return {"action": "noop", "account_id": account_id}

        # Disabled statuses.
        if not account_id:
            return {"action": "skipped", "reason": "no dotmac_sub account"}
        employee.dotmac_sub_account_id = account_id
        if account is None:
            account = client.get_staff_account(email)
        if account and not account.get("is_active", True):
            _mark_synced(employee)
            return {"action": "noop", "account_id": account_id}
        client.set_staff_account_active(account_id, is_active=False)
        _mark_synced(employee)
        return {"action": "disabled", "account_id": account_id}
    finally:
        if owns_client:
            client.close()


def _mark_synced(employee: Employee) -> None:
    employee.dotmac_sub_staff_synced_at = datetime.now(timezone.utc)


def reconcile_staff_accounts(db: Session, organization_id: UUID) -> dict[str, Any]:
    """Nightly sweep: push every syncable employee's state. Error-isolated."""
    if not settings.dotmac_sub_staff_sync_enabled:
        return {"success": True, "skipped": "staff sync disabled"}

    config = DotmacSubConfig.for_org(db, organization_id)
    if not config.is_configured():
        return {"success": True, "skipped": "dotmac_sub not configured"}

    counts: dict[str, int] = {}
    errors: list[str] = []
    with DotmacSubClient(config) as client:
        employees = (
            db.query(Employee)
            .filter(
                Employee.organization_id == organization_id,
                Employee.status.in_(
                    [s for s in (_ENABLED_STATUSES | _DISABLED_STATUSES)]
                ),
            )
            .all()
        )
        for employee in employees:
            try:
                result = sync_employee(db, employee, client=client)
                counts[result["action"]] = counts.get(result["action"], 0) + 1
                db.commit()
            except Exception as e:  # noqa: BLE001 — isolate per-employee failures
                db.rollback()
                errors.append(f"{employee.employee_code}: {e}")
                logger.exception(
                    "Staff sync failed for employee %s", employee.employee_id
                )

    return {"success": not errors, "counts": counts, "errors": errors[:20]}
