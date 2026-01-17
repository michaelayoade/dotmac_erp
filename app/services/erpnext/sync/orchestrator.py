"""
ERPNext Sync Orchestrator.

Coordinates the full migration process with proper dependency ordering.
"""
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.sync import SyncHistory, SyncJobStatus, SyncType
from app.services.erpnext.client import ERPNextClient, ERPNextConfig

from .base import SyncResult
from .accounts import AccountSyncService
from .items import ItemCategorySyncService, ItemSyncService
from .assets import AssetCategorySyncService, AssetSyncService
from .contacts import CustomerSyncService, SupplierSyncService
from .warehouses import WarehouseSyncService

logger = logging.getLogger(__name__)


@dataclass
class MigrationConfig:
    """Configuration for ERPNext migration."""

    # ERPNext connection
    erpnext_url: str
    erpnext_api_key: str
    erpnext_api_secret: str
    erpnext_company: Optional[str] = None

    # Sync options
    sync_type: SyncType = SyncType.FULL
    batch_size: int = 100

    # Entity types to sync (empty = all)
    entity_types: Optional[list[str]] = None

    # DotMac Books configuration
    ar_control_account_id: Optional[uuid.UUID] = None
    ap_control_account_id: Optional[uuid.UUID] = None
    default_inventory_account_id: Optional[uuid.UUID] = None
    default_asset_account_id: Optional[uuid.UUID] = None
    default_depreciation_account_id: Optional[uuid.UUID] = None


# Sync phases with dependencies
SYNC_PHASES = [
    {
        "name": "Phase 1: Foundation",
        "entities": ["accounts", "item_categories", "asset_categories", "warehouses"],
    },
    {
        "name": "Phase 2: Master Data",
        "entities": ["customers", "suppliers", "items", "assets"],
    },
    # Phase 3 (transactions) can be added later
]

# All supported entity types
SUPPORTED_ENTITIES = {
    "accounts": "Chart of Accounts",
    "item_categories": "Item Categories",
    "asset_categories": "Asset Categories",
    "warehouses": "Warehouses",
    "customers": "Customers",
    "suppliers": "Suppliers",
    "items": "Items",
    "assets": "Assets",
}


