"""License re-validation Celery task.

Runs daily to detect license expiry before the next startup and notify
administrators via the existing :mod:`app.services.notification` system.
"""

from __future__ import annotations

import logging
import os
from uuid import UUID

from celery import shared_task

from app.db.session_context import session_for_org
from app.tenant_catalog import organization_ids

logger = logging.getLogger(__name__)


def _list_organization_ids() -> list[UUID]:
    """Every tenant, deactivated ones included.

    The scan this replaced enumerated organizations from ``Person`` rows joined
    to admin/finance_manager roles, with no organization-active predicate, so a
    deactivated tenant's administrators were notified like everyone else's. This
    change repairs discovery; it does not change who is notified.
    """
    return organization_ids(include_inactive=True)


@shared_task
def revalidate_license() -> dict:
    """Re-validate the license file and send admin notifications.

    Returns:
        Dict with validation results and notification counts.
    """
    from app.licensing.enforcement import _is_dev_mode, validate_license
    from app.licensing.schema import LicenseStatus
    from app.licensing.state import set_license_state

    results: dict[str, object] = {
        "status": "skipped",
        "notifications_sent": 0,
        "errors": [],
    }

    if _is_dev_mode():
        results["status"] = "dev_mode"
        return results

    path = os.getenv("LICENSE_FILE_PATH", "/app/license/dotmac.lic")
    state = validate_license(path)
    set_license_state(state)
    results["status"] = state.status.value

    if state.status in {LicenseStatus.VALID, LicenseStatus.DEV_MODE}:
        logger.info("License re-validation: %s", state.status.value)
        return results

    # Send admin notifications for non-normal states
    _notify_admins(state, results)

    return results


def _notify_admins(state, results: dict) -> None:
    """Send notifications to finance/admin users about license status."""
    from app.licensing.schema import LicenseStatus
    from app.models.notification import (
        EntityType,
        NotificationChannel,
        NotificationType,
    )
    from app.services.notification import NotificationService

    notification_service = NotificationService()
    title_map = {
        LicenseStatus.EXPIRING_SOON: "License Expiring Soon",
        LicenseStatus.GRACE_PERIOD: "License Expired — Grace Period Active",
        LicenseStatus.EXPIRED: "License Expired — Renewal Required",
        LicenseStatus.INVALID: "License Validation Failed",
        LicenseStatus.MISSING: "License File Missing",
    }
    title = title_map.get(state.status, f"License Status: {state.status.value}")

    message_parts = [title]
    if state.payload:
        message_parts.append(f"License: {state.payload.license_id}")
        message_parts.append(
            f"Expires: {state.payload.expires_at.strftime('%Y-%m-%d')}"
        )
    if state.error:
        message_parts.append(f"Detail: {state.error}")

    message = ". ".join(message_parts)

    notification_type = (
        NotificationType.OVERDUE
        if state.status in {LicenseStatus.EXPIRED, LicenseStatus.GRACE_PERIOD}
        else NotificationType.DUE_SOON
    )

    try:
        from sqlalchemy import select

        from app.models.person import Person
        from app.models.rbac import PersonRole, Role

        # Notify all admin/finance_manager-role users, one tenant at a time.
        # The retired version ran this join once across every tenant and grouped
        # the rows by ``Person.organization_id``. The grouping key is now the
        # session's own scope, so the predicate below says exactly what the
        # grouping used to say — and says it to a database that enforces it.
        for org_id in _list_organization_ids():
            with session_for_org(org_id) as db:
                person_ids = list(
                    db.scalars(
                        select(PersonRole.person_id)
                        .join(Role, PersonRole.role_id == Role.id)
                        .join(Person, PersonRole.person_id == Person.id)
                        .where(
                            Person.organization_id == org_id,
                            Role.name.in_(["admin", "finance_manager"]),
                            Role.is_active.is_(True),
                        )
                    ).all()
                )
                for person_id in person_ids:
                    try:
                        notification_service.create(
                            db,
                            organization_id=org_id,
                            recipient_id=person_id,
                            entity_type=EntityType.SYSTEM,
                            entity_id=person_id,  # no specific entity
                            notification_type=notification_type,
                            title=title,
                            message=message,
                            channel=NotificationChannel.BOTH,
                            action_url="/admin/license",
                        )
                        results["notifications_sent"] = (
                            int(results["notifications_sent"]) + 1
                        )
                    except Exception as exc:
                        logger.exception("Failed to notify %s: %s", person_id, exc)
                        errors = results.get("errors", [])
                        if isinstance(errors, list):
                            errors.append(str(exc))
                db.commit()
    except Exception as exc:
        logger.exception("Failed to send license notifications: %s", exc)
        errors = results.get("errors", [])
        if isinstance(errors, list):
            errors.append(str(exc))
