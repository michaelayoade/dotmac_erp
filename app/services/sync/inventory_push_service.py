"""
Inventory Push Service - Push inventory data from ERP to CRM.

Handles:
- Pushing full inventory snapshot to CRM
- Pushing incremental inventory changes
- Tracking last push state for delta sync
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.inventory.item import Item
from app.models.inventory.item_category import ItemCategory
from app.services.inventory.balance import InventoryBalanceService

logger = logging.getLogger(__name__)


class InventoryPushError(Exception):
    """Error pushing inventory to CRM."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


@dataclass
class PushResult:
    """Result of inventory push operation."""

    success: bool
    items_pushed: int
    errors: list[str]
    crm_response: Optional[dict] = None


class InventoryPushService:
    """
    Service for pushing inventory data to DotMac CRM.

    Sends inventory items with stock levels to CRM's inventory webhook
    so CRM can maintain a local cache for installation assignments.
    """

    def __init__(self, db: Session):
        self.db = db
        self._client: Optional[httpx.Client] = None

    @property
    def is_configured(self) -> bool:
        """Check if CRM inventory push is configured."""
        return bool(settings.crm_inventory_webhook_url and settings.crm_api_token)

    @property
    def client(self) -> httpx.Client:
        """Lazy-initialize HTTP client."""
        if self._client is None:
            if not self.is_configured:
                raise InventoryPushError(
                    "CRM inventory push not configured. "
                    "Set CRM_INVENTORY_WEBHOOK_URL and CRM_API_TOKEN."
                )

            self._client = httpx.Client(
                timeout=settings.crm_request_timeout,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Authorization": f"Bearer {settings.crm_api_token}",
                },
            )
        return self._client

    def close(self) -> None:
        """Close HTTP client."""
        if self._client:
            self._client.close()
            self._client = None

    def __enter__(self) -> "InventoryPushService":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def push_full_inventory(
        self,
        organization_id: UUID,
        include_zero_stock: bool = False,
    ) -> PushResult:
        """
        Push full inventory snapshot to CRM.

        Args:
            organization_id: Organization to push inventory for
            include_zero_stock: Include items with zero available stock

        Returns:
            PushResult with push statistics
        """
        if not self.is_configured:
            return PushResult(
                success=False,
                items_pushed=0,
                errors=["CRM inventory push not configured"],
            )

        logger.info("Starting full inventory push to CRM for org %s", organization_id)

        # Build inventory payload
        items = self._get_inventory_items(organization_id, include_zero_stock)

        if not items:
            logger.info("No inventory items to push")
            return PushResult(success=True, items_pushed=0, errors=[])

        payload = {
            "sync_type": "full",
            "organization_id": str(organization_id),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "items": items,
        }

        return self._send_to_crm(payload)

    def push_items(
        self,
        organization_id: UUID,
        item_ids: list[UUID],
    ) -> PushResult:
        """
        Push specific items to CRM (for incremental updates).

        Args:
            organization_id: Organization ID
            item_ids: List of item IDs to push

        Returns:
            PushResult with push statistics
        """
        if not self.is_configured:
            return PushResult(
                success=False,
                items_pushed=0,
                errors=["CRM inventory push not configured"],
            )

        if not item_ids:
            return PushResult(success=True, items_pushed=0, errors=[])

        logger.info(
            "Pushing %d items to CRM for org %s", len(item_ids), organization_id
        )

        items = self._get_inventory_items(
            organization_id,
            include_zero_stock=True,
            item_ids=item_ids,
        )

        payload = {
            "sync_type": "incremental",
            "organization_id": str(organization_id),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "items": items,
        }

        return self._send_to_crm(payload)

    def push_low_stock_alerts(self, organization_id: UUID) -> PushResult:
        """
        Push items below reorder point to CRM for alerts.

        Args:
            organization_id: Organization ID

        Returns:
            PushResult with push statistics
        """
        if not self.is_configured:
            return PushResult(
                success=False,
                items_pushed=0,
                errors=["CRM inventory push not configured"],
            )

        logger.info("Pushing low stock alerts to CRM for org %s", organization_id)

        low_stock_items = InventoryBalanceService.get_low_stock_items(
            self.db, organization_id
        )

        if not low_stock_items:
            logger.info("No low stock items to push")
            return PushResult(success=True, items_pushed=0, errors=[])

        items = []
        for item in low_stock_items:
            items.append(
                {
                    "item_id": str(item.item_id),
                    "item_code": item.item_code,
                    "item_name": item.item_name,
                    "quantity_on_hand": str(item.quantity_on_hand),
                    "quantity_available": str(item.quantity_available),
                    "reorder_point": str(item.reorder_point),
                    "reorder_quantity": str(item.reorder_quantity)
                    if item.reorder_quantity
                    else None,
                    "suggested_order_qty": str(item.suggested_order_qty),
                    "is_below_reorder": True,
                }
            )

        payload = {
            "sync_type": "low_stock_alert",
            "organization_id": str(organization_id),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "items": items,
        }

        return self._send_to_crm(payload)

    def _get_inventory_items(
        self,
        organization_id: UUID,
        include_zero_stock: bool = False,
        item_ids: Optional[list[UUID]] = None,
    ) -> list[dict]:
        """
        Get inventory items with stock data for push.

        Args:
            organization_id: Organization ID
            include_zero_stock: Include zero stock items
            item_ids: Optional list to filter specific items

        Returns:
            List of item dictionaries ready for JSON serialization
        """
        stmt = (
            select(Item, ItemCategory)
            .outerjoin(ItemCategory, Item.category_id == ItemCategory.category_id)
            .where(
                Item.organization_id == organization_id,
                Item.is_active.is_(True),
                Item.track_inventory.is_(True),
            )
        )

        if item_ids:
            stmt = stmt.where(Item.item_id.in_(item_ids))

        stmt = stmt.order_by(Item.item_code)
        results = self.db.execute(stmt).all()

        items = []
        for item, category in results:
            on_hand = InventoryBalanceService.get_on_hand(
                self.db, organization_id, item.item_id
            )
            reserved = InventoryBalanceService.get_reserved(
                self.db, organization_id, item.item_id
            )
            available = on_hand - reserved

            if not include_zero_stock and available <= 0:
                continue

            reorder_point = item.reorder_point or Decimal("0")
            is_below_reorder = available <= reorder_point if reorder_point else False

            items.append(
                {
                    "item_id": str(item.item_id),
                    "item_code": item.item_code,
                    "item_name": item.item_name,
                    "description": item.description,
                    "category_code": category.category_code if category else None,
                    "category_name": category.category_name if category else None,
                    "base_uom": item.base_uom,
                    "quantity_on_hand": str(on_hand),
                    "quantity_reserved": str(reserved),
                    "quantity_available": str(available),
                    "reorder_point": str(item.reorder_point)
                    if item.reorder_point
                    else None,
                    "list_price": str(item.list_price) if item.list_price else None,
                    "currency_code": item.currency_code,
                    "barcode": item.barcode,
                    "is_below_reorder": is_below_reorder,
                }
            )

        return items

    def _send_to_crm(self, payload: dict) -> PushResult:
        """
        Send inventory payload to CRM webhook.

        Args:
            payload: JSON payload to send

        Returns:
            PushResult with operation status
        """
        url = settings.crm_inventory_webhook_url
        if not url:
            return PushResult(
                success=False,
                items_pushed=0,
                errors=["CRM_INVENTORY_WEBHOOK_URL not configured"],
            )

        items_count = len(payload.get("items", []))
        errors: list[str] = []

        for attempt in range(settings.crm_max_retries):
            try:
                logger.info(
                    "Sending %d items to CRM (attempt %d/%d)",
                    items_count,
                    attempt + 1,
                    settings.crm_max_retries,
                )

                response = self.client.post(url, json=payload)

                if response.status_code == 401:
                    return PushResult(
                        success=False,
                        items_pushed=0,
                        errors=["CRM authentication failed - check CRM_API_TOKEN"],
                    )

                if response.status_code == 429:
                    # Rate limited
                    retry_after = int(response.headers.get("Retry-After", 5))
                    logger.warning("Rate limited, waiting %d seconds", retry_after)
                    import time

                    time.sleep(retry_after)
                    continue

                response.raise_for_status()

                crm_response = response.json() if response.content else {}

                logger.info(
                    "Successfully pushed %d items to CRM: %s",
                    items_count,
                    crm_response.get("message", "OK"),
                )

                return PushResult(
                    success=True,
                    items_pushed=items_count,
                    errors=[],
                    crm_response=crm_response,
                )

            except httpx.HTTPStatusError as e:
                error_msg = f"HTTP {e.response.status_code}: {e.response.text[:200]}"
                logger.error("CRM push failed: %s", error_msg)
                errors.append(error_msg)

                if e.response.status_code >= 500:
                    # Server error - retry
                    import time

                    time.sleep(2**attempt)
                    continue
                break

            except httpx.RequestError as e:
                error_msg = f"Request failed: {str(e)}"
                logger.error("CRM push failed: %s", error_msg)
                errors.append(error_msg)
                import time

                time.sleep(2**attempt)

        return PushResult(
            success=False,
            items_pushed=0,
            errors=errors or ["Max retries exceeded"],
        )

    def health_check(self) -> dict:
        """
        Check CRM inventory webhook connectivity.

        Returns:
            Dict with health status
        """
        if not self.is_configured:
            return {
                "configured": False,
                "healthy": False,
                "message": "CRM_INVENTORY_WEBHOOK_URL or CRM_API_TOKEN not set",
            }

        try:
            # Send minimal ping payload
            response = self.client.post(
                settings.crm_inventory_webhook_url,  # type: ignore
                json={
                    "sync_type": "health_check",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "items": [],
                },
            )

            if response.status_code in (200, 201, 204):
                return {
                    "configured": True,
                    "healthy": True,
                    "message": "CRM inventory webhook accessible",
                    "webhook_url": settings.crm_inventory_webhook_url,
                }
            else:
                return {
                    "configured": True,
                    "healthy": False,
                    "message": f"CRM returned status {response.status_code}",
                    "webhook_url": settings.crm_inventory_webhook_url,
                }

        except Exception as e:
            return {
                "configured": True,
                "healthy": False,
                "message": str(e),
                "webhook_url": settings.crm_inventory_webhook_url,
            }