class ERPNextSyncOrchestrator:
    """
    Orchestrates ERPNext to DotMac Books migration.

    Handles:
    - Dependency ordering
    - Phase execution
    - Progress tracking
    - Error aggregation
    """

    def __init__(
        self,
        db: Session,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        config: MigrationConfig,
    ):
        self.db = db
        self.organization_id = organization_id
        self.user_id = user_id
        self.config = config
        self._client: Optional[ERPNextClient] = None
        self._history: Optional[SyncHistory] = None

    @property
    def client(self) -> ERPNextClient:
        """Lazy-initialize ERPNext client."""
        if self._client is None:
            self._client = ERPNextClient(
                ERPNextConfig(
                    url=self.config.erpnext_url,
                    api_key=self.config.erpnext_api_key,
                    api_secret=self.config.erpnext_api_secret,
                    company=self.config.erpnext_company,
                )
            )
        return self._client

    def _get_service(self, entity_type: str) -> Any:
        """Get sync service for entity type."""
        services = {
            "accounts": AccountSyncService,
            "item_categories": ItemCategorySyncService,
            "asset_categories": AssetCategorySyncService,
            "warehouses": WarehouseSyncService,
            "customers": CustomerSyncService,
            "suppliers": SupplierSyncService,
            "items": ItemSyncService,
            "assets": AssetSyncService,
        }

        service_class = services.get(entity_type)
        if not service_class:
            raise ValueError(f"Unknown entity type: {entity_type}")

        # Create service with config
        service = service_class(
            db=self.db,
            organization_id=self.organization_id,
            user_id=self.user_id,
        )

        # Inject account IDs where needed
        if hasattr(service, "ar_control_account_id"):
            service.ar_control_account_id = self.config.ar_control_account_id
        if hasattr(service, "ap_control_account_id"):
            service.ap_control_account_id = self.config.ap_control_account_id
        if hasattr(service, "inventory_account_id"):
            service.inventory_account_id = self.config.default_inventory_account_id
        if hasattr(service, "asset_account_id"):
            service.asset_account_id = self.config.default_asset_account_id
        if hasattr(service, "depreciation_account_id"):
            service.depreciation_account_id = self.config.default_depreciation_account_id

        return service

    def _create_history(self, entity_types: list[str]) -> SyncHistory:
        """Create sync history record."""
        history = SyncHistory(
            organization_id=self.organization_id,
            source_system="erpnext",
            sync_type=self.config.sync_type,
            entity_types=entity_types,
            created_by_user_id=self.user_id,
        )
        self.db.add(history)
        self.db.flush()
        return history

    def _filter_entities(self, entities: list[str]) -> list[str]:
        """Filter entities based on config."""
        if not self.config.entity_types:
            return entities
        return [e for e in entities if e in self.config.entity_types]

    def test_connection(self) -> dict[str, Any]:
        """Test ERPNext connection."""
        try:
            result = self.client.test_connection()
            return {"success": True, "user": result.get("user")}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def preview(self) -> dict[str, Any]:
        """
        Preview what will be synced.

        Returns counts for each entity type.
        """
        preview = {
            "entity_types": {},
            "total_records": 0,
        }

        # Determine entities to sync
        all_entities = []
        for phase in SYNC_PHASES:
            all_entities.extend(phase["entities"])

        entities = self._filter_entities(all_entities)

        for entity_type in entities:
            try:
                count = self._get_count(entity_type)
                preview["entity_types"][entity_type] = {
                    "name": SUPPORTED_ENTITIES.get(entity_type, entity_type),
                    "count": count,
                }
                preview["total_records"] += count
            except Exception as e:
                logger.warning("Failed to get count for %s: %s", entity_type, e)
                preview["entity_types"][entity_type] = {
                    "name": SUPPORTED_ENTITIES.get(entity_type, entity_type),
                    "count": 0,
                    "error": str(e),
                }

        return preview

    def _get_count(self, entity_type: str) -> int:
        """Get count of records for entity type."""
        doctype_map = {
            "accounts": "Account",
            "item_categories": "Item Group",
            "asset_categories": "Asset Category",
            "warehouses": "Warehouse",
            "customers": "Customer",
            "suppliers": "Supplier",
            "items": "Item",
            "assets": "Asset",
        }

        doctype = doctype_map.get(entity_type)
        if not doctype:
            return 0

        filters = {}
        if self.config.erpnext_company and entity_type in ["accounts", "assets", "warehouses"]:
            filters["company"] = self.config.erpnext_company
        if entity_type in ["customers", "suppliers", "items"]:
            filters["disabled"] = 0

        return self.client.get_count(doctype, filters)

    def run(self) -> SyncHistory:
        """
        Execute the full migration.

        Returns:
            SyncHistory with results
        """
        # Determine entities to sync
        all_entities = []
        for phase in SYNC_PHASES:
            all_entities.extend(phase["entities"])

        entities = self._filter_entities(all_entities)

        # Create history record
        self._history = self._create_history(entities)
        self._history.start()

        try:
            # Execute phases in order
            for phase in SYNC_PHASES:
                phase_entities = self._filter_entities(phase["entities"])
                if not phase_entities:
                    continue

                logger.info("Starting %s", phase["name"])

                for entity_type in phase_entities:
                    result = self._sync_entity_type(entity_type)
                    self._update_history(result)

                # Commit after each phase
                self.db.commit()

            self._history.complete()

        except Exception as e:
            logger.exception("Migration failed: %s", e)
            self._history.fail(str(e))
            self.db.rollback()

        finally:
            if self._client:
                self._client.close()

        self.db.commit()
        return self._history

    def _sync_entity_type(self, entity_type: str) -> SyncResult:
        """Sync a single entity type."""
        logger.info("Syncing %s", entity_type)

        try:
            service = self._get_service(entity_type)
            incremental = self.config.sync_type == SyncType.INCREMENTAL
            result = service.sync(
                client=self.client,
                incremental=incremental,
                batch_size=self.config.batch_size,
            )
            return result

        except Exception as e:
            logger.exception("Failed to sync %s: %s", entity_type, e)
            result = SyncResult(entity_type=entity_type)
            result.add_error("system", str(e))
            return result

    def _update_history(self, result: SyncResult) -> None:
        """Update history record with result."""
        if not self._history:
            return

        self._history.total_records += result.total_records
        self._history.synced_count += result.synced_count
        self._history.skipped_count += result.skipped_count
        self._history.error_count += result.error_count

        # Add errors (capped)
        for error in result.errors:
            self._history.add_error(
                doctype=result.entity_type,
                name=error.get("name", ""),
                error=error.get("error", ""),
            )

    def run_single(self, entity_type: str) -> SyncResult:
        """
        Sync a single entity type only.

        Useful for retrying or selective sync.
        """
        if entity_type not in SUPPORTED_ENTITIES:
            raise ValueError(f"Unknown entity type: {entity_type}")

        try:
            result = self._sync_entity_type(entity_type)
            self.db.commit()
            return result
        finally:
            if self._client:
                self._client.close()
