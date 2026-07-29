"""Inventory notification orchestration."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.notification import (
    EntityType,
    NotificationChannel,
    NotificationType,
)
from app.services.common import coerce_uuid
from app.services.inventory.balance import InventoryBalanceService
from app.services.notification import NotificationService
from app.services.rbac import get_users_with_permission

INVENTORY_RECEIPT_APPROVER_PERMISSION = "inventory:receipt_approvals:approve"


class InventoryNotificationService:
    """Create actionable inventory alerts for authorized users."""

    def __init__(self, db: Session):
        self.db = db

    def notify_low_stock(self, organization_id: UUID) -> dict[str, int]:
        """Send a daily low-stock summary to inventory receipt approvers."""
        org_id = coerce_uuid(organization_id)
        low_stock_items = InventoryBalanceService.get_low_stock_items(
            self.db,
            org_id,
        )
        if not low_stock_items:
            return {"low_stock_items": 0, "notifications_sent": 0}

        recipient_ids = {
            assignment.person_id
            for assignment in get_users_with_permission(
                self.db,
                org_id,
                INVENTORY_RECEIPT_APPROVER_PERMISSION,
            )
        }
        if not recipient_ids:
            return {
                "low_stock_items": len(low_stock_items),
                "notifications_sent": 0,
            }

        item_names = [item.item_name for item in low_stock_items[:3]]
        sample = ", ".join(item_names)
        if len(low_stock_items) > len(item_names):
            sample = f"{sample}, and {len(low_stock_items) - len(item_names)} more"

        count = len(low_stock_items)
        noun = "item requires" if count == 1 else "items require"
        since = datetime.now(timezone.utc).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        notification_service = NotificationService()
        sent = 0
        for recipient_id in recipient_ids:
            notification = notification_service.create_if_not_sent_since(
                self.db,
                organization_id=org_id,
                recipient_id=recipient_id,
                entity_type=EntityType.SYSTEM,
                entity_id=org_id,
                notification_type=NotificationType.ALERT,
                title="Inventory stock requires attention",
                message=f"{count} {noun} attention: {sample}.",
                since=since,
                channel=NotificationChannel.IN_APP,
                action_url="/inventory/reports/low-stock",
            )
            if notification is not None:
                sent += 1

        return {"low_stock_items": count, "notifications_sent": sent}
