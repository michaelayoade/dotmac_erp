"""
Schemas for bulk action operations.

These schemas define the request/response formats for bulk operations
like delete, export, and status updates across different modules.
"""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class BulkActionRequest(BaseModel):
    """Base request for bulk actions."""

    ids: list[UUID] = Field(..., min_length=1, description="List of entity IDs to act upon")
    action: str = Field(default="", description="Action type (for routing)")


class BulkDeleteRequest(BulkActionRequest):
    """Request for bulk delete operations."""

    pass


class BulkUpdateRequest(BulkActionRequest):
    """Request for bulk update operations with field updates."""

    updates: dict[str, Any] = Field(default_factory=dict, description="Fields to update")


class BulkStatusUpdateRequest(BulkActionRequest):
    """Request for bulk status update operations."""

    status: str = Field(..., description="New status value")


class BulkExportRequest(BulkActionRequest):
    """Request for bulk export operations."""

    format: str = Field(default="csv", description="Export format (csv, xlsx)")


class BulkActionResult(BaseModel):
    """Response from bulk action operations."""

    success_count: int = Field(default=0, description="Number of successful operations")
    failed_count: int = Field(default=0, description="Number of failed operations")
    errors: list[str] = Field(default_factory=list, description="Error messages for failures")
    message: str = Field(default="", description="Summary message")

    @classmethod
    def success(cls, count: int, message: str = "") -> "BulkActionResult":
        """Create a successful result."""
        return cls(
            success_count=count,
            failed_count=0,
            message=message or f"Successfully processed {count} items",
        )

    @classmethod
    def partial(cls, success: int, failed: int, errors: list[str]) -> "BulkActionResult":
        """Create a partial success result."""
        return cls(
            success_count=success,
            failed_count=failed,
            errors=errors,
            message=f"{success} succeeded, {failed} failed",
        )

    @classmethod
    def failure(cls, message: str) -> "BulkActionResult":
        """Create a failure result."""
        return cls(
            success_count=0,
            failed_count=0,
            errors=[message],
            message=message,
        )
