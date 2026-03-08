"""
Common helper functions for IFRS services.

Provides reusable functions for entity validation, retrieval, and status management.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar, cast
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, inspect, select
from sqlalchemy.orm import Session

from app.services.common import coerce_uuid

# Type variable for model classes
T = TypeVar("T")


def get_model_pk_column(model_class: type[T]) -> str:
    """
    Get the primary key column name for a model.

    Args:
        model_class: SQLAlchemy model class

    Returns:
        Primary key column name
    """
    mapper = inspect(model_class)
    if mapper is None:
        raise ValueError(f"Unable to inspect model {model_class.__name__}")
    pk_cols = mapper.primary_key
    if pk_cols:
        return cast(str, pk_cols[0].name)
    # Fallback to common naming convention
    name = model_class.__name__
    return f"{name[0].lower()}{name[1:]}_id"


def get_entity_display_name(model_class: type[T]) -> str:
    """
    Get a human-readable display name from a model class.

    Args:
        model_class: SQLAlchemy model class

    Returns:
        Display name (e.g., "Supplier", "Customer Invoice")
    """
    name = model_class.__name__
    # Convert CamelCase to spaces
    result: list[str] = []
    for char in name:
        if char.isupper() and result:
            result.append(" ")
        result.append(char)
    return "".join(result)


def validate_unique_code(
    db: Session,
    model_class: type[T],
    org_id: UUID | str,
    code_value: str,
    code_field_name: str = "code",
    entity_name: str | None = None,
    exclude_id: UUID | str | None = None,
    parent_id: UUID | str | None = None,
    parent_field_name: str | None = None,
) -> None:
    """
    Validate that a code is unique within an organization (and optionally within a parent entity).

    Args:
        db: Database session
        model_class: SQLAlchemy model class
        org_id: Organization ID to scope the check
        code_value: The code value to check for uniqueness
        code_field_name: Name of the code field on the model (e.g., "supplier_code")
        entity_name: Human-readable entity name for error messages (auto-derived if not provided)
        exclude_id: Entity ID to exclude from the check (for updates)
        parent_id: Optional parent entity ID for hierarchical uniqueness
        parent_field_name: Field name for parent relationship (e.g., "warehouse_id")

    Raises:
        HTTPException(400): If the code already exists
    """
    org_id = coerce_uuid(org_id)

    # Build base filter
    code_column = getattr(model_class, code_field_name, None)
    if code_column is None:
        raise ValueError(
            f"Model {model_class.__name__} has no field '{code_field_name}'"
        )

    org_column = getattr(model_class, "organization_id", None)
    if org_column is None:
        raise ValueError(f"Model {model_class.__name__} has no 'organization_id' field")

    filters = [
        org_column == org_id,
        code_column == code_value,
    ]

    # Add parent scope if provided
    if parent_id is not None and parent_field_name:
        parent_column = getattr(model_class, parent_field_name, None)
        if parent_column is not None:
            filters.append(parent_column == coerce_uuid(parent_id))

    # Exclude current entity for updates
    if exclude_id is not None:
        pk_column_name = get_model_pk_column(model_class)
        pk_column = getattr(model_class, pk_column_name, None)
        if pk_column is not None:
            filters.append(pk_column != coerce_uuid(exclude_id))

    try:
        existing = db.query(model_class).filter(and_(*filters)).first()
    except Exception:
        existing = db.scalar(select(model_class).where(and_(*filters)))

    if existing:
        display_name = entity_name or get_entity_display_name(model_class)
        raise HTTPException(
            status_code=400,
            detail=f"{display_name} code '{code_value}' already exists",
        )


def get_org_scoped_entity(
    db: Session,
    model_class: type[T],
    entity_id: UUID | str,
    org_id: UUID | str,
    entity_name: str | None = None,
    raise_on_missing: bool = True,
) -> T | None:
    """
    Retrieve an entity by ID and validate it belongs to the specified organization.

    Args:
        db: Database session
        model_class: SQLAlchemy model class
        entity_id: Entity primary key
        org_id: Organization ID to validate against
        entity_name: Human-readable entity name for error messages
        raise_on_missing: If True, raises 404 when entity not found; otherwise returns None

    Returns:
        The entity if found and belongs to the organization, None if not found and raise_on_missing=False

    Raises:
        HTTPException(404): If entity not found and raise_on_missing=True
    """
    entity_id = coerce_uuid(entity_id)
    org_id = coerce_uuid(org_id)

    entity = db.get(model_class, entity_id)

    # Check if found and belongs to org
    if entity is None or getattr(entity, "organization_id", None) != org_id:
        if raise_on_missing:
            display_name = entity_name or get_entity_display_name(model_class)
            raise HTTPException(status_code=404, detail=f"{display_name} not found")
        return None

    return entity


def get_org_scoped_entity_by_field(
    db: Session,
    model_class: type[T],
    org_id: UUID | str,
    field_name: str,
    field_value: Any,
    entity_name: str | None = None,
    raise_on_missing: bool = True,
) -> T | None:
    """
    Retrieve an entity by a specific field value and validate organization scope.

    Args:
        db: Database session
        model_class: SQLAlchemy model class
        org_id: Organization ID to scope the query
        field_name: Name of the field to search by
        field_value: Value to match
        entity_name: Human-readable entity name for error messages
        raise_on_missing: If True, raises 404 when entity not found

    Returns:
        The entity if found, None if not found and raise_on_missing=False

    Raises:
        HTTPException(404): If entity not found and raise_on_missing=True
    """
    org_id = coerce_uuid(org_id)

    field_column = getattr(model_class, field_name, None)
    if field_column is None:
        raise ValueError(f"Model {model_class.__name__} has no field '{field_name}'")

    org_column = getattr(model_class, "organization_id", None)
    if org_column is None:
        raise ValueError(f"Model {model_class.__name__} has no 'organization_id' field")

    try:
        entity = (
            db.query(model_class)
            .filter(and_(org_column == org_id, field_column == field_value))
            .first()
        )
    except Exception:
        entity = db.scalar(
            select(model_class).where(
                and_(org_column == org_id, field_column == field_value)
            )
        )

    if entity is None and raise_on_missing:
        display_name = entity_name or get_entity_display_name(model_class)
        raise HTTPException(status_code=404, detail=f"{display_name} not found")

    return entity


def toggle_entity_status(
    db: Session,
    model_class: type[T],
    entity_id: UUID | str,
    org_id: UUID | str,
    is_active: bool,
    entity_name: str | None = None,
    status_field: str = "is_active",
    pre_check: Callable[[Session, T], None] | None = None,
) -> T:
    """
    Toggle the active status of an entity.

    Args:
        db: Database session
        model_class: SQLAlchemy model class
        entity_id: Entity primary key
        org_id: Organization ID to validate against
        is_active: New active status
        entity_name: Human-readable entity name for error messages
        status_field: Name of the status field (default: "is_active")
        pre_check: Optional callback to validate before status change.
                   Should raise HTTPException if validation fails.

    Returns:
        The updated entity

    Raises:
        HTTPException(404): If entity not found
        HTTPException(400): If pre_check validation fails
    """
    entity = get_org_scoped_entity(
        db=db,
        model_class=model_class,
        entity_id=entity_id,
        org_id=org_id,
        entity_name=entity_name,
    )
    if entity is None:
        raise HTTPException(status_code=404, detail=f"{entity_name} not found")

    # Run pre-check if provided
    if pre_check is not None:
        pre_check(db, entity)

    # Update status
    setattr(entity, status_field, is_active)

    db.flush()
    db.refresh(entity)

    return entity


def activate_entity(
    db: Session,
    model_class: type[T],
    entity_id: UUID | str,
    org_id: UUID | str,
    entity_name: str | None = None,
    status_field: str = "is_active",
) -> T:
    """
    Activate an entity.

    Args:
        db: Database session
        model_class: SQLAlchemy model class
        entity_id: Entity primary key
        org_id: Organization ID to validate against
        entity_name: Human-readable entity name for error messages
        status_field: Name of the status field (default: "is_active")

    Returns:
        The activated entity
    """
    return toggle_entity_status(
        db=db,
        model_class=model_class,
        entity_id=entity_id,
        org_id=org_id,
        is_active=True,
        entity_name=entity_name,
        status_field=status_field,
    )


def deactivate_entity(
    db: Session,
    model_class: type[T],
    entity_id: UUID | str,
    org_id: UUID | str,
    entity_name: str | None = None,
    status_field: str = "is_active",
    pre_check: Callable[[Session, T], None] | None = None,
) -> T:
    """
    Deactivate an entity with optional pre-check validation.

    Args:
        db: Database session
        model_class: SQLAlchemy model class
        entity_id: Entity primary key
        org_id: Organization ID to validate against
        entity_name: Human-readable entity name for error messages
        status_field: Name of the status field (default: "is_active")
        pre_check: Optional callback to validate before deactivation.
                   Should raise HTTPException if validation fails.

    Returns:
        The deactivated entity
    """
    return toggle_entity_status(
        db=db,
        model_class=model_class,
        entity_id=entity_id,
        org_id=org_id,
        is_active=False,
        entity_name=entity_name,
        status_field=status_field,
        pre_check=pre_check,
    )
