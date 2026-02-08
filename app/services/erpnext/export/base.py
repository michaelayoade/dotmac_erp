"""
Base Export Service for pushing DotMac ERP changes to ERPNext.

During the transition period, changes made in DotMac need to be reflected
back in ERPNext to maintain consistency until the full migration is complete.
"""

import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Generic, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import Base
from app.models.sync import SyncEntity, SyncStatus
from app.services.erpnext.client import ERPNextClient, ERPNextError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=Base)


@dataclass
class ExportResult:
    """Result of an export operation."""

    entity_type: str
    total_records: int = 0
    exported_count: int = 0
    skipped_count: int = 0
    error_count: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)

    def add_error(self, entity_id: str, error: str) -> None:
        """Add error to results."""
        if len(self.errors) < 100:
            self.errors.append({"id": entity_id, "error": error})
        self.error_count += 1

    @property
    def success_rate(self) -> float:
        """Calculate success rate."""
        if self.total_records == 0:
            return 0.0
        return (self.exported_count / self.total_records) * 100


class BaseExportService(ABC, Generic[T]):
    """
    Base class for exporting entities from DotMac ERP to ERPNext.

    Handles:
    - Bidirectional sync state tracking
    - Create/update detection
    - Error handling and retry logic
    - Batch processing
    """

    # Subclasses must define these
    target_doctype: str  # ERPNext DocType name
    source_table: str  # DotMac ERP table (schema.table)

    def __init__(
        self,
        db: Session,
        client: ERPNextClient,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ):
        self.db = db
        self.client = client
        self.organization_id = organization_id
        self.user_id = user_id
        self._sync_entity_cache: dict[uuid.UUID, SyncEntity] = {}

    @abstractmethod
    def get_pending_exports(self) -> list[T]:
        """
        Get entities that need to be exported to ERPNext.

        This typically includes:
        - New entities created in DotMac (no erpnext_id)
        - Entities modified since last sync

        Returns:
            List of entities to export
        """
        pass

    @abstractmethod
    def transform_for_export(self, entity: T) -> dict[str, Any]:
        """
        Transform DotMac ERP entity to ERPNext format.

        Args:
            entity: DotMac ERP entity

        Returns:
            Dictionary suitable for ERPNext API
        """
        pass

    @abstractmethod
    def get_entity_id(self, entity: T) -> uuid.UUID:
        """Get the primary key ID from an entity."""
        pass

    @abstractmethod
    def get_erpnext_id(self, entity: T) -> str | None:
        """Get the ERPNext document name from entity (if synced before)."""
        pass

    @abstractmethod
    def set_erpnext_id(self, entity: T, erpnext_id: str) -> None:
        """Set the ERPNext document name on entity after export."""
        pass

    def get_sync_entity_by_target(self, target_id: uuid.UUID) -> SyncEntity | None:
        """
        Get existing sync entity record by target ID.

        Uses cache to avoid repeated DB queries.
        """
        if target_id in self._sync_entity_cache:
            return self._sync_entity_cache[target_id]

        result = self.db.execute(
            select(SyncEntity).where(
                SyncEntity.organization_id == self.organization_id,
                SyncEntity.source_system == "erpnext",
                SyncEntity.source_doctype == self.target_doctype,
                SyncEntity.target_table == self.source_table,
                SyncEntity.target_id == target_id,
            )
        ).scalar_one_or_none()

        if result:
            self._sync_entity_cache[target_id] = result

        return result

    def should_create_in_erpnext(self, entity: T) -> bool:
        """
        Determine if entity should be created as new document in ERPNext.

        Override for custom logic (e.g., only export certain statuses).
        """
        return self.get_erpnext_id(entity) is None

    def export_single(self, entity: T) -> tuple[bool, str | None]:
        """
        Export a single entity to ERPNext.

        Args:
            entity: DotMac ERP entity to export

        Returns:
            Tuple of (success, error_message)
        """
        entity_id = self.get_entity_id(entity)
        erpnext_id = self.get_erpnext_id(entity)

        try:
            data = self.transform_for_export(entity)

            if erpnext_id:
                # Update existing document
                result = self.client.update_document(
                    self.target_doctype,
                    erpnext_id,
                    data,
                )
                logger.debug(
                    "Updated %s %s in ERPNext",
                    self.target_doctype,
                    erpnext_id,
                )
            else:
                # Create new document
                result = self.client.create_document(
                    self.target_doctype,
                    data,
                )
                new_erpnext_id = result.get("name")
                if new_erpnext_id:
                    self.set_erpnext_id(entity, new_erpnext_id)
                    erpnext_id = new_erpnext_id

                logger.debug(
                    "Created %s %s in ERPNext (ID: %s)",
                    self.target_doctype,
                    entity_id,
                    new_erpnext_id,
                )

            # Update sync tracking
            self._update_sync_entity(entity_id, erpnext_id)

            return True, None

        except ERPNextError as e:
            error_msg = f"ERPNext API error: {e.message}"
            logger.error(
                "Failed to export %s %s: %s",
                self.target_doctype,
                entity_id,
                error_msg,
            )
            return False, error_msg

        except Exception as e:
            error_msg = str(e)
            logger.exception(
                "Unexpected error exporting %s %s: %s",
                self.target_doctype,
                entity_id,
                error_msg,
            )
            return False, error_msg

    def _update_sync_entity(
        self, target_id: uuid.UUID, source_name: str | None
    ) -> None:
        """Update or create sync entity after successful export."""
        if source_name is None:
            raise ValueError("source_name is required for sync entity updates")
        sync_entity = self.get_sync_entity_by_target(target_id)

        if sync_entity:
            sync_entity.source_name = source_name
            sync_entity.sync_status = SyncStatus.SYNCED
            sync_entity.synced_at = datetime.utcnow()
            sync_entity.error_message = None
        else:
            sync_entity = SyncEntity(
                organization_id=self.organization_id,
                source_system="erpnext",
                source_doctype=self.target_doctype,
                source_name=source_name,
                target_table=self.source_table,
                target_id=target_id,
                sync_status=SyncStatus.SYNCED,
                synced_at=datetime.utcnow(),
            )
            self.db.add(sync_entity)
            self._sync_entity_cache[target_id] = sync_entity

    def export_batch(
        self,
        entities: list[T] | None = None,
        batch_size: int = 50,
    ) -> ExportResult:
        """
        Export multiple entities to ERPNext.

        Args:
            entities: Specific entities to export (if None, uses get_pending_exports)
            batch_size: Number of records to process before flushing

        Returns:
            ExportResult with statistics
        """
        result = ExportResult(entity_type=self.target_doctype)

        if entities is None:
            entities = self.get_pending_exports()

        result.total_records = len(entities)

        logger.info(
            "Starting export of %d %s records to ERPNext",
            result.total_records,
            self.target_doctype,
        )

        batch_count = 0

        for entity in entities:
            entity_id = self.get_entity_id(entity)

            success, error = self.export_single(entity)

            if success:
                result.exported_count += 1
                batch_count += 1

                if batch_count >= batch_size:
                    self.db.flush()
                    batch_count = 0
            else:
                result.add_error(str(entity_id), error or "Unknown error")

        # Final flush
        if batch_count > 0:
            self.db.flush()

        logger.info(
            "Completed %s export: %d total, %d exported, %d errors",
            self.target_doctype,
            result.total_records,
            result.exported_count,
            result.error_count,
        )

        return result

    def submit_document(self, entity: T) -> tuple[bool, str | None]:
        """
        Submit a document in ERPNext (for workflow documents).

        Args:
            entity: DotMac ERP entity (must have erpnext_id)

        Returns:
            Tuple of (success, error_message)
        """
        erpnext_id = self.get_erpnext_id(entity)
        if not erpnext_id:
            return False, "Entity has no ERPNext ID - export first"

        try:
            self.client.submit_document(self.target_doctype, erpnext_id)
            logger.debug(
                "Submitted %s %s in ERPNext",
                self.target_doctype,
                erpnext_id,
            )
            return True, None
        except ERPNextError as e:
            return False, f"Failed to submit: {e.message}"

    def cancel_document(self, entity: T) -> tuple[bool, str | None]:
        """
        Cancel a document in ERPNext.

        Args:
            entity: DotMac ERP entity (must have erpnext_id)

        Returns:
            Tuple of (success, error_message)
        """
        erpnext_id = self.get_erpnext_id(entity)
        if not erpnext_id:
            return False, "Entity has no ERPNext ID"

        try:
            self.client.cancel_document(self.target_doctype, erpnext_id)
            logger.debug(
                "Cancelled %s %s in ERPNext",
                self.target_doctype,
                erpnext_id,
            )
            return True, None
        except ERPNextError as e:
            return False, f"Failed to cancel: {e.message}"
