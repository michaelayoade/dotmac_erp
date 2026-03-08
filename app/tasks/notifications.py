"""
Notification delivery background tasks.

Handles:
- Delivery of pending email notifications stored in public.notification
- Delivery of pending Nextcloud Talk notifications
"""

import logging
from datetime import UTC, datetime
from typing import TypedDict

from celery import shared_task
from sqlalchemy import select

from app.db import SessionLocal
from app.models.email_profile import EmailModule
from app.models.notification import Notification, NotificationChannel
from app.services.email import send_email

logger = logging.getLogger(__name__)


class NotificationEmailDispatchResults(TypedDict):
    processed: int
    sent: int
    skipped: int
    failed: int


@shared_task
def process_pending_notification_emails(
    batch_size: int = 100,
) -> NotificationEmailDispatchResults:
    """
    Send pending email notifications and mark delivery status.

    Notes:
    - Only notifications with channel EMAIL/BOTH and email_sent=False are selected.
    - Uses FOR UPDATE SKIP LOCKED to prevent duplicate processing across workers.
    """
    results: NotificationEmailDispatchResults = {
        "processed": 0,
        "sent": 0,
        "skipped": 0,
        "failed": 0,
    }

    with SessionLocal() as db:
        stmt = (
            select(Notification)
            .where(Notification.email_sent == False)  # noqa: E712
            .where(
                Notification.channel.in_(
                    [
                        NotificationChannel.EMAIL,
                        NotificationChannel.BOTH,
                        NotificationChannel.ALL,
                    ]
                )
            )
            .order_by(Notification.created_at.asc())
            .limit(batch_size)
            .with_for_update(of=Notification, skip_locked=True)
        )
        notifications = list(db.execute(stmt).scalars().all())

        for notification in notifications:
            results["processed"] += 1

            recipient_email = None
            if notification.recipient and notification.recipient.email:
                recipient_email = notification.recipient.email.strip()

            if not recipient_email:
                # No email address available; leave as unsent for operator visibility.
                logger.warning(
                    "Skipping notification %s: recipient email missing",
                    notification.notification_id,
                )
                results["skipped"] += 1
                continue

            try:
                body_text = notification.message
                body_html = (
                    f"<p>{notification.message}</p>"
                    if notification.message
                    else "<p>You have a new notification in Dotmac ERP.</p>"
                )
                if notification.action_url:
                    body_html += f'<p><a href="{notification.action_url}">Open notification</a></p>'

                ok = send_email(
                    db=db,
                    to_email=recipient_email,
                    subject=notification.title,
                    body_html=body_html,
                    body_text=body_text,
                    module=EmailModule.ADMIN,
                    organization_id=notification.organization_id,
                )
                if ok:
                    notification.email_sent = True
                    notification.email_sent_at = datetime.now(UTC)
                    results["sent"] += 1
                else:
                    results["failed"] += 1
            except Exception:
                logger.exception(
                    "Failed sending notification email %s",
                    notification.notification_id,
                )
                results["failed"] += 1

        db.commit()

    if results["processed"] > 0:
        logger.info(
            "Notification email dispatch: processed=%d sent=%d skipped=%d failed=%d",
            results["processed"],
            results["sent"],
            results["skipped"],
            results["failed"],
        )

    return results


class NextcloudDispatchResults(TypedDict):
    processed: int
    sent: int
    skipped: int
    failed: int


@shared_task
def process_pending_nextcloud_notifications(
    batch_size: int = 100,
) -> NextcloudDispatchResults:
    """
    Send pending Nextcloud Talk notifications and mark delivery status.

    Notes:
    - Only notifications with channel NEXTCLOUD/ALL and nextcloud_sent=False are selected.
    - Uses FOR UPDATE SKIP LOCKED to prevent duplicate processing across workers.
    - Requires nextcloud_server_url, nextcloud_username, nextcloud_password in
      domain settings (notifications domain).
    """
    from app.services.nextcloud.client import (
        NextcloudError,
        NextcloudTalkClient,
        is_configured,
    )

    results: NextcloudDispatchResults = {
        "processed": 0,
        "sent": 0,
        "skipped": 0,
        "failed": 0,
    }

    with SessionLocal() as db:
        if not is_configured(db):
            return results

        stmt = (
            select(Notification)
            .where(Notification.nextcloud_sent == False)  # noqa: E712
            .where(
                Notification.channel.in_(
                    [
                        NotificationChannel.NEXTCLOUD,
                        NotificationChannel.ALL,
                    ]
                )
            )
            .order_by(Notification.created_at.asc())
            .limit(batch_size)
            .with_for_update(of=Notification, skip_locked=True)
        )
        notifications = list(db.execute(stmt).scalars().all())

        if not notifications:
            return results

        client = NextcloudTalkClient.from_db(db)

        for notification in notifications:
            results["processed"] += 1

            nc_user_id = None
            if notification.recipient:
                nc_user_id = notification.recipient.nextcloud_user_id

            if not nc_user_id:
                logger.warning(
                    "Skipping notification %s: recipient nextcloud_user_id missing",
                    notification.notification_id,
                )
                results["skipped"] += 1
                continue

            try:
                message = f"**{notification.title}**\n{notification.message}"
                if notification.action_url:
                    message += f"\n\n{notification.action_url}"

                client.send_to_user(nc_user_id, message)
                notification.nextcloud_sent = True
                notification.nextcloud_sent_at = datetime.now(UTC)
                results["sent"] += 1
            except NextcloudError:
                logger.exception(
                    "Failed sending Nextcloud notification %s",
                    notification.notification_id,
                )
                results["failed"] += 1
            except Exception:
                logger.exception(
                    "Unexpected error sending Nextcloud notification %s",
                    notification.notification_id,
                )
                results["failed"] += 1

        db.commit()

    if results["processed"] > 0:
        logger.info(
            "Nextcloud dispatch: processed=%d sent=%d skipped=%d failed=%d",
            results["processed"],
            results["sent"],
            results["skipped"],
            results["failed"],
        )

    return results
