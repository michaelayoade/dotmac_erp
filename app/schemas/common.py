from typing import Generic, TypeVar

from pydantic import BaseModel, Field, computed_field

T = TypeVar("T")


class ListResponse(BaseModel, Generic[T]):
    """Standard paginated list response.

    Attributes:
        items: List of items in this page
        total: Total number of items matching the query (preferred field name)
        count: Alias for total (deprecated, use 'total' instead)
        limit: Maximum items per page
        offset: Number of items skipped
        has_more: Whether there are more items beyond this page

    Note: Both 'total' and 'count' return the same value for backwards compatibility.
    New code should use 'total' as it's the more common REST API convention.
    """

    items: list[T]
    total: int = Field(description="Total number of items matching the query")
    limit: int = Field(description="Maximum items per page")
    offset: int = Field(description="Number of items skipped")

    @computed_field
    def count(self) -> int:
        """Deprecated: Use 'total' instead. Alias for backwards compatibility."""
        return self.total

    @computed_field
    def has_more(self) -> bool:
        """Whether there are more items beyond this page."""
        return self.offset + len(self.items) < self.total

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "items": [],
                    "total": 100,
                    "count": 100,
                    "limit": 50,
                    "offset": 0,
                    "has_more": True,
                }
            ]
        }
    }
