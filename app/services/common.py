"""Common helpers shared across all services.

This module provides:
- UUID coercion
- Query ordering and pagination
- Paginated result types for list endpoints
- Base error classes for service layer
"""

import uuid
from dataclasses import dataclass, field
from typing import Generic, List, Optional, TypeVar

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select


T = TypeVar("T")


# =============================================================================
# Base Error Classes
# =============================================================================


class ServiceError(Exception):
    """Base class for all service-level errors.

    These errors are raised by service methods and should be caught
    by route handlers to return appropriate HTTP responses.
    """

    def __init__(self, message: str = "Service error occurred") -> None:
        super().__init__(message)
        self.message = message


class NotFoundError(ServiceError):
    """Raised when a requested resource is not found."""

    def __init__(self, message: str = "Resource not found") -> None:
        super().__init__(message)


class ValidationError(ServiceError):
    """Raised when input validation fails."""

    def __init__(self, message: str = "Validation error") -> None:
        super().__init__(message)


class ConflictError(ServiceError):
    """Raised when an operation conflicts with current state."""

    def __init__(self, message: str = "Conflict error") -> None:
        super().__init__(message)


class ForbiddenError(ServiceError):
    """Raised when an operation is not permitted."""

    def __init__(self, message: str = "Operation not permitted") -> None:
        super().__init__(message)


class RateLimitError(ServiceError):
    """Raised when a rate limit is exceeded.

    Includes retry_after to indicate when the client can retry.
    """

    def __init__(
        self,
        message: str = "Rate limit exceeded",
        retry_after: int = 60,
    ) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class AuthenticationError(ServiceError):
    """Raised when authentication fails."""

    def __init__(self, message: str = "Authentication failed") -> None:
        super().__init__(message)


class AuthorizationError(ServiceError):
    """Raised when authorization fails (user authenticated but not authorized)."""

    def __init__(self, message: str = "Not authorized") -> None:
        super().__init__(message)


# =============================================================================
# UUID Helpers
# =============================================================================


def coerce_uuid(value, *, raise_http: bool = True):
    """Coerce a value to UUID, returning None if value is None."""
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        if raise_http:
            raise HTTPException(status_code=400, detail="Invalid UUID") from exc
        raise


# =============================================================================
# Query Helpers (Legacy - for SQLAlchemy Query objects)
# =============================================================================


def apply_ordering(query, order_by, order_dir, allowed_columns):
    """Apply ordering to a SQLAlchemy Query object."""
    if order_by not in allowed_columns:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid order_by. Allowed: {', '.join(sorted(allowed_columns))}",
        )
    column = allowed_columns[order_by]
    if order_dir == "desc":
        return query.order_by(column.desc())
    return query.order_by(column.asc())


def apply_pagination(query, limit, offset):
    """Apply pagination to a SQLAlchemy Query object."""
    return query.limit(limit).offset(offset)


# =============================================================================
# Pagination Types (for SQLAlchemy 2.0 Select statements)
# =============================================================================


@dataclass
class PaginationParams:
    """Pagination parameters for list queries.

    Attributes:
        offset: Number of records to skip (default 0).
        limit: Maximum number of records to return (default 50).
    """

    offset: int = 0
    limit: int = 50

    @classmethod
    def from_page(cls, page: int = 1, per_page: int = 50) -> "PaginationParams":
        """Create from page number (1-indexed) and page size."""
        return cls(offset=(max(1, page) - 1) * per_page, limit=per_page)


@dataclass
class PaginatedResult(Generic[T]):
    """Result of a paginated list query.

    Attributes:
        items: List of items for the current page.
        total: Total count of items matching the query.
        offset: Current offset.
        limit: Current limit.
    """

    items: List[T] = field(default_factory=list)
    total: int = 0
    offset: int = 0
    limit: int = 50

    @property
    def page(self) -> int:
        """Current page number (1-indexed)."""
        if self.limit == 0:
            return 1
        return (self.offset // self.limit) + 1

    @property
    def total_pages(self) -> int:
        """Total number of pages."""
        if self.limit == 0 or self.total == 0:
            return 1
        return (self.total + self.limit - 1) // self.limit

    @property
    def has_next(self) -> bool:
        """Whether there's a next page."""
        return self.offset + self.limit < self.total

    @property
    def has_prev(self) -> bool:
        """Whether there's a previous page."""
        return self.offset > 0


def paginate(
    db: Session,
    stmt: Select,
    params: Optional[PaginationParams] = None,
    *,
    count_column=None,
) -> PaginatedResult:
    """Execute a paginated query and return results with total count.

    Works with SQLAlchemy 2.0 select() statements.

    Args:
        db: SQLAlchemy database session.
        stmt: SQLAlchemy Select statement.
        params: Pagination parameters. If None, uses defaults.
        count_column: Optional column to count distinct values. Use this when
            the query includes joins that may duplicate rows. Pass the primary
            key column of the main entity (e.g., Employee.employee_id).

    Returns:
        PaginatedResult with items and pagination metadata.

    Example:
        # Simple query (no joins)
        stmt = select(Employee).where(Employee.org_id == org_id)
        result = paginate(db, stmt, PaginationParams(offset=0, limit=20))

        # Query with joins (use count_column to avoid inflated count)
        stmt = select(Employee).join(Department).where(...)
        result = paginate(db, stmt, params, count_column=Employee.employee_id)
    """
    if params is None:
        params = PaginationParams()

    # Get total count
    base_stmt = stmt.order_by(None).limit(None).offset(None)
    if count_column is not None:
        # Use COUNT(DISTINCT pk) for joined queries to avoid inflated counts
        count_base = (
            base_stmt.with_only_columns(
                count_column,
                maintain_column_froms=True,
            )
            .distinct()
            .subquery()
        )
        count_stmt = select(func.count()).select_from(count_base)
    else:
        # Simple count for non-joined queries
        count_stmt = select(func.count()).select_from(base_stmt.subquery())
    total = db.scalar(count_stmt) or 0

    # Apply pagination and fetch items
    paginated_stmt = stmt.offset(params.offset).limit(params.limit)
    items = list(db.scalars(paginated_stmt).all())

    return PaginatedResult(
        items=items,
        total=total,
        offset=params.offset,
        limit=params.limit,
    )
