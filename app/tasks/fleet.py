"""
Fleet Module Background Tasks - Celery tasks for fleet management workflows.

Handles:
- Document expiry notifications (insurance, registration, permits)
- Maintenance due reminders
"""

import logging
from datetime import date, datetime, time, timedelta
from typing import Any, List
from uuid import UUID

from celery import shared_task
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session, selectinload

from app.db import SessionLocal
from app.models.fleet.enums import MaintenanceStatus
from app.models.fleet.maintenance import MaintenanceRecord
from app.models.fleet.vehicle_document import VehicleDocument
from app.models.notification import (
    EntityType,
    Notification,
    NotificationChannel,
    NotificationType,
)

logger = logging.getLogger(__name__)


def _get_operations_recipients(db: Session, organization_id: UUID) -> List[UUID]:
    """
    Get user IDs with operations/fleet management roles for an organization.

    Scopes recipients by organization via the Person table to ensure
    notifications are only sent to users belonging to the correct tenant.

    Args:
        db: Database session
        organization_id: Organization to scope recipients to

    Returns:
        List of person_ids with operations access in the given org
    """
    from app.models.person import Person
    from app.models.rbac import PersonRole, Role

    stmt = (
        select(PersonRole.person_id)
        .join(Role, PersonRole.role_id == Role.id)
        .join(Person, PersonRole.person_id == Person.id)
        .where(
            Person.organization_id == organization_id,
            Role.name.in_(["operations_manager", "fleet_manager", "admin"]),
            Role.is_active.is_(True),
        )
    )
    return list(db.scalars(stmt).all())


def _get_organization_ids_with_documents(
    db: Session, expiry_cutoff: date
) -> List[UUID]:
    """Get distinct organization IDs that have documents expiring within cutoff."""
    stmt = select(distinct(VehicleDocument.organization_id)).where(
        VehicleDocument.expiry_date.isnot(None),
        VehicleDocument.expiry_date <= expiry_cutoff,
    )
    return list(db.scalars(stmt).all())


def _notification_already_sent_today(
    db: Session,
    entity_id: UUID,
    notification_type: NotificationType,
) -> bool:
    """Check if a notification was already sent today for this entity and type."""
    today_start = datetime.combine(date.today(), time.min)
    count = db.scalar(
        select(func.count(Notification.notification_id)).where(
            Notification.entity_id == entity_id,
            Notification.notification_type == notification_type,
            Notification.created_at >= today_start,
        )
    )
    return (count or 0) > 0


def _get_organization_ids_with_maintenance(
    db: Session, date_cutoff: date
) -> List[UUID]:
    """Get distinct organization IDs that have scheduled maintenance due within cutoff."""
    stmt = select(distinct(MaintenanceRecord.organization_id)).where(
        MaintenanceRecord.status == MaintenanceStatus.SCHEDULED,
        MaintenanceRecord.scheduled_date <= date_cutoff,
    )
    return list(db.scalars(stmt).all())


@shared_task
def process_document_expiry_notifications() -> dict[str, Any]:
    """
    Send notifications for vehicle documents that are expiring soon or expired.

    Processes per-organization to maintain multi-tenant isolation.

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
    expiry_cutoff = today + timedelta(days=30)

    with SessionLocal() as db:
        notification_service = NotificationService()

        # Get organizations that have expiring documents
        org_ids = _get_organization_ids_with_documents(db, expiry_cutoff)

        for org_id in org_ids:
            # Get documents for this organization with eager-loaded vehicle
            stmt = (
                select(VehicleDocument)
                .options(selectinload(VehicleDocument.vehicle))
                .where(
                    VehicleDocument.organization_id == org_id,
                    VehicleDocument.expiry_date.isnot(None),
                    VehicleDocument.expiry_date <= expiry_cutoff,
                )
            )
            documents = list(db.scalars(stmt).all())
            results["documents_checked"] += len(documents)

            # Get recipients scoped to this organization
            recipients = _get_operations_recipients(db, org_id)
            if not recipients:
                continue

            for doc in documents:
                try:
                    days_until = (
                        (doc.expiry_date - today).days if doc.expiry_date else None
                    )

                    if days_until is None:
                        continue

                    # Determine notification type
                    if days_until < 0:
                        notification_type = NotificationType.OVERDUE
                        title = f"Document Expired: {doc.vehicle.registration_number}"
                        message = (
                            f"{doc.document_type.value.replace('_', ' ').title()} "
                            f"({doc.document_number}) for vehicle "
                            f"{doc.vehicle.registration_number} has expired."
                        )
                    elif days_until in reminder_days:
                        notification_type = NotificationType.DUE_SOON
                        title = f"Document Expiring: {doc.vehicle.registration_number}"
                        message = (
                            f"{doc.document_type.value.replace('_', ' ').title()} "
                            f"({doc.document_number}) for vehicle "
                            f"{doc.vehicle.registration_number} expires in {days_until} days."
                        )
                    else:
                        continue

                    # Skip if already notified today for this document
                    if _notification_already_sent_today(
                        db, doc.document_id, notification_type
                    ):
                        continue

                    for recipient_id in recipients:
                        notification_service.create(
                            db,
                            organization_id=org_id,
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
                    logger.exception(
                        "Failed to notify for document %s", doc.document_id
                    )
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

    Processes per-organization to maintain multi-tenant isolation.

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
    date_cutoff = today + timedelta(days=7)

    with SessionLocal() as db:
        notification_service = NotificationService()

        # Get organizations that have due maintenance
        org_ids = _get_organization_ids_with_maintenance(db, date_cutoff)

        for org_id in org_ids:
            # Get maintenance records for this organization with eager-loaded vehicle
            stmt = (
                select(MaintenanceRecord)
                .options(selectinload(MaintenanceRecord.vehicle))
                .where(
                    MaintenanceRecord.organization_id == org_id,
                    MaintenanceRecord.status == MaintenanceStatus.SCHEDULED,
                    MaintenanceRecord.scheduled_date <= date_cutoff,
                )
            )
            records = list(db.scalars(stmt).all())
            results["records_checked"] += len(records)

            # Get recipients scoped to this organization
            recipients = _get_operations_recipients(db, org_id)
            if not recipients:
                continue

            for record in records:
                try:
                    days_until = (record.scheduled_date - today).days

                    # Determine notification type
                    if days_until < 0:
                        notification_type = NotificationType.OVERDUE
                        title = (
                            f"Maintenance Overdue: {record.vehicle.registration_number}"
                        )
                        message = (
                            f"{record.maintenance_type.value.replace('_', ' ').title()} maintenance "
                            f"for {record.vehicle.registration_number} is {abs(days_until)} days overdue."
                        )
                    elif days_until in reminder_days:
                        notification_type = NotificationType.DUE_SOON
                        title = f"Maintenance Due: {record.vehicle.registration_number}"
                        message = (
                            f"{record.maintenance_type.value.replace('_', ' ').title()} maintenance "
                            f"for {record.vehicle.registration_number} is scheduled in {days_until} days."
                        )
                    else:
                        continue

                    # Skip if already notified today for this maintenance record
                    if _notification_already_sent_today(
                        db, record.maintenance_id, notification_type
                    ):
                        continue

                    for recipient_id in recipients:
                        notification_service.create(
                            db,
                            organization_id=org_id,
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
                    logger.exception(
                        "Failed to notify for maintenance %s", record.maintenance_id
                    )
                    results["errors"].append(str(e))

        db.commit()

    logger.info(
        "Completed maintenance due notifications: %s checked, %s sent, %s errors",
        results["records_checked"],
        results["notifications_sent"],
        len(results["errors"]),
    )
    return results
