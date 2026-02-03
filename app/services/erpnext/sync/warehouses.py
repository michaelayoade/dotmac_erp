"""
Warehouse Sync Service - ERPNext to DotMac ERP.
"""
import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.inventory.warehouse import Warehouse
from app.services.erpnext.mappings.warehouses import WarehouseMapping

from .base import BaseSyncService


class WarehouseSyncService(BaseSyncService[Warehouse]):
    """Sync Warehouses from ERPNext."""

    source_doctype = "Warehouse"
    target_table = "inv.warehouse"

    def __init__(
        self,
        db: Session,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ):
        super().__init__(db, organization_id, user_id)
        self._mapping = WarehouseMapping()
        self._warehouse_cache: dict[str, Warehouse] = {}

    def fetch_records(self, client: Any, since: Optional[datetime] = None):
        """Fetch warehouses from ERPNext."""
        if since:
            yield from client.get_modified_since(
                doctype="Warehouse",
                since=since,
            )
        else:
            yield from client.get_warehouses()

    def transform_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """Transform ERPNext warehouse to DotMac ERP format."""
        return self._mapping.transform_record(record)

    def create_entity(self, data: dict[str, Any]) -> Warehouse:
        """Create Warehouse entity."""
        data.pop("_parent_source_name", None)  # Skip hierarchy for now
        data.pop("_source_modified", None)
        is_group = data.pop("_is_group", False)

        # Skip group warehouses (they're just for organization in ERPNext)
        if is_group:
            # Create a minimal placeholder
            pass

        warehouse = Warehouse(
            organization_id=self.organization_id,
            warehouse_code=data["warehouse_code"],
            warehouse_name=data["warehouse_name"],
            is_active=data.get("is_active", True),
            is_receiving=data.get("is_receiving", True),
            is_shipping=data.get("is_shipping", True),
            is_consignment=data.get("is_consignment", False),
            is_transit=data.get("is_transit", False),
        )
        return warehouse

    def update_entity(self, entity: Warehouse, data: dict[str, Any]) -> Warehouse:
        """Update existing Warehouse."""
        data.pop("_parent_source_name", None)
        data.pop("_source_modified", None)
        data.pop("_is_group", None)

        entity.warehouse_name = data["warehouse_name"]
        entity.is_active = data.get("is_active", True)
        entity.is_receiving = data.get("is_receiving", True)
        entity.is_shipping = data.get("is_shipping", True)
        entity.is_consignment = data.get("is_consignment", False)
        entity.is_transit = data.get("is_transit", False)

        return entity

    def get_entity_id(self, entity: Warehouse) -> uuid.UUID:
        """Get warehouse ID."""
        return entity.warehouse_id

    def find_existing_entity(self, source_name: str) -> Optional[Warehouse]:
        """Find existing warehouse by code."""
        if source_name in self._warehouse_cache:
            return self._warehouse_cache[source_name]

        sync_entity = self.get_sync_entity(source_name)
        if sync_entity and sync_entity.target_id:
            warehouse = self.db.get(Warehouse, sync_entity.target_id)
            if warehouse:
                self._warehouse_cache[source_name] = warehouse
                return warehouse

        # Try by code (truncated source_name)
        code = source_name[:30] if source_name else None
        if code:
            result = self.db.execute(
                select(Warehouse).where(
                    Warehouse.organization_id == self.organization_id,
                    Warehouse.warehouse_code == code,
                )
            ).scalar_one_or_none()

            if result:
                self._warehouse_cache[source_name] = result
                return result

        return None
