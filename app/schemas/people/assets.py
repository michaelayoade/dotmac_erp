"""
Asset assignment schemas.
"""

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.people.assets.assignment import AssetCondition, AssignmentStatus


class AssetAssignmentBase(BaseModel):
    """Base asset assignment schema."""

    asset_id: UUID
    employee_id: UUID
    issued_on: date
    expected_return_date: date | None = None
    condition_on_issue: AssetCondition | None = None
    notes: str | None = None


class AssetAssignmentCreate(AssetAssignmentBase):
    """Create asset assignment request."""

    pass


class AssetAssignmentReturnRequest(BaseModel):
    """Return an asset assignment."""

    returned_on: date | None = None
    condition_on_return: AssetCondition | None = None
    notes: str | None = None


class AssetAssignmentTransferRequest(BaseModel):
    """Transfer an asset to another employee."""

    new_employee_id: UUID
    issued_on: date | None = None
    expected_return_date: date | None = None
    condition_on_issue: AssetCondition | None = None
    notes: str | None = None


class AssetAssignmentRead(AssetAssignmentBase):
    """Asset assignment response."""

    model_config = ConfigDict(from_attributes=True)

    assignment_id: UUID
    organization_id: UUID
    returned_on: date | None = None
    status: AssignmentStatus
    condition_on_return: AssetCondition | None = None
    transfer_from_assignment_id: UUID | None = None
    created_at: datetime
    updated_at: datetime | None = None


class AssetAssignmentListResponse(BaseModel):
    """Paginated asset assignment list response."""

    items: list[AssetAssignmentRead]
    total: int
    offset: int
    limit: int
