"""
Milestone Pydantic Schemas.

Schemas for PM Milestone API endpoints.
"""

from datetime import date, datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.pm import MilestoneStatus


# =============================================================================
# Milestone Schemas
# =============================================================================


class MilestoneBase(BaseModel):
    """Base milestone schema."""

    milestone_code: str = Field(max_length=30)
    milestone_name: str = Field(max_length=200)
    description: Optional[str] = None
    project_id: UUID
    target_date: date
    linked_task_id: Optional[UUID] = None


class MilestoneCreate(MilestoneBase):
    """Create milestone request."""

    pass


class MilestoneUpdate(BaseModel):
    """Update milestone request."""

    milestone_code: Optional[str] = Field(default=None, max_length=30)
    milestone_name: Optional[str] = Field(default=None, max_length=200)
    description: Optional[str] = None
    target_date: Optional[date] = None
    linked_task_id: Optional[UUID] = None


class MilestoneRead(MilestoneBase):
    """Milestone response."""

    model_config = ConfigDict(from_attributes=True)

    milestone_id: UUID
    organization_id: UUID
    status: MilestoneStatus
    actual_date: Optional[date] = None
    created_at: datetime
    updated_at: Optional[datetime] = None


class MilestoneWithDetails(MilestoneRead):
    """Milestone with related data."""

    model_config = ConfigDict(from_attributes=True)

    project_name: Optional[str] = None
    linked_task_name: Optional[str] = None
    is_overdue: bool = False
    days_until_target: int = 0


class MilestoneListResponse(BaseModel):
    """Paginated milestone list response."""

    items: List[MilestoneRead]
    total: int
    offset: int
    limit: int


# =============================================================================
# Milestone Action Schemas
# =============================================================================


class MilestoneAchieveRequest(BaseModel):
    """Request to mark milestone as achieved."""

    actual_date: Optional[date] = None


class MilestoneAchieveResponse(BaseModel):
    """Response after achieving milestone."""

    milestone_id: UUID
    status: MilestoneStatus
    actual_date: date
