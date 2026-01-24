"""
Asset assignment schemas.
"""
from datetime import date, datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.people.assets.assignment import AssignmentStatus, AssetCondition


class AssetAssignmentBase(BaseModel):
    """Base asset assignment schema."""

    asset_id: UUID
    employee_id: UUID
    issued_on: date
    expected_return_date: Optional[date] = None
    condition_on_issue: Optional[AssetCondition] = None
    notes: Optional[str] = None


class AssetAssignmentCreate(AssetAssignmentBase):
    """Create asset assignment request."""

    pass


class AssetAssignmentReturnRequest(BaseModel):
    """Return an asset assignment."""

    returned_on: Optional[date] = None
    condition_on_return: Optional[AssetCondition] = None
    notes: Optional[str] = None


class AssetAssignmentTransferRequest(BaseModel):
    """Transfer an asset to another employee."""

    new_employee_id: UUID
    issued_on: Optional[date] = None
    expected_return_date: Optional[date] = None
    condition_on_issue: Optional[AssetCondition] = None
    notes: Optional[str] = None


class AssetAssignmentRead(AssetAssignmentBase):
    """Asset assignment response."""

    model_config = ConfigDict(from_attributes=True)

    assignment_id: UUID
    organization_id: UUID
    returned_on: Optional[date] = None
    status: AssignmentStatus
    condition_on_return: Optional[AssetCondition] = None
    transfer_from_assignment_id: Optional[UUID] = None
    created_at: datetime
    updated_at: Optional[datetime] = None


class AssetAssignmentListResponse(BaseModel):
    """Paginated asset assignment list response."""

    items: List[AssetAssignmentRead]
    total: int
    offset: int
    limit: int
