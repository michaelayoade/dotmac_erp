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
from sqlalchemy import and_, or_, select
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
    search_fields: list[str] = []
    date_field: str = ""  # Column name for date range filtering (e.g. "invoice_date")

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
            SQLAlchemy select statement
        """
        id_col = getattr(self.model, self.id_field)
        org_col = getattr(self.model, self.org_field)

        coerced_ids = [coerce_uuid(id) for id in ids]

        return select(self.model).where(
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
        return cast(list[T], self.db.scalars(self._get_base_query(ids)).all())

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

    def _build_csv(self, entities: list[T]) -> Response:
        """
        Build a CSV response from a list of entities.

        Args:
            entities: List of entity instances

        Returns:
            Response with CSV content
        """
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

        return Response(
            content=output.getvalue(),
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="{self._get_export_filename()}"',
            },
        )

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
            Response with CSV data
        """
        if not ids:
            raise HTTPException(status_code=400, detail="No IDs provided")

        entities = self._get_entities(ids)

        if not entities:
            raise HTTPException(
                status_code=404, detail="No entities found with provided IDs"
            )

        return self._build_csv(entities)

    def _get_all_query(self, search: str = ""):
        """
        Get query for all entities in the org, optionally filtered by search.

        Uses ``search_fields`` class attribute to determine which columns
        to apply an ILIKE filter on.

        Args:
            search: Optional search term

        Returns:
            SQLAlchemy query
        """
        org_col = getattr(self.model, self.org_field)
        query = select(self.model).where(
            org_col == self.organization_id,
        )

        if search and self.search_fields:
            term = f"%{search}%"
            conditions = []
            for field_name in self.search_fields:
                col = getattr(self.model, field_name, None)
                if col is not None:
                    conditions.append(col.ilike(term))
            if conditions:
                query = query.where(or_(*conditions))

        return query

    async def export_all(
        self,
        search: str = "",
        status: str = "",
        start_date: str = "",
        end_date: str = "",
        extra_filters: dict[str, Any] | None = None,
        format: str = "csv",
    ) -> Response:
        """
        Export all records matching filters to CSV.

        Args:
            search: Optional search term
            status: Optional status filter
            start_date: Optional start date (ISO format YYYY-MM-DD)
            end_date: Optional end date (ISO format YYYY-MM-DD)
            extra_filters: Optional dict of {column_name: value} for
                entity-specific filters (e.g. customer_id, category)
            format: Export format (only 'csv' supported currently)

        Returns:
            Response with CSV data
        """
        from datetime import date as date_type

        query = self._get_all_query(search)

        if status:
            status_col = getattr(self.model, "status", None)
            if status_col is not None:
                query = query.where(status_col == status)

        # Date range filtering using the date_field class attribute
        if self.date_field and (start_date or end_date):
            date_col = getattr(self.model, self.date_field, None)
            if date_col is not None:
                if start_date:
                    try:
                        query = query.where(
                            date_col >= date_type.fromisoformat(start_date)
                        )
                    except ValueError:
                        logger.warning("Invalid start_date: %r", start_date)
                if end_date:
                    try:
                        query = query.where(
                            date_col <= date_type.fromisoformat(end_date)
                        )
                    except ValueError:
                        logger.warning("Invalid end_date: %r", end_date)

        # Entity-specific filters (customer_id, supplier_id, category, etc.)
        if extra_filters:
            for col_name, value in extra_filters.items():
                if value:
                    col = getattr(self.model, col_name, None)
                    if col is not None:
                        query = query.where(col == value)

        entities = cast(list[T], self.db.scalars(query).all())

        if not entities:
            logger.info(
                "Export all: no %s found for org %s (search=%r, status=%r)",
                self.model.__name__,
                self.organization_id,
                search,
                status,
            )
            return self._build_csv([])

        logger.info(
            "Exporting all %d %s for org %s (search=%r, status=%r)",
            len(entities),
            self.model.__name__,
            self.organization_id,
            search,
            status,
        )

        return self._build_csv(entities)

    def _get_export_filename(self) -> str:
        """Get the filename for export. Override in subclasses."""
        from datetime import datetime

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"export_{timestamp}.csv"
