"""
Base Sync Service for ERPNext to DotMac ERP migration.
"""

import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Generic, Optional, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import Base
from app.models.sync import SyncEntity, SyncStatus

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=Base)


@dataclass
class SyncResult:
    """Result of a sync operation."""

    entity_type: str
    total_records: int = 0
    synced_count: int = 0
    skipped_count: int = 0
    error_count: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)

    def add_error(self, source_name: str, error: str) -> None:
        """Add error to results."""
        if len(self.errors) < 100:
            self.errors.append({"name": source_name, "error": error})
        self.error_count += 1

    @property
    def success_rate(self) -> float:
        """Calculate success rate."""
        if self.total_records == 0:
            return 0.0
        return (self.synced_count / self.total_records) * 100


class BaseSyncService(ABC, Generic[T]):
    """
    Base class for syncing entities from ERPNext to DotMac ERP.

    Handles:
    - Sync state tracking
    - Duplicate detection
    - Error handling
    - Batch processing
    """

    # Subclasses must define these
    source_doctype: str  # ERPNext DocType name
    target_table: str  # DotMac ERP table (schema.table)

    def __init__(
        self,
        db: Session,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ):
        self.db = db
        self.organization_id = organization_id
        self.user_id = user_id
        self._sync_entity_cache: dict[str, SyncEntity] = {}

    @abstractmethod
    def fetch_records(self, client: Any, since: Optional[datetime] = None):
        """
        Fetch records from ERPNext.

        Args:
            client: ERPNext client instance
            since: Optional timestamp for incremental sync

        Yields:
            ERPNext document dictionaries
        """
        pass

    @abstractmethod
    def transform_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """
        Transform ERPNext record to DotMac ERP format.

        Args:
            record: ERPNext document

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

    def get_unique_key(self, record: dict[str, Any]) -> str:
        """
        Get unique identifier from ERPNext record.

        Default: 'name' field (ERPNext's standard ID).
        Override for custom unique keys.
        """
        return str(record.get("name", ""))

    def get_sync_entity(self, source_name: str) -> Optional[SyncEntity]:
        """
        Get existing sync entity record.

        Uses cache to avoid repeated DB queries.
        """
        if source_name in self._sync_entity_cache:
            return self._sync_entity_cache[source_name]

        result = self.db.execute(
            select(SyncEntity).where(
                SyncEntity.organization_id == self.organization_id,
                SyncEntity.source_system == "erpnext",
                SyncEntity.source_doctype == self.source_doctype,
                SyncEntity.source_name == source_name,
            )
        ).scalar_one_or_none()

        if result:
            self._sync_entity_cache[source_name] = result

        return result

    def create_sync_entity(self, source_name: str) -> SyncEntity:
        """Create new sync entity tracking record."""
        sync_entity = SyncEntity(
            organization_id=self.organization_id,
            source_system="erpnext",
            source_doctype=self.source_doctype,
            source_name=source_name,
            target_table=self.target_table,
            sync_status=SyncStatus.PENDING,
        )
        self.db.add(sync_entity)
        self._sync_entity_cache[source_name] = sync_entity
        return sync_entity

    def find_existing_entity(self, source_name: str) -> Optional[T]:
        """
        Find existing DotMac ERP entity by sync record.

        Returns None if no sync record or target_id not set.
        """
        sync_entity = self.get_sync_entity(source_name)
        if not sync_entity or not sync_entity.target_id:
            return None

        # Subclasses should override this to actually fetch the entity
        return None

    def should_update(
        self, sync_entity: SyncEntity, source_modified: Optional[datetime]
    ) -> bool:
        """
        Determine if entity should be updated.

        For incremental sync, only update if source is newer.
        """
        if sync_entity.sync_status == SyncStatus.FAILED:
            # Retry failed records
            return True

        if source_modified is None:
            # No modification timestamp - always update
            return True

        if sync_entity.source_modified is None:
            # No recorded modification - update
            return True

        # Normalize timestamps for comparison (strip timezone info)
        source_ts = (
            source_modified.replace(tzinfo=None)
            if source_modified.tzinfo
            else source_modified
        )
        stored_ts = (
            sync_entity.source_modified.replace(tzinfo=None)
            if sync_entity.source_modified.tzinfo
            else sync_entity.source_modified
        )

        # Update if source is newer
        return source_ts > stored_ts

    def sync(
        self,
        client: Any,
        incremental: bool = False,
        batch_size: int = 100,
    ) -> SyncResult:
        """
        Execute sync from ERPNext.

        Args:
            client: ERPNext client instance
            incremental: If True, only sync modified records
            batch_size: Number of records per batch commit

        Returns:
            SyncResult with statistics
        """
        result = SyncResult(entity_type=self.source_doctype)

        # Determine since timestamp for incremental
        since = None
        if incremental:
            since = self._get_last_sync_time()

        logger.info(
            "Starting %s sync for %s (since=%s)",
            "incremental" if incremental else "full",
            self.source_doctype,
            since,
        )

        batch_count = 0

        try:
            for record in self.fetch_records(client, since):
                result.total_records += 1
                source_name = self.get_unique_key(record)

                try:
                    # Use a savepoint so a single-record error doesn't
                    # roll back the entire batch / session.
                    savepoint = self.db.begin_nested()
                    try:
                        entity = self._sync_single_record(record, result)
                        if entity:
                            batch_count += 1
                        savepoint.commit()
                    except Exception:
                        savepoint.rollback()
                        raise

                    # Commit batch to DB periodically
                    if batch_count >= batch_size:
                        self.db.commit()
                        batch_count = 0

                except Exception as e:
                    logger.exception(
                        "Error syncing %s %s: %s",
                        self.source_doctype,
                        source_name,
                        str(e),
                    )
                    result.add_error(source_name, str(e))

                    # Record the error as a sync entity
                    try:
                        sync_entity = SyncEntity(
                            organization_id=self.organization_id,
                            source_system="erpnext",
                            source_doctype=self.source_doctype,
                            source_name=source_name,
                            target_table=self.target_table,
                            sync_status=SyncStatus.FAILED,
                            error_message=str(e)[:500],
                            retry_count=1,
                        )
                        self.db.add(sync_entity)
                        self.db.flush()
                    except Exception:
                        self.db.rollback()

            # Final commit
            if batch_count:
                self.db.commit()

        except Exception as e:
            logger.exception("Sync failed for %s: %s", self.source_doctype, str(e))
            result.add_error("system", f"Sync failed: {str(e)}")

        logger.info(
            "Completed %s sync: %d total, %d synced, %d skipped, %d errors",
            self.source_doctype,
            result.total_records,
            result.synced_count,
            result.skipped_count,
            result.error_count,
        )

        return result

    def _sync_single_record(
        self, record: dict[str, Any], result: SyncResult
    ) -> Optional[T]:
        """
        Sync a single record.

        Returns the entity if created/updated, None if skipped.
        """
        source_name = self.get_unique_key(record)
        source_modified = record.get("modified")
        if isinstance(source_modified, str):
            try:
                source_modified = datetime.fromisoformat(source_modified)
            except ValueError:
                source_modified = None

        # Get or create sync entity
        sync_entity = self.get_sync_entity(source_name)
        if not sync_entity:
            sync_entity = self.create_sync_entity(source_name)

        # Check if update needed
        if sync_entity.sync_status == SyncStatus.SYNCED:
            if not self.should_update(sync_entity, source_modified):
                result.skipped_count += 1
                return None

        # Transform record
        data = self.transform_record(record)

        # Create or update entity
        existing = self.find_existing_entity(source_name)
        if existing:
            entity = self.update_entity(existing, data)
        else:
            entity = self.create_entity(data)
            self.db.add(entity)
            self.db.flush()  # Get ID

        # Update sync entity
        sync_entity.target_id = self.get_entity_id(entity)
        sync_entity.source_modified = source_modified
        sync_entity.mark_synced(sync_entity.target_id)

        result.synced_count += 1
        return entity

    def _get_last_sync_time(self) -> Optional[datetime]:
        """Get the most recent sync timestamp for incremental sync."""
        result = self.db.execute(
            select(SyncEntity.synced_at)
            .where(
                SyncEntity.organization_id == self.organization_id,
                SyncEntity.source_system == "erpnext",
                SyncEntity.source_doctype == self.source_doctype,
                SyncEntity.sync_status == SyncStatus.SYNCED,
            )
            .order_by(SyncEntity.synced_at.desc())
            .limit(1)
        ).scalar_one_or_none()

        return result

    def resolve_parent_id(
        self, parent_source_name: Optional[str]
    ) -> Optional[uuid.UUID]:
        """
        Resolve parent entity ID from source name.

        Used for hierarchical entities (accounts, categories).
        """
        if not parent_source_name:
            return None

        sync_entity = self.get_sync_entity(parent_source_name)
        if sync_entity and sync_entity.target_id:
            return sync_entity.target_id

        return None
