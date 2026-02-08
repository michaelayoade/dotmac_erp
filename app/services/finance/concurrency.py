"""
Concurrency Control - Optimistic locking utilities.

Provides atomic status transitions with version checking to prevent
race conditions in concurrent updates.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, TypeVar, cast
from uuid import UUID

from sqlalchemy import inspect, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from app.services.common import coerce_uuid

logger = logging.getLogger(__name__)

T = TypeVar("T")


class ConcurrencyError(Exception):
    """Raised when a concurrent modification is detected."""

    pass


class StaleDataError(ConcurrencyError):
    """Raised when the entity version doesn't match expected version."""

    pass


class InvalidStatusTransitionError(ConcurrencyError):
    """Raised when attempting an invalid status transition."""

    pass


@dataclass
class TransitionResult:
    """Result of an atomic status transition."""

    success: bool
    new_version: int = 0
    entity: Any | None = None
    error: str | None = None


def atomic_status_transition(
    db: Session,
    model_class: type[T],
    entity_id: UUID,
    expected_version: int,
    from_status: Any,
    to_status: Any,
    organization_id: UUID | None = None,
    additional_updates: dict[str, Any] | None = None,
    id_column: str = None,
    status_column: str = "status",
    version_column: str = "version",
    org_column: str = "organization_id",
) -> TransitionResult:
    """
    Perform an atomic status transition with optimistic locking.

    Uses UPDATE ... WHERE to atomically check and update status,
    preventing race conditions.

    Args:
        db: Database session
        model_class: SQLAlchemy model class
        entity_id: Primary key of the entity
        expected_version: Version the client expects (for optimistic locking)
        from_status: Current status required for transition
        to_status: New status to set
        organization_id: Optional organization filter (for multi-tenancy)
        additional_updates: Additional columns to update
        id_column: Name of the ID column (auto-detected if None)
        status_column: Name of the status column
        version_column: Name of the version column
        org_column: Name of the organization_id column

    Returns:
        TransitionResult with success status and new version

    Raises:
        StaleDataError: If version doesn't match (concurrent modification)
        InvalidStatusTransitionError: If current status doesn't match from_status

    Example:
        result = atomic_status_transition(
            db=db,
            model_class=SupplierInvoice,
            entity_id=invoice_id,
            expected_version=invoice.version,
            from_status=SupplierInvoiceStatus.APPROVED,
            to_status=SupplierInvoiceStatus.POSTED,
            organization_id=org_id,
            additional_updates={
                "posted_by_user_id": user_id,
                "posted_at": datetime.now(timezone.utc),
            },
        )
        if not result.success:
            raise HTTPException(status_code=409, detail=result.error)
    """
    # Auto-detect ID column from model's primary key
    if id_column is None:
        mapper = inspect(model_class)
        if mapper is None:
            raise ValueError(f"Cannot inspect model {model_class}")
        pk_columns = [c.name for c in mapper.primary_key]
        if len(pk_columns) != 1:
            raise ValueError(f"Cannot auto-detect ID column for {model_class.__name__}")
        id_column = pk_columns[0]

    entity_id = coerce_uuid(entity_id)

    # Build update values
    update_values = {
        status_column: to_status,
        version_column: getattr(model_class, version_column) + 1,
    }

    if additional_updates:
        update_values.update(additional_updates)

    # Build WHERE conditions
    conditions = [
        getattr(model_class, id_column) == entity_id,
        getattr(model_class, version_column) == expected_version,
        getattr(model_class, status_column) == from_status,
    ]

    if organization_id:
        conditions.append(
            getattr(model_class, org_column) == coerce_uuid(organization_id)
        )

    # Execute atomic update
    stmt = (
        update(model_class)
        .where(*conditions)
        .values(**update_values)
        .returning(getattr(model_class, id_column))
    )

    result = cast(CursorResult[Any], db.execute(stmt))
    rows_affected = result.rowcount or 0

    if rows_affected == 0:
        # Update failed - determine why
        # Reload entity to check current state
        entity = db.get(model_class, entity_id)

        if entity is None:
            return TransitionResult(
                success=False,
                error=f"{model_class.__name__} not found",
            )

        current_version = getattr(entity, version_column)
        current_status = getattr(entity, status_column)

        if current_version != expected_version:
            logger.warning(
                "Stale data: %s %s version mismatch (expected=%d, current=%d)",
                model_class.__name__,
                entity_id,
                expected_version,
                current_version,
            )
            return TransitionResult(
                success=False,
                error=f"Concurrent modification detected. Expected version {expected_version}, but current version is {current_version}. Please refresh and try again.",
            )

        if current_status != from_status:
            logger.warning(
                "Invalid transition: %s %s status mismatch (expected=%s, current=%s)",
                model_class.__name__,
                entity_id,
                from_status,
                current_status,
            )
            return TransitionResult(
                success=False,
                error=f"Cannot transition from {current_status} to {to_status}. Expected current status to be {from_status}.",
            )

        # Some other condition failed
        return TransitionResult(
            success=False,
            error="Update failed for unknown reason",
        )

    # Commit the change
    db.commit()

    # Reload to get updated entity
    entity = db.get(model_class, entity_id)
    new_version = getattr(entity, version_column) if entity else expected_version + 1

    logger.debug(
        "Atomic transition: %s %s %s -> %s (version %d -> %d)",
        model_class.__name__,
        entity_id,
        from_status,
        to_status,
        expected_version,
        new_version,
    )

    return TransitionResult(
        success=True,
        new_version=new_version,
        entity=entity,
    )


