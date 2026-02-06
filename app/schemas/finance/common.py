"""
Common IFRS Schemas.

Shared Pydantic schemas for IFRS APIs.
"""

from datetime import datetime
from decimal import Decimal
from typing import Generic, Optional, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


T = TypeVar("T")


class ListResponse(BaseModel, Generic[T]):
    """Generic list response with pagination."""

    items: list[T]
    count: int
    limit: int
    offset: int


class AmountSchema(BaseModel):
    """Currency amount with code."""

    amount: Decimal = Field(decimal_places=2)
    currency_code: str = Field(max_length=3)


class PeriodSchema(BaseModel):
    """Fiscal period reference."""

    fiscal_period_id: UUID
    period_name: Optional[str] = None


class AuditInfoSchema(BaseModel):
    """Audit trail information."""

    model_config = ConfigDict(from_attributes=True)

    created_at: datetime
    updated_at: Optional[datetime] = None
    created_by_user_id: Optional[UUID] = None


class PostingResultSchema(BaseModel):
    """Result of GL posting operation."""

    success: bool
    journal_entry_id: Optional[UUID] = None
    entry_number: Optional[str] = None
    message: Optional[str] = None


class BatchResultSchema(BaseModel):
    """Result of batch operation."""

    total: int
    successful: int
    failed: int
    errors: list[str] = []


__all__ = [
    "ListResponse",
    "AmountSchema",
    "PeriodSchema",
    "AuditInfoSchema",
    "PostingResultSchema",
    "BatchResultSchema",
]
