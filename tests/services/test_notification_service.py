"""Tests for app/services/notification.py."""

import uuid
from unittest.mock import MagicMock

from app.models.notification import EntityType, NotificationChannel, NotificationType
from app.services.notification import NotificationService


class TestNotificationServiceCreate:
    """Tests for NotificationService.create defaults."""

    def test_create_defaults_mentions_to_both_channels(self):
        """MENTION notifications should default to BOTH when channel is omitted."""
        db = MagicMock()
        service = NotificationService()

        notification = service.create(
            db=db,
            organization_id=uuid.uuid4(),
            recipient_id=uuid.uuid4(),
            entity_type=EntityType.INVOICE,
            entity_id=uuid.uuid4(),
            notification_type=NotificationType.MENTION,
            title="Mentioned in comment",
            message="You were mentioned.",
        )

        assert notification.channel == NotificationChannel.BOTH
        db.add.assert_called_once()
        db.flush.assert_called_once()

    def test_create_defaults_non_mentions_to_in_app(self):
        """Non-MENTION notifications should continue defaulting to IN_APP."""
        db = MagicMock()
        service = NotificationService()

        notification = service.create(
            db=db,
            organization_id=uuid.uuid4(),
            recipient_id=uuid.uuid4(),
            entity_type=EntityType.INVOICE,
            entity_id=uuid.uuid4(),
            notification_type=NotificationType.COMMENT,
            title="New comment",
            message="A new comment was added.",
        )

        assert notification.channel == NotificationChannel.IN_APP


class TestNotificationServiceMarkRead:
    """Tests for NotificationService.mark_read."""

    def test_mark_read_scopes_to_recipient_and_organization(self):
        """mark_read should only update the current recipient within org scope."""
        db = MagicMock()
        notification = MagicMock()
        notification.is_read = False
        db.scalar.return_value = notification
        service = NotificationService()
        notification_id = uuid.uuid4()
        recipient_id = uuid.uuid4()
        organization_id = uuid.uuid4()

        marked = service.mark_read(
            db=db,
            notification_id=notification_id,
            recipient_id=recipient_id,
            organization_id=organization_id,
        )

        assert marked is True
        db.flush.assert_called_once()
        notification.mark_read.assert_called_once()

        query = db.scalar.call_args.args[0]
        compiled = str(query)
        assert "notification.notification_id =" in compiled
        assert "notification.recipient_id =" in compiled
        assert "notification.organization_id =" in compiled

    def test_mark_read_returns_false_when_no_rows_updated(self):
        """mark_read should report failure when nothing was updated."""
        db = MagicMock()
        db.scalar.return_value = None
        service = NotificationService()

        marked = service.mark_read(
            db=db,
            notification_id=uuid.uuid4(),
            recipient_id=uuid.uuid4(),
        )

        assert marked is False

    def test_mark_read_is_idempotent_for_already_read(self):
        """Marking an already-read notification is a no-op success, not a miss."""
        db = MagicMock()
        notification = MagicMock()
        notification.is_read = True
        db.scalar.return_value = notification
        service = NotificationService()

        marked = service.mark_read(
            db=db,
            notification_id=uuid.uuid4(),
            recipient_id=uuid.uuid4(),
        )

        assert marked is True
        notification.mark_read.assert_not_called()
        db.flush.assert_not_called()
