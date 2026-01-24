"""
Support Module Schemas.

Pydantic schemas for support ticket entities.
"""

from datetime import date, datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class TicketBase(BaseModel):
    """Base schema for ticket data."""

    subject: str = Field(..., max_length=255)
    description: Optional[str] = None
    status: str = Field(default="OPEN")
    priority: str = Field(default="MEDIUM")
    raised_by_email: Optional[str] = Field(None, max_length=255)
    opening_date: date
    resolution_date: Optional[date] = None
    resolution: Optional[str] = None


class TicketCreate(TicketBase):
    """Schema for creating a ticket."""

    ticket_number: str = Field(..., max_length=50)
    organization_id: UUID
    raised_by_id: Optional[UUID] = None
    assigned_to_id: Optional[UUID] = None
    project_id: Optional[UUID] = None


class TicketUpdate(BaseModel):
    """Schema for updating a ticket.

    Note: Status changes should use the /status endpoint.
    Resolution should use the /resolve endpoint.
    Assignment should use the /assign endpoint.
    """

    subject: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = None
    priority: Optional[str] = None
    raised_by_email: Optional[str] = Field(None, max_length=255)
    project_id: Optional[UUID] = None
    category_id: Optional[UUID] = None
    team_id: Optional[UUID] = None


class TicketRead(TicketBase):
    """Schema for reading a ticket."""

    model_config = ConfigDict(from_attributes=True)

    ticket_id: UUID
    organization_id: UUID
    ticket_number: str
    raised_by_id: Optional[UUID] = None
    assigned_to_id: Optional[UUID] = None
    project_id: Optional[UUID] = None
    erpnext_id: Optional[str] = None
    last_synced_at: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime] = None


class TicketSummary(BaseModel):
    """Summary schema for ticket list views."""

    model_config = ConfigDict(from_attributes=True)

    ticket_id: UUID
    ticket_number: str
    subject: str
    status: str
    priority: str
    opening_date: date
    resolution_date: Optional[date] = None


class TicketListResponse(BaseModel):
    """Paginated ticket list response."""

    items: list[TicketSummary]
    total: int
    page: int
    per_page: int


class TicketFilters(BaseModel):
    """Filters for ticket list queries."""

    status: Optional[str] = None
    priority: Optional[str] = None
    assigned_to_id: Optional[UUID] = None
    search: Optional[str] = None
    date_from: Optional[date] = None
    date_to: Optional[date] = None


class TicketDetail(TicketRead):
    """Extended ticket schema with relationships for detail view."""

    assigned_to_name: Optional[str] = None
    raised_by_name: Optional[str] = None
    project_name: Optional[str] = None
    project_code: Optional[str] = None
    linked_expense_count: int = 0


class TicketStats(BaseModel):
    """Ticket statistics for dashboard."""

    total: int = 0
    open: int = 0
    on_hold: int = 0
    resolved: int = 0
    closed: int = 0
    urgent: int = 0
    unassigned: int = 0
    active: int = 0


class TicketAssign(BaseModel):
    """Schema for ticket assignment."""

    assigned_to_id: UUID


class TicketResolve(BaseModel):
    """Schema for resolving a ticket."""

    resolution: str = Field(..., min_length=1, max_length=5000)


class TicketStatusUpdate(BaseModel):
    """Schema for status update."""

    status: str = Field(..., pattern="^(OPEN|REPLIED|ON_HOLD|RESOLVED|CLOSED)$")
    notes: Optional[str] = None


class TicketSearchResult(BaseModel):
    """Minimal ticket info for typeahead/autocomplete."""

    model_config = ConfigDict(from_attributes=True)

    ticket_id: UUID
    ticket_number: str
    subject: str
    status: str
