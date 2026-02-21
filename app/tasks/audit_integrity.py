"""
Audit Integrity Tasks — Celery tasks for audit log verification.

Handles:
- Hash chain integrity verification across all organizations
- Alerts on tamper detection
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from celery import shared_task

from app.db import SessionLocal
from app.services.common import coerce_uuid

logger = logging.getLogger(__name__)


def _resolve_org_id(organization_id: UUID | str | None) -> UUID | None:
    """Coerce an optional organization identifier for tenant-scoped tasks."""
    if organization_id is None:
        return None
    return UUID(str(coerce_uuid(organization_id)))


@shared_task
def verify_audit_hash_chain(
    days_back: int = 1,
    organization_id: UUID | str | None = None,
) -> dict[str, Any]:
    """Verify audit log hash chain integrity.

    Checks that no audit records have been tampered with by recomputing
    the SHA-256 hash chain and comparing against stored hashes.

    When run daily with ``days_back=1``, this verifies yesterday's records.
    For initial/full verification, increase ``days_back`` as needed.

    Args:
        days_back: Number of days to look back from now.
        organization_id: Limit to a specific org (default: all orgs).

    Returns:
        Dict with verification results per organization.
    """
    logger.info(
        "Starting audit hash chain verification (days_back=%d, org=%s)",
        days_back,
        organization_id,
    )

    results: dict[str, Any] = {
        "organizations_checked": 0,
        "organizations_valid": 0,
        "organizations_invalid": 0,
        "invalid_orgs": [],
        "errors": [],
    }

    to_date = datetime.now(UTC)
    from_date = to_date - timedelta(days=days_back)

    with SessionLocal() as db:
        from sqlalchemy import distinct, select

        from app.models.finance.audit.audit_log import AuditLog

        # Determine which organizations to verify
        target_org_id = _resolve_org_id(organization_id)

        if target_org_id:
            org_ids = [target_org_id]
        else:
            # Get all organizations that have audit records in the period
            stmt = select(distinct(AuditLog.organization_id)).where(
                AuditLog.occurred_at >= from_date,
                AuditLog.occurred_at <= to_date,
            )
            org_ids = list(db.scalars(stmt).all())

        if not org_ids:
            logger.info("No audit records found in period — nothing to verify")
            return results

        from app.services.finance.platform.audit_log import AuditLogService

        for org_id in org_ids:
            results["organizations_checked"] += 1
            try:
                is_valid, first_invalid_id = AuditLogService.verify_hash_chain(
                    db=db,
                    organization_id=org_id,
                    from_date=from_date,
                    to_date=to_date,
                )

                if is_valid:
                    results["organizations_valid"] += 1
                else:
                    results["organizations_invalid"] += 1
                    results["invalid_orgs"].append(
                        {
                            "organization_id": str(org_id),
                            "first_invalid_audit_id": first_invalid_id,
                        }
                    )
                    logger.error(
                        "AUDIT INTEGRITY VIOLATION: org=%s first_invalid=%s",
                        org_id,
                        first_invalid_id,
                    )

                    # Create alert notification for admins
                    _notify_integrity_violation(
                        db, org_id, first_invalid_id, from_date, to_date
                    )

            except Exception as e:
                logger.exception("Failed to verify hash chain for org %s", org_id)
                results["errors"].append(
                    {"organization_id": str(org_id), "error": str(e)}
                )

        db.commit()

    logger.info(
        "Hash chain verification complete: %d checked, %d valid, %d invalid, %d errors",
        results["organizations_checked"],
        results["organizations_valid"],
        results["organizations_invalid"],
        len(results["errors"]),
    )
    return results


def _notify_integrity_violation(
    db: Any,
    organization_id: UUID,
    first_invalid_id: str | None,
    from_date: datetime,
    to_date: datetime,
) -> None:
    """Send alert notification when hash chain integrity is compromised."""
    try:
        from app.models.notification import (
            EntityType,
            NotificationChannel,
            NotificationType,
        )
        from app.services.notification import NotificationService

        notification_service = NotificationService()

        # Find admin users for this organization
        from sqlalchemy import select

        from app.models.rbac import RoleAssignment

        stmt = select(RoleAssignment.person_id).where(
            RoleAssignment.organization_id == organization_id,
            RoleAssignment.role_name.in_(["admin", "finance_manager", "accountant"]),
        )
        recipient_ids = list(db.scalars(stmt).all())

        period_str = (
            f"{from_date.strftime('%d %b %Y')} – {to_date.strftime('%d %b %Y')}"
        )

        for recipient_id in recipient_ids:
            notification_service.create(
                db,
                organization_id=organization_id,
                recipient_id=recipient_id,
                entity_type=EntityType.SYSTEM,
                entity_id=organization_id,
                notification_type=NotificationType.ALERT,
                title="Audit Log Integrity Alert",
                message=(
                    f"Hash chain verification failed for period {period_str}. "
                    f"First invalid record: {first_invalid_id or 'unknown'}. "
                    "This may indicate unauthorized modification of audit records."
                ),
                channel=NotificationChannel.BOTH,
                action_url="/admin/data-changes",
            )

    except Exception:
        logger.warning(
            "Failed to send integrity violation notification for org %s",
            organization_id,
            exc_info=True,
        )
