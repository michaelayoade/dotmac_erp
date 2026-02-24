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
