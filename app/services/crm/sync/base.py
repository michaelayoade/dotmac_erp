"""
Base Sync Service for CRM to DotMac ERP integration.

Provides common functionality for syncing entities from crm.dotmac.io.
Follows the same pattern as ERPNext sync but adapted for CRM API.
"""

import logging
import uuid
from abc import ABC, abstractmethod
from collections.abc import Generator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Generic, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import Base
from app.models.sync import SyncEntity, SyncStatus
from app.services.crm.client import CRMClient

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=Base)

# Source system identifier for CRM
CRM_SOURCE_SYSTEM = "crm"


@dataclass
class SyncResult:
    """Result of a sync operation."""

    entity_type: str
    total_records: int = 0
    created_count: int = 0
    updated_count: int = 0
    skipped_count: int = 0
    error_count: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)

    def add_error(self, source_id: str, error: str) -> None:
        """Add error to results."""
        if len(self.errors) < 100:  # Cap error list
            self.errors.append({"id": source_id, "error": error})
        self.error_count += 1

    @property
    def synced_count(self) -> int:
        """Total successfully synced (created + updated)."""
        return self.created_count + self.updated_count

    @property
    def success_rate(self) -> float:
        """Calculate success rate."""
        if self.total_records == 0:
            return 0.0
        return (self.synced_count / self.total_records) * 100

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for logging/API response."""
        return {
            "entity_type": self.entity_type,
            "total_records": self.total_records,
            "created": self.created_count,
            "updated": self.updated_count,
            "skipped": self.skipped_count,
            "errors": self.error_count,
            "success_rate": f"{self.success_rate:.1f}%",
        }


class BaseCRMSyncService(ABC, Generic[T]):
    """
    Base class for syncing entities from CRM to DotMac ERP.

    Handles:
    - Sync state tracking via SyncEntity
    - Create vs update detection
    - Error handling and retry tracking
    - Incremental sync support

    Subclasses must implement:
    - source_entity_type: CRM entity name (e.g., 'ticket', 'project')
    - target_table: ERP table name (e.g., 'support.ticket')
    - fetch_records(): Fetch from CRM API
    - transform_record(): Map CRM fields to ERP fields
    - create_entity(): Create new ERP entity
    - update_entity(): Update existing ERP entity
    - get_entity_id(): Get PK from entity
    """

    # Subclasses must define these
    source_entity_type: str  # CRM entity type (e.g., 'ticket')
    target_table: str  # DotMac ERP table (e.g., 'support.ticket')

    def __init__(
        self,
        db: Session,
        organization_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
    ):
        self.db = db
        self.organization_id = organization_id
        self.user_id = user_id
        self._sync_entity_cache: dict[str, SyncEntity] = {}

    @abstractmethod
    def fetch_records(
        self,
        client: CRMClient,
        since: datetime | None = None,
    ) -> Generator[dict[str, Any], None, None]:
        """
        Fetch records from CRM.

        Args:
            client: CRM client instance
            since: Optional timestamp for incremental sync

        Yields:
            CRM record dictionaries
        """
        pass

    @abstractmethod
    def transform_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """
        Transform CRM record to DotMac ERP format.

        Args:
            record: CRM record

        Returns:
            Transformed data for DotMac ERP entity
        """
        pass

    @abstractmethod
    def create_entity(self, data: dict[str, Any]) -> T:
        """
        Create DotMac ERP entity from transformed data.

        Args:
            data: Transformed data

        Returns:
            Created entity (not yet committed)
        """
        pass

    @abstractmethod
    def update_entity(self, entity: T, data: dict[str, Any]) -> T:
        """
        Update existing DotMac ERP entity.

        Args:
            entity: Existing entity
            data: New transformed data

        Returns:
            Updated entity
        """
        pass

    @abstractmethod
    def get_entity_id(self, entity: T) -> uuid.UUID:
        """Get the primary key ID from an entity."""
        pass

    def get_source_id(self, record: dict[str, Any]) -> str:
        """
        Get unique identifier from CRM record.

        Default: 'id' field. Override for custom IDs.
        """
        return str(record.get("id", ""))

    def get_source_modified(self, record: dict[str, Any]) -> datetime | None:
        """
        Get modification timestamp from CRM record.

        Used for incremental sync tracking.
        """
        updated_at = record.get("updated_at") or record.get("modified_at")
        if updated_at:
            if isinstance(updated_at, datetime):
                return updated_at
            try:
                return datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass
        return None

    # =========================================================================
    # Sync Entity Management
    # =========================================================================

    def get_sync_entity(self, source_id: str) -> SyncEntity | None:
        """
        Get existing sync entity record.

        Uses cache to avoid repeated DB queries.
        """
        if source_id in self._sync_entity_cache:
            return self._sync_entity_cache[source_id]

        result = self.db.execute(
            select(SyncEntity).where(
                SyncEntity.organization_id == self.organization_id,
                SyncEntity.source_system == CRM_SOURCE_SYSTEM,
                SyncEntity.source_doctype == self.source_entity_type,
                SyncEntity.source_name == source_id,
            )
        ).scalar_one_or_none()

        if result:
            self._sync_entity_cache[source_id] = result
        return result

    def create_sync_entity(
        self,
        source_id: str,
        source_modified: datetime | None = None,
    ) -> SyncEntity:
        """Create a new sync entity record."""
        sync_entity = SyncEntity(
            organization_id=self.organization_id,
            source_system=CRM_SOURCE_SYSTEM,
            source_doctype=self.source_entity_type,
            source_name=source_id,
            target_table=self.target_table,
            sync_status=SyncStatus.PENDING,
            source_modified=source_modified,
        )
        self.db.add(sync_entity)
        self._sync_entity_cache[source_id] = sync_entity
        return sync_entity

    def get_existing_entity(self, sync_entity: SyncEntity) -> T | None:
        """
        Get existing ERP entity from sync record.

        Override if entity lookup is more complex.
        """
        if not sync_entity.target_id:
            return None
        # This is a simplified lookup - subclasses should override
        # with proper entity class reference
        return None

    def get_last_sync_time(self) -> datetime | None:
        """
        Get the most recent successful sync time for this entity type.

        Used for incremental sync.
        """
        result = self.db.execute(
            select(SyncEntity.synced_at)
            .where(
                SyncEntity.organization_id == self.organization_id,
                SyncEntity.source_system == CRM_SOURCE_SYSTEM,
                SyncEntity.source_doctype == self.source_entity_type,
                SyncEntity.sync_status == SyncStatus.SYNCED,
            )
            .order_by(SyncEntity.synced_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        return result

    # =========================================================================
    # Main Sync Logic
    # =========================================================================

    def sync(
        self,
        client: CRMClient,
        incremental: bool = True,
        batch_size: int = 100,
    ) -> SyncResult:
        """
        Execute sync from CRM to ERP.

        Args:
            client: CRM client instance
            incremental: If True, only sync records modified since last sync
            batch_size: Commit after this many records

        Returns:
            SyncResult with statistics
        """
        result = SyncResult(entity_type=self.source_entity_type)

        # Get last sync time for incremental sync
        since = self.get_last_sync_time() if incremental else None
        if since:
            logger.info(
                "Starting incremental %s sync since %s",
                self.source_entity_type,
                since.isoformat(),
            )
        else:
            logger.info("Starting full %s sync", self.source_entity_type)

        processed = 0

        try:
            for record in self.fetch_records(client, since):
                result.total_records += 1
                source_id = self.get_source_id(record)

                if not source_id:
                    result.add_error("unknown", "Record has no ID")
                    continue

                try:
                    self._sync_single_record(record, source_id, result)
                    processed += 1

                    # Commit in batches
                    if processed % batch_size == 0:
                        self.db.flush()
                        logger.debug(
                            "Flushed batch: %d %s records",
                            processed,
                            self.source_entity_type,
                        )

                except Exception as e:
                    logger.exception(
                        "Error syncing %s %s: %s",
                        self.source_entity_type,
                        source_id,
                        str(e),
                    )
                    result.add_error(source_id, str(e))

            # Final flush
            self.db.flush()

        except Exception as e:
            logger.exception("Sync failed for %s: %s", self.source_entity_type, str(e))
            raise

        logger.info(
            "Completed %s sync: %s",
            self.source_entity_type,
            result.to_dict(),
        )
        return result

    def _sync_single_record(
        self,
        record: dict[str, Any],
        source_id: str,
        result: SyncResult,
    ) -> None:
        """
        Sync a single record from CRM.

        Creates or updates ERP entity and tracks sync state.
        """
        source_modified = self.get_source_modified(record)

        # Get or create sync entity
        sync_entity = self.get_sync_entity(source_id)

        if sync_entity is None:
            # Check if push-based sync (CRMSyncMapping) already created this entity
            # to prevent duplicate creation across the two sync systems
            existing_id = self._check_push_sync_mapping(source_id)
            if existing_id is not None:
                sync_entity = self.create_sync_entity(source_id, source_modified)
                sync_entity.mark_synced(existing_id)
                result.skipped_count += 1
                logger.debug(
                    "Skipped %s %s: already exists via push sync (local_id=%s)",
                    self.source_entity_type,
                    source_id,
                    existing_id,
                )
                return

            # New record - create
            sync_entity = self.create_sync_entity(source_id, source_modified)

            try:
                data = self.transform_record(record)
                entity = self.create_entity(data)
                self.db.add(entity)
                self.db.flush()

                sync_entity.mark_synced(self.get_entity_id(entity))
                result.created_count += 1

            except Exception as e:
                sync_entity.mark_failed(str(e))
                raise

        else:
            # Existing record - check if update needed
            if (
                sync_entity.sync_status == SyncStatus.SYNCED
                and source_modified
                and sync_entity.source_modified
                and source_modified <= sync_entity.source_modified
            ):
                # Already synced and not modified
                result.skipped_count += 1
                return

            try:
                data = self.transform_record(record)
                existing = self.get_existing_entity(sync_entity)

                if existing:
                    self.update_entity(existing, data)
                    sync_entity.source_modified = source_modified
                    sync_entity.mark_synced(self.get_entity_id(existing))
                    result.updated_count += 1
                else:
                    # Sync entity exists but target doesn't - recreate
                    entity = self.create_entity(data)
                    self.db.add(entity)
                    self.db.flush()
                    sync_entity.mark_synced(self.get_entity_id(entity))
                    result.created_count += 1

            except Exception as e:
                sync_entity.mark_failed(str(e))
                raise

    # =========================================================================
    # Webhook Handling
    # =========================================================================

    def handle_webhook(
        self,
        client: CRMClient,
        event_type: str,
        payload: dict[str, Any],
    ) -> SyncResult:
        """
        Handle a webhook event from CRM.

        Args:
            client: CRM client (may be used to fetch full record)
            event_type: Event type (e.g., 'created', 'updated', 'deleted')
            payload: Webhook payload with record data

        Returns:
            SyncResult for this single record
        """
        result = SyncResult(entity_type=self.source_entity_type)
        result.total_records = 1

        source_id = self.get_source_id(payload)
        if not source_id:
            result.add_error("unknown", "Webhook payload has no ID")
            return result

        try:
            if event_type == "deleted":
                # Handle deletion (soft delete in ERP)
                self._handle_deletion(source_id, result)
            else:
                # Created or updated - sync the record
                self._sync_single_record(payload, source_id, result)
                self.db.flush()

        except Exception as e:
            logger.exception(
                "Error handling %s webhook for %s %s: %s",
                event_type,
                self.source_entity_type,
                source_id,
                str(e),
            )
            result.add_error(source_id, str(e))

        return result

    # =========================================================================
    # Push-Sync Cross-Check
    # =========================================================================

    # Map pull-based source_entity_type to push-based CRMEntityType values
    _ENTITY_TYPE_TO_CRM: dict[str, str] = {
        "project": "PROJECT",
        "ticket": "TICKET",
        "work_order": "WORK_ORDER",
    }

    def _check_push_sync_mapping(self, source_id: str) -> uuid.UUID | None:
        """
        Check if the push-based sync system (CRMSyncMapping) already created
        a local entity for this CRM source_id.

        Returns the local_entity_id if found, None otherwise.
        """
        from app.models.sync.dotmac_crm_sync import CRMSyncMapping

        crm_type = self._ENTITY_TYPE_TO_CRM.get(self.source_entity_type)
        if not crm_type:
            return None

        mapping = self.db.scalar(
            select(CRMSyncMapping).where(
                CRMSyncMapping.organization_id == self.organization_id,
                CRMSyncMapping.crm_entity_type == crm_type,
                CRMSyncMapping.crm_id == source_id,
            )
        )
        if mapping:
            return mapping.local_entity_id
        return None

    def _handle_deletion(self, source_id: str, result: SyncResult) -> None:
        """
        Handle deletion of a CRM record.

        Default: Mark as skipped. Override to implement soft delete.
        """
        sync_entity = self.get_sync_entity(source_id)
        if sync_entity:
            sync_entity.mark_skipped("Deleted in CRM")
        result.skipped_count += 1
