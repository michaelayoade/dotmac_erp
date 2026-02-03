"""
Fleet Module Background Tasks - Celery tasks for fleet management workflows.

Handles:
- Document expiry notifications (insurance, registration, permits)
- Maintenance due reminders
"""

import logging
from datetime import date, timedelta
from typing import Any, List
from uuid import UUID

from celery import shared_task
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.fleet.enums import MaintenanceStatus
from app.models.fleet.maintenance import MaintenanceRecord
from app.models.fleet.vehicle_document import VehicleDocument
from app.models.notification import EntityType, NotificationChannel, NotificationType

logger = logging.getLogger(__name__)


def _get_operations_recipients(db: Session) -> List[UUID]:
    """
    Get user IDs with operations/fleet management roles.

    Args:
        db: Database session

    Returns:
        List of person_ids with operations access
    """
    from app.models.rbac import PersonRole, Role

    stmt = (
        select(PersonRole.person_id)
        .join(Role, PersonRole.role_id == Role.id)
        .where(
            Role.name.in_(["operations_manager", "fleet_manager", "admin"]),
            Role.is_active.is_(True),
        )
    )
    return list(db.scalars(stmt).all())


@shared_task
def process_document_expiry_notifications() -> dict[str, Any]:
    """
    Send notifications for vehicle documents that are expiring soon or expired.

    Sends reminders:
    - 30 days before expiry
    - 14 days before expiry
    - 7 days before expiry
    - When expired

    Returns:
        Dict with notification statistics
    """
    from app.services.notification import NotificationService

    logger.info("Processing fleet document expiry notifications")

    results: dict[str, Any] = {
        "documents_checked": 0,
        "notifications_sent": 0,
        "errors": [],
    }

    today = date.today()
    reminder_days = [30, 14, 7]

    with SessionLocal() as db:
        notification_service = NotificationService()

        # Get documents expiring within 30 days or already expired
        expiry_cutoff = today + timedelta(days=30)
        stmt = select(VehicleDocument).where(
            VehicleDocument.expiry_date.isnot(None),
            VehicleDocument.expiry_date <= expiry_cutoff,
        )
        documents = list(db.scalars(stmt).all())
        results["documents_checked"] = len(documents)

        for doc in documents:
            try:
                days_until = (doc.expiry_date - today).days if doc.expiry_date else None

                if days_until is None:
                    continue

                # Determine notification type
                if days_until < 0:
                    notification_type = NotificationType.OVERDUE
                    title = f"Document Expired: {doc.vehicle.registration_number}"
                    message = (
                        f"{doc.document_type.replace('_', ' ').title()} "
                        f"({doc.document_number}) for vehicle "
                        f"{doc.vehicle.registration_number} has expired."
                    )
                elif days_until in reminder_days:
                    notification_type = NotificationType.DUE_SOON
                    title = f"Document Expiring: {doc.vehicle.registration_number}"
                    message = (
                        f"{doc.document_type.replace('_', ' ').title()} "
                        f"({doc.document_number}) for vehicle "
                        f"{doc.vehicle.registration_number} expires in {days_until} days."
                    )
                else:
                    # Skip if not a reminder day
                    continue

                # Get recipients
                recipients = _get_operations_recipients(db)

                for recipient_id in recipients:
                    notification_service.create(
                        db,
                        organization_id=doc.organization_id,
                        recipient_id=recipient_id,
                        entity_type=EntityType.SYSTEM,
                        entity_id=doc.document_id,
                        notification_type=notification_type,
                        title=title,
                        message=message,
                        channel=NotificationChannel.BOTH,
                        action_url=f"/operations/fleet/documents/{doc.document_id}",
                    )
                    results["notifications_sent"] += 1

            except Exception as e:
                logger.exception(f"Failed to notify for document {doc.document_id}")
                results["errors"].append(str(e))

        db.commit()

    logger.info(
        "Completed document expiry notifications: %s checked, %s sent, %s errors",
        results["documents_checked"],
        results["notifications_sent"],
        len(results["errors"]),
    )
    return results


@shared_task
def process_maintenance_due_notifications() -> dict[str, Any]:
    """
    Send notifications for maintenance records that are due soon or overdue.

    Sends reminders:
    - 7 days before scheduled date
    - 3 days before scheduled date
    - 1 day before scheduled date
    - When overdue

    Returns:
        Dict with notification statistics
    """
    from app.services.notification import NotificationService

    logger.info("Processing maintenance due notifications")

    results: dict[str, Any] = {
        "records_checked": 0,
        "notifications_sent": 0,
        "errors": [],
    }

    today = date.today()
    reminder_days = [7, 3, 1]

    with SessionLocal() as db:
        notification_service = NotificationService()

        # Get scheduled maintenance within 7 days or overdue
        date_cutoff = today + timedelta(days=7)
        stmt = select(MaintenanceRecord).where(
            MaintenanceRecord.status == MaintenanceStatus.SCHEDULED,
            MaintenanceRecord.scheduled_date <= date_cutoff,
        )
        records = list(db.scalars(stmt).all())
        results["records_checked"] = len(records)

        for record in records:
            try:
                days_until = (record.scheduled_date - today).days

                # Determine notification type
                if days_until < 0:
                    notification_type = NotificationType.OVERDUE
                    title = f"Maintenance Overdue: {record.vehicle.registration_number}"
                    message = (
                        f"{record.maintenance_type.replace('_', ' ').title()} maintenance "
                        f"for {record.vehicle.registration_number} is {abs(days_until)} days overdue."
                    )
                elif days_until in reminder_days:
                    notification_type = NotificationType.DUE_SOON
                    title = f"Maintenance Due: {record.vehicle.registration_number}"
                    message = (
                        f"{record.maintenance_type.replace('_', ' ').title()} maintenance "
                        f"for {record.vehicle.registration_number} is scheduled in {days_until} days."
                    )
                else:
                    continue

                # Get recipients
                recipients = _get_operations_recipients(db)

                for recipient_id in recipients:
                    notification_service.create(
                        db,
                        organization_id=record.organization_id,
                        recipient_id=recipient_id,
                        entity_type=EntityType.SYSTEM,
                        entity_id=record.maintenance_id,
                        notification_type=notification_type,
                        title=title,
                        message=message,
                        channel=NotificationChannel.BOTH,
                        action_url=f"/operations/fleet/maintenance/{record.maintenance_id}",
                    )
                    results["notifications_sent"] += 1

            except Exception as e:
                logger.exception(f"Failed to notify for maintenance {record.maintenance_id}")
                results["errors"].append(str(e))

        db.commit()

    logger.info(
        "Completed maintenance due notifications: %s checked, %s sent, %s errors",
        results["records_checked"],
        results["notifications_sent"],
        len(results["errors"]),
    )
    return results
