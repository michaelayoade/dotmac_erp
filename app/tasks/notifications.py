"""
Notification delivery background tasks.

Handles:
- Delivery of pending email notifications stored in public.notification
- Delivery of pending Nextcloud Talk notifications
"""

import html
import logging
from datetime import UTC, datetime, timedelta
from typing import TypedDict

from celery import shared_task
from sqlalchemy import select

from app.db import SessionLocal
from app.models.email_profile import EmailModule
from app.models.notification import Notification, NotificationChannel
from app.services.email import person_can_receive_email, send_email

logger = logging.getLogger(__name__)


# Notifications older than this are considered permanently failed and will not
# be retried.  They remain in the database with email_sent=False for operator
# inspection but are excluded from the processing query.
_DEAD_LETTER_AGE = timedelta(days=3)


class NotificationEmailDispatchResults(TypedDict):
    processed: int
    sent: int
    skipped: int
    failed: int
    dead_letter: int


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
        "dead_letter": 0,
    }

    with SessionLocal() as db:
        cutoff = datetime.now(UTC) - _DEAD_LETTER_AGE

        # Count dead-lettered notifications for observability (lightweight).
        dead_letter_count_stmt = (
            select(Notification.notification_id)
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
            .where(Notification.created_at < cutoff)
        )
        dead_letter_ids = list(db.execute(dead_letter_count_stmt).scalars().all())
        results["dead_letter"] = len(dead_letter_ids)
        if dead_letter_ids:
            logger.warning(
                "Dead-letter: %d notification emails older than %s days will not be retried",
                len(dead_letter_ids),
                _DEAD_LETTER_AGE.days,
            )

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
            .where(Notification.created_at >= cutoff)
            .order_by(Notification.created_at.asc())
            .limit(batch_size)
            .with_for_update(of=Notification, skip_locked=True)
        )
        notifications = list(db.execute(stmt).scalars().all())

        for notification in notifications:
            results["processed"] += 1

            if not person_can_receive_email(notification.recipient):
                logger.info(
                    "Suppressing notification email %s: recipient is inactive",
                    notification.notification_id,
                )
                notification.email_sent = True
                notification.email_sent_at = datetime.now(UTC)
                results["skipped"] += 1
                continue

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
                safe_message = (
                    html.escape(notification.message)
                    if notification.message
                    else None
                )
                body_html = (
                    f"<p>{safe_message}</p>"
                    if safe_message
                    else "<p>You have a new notification in Dotmac ERP.</p>"
                )
                if notification.action_url:
                    url = notification.action_url
                    if url.startswith("/") or url.startswith("http"):
                        safe_url = html.escape(url)
                        body_html += f'<p><a href="{safe_url}">Open notification</a></p>'

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

    if results["processed"] > 0 or results["dead_letter"] > 0:
        logger.info(
            "Notification email dispatch: processed=%d sent=%d skipped=%d failed=%d dead_letter=%d",
            results["processed"],
            results["sent"],
            results["skipped"],
            results["failed"],
            results["dead_letter"],
        )

    return results


class NextcloudDispatchResults(TypedDict):
    processed: int
    sent: int
    skipped: int
    failed: int
    dead_letter: int


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
        "dead_letter": 0,
    }

    with SessionLocal() as db:
        if not is_configured(db):
            return results

        cutoff = datetime.now(UTC) - _DEAD_LETTER_AGE

        # Count dead-lettered Nextcloud notifications for observability.
        dead_nc_stmt = (
            select(Notification.notification_id)
            .where(Notification.nextcloud_sent == False)  # noqa: E712
            .where(
                Notification.channel.in_(
                    [
                        NotificationChannel.NEXTCLOUD,
                        NotificationChannel.ALL,
                    ]
                )
            )
            .where(Notification.created_at < cutoff)
        )
        dead_nc_ids = list(db.execute(dead_nc_stmt).scalars().all())
        results["dead_letter"] = len(dead_nc_ids)
        if dead_nc_ids:
            logger.warning(
                "Dead-letter: %d Nextcloud notifications older than %s days will not be retried",
                len(dead_nc_ids),
                _DEAD_LETTER_AGE.days,
            )

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
            .where(Notification.created_at >= cutoff)
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

    if results["processed"] > 0 or results["dead_letter"] > 0:
        logger.info(
            "Nextcloud dispatch: processed=%d sent=%d skipped=%d failed=%d dead_letter=%d",
            results["processed"],
            results["sent"],
            results["skipped"],
            results["failed"],
            results["dead_letter"],
        )

    return results
