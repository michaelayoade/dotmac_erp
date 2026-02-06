"""
Central list helpers for pagination, search, and filtering.

Provides consistent patterns across all list views.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TypeVar
from urllib.parse import urlencode

from sqlalchemy.orm import Query

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass
class ListParams:
    """Parameters for list queries with pagination and search."""

    page: int = 1
    limit: int = 50
    search: Optional[str] = None
    sort_by: Optional[str] = None
    sort_dir: str = "asc"
    filters: Dict[str, Any] = field(default_factory=dict)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.limit

    @classmethod
    def from_request(
        cls,
        page: int = 1,
        limit: int = 50,
        search: Optional[str] = None,
        sort: Optional[str] = None,
        **filters: Any,
    ) -> "ListParams":
        """Create ListParams from request query parameters."""
        # Parse sort parameter (e.g., "name" or "-name" for descending)
        sort_by = None
        sort_dir = "asc"
        if sort:
            if sort.startswith("-"):
                sort_by = sort[1:]
                sort_dir = "desc"
            else:
                sort_by = sort

        # Clean up filters - remove None and empty string values
        clean_filters = {k: v for k, v in filters.items() if v not in (None, "")}

        return cls(
            page=max(1, page),
            limit=min(max(10, limit), 500),  # Clamp between 10 and 500
            search=search.strip() if search else None,
            sort_by=sort_by,
            sort_dir=sort_dir,
            filters=clean_filters,
        )


@dataclass
class ListResult:
    """Result of a paginated list query."""

    items: List[Any]
    total_count: int
    page: int
    limit: int
    search: Optional[str] = None
    filters: Dict[str, Any] = field(default_factory=dict)

    @property
    def total_pages(self) -> int:
        if self.total_count == 0:
            return 1
        return (self.total_count + self.limit - 1) // self.limit

    @property
    def has_prev(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.total_pages

    @property
    def start_index(self) -> int:
        """1-based start index for display."""
        if self.total_count == 0:
            return 0
        return (self.page - 1) * self.limit + 1

    @property
    def end_index(self) -> int:
        """1-based end index for display."""
        return min(self.page * self.limit, self.total_count)

    def pagination_context(self, base_url: str = "") -> Dict[str, Any]:
        """Generate context for pagination template component."""
        return {
            "page": self.page,
            "limit": self.limit,
            "total_count": self.total_count,
            "total_pages": self.total_pages,
            "has_prev": self.has_prev,
            "has_next": self.has_next,
            "start_index": self.start_index,
            "end_index": self.end_index,
            "search": self.search,
            "filters": self.filters,
            "base_url": base_url,
            "query_string": self._build_query_string(),
        }

    def _build_query_string(self) -> str:
        """Build query string preserving search and filters."""
        params = {}
        if self.search:
            params["search"] = self.search
        params.update(self.filters)
        return urlencode(params) if params else ""


def paginate_query(query: Query, params: ListParams) -> ListResult:
    """
    Apply pagination to a SQLAlchemy query and return results.

    Args:
        query: SQLAlchemy query to paginate
        params: Pagination and filter parameters

    Returns:
        ListResult with items and pagination metadata
    """
    # Get total count before pagination
    total_count = query.count()

    # Apply pagination
    items = query.offset(params.offset).limit(params.limit).all()

    return ListResult(
        items=items,
        total_count=total_count,
        page=params.page,
        limit=params.limit,
        search=params.search,
        filters=params.filters,
    )


# Common page size options for UI
PAGE_SIZE_OPTIONS = [25, 50, 100, 200]
