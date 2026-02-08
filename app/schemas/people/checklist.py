"""
Checklist template schemas.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.people.hr.checklist_template import ChecklistTemplateType


class ChecklistItemBase(BaseModel):
    """Base checklist item schema."""

    item_name: str = Field(max_length=500)
    is_required: bool = True
    sequence: int = 0


class ChecklistItemCreate(ChecklistItemBase):
    """Create checklist item request."""

    pass


class ChecklistItemRead(ChecklistItemBase):
    """Checklist item response."""

    model_config = ConfigDict(from_attributes=True)

    item_id: UUID
    template_id: UUID


class ChecklistTemplateBase(BaseModel):
    """Base checklist template schema."""

    template_code: str = Field(max_length=30)
    template_name: str = Field(max_length=200)
    description: str | None = None
    template_type: ChecklistTemplateType
    is_active: bool = True


class ChecklistTemplateCreate(ChecklistTemplateBase):
    """Create checklist template request."""

    items: list[ChecklistItemCreate] = []


class ChecklistTemplateUpdate(BaseModel):
    """Update checklist template request."""

    template_code: str | None = Field(default=None, max_length=30)
    template_name: str | None = Field(default=None, max_length=200)
    description: str | None = None
    template_type: ChecklistTemplateType | None = None
    is_active: bool | None = None
    items: list[ChecklistItemCreate] | None = None


class ChecklistTemplateRead(ChecklistTemplateBase):
    """Checklist template response."""

    model_config = ConfigDict(from_attributes=True)

    template_id: UUID
    organization_id: UUID
    created_at: datetime
    updated_at: datetime | None = None
    items: list[ChecklistItemRead] = []


class ChecklistTemplateListResponse(BaseModel):
    """Paginated checklist template list response."""

    items: list[ChecklistTemplateRead]
    total: int
    offset: int
    limit: int
