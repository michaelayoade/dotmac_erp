"""Do not label zero-send, failed or expired notifications as successfully sent."""

BRANCH = "fix/erp-push-delivery-outcomes"
TITLE = "fix(notifications): persist honest push outcomes and bounded retries"
TESTS = ["tests/tasks/test_push_delivery_outcomes.py", "tests/tasks/test_notifications_task.py"]


def apply(change, write, rewrite):
    change("app/models/notification.py", '''    push_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
''', '''    push_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # "sent" means provider acceptance, not proof of handset delivery. Old
    # records are labelled legacy_processed because push_sent formerly meant
    # attempted or expired as well as actually sent.
    push_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="pending", server_default="pending"
    )
    push_retry_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    push_next_retry_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
''')
    write("alembic/versions/20260918_push_delivery_outcomes.py", '''"""Record honest mobile push outcomes without replaying historical sends.

Revision ID: 20260918_push_delivery_outcomes
Revises: 20260915_merge_workforce_kpi
"""

from alembic import op
import sqlalchemy as sa

revision = "20260918_push_delivery_outcomes"
down_revision = "20260915_merge_workforce_kpi"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("notification", sa.Column("push_status", sa.String(24), nullable=False, server_default="pending"), schema="public")
    op.add_column("notification", sa.Column("push_retry_count", sa.Integer(), nullable=False, server_default="0"), schema="public")
    op.add_column("notification", sa.Column("push_next_retry_at", sa.DateTime(), nullable=True), schema="public")
    # Historical true values are ambiguous. Preserve the old anti-replay flag,
    # but do not retroactively claim successful provider acceptance.
    op.execute("UPDATE public.notification SET push_status = 'legacy_processed' WHERE push_sent = true")


def downgrade() -> None:
    # Old workers only understand push_sent. Preserve terminal suppression on
    # rollback so no-device, failed or expired records are not re-broadcast.
    op.execute("UPDATE public.notification SET push_sent = true WHERE push_status IN ('no_device', 'failed', 'expired', 'partial', 'legacy_processed')")
    op.drop_column("notification", "push_next_retry_at", schema="public")
    op.drop_column("notification", "push_retry_count", schema="public")
    op.drop_column("notification", "push_status", schema="public")
''')
    path = "app/tasks/notifications.py"
    change(path, '''# Push notifications older than this are stale (the user has seen the in-app
# inbox by now); they are swept to push_sent without delivery so the queue
# drains instead of blasting old backlog after first deploy/downtime.
_PUSH_STALE_AGE = timedelta(hours=24)''', '''# Old mobile pushes expire rather than being falsely labelled sent. In-app
# notifications remain canonical; expiry does not imply the user read them.
_PUSH_STALE_AGE = timedelta(hours=24)
_PUSH_RETRY_DELAYS = (timedelta(minutes=5), timedelta(minutes=15), timedelta(hours=1))


def _dispatch_notification_push(notification, push_service, now, results: dict) -> None:
    """Record provider acceptance separately from no-device and retry outcomes.

    Partial fan-out is terminal: blindly retrying the entire recipient would
    duplicate delivery to devices that already accepted the push. The in-app
    notification remains available on every device.
    """
    results["processed"] += 1
    try:
        devices = push_service.active_tokens_for_person(notification.recipient_id)
        if not devices:
            notification.push_status = "no_device"
            notification.push_next_retry_at = None
            notification.push_sent = False
            notification.push_sent_at = None
            results["skipped"] += 1
            return
        data = {"notification_id": str(notification.notification_id)}
        if notification.action_url:
            data["action_url"] = notification.action_url
        sent = push_service.send_to_person(
            notification.recipient_id,
            title=notification.title,
            body=notification.message,
            data=data,
        )
        if sent > 0:
            notification.push_sent = True
            notification.push_sent_at = now
            notification.push_next_retry_at = None
            notification.push_status = "sent" if sent >= len(devices) else "partial"
            results["sent"] += sent
            if notification.push_status == "partial":
                results["partial"] += 1
            return
    except _DB_RETRYABLE_ERRORS:
        raise
    except Exception:
        logger.exception("Push attempt failed for notification %s", notification.notification_id)

    notification.push_sent = False
    notification.push_sent_at = None
    notification.push_retry_count += 1
    results["failed"] += 1
    if notification.push_retry_count > len(_PUSH_RETRY_DELAYS):
        notification.push_status = "failed"
        notification.push_next_retry_at = None
        results["dead_letter"] += 1
    else:
        notification.push_status = "retry"
        notification.push_next_retry_at = now + _PUSH_RETRY_DELAYS[notification.push_retry_count - 1]
''')
    rewrite(path, "process_pending_push_notifications", lambda _: '''def process_pending_push_notifications(
    self,
    batch_size: int = 100,
) -> dict:
    """Dispatch fully tenant-scoped push batches with explicit delivery state."""
    from app.services.push import PushService

    results: dict = {
        "processed": 0, "sent": 0, "swept": 0, "failed": 0,
        "skipped": 0, "partial": 0, "dead_letter": 0,
    }
    if not PushService.is_configured():
        return results
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    try:
        for organization_id in active_organization_ids():
            with session_for_org(organization_id) as db:
                now = datetime.now(UTC)
                cutoff = now - _PUSH_STALE_AGE
                # Accommodate an old worker finishing during a rolling upgrade.
                # Never replay a true legacy flag or relabel it as proven sent.
                db.execute(
                    update(Notification)
                    .where(Notification.organization_id == organization_id)
                    .where(Notification.push_sent.is_(True))
                    .where(Notification.push_status == "pending")
                    .values(push_status="legacy_processed")
                )
                eligible = (
                    Notification.organization_id == organization_id,
                    Notification.push_sent.is_(False),
                    Notification.push_status.in_(["pending", "retry"]),
                    Notification.channel.in_([
                        NotificationChannel.IN_APP, NotificationChannel.BOTH,
                        NotificationChannel.ALL,
                    ]),
                )
                swept = db.execute(
                    update(Notification).where(*eligible)
                    .where(Notification.created_at < cutoff)
                    .values(push_status="expired", push_next_retry_at=None)
                )
                results["swept"] += swept.rowcount or 0
                stmt = (
                    select(Notification).where(*eligible)
                    .where(Notification.created_at >= cutoff)
                    .where((Notification.push_next_retry_at.is_(None)) | (Notification.push_next_retry_at <= now))
                    .order_by(Notification.created_at.asc())
                    .limit(batch_size)
                    .with_for_update(of=Notification, skip_locked=True)
                )
                notifications = list(db.execute(stmt).scalars().all())
                push_service = PushService(db)
                for notification in notifications:
                    _dispatch_notification_push(notification, push_service, now, results)
                db.commit()
    except _DB_RETRYABLE_ERRORS as exc:
        logger.warning("Push dispatch DB error; retrying: %s", exc)
        raise self.retry(exc=exc)
    if results["processed"] or results["swept"]:
        logger.info("Push dispatch: %s", results)
    return results
''')
    write("tests/tasks/test_push_delivery_outcomes.py", '''"""Regression: a dispatch attempt is not a successful push send."""

from contextlib import nullcontext
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.tasks import notifications as tasks


def record():
    return SimpleNamespace(
        notification_id=uuid4(), recipient_id=uuid4(), action_url=None,
        title="Synthetic notification", message="Test only", push_sent=False,
        push_sent_at=None, push_status="pending", push_retry_count=0,
        push_next_retry_at=None,
    )


def counters():
    return dict.fromkeys(["processed", "sent", "swept", "failed", "skipped", "partial", "dead_letter"], 0)


def test_no_device_is_skipped_not_sent():
    notification, service, results = record(), MagicMock(), counters()
    service.active_tokens_for_person.return_value = []
    tasks._dispatch_notification_push(notification, service, datetime.now(timezone.utc), results)
    assert notification.push_status == "no_device"
    assert notification.push_sent is False
    assert notification.push_sent_at is None
    assert results["skipped"] == 1
    service.send_to_person.assert_not_called()


@pytest.mark.parametrize("exception", [False, True])
def test_zero_send_or_exception_gets_bounded_retry_without_false_success(exception):
    notification, service, results = record(), MagicMock(), counters()
    service.active_tokens_for_person.return_value = [object()]
    service.send_to_person.return_value = 0
    if exception:
        service.send_to_person.side_effect = RuntimeError("synthetic transport failure")
    now = datetime.now(timezone.utc)
    for _ in range(3):
        tasks._dispatch_notification_push(notification, service, now, results)
        assert notification.push_status == "retry"
        assert notification.push_next_retry_at > now
        assert notification.push_sent is False
        assert notification.push_sent_at is None
    tasks._dispatch_notification_push(notification, service, now, results)
    assert notification.push_status == "failed"
    assert notification.push_next_retry_at is None
    assert notification.push_retry_count == 4
    assert results["dead_letter"] == 1
    assert results["sent"] == 0


@pytest.mark.parametrize(("sent", "status"), [(1, "partial"), (2, "sent")])
def test_provider_acceptance_sets_sent_flag_and_partial_is_not_retried(sent, status):
    notification, service, results = record(), MagicMock(), counters()
    service.active_tokens_for_person.return_value = [object(), object()]
    service.send_to_person.return_value = sent
    now = datetime.now(timezone.utc)
    tasks._dispatch_notification_push(notification, service, now, results)
    assert notification.push_status == status
    assert notification.push_sent is True
    assert notification.push_sent_at == now
    assert notification.push_next_retry_at is None
    assert results["sent"] == sent


def test_task_queries_are_org_scoped_and_expiry_is_not_a_send(monkeypatch):
    from app.services.push import PushService
    org = uuid4()
    db = MagicMock()
    db.execute.return_value.rowcount = 3
    db.execute.return_value.scalars.return_value.all.return_value = []
    monkeypatch.setattr(PushService, "is_configured", staticmethod(lambda: True))
    monkeypatch.setattr(tasks, "active_organization_ids", lambda: [org])
    monkeypatch.setattr(tasks, "session_for_org", lambda value: nullcontext(db))
    results = tasks.process_pending_push_notifications.run()
    statements = [call.args[0] for call in db.execute.call_args_list]
    assert len(statements) == 3
    assert all("notification.organization_id" in str(statement) for statement in statements)
    assert "push_status" in str(statements[-1])
    assert "push_next_retry_at" in str(statements[-1])
    assert "SKIP LOCKED" in str(statements[-1])
    expired_values = statements[1].compile().params
    assert expired_values["push_status"] == "expired"
    assert "push_sent" not in expired_values
    assert results["swept"] == 3
    assert results["sent"] == 0
    db.commit.assert_called_once_with()


def test_unconfigured_push_does_not_open_sessions(monkeypatch):
    from app.services.push import PushService
    discover = MagicMock()
    monkeypatch.setattr(PushService, "is_configured", staticmethod(lambda: False))
    monkeypatch.setattr(tasks, "active_organization_ids", discover)
    assert tasks.process_pending_push_notifications.run()["processed"] == 0
    discover.assert_not_called()
''')
    write("docs/runbooks/push-delivery-outcomes.md", '''# Mobile push delivery state

Apply the additive Alembic revision before starting workers with this code.
`push_sent` now means at least one FCM send was reported successful; it does not
prove the handset received or displayed it. `push_status` distinguishes sent,
partial, no_device, retry, failed, expired and legacy_processed records.

Historical true flags cannot establish delivery and are backfilled as
legacy_processed without replay. Old records are expired after 24 hours without
setting a new successful-send flag. Zero sends and exceptions receive three
bounded retries (5 minutes, 15 minutes, 1 hour), then become terminal failures.
A partially accepted fan-out is terminal to avoid re-sending to accepted devices.
A process crash after external acceptance but before database commit can still
cause redelivery; this change does not claim exactly-once external delivery.

Push batches now use the same explicit organization discovery and scoped-session
pattern as email dispatch. Existing service return types, device registration,
FCM configuration and in-app notification availability are unchanged.

Downgrade restores old terminal suppression flags to avoid broadcasting already
processed records. Configure or repair real device registrations separately;
this patch does not create tokens, send test pushes, or change credentials.
''')
