"""
Milestone Pydantic Schemas.

Schemas for PM Milestone API endpoints.
"""

from datetime import date, datetime
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
    description: str | None = None
    project_id: UUID
    target_date: date
    linked_task_id: UUID | None = None


class MilestoneCreate(MilestoneBase):
    """Create milestone request."""

    pass


class MilestoneUpdate(BaseModel):
    """Update milestone request."""

    milestone_code: str | None = Field(default=None, max_length=30)
    milestone_name: str | None = Field(default=None, max_length=200)
    description: str | None = None
    target_date: date | None = None
    linked_task_id: UUID | None = None


class MilestoneRead(MilestoneBase):
    """Milestone response."""

    model_config = ConfigDict(from_attributes=True)

    milestone_id: UUID
    organization_id: UUID
    status: MilestoneStatus
    actual_date: date | None = None
    created_at: datetime
    updated_at: datetime | None = None


class MilestoneWithDetails(MilestoneRead):
    """Milestone with related data."""

    model_config = ConfigDict(from_attributes=True)

    project_name: str | None = None
    linked_task_name: str | None = None
    is_overdue: bool = False
    days_until_target: int = 0


class MilestoneListResponse(BaseModel):
    """Paginated milestone list response."""

    items: list[MilestoneRead]
    total: int
    offset: int
    limit: int


# =============================================================================
# Milestone Action Schemas
# =============================================================================


class MilestoneAchieveRequest(BaseModel):
    """Request to mark milestone as achieved."""

    actual_date: date | None = None


class MilestoneAchieveResponse(BaseModel):
    """Response after achieving milestone."""

    milestone_id: UUID
    status: MilestoneStatus
    actual_date: date
