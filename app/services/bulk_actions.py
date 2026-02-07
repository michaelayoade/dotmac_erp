"""
Base Bulk Action Service.

Provides generic bulk operations that module-specific services can extend.
Handles common patterns for delete, export, and status updates.
"""

from __future__ import annotations

import csv
import io
import logging
from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar, cast
from uuid import UUID

from fastapi import HTTPException, Response
from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.schemas.bulk_actions import BulkActionResult
from app.services.common import coerce_uuid

logger = logging.getLogger(__name__)

# Type variable for the model class
T = TypeVar("T")


class BulkActionService(ABC, Generic[T]):
    """
    Base class for bulk operations on entities.

    Subclasses should define:
    - model: The SQLAlchemy model class
    - id_field: The primary key field name (default: "id")
    - org_field: The organization_id field name (default: "organization_id")
    - export_fields: List of tuples (field_name, header_name) for CSV export
    """

    model: type[T]
    id_field: str = "id"
    org_field: str = "organization_id"
    export_fields: list[tuple[str, str]] = []

    def __init__(self, db: Session, organization_id: UUID, user_id: UUID | None = None):
        """
        Initialize the bulk action service.

        Args:
            db: Database session
            organization_id: Organization to scope operations to
            user_id: User performing the action (for audit)
        """
        self.db = db
        self.organization_id = coerce_uuid(organization_id)
        self.user_id = coerce_uuid(user_id) if user_id else None

    def _get_base_query(self, ids: list[UUID]):
        """
        Get base query filtered by organization and IDs.

        Args:
            ids: List of entity IDs to filter

        Returns:
            SQLAlchemy query
        """
        id_col = getattr(self.model, self.id_field)
        org_col = getattr(self.model, self.org_field)

        coerced_ids = [coerce_uuid(id) for id in ids]

        return self.db.query(self.model).filter(
            and_(
                org_col == self.organization_id,
                id_col.in_(coerced_ids),
            )
        )

    def _get_entities(self, ids: list[UUID]) -> list[T]:
        """
        Fetch entities by IDs, scoped to organization.

        Args:
            ids: List of entity IDs

        Returns:
            List of entity instances
        """
        return cast(list[T], self._get_base_query(ids).all())

    @abstractmethod
    def can_delete(self, entity: T) -> tuple[bool, str]:
        """
        Check if an entity can be deleted.

        Args:
            entity: Entity to check

        Returns:
            Tuple of (can_delete, reason_if_not)
        """
        ...

    async def bulk_delete(self, ids: list[UUID]) -> BulkActionResult:
        """
        Delete multiple records by ID.

        Args:
            ids: List of entity IDs to delete

        Returns:
            BulkActionResult with counts and any errors
        """
        if not ids:
            return BulkActionResult.failure("No IDs provided")

        entities = self._get_entities(ids)

        if not entities:
            return BulkActionResult.failure("No entities found with provided IDs")

        success_count = 0
        failed_count = 0
        errors: list[str] = []

        for entity in entities:
            can_del, reason = self.can_delete(entity)
            if not can_del:
                failed_count += 1
                errors.append(reason)
            else:
                try:
                    self.db.delete(entity)
                    success_count += 1
                except Exception as e:
                    failed_count += 1
                    errors.append(f"Failed to delete: {str(e)}")

        if success_count > 0:
            self.db.commit()

        if failed_count > 0:
            return BulkActionResult.partial(success_count, failed_count, errors)

        return BulkActionResult.success(success_count, f"Deleted {success_count} items")

    async def bulk_update_status(
        self,
        ids: list[UUID],
        status_field: str,
        new_status: Any,
    ) -> BulkActionResult:
        """
        Update status on multiple records.

        Args:
            ids: List of entity IDs
            status_field: Name of the status field
            new_status: New status value

        Returns:
            BulkActionResult with counts
        """
        if not ids:
            return BulkActionResult.failure("No IDs provided")

        entities = self._get_entities(ids)

        if not entities:
            return BulkActionResult.failure("No entities found with provided IDs")

        success_count = 0
        failed_count = 0
        errors: list[str] = []

        for entity in entities:
            try:
                if hasattr(entity, status_field):
                    setattr(entity, status_field, new_status)
                    success_count += 1
                else:
                    failed_count += 1
                    errors.append(f"Entity has no field '{status_field}'")
            except Exception as e:
                failed_count += 1
                errors.append(f"Failed to update: {str(e)}")

        if success_count > 0:
            self.db.commit()

        if failed_count > 0:
            return BulkActionResult.partial(success_count, failed_count, errors)

        return BulkActionResult.success(success_count, f"Updated {success_count} items")

    async def bulk_activate(self, ids: list[UUID]) -> BulkActionResult:
        """Activate multiple records (set is_active=True)."""
        return await self.bulk_update_status(ids, "is_active", True)

    async def bulk_deactivate(self, ids: list[UUID]) -> BulkActionResult:
        """Deactivate multiple records (set is_active=False)."""
        return await self.bulk_update_status(ids, "is_active", False)

    def _get_export_value(self, entity: T, field_name: str) -> str:
        """
        Get the value of a field for export, handling nested attributes.

        Args:
            entity: Entity instance
            field_name: Field name (can use dot notation for nested)

        Returns:
            String value for CSV export
        """
        value: Any = entity
        for part in field_name.split("."):
            if value is None:
                return ""
            value = getattr(value, part, None)

        if value is None:
            return ""
        if isinstance(value, (list, dict)):
            import json

            return json.dumps(value)
        return str(value)

    async def bulk_export(
        self,
        ids: list[UUID],
        format: str = "csv",
    ) -> Response:
        """
        Export selected records to CSV.

        Args:
            ids: List of entity IDs to export
            format: Export format (only 'csv' supported currently)

        Returns:
            StreamingResponse with CSV data
        """
        if not ids:
            raise HTTPException(status_code=400, detail="No IDs provided")

        entities = self._get_entities(ids)

        if not entities:
            raise HTTPException(
                status_code=404, detail="No entities found with provided IDs"
            )

        # Create CSV in memory
        output = io.StringIO()
        writer = csv.writer(output)

        # Write header
        headers = [header for _, header in self.export_fields]
        writer.writerow(headers)

        # Write data rows
        for entity in entities:
            row = [
                self._get_export_value(entity, field) for field, _ in self.export_fields
            ]
            writer.writerow(row)

        output.seek(0)

        # Return as a plain response (content is already in memory)
        return Response(
            content=output.getvalue(),
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="{self._get_export_filename()}"',
            },
        )

    def _get_export_filename(self) -> str:
        """Get the filename for export. Override in subclasses."""
        from datetime import datetime

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"export_{timestamp}.csv"