def atomic_version_update(
    db: Session,
    model_class: type[T],
    entity_id: UUID,
    expected_version: int,
    updates: dict[str, Any],
    organization_id: UUID | None = None,
    id_column: str = None,
    version_column: str = "version",
    org_column: str = "organization_id",
) -> TransitionResult:
    """
    Perform an atomic update with version checking (no status transition).

    Args:
        db: Database session
        model_class: SQLAlchemy model class
        entity_id: Primary key of the entity
        expected_version: Version the client expects
        updates: Column updates to apply
        organization_id: Optional organization filter
        id_column: Name of the ID column (auto-detected if None)
        version_column: Name of the version column
        org_column: Name of the organization_id column

    Returns:
        TransitionResult with success status and new version
    """
    if id_column is None:
        mapper = inspect(model_class)
        if mapper is None:
            raise ValueError(f"Cannot inspect model {model_class}")
        pk_columns = [c.name for c in mapper.primary_key]
        if len(pk_columns) != 1:
            raise ValueError(f"Cannot auto-detect ID column for {model_class.__name__}")
        id_column = pk_columns[0]

    entity_id = coerce_uuid(entity_id)

    update_values = dict(updates)
    update_values[version_column] = getattr(model_class, version_column) + 1

    conditions = [
        getattr(model_class, id_column) == entity_id,
        getattr(model_class, version_column) == expected_version,
    ]

    if organization_id:
        conditions.append(
            getattr(model_class, org_column) == coerce_uuid(organization_id)
        )

    stmt = (
        update(model_class)
        .where(*conditions)
        .values(**update_values)
        .returning(getattr(model_class, id_column))
    )

    result = cast(CursorResult[Any], db.execute(stmt))
    rows_affected = result.rowcount or 0

    if rows_affected == 0:
        entity = db.get(model_class, entity_id)
        if entity is None:
            return TransitionResult(
                success=False,
                error=f"{model_class.__name__} not found",
            )

        current_version = getattr(entity, version_column)
        if current_version != expected_version:
            return TransitionResult(
                success=False,
                error=f"Concurrent modification detected. Expected version {expected_version}, but current version is {current_version}.",
            )

        return TransitionResult(
            success=False,
            error="Update failed for unknown reason",
        )

    db.commit()
    entity = db.get(model_class, entity_id)
    new_version = getattr(entity, version_column) if entity else expected_version + 1

    return TransitionResult(
        success=True,
        new_version=new_version,
        entity=entity,
    )


def check_version(
    db: Session,
    model_class: type[T],
    entity_id: UUID,
    expected_version: int,
    id_column: str = None,
    version_column: str = "version",
) -> bool:
    """
    Check if an entity's version matches the expected version.

    Args:
        db: Database session
        model_class: SQLAlchemy model class
        entity_id: Primary key of the entity
        expected_version: Version to check against
        id_column: Name of the ID column
        version_column: Name of the version column

    Returns:
        True if versions match, False otherwise
    """
    entity = db.get(model_class, coerce_uuid(entity_id))
    if entity is None:
        return False

    current_version = getattr(entity, version_column)
    return bool(current_version == expected_version)
