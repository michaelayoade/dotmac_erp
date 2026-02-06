"""
Lifecycle Management Pydantic Schemas.

Schemas for onboarding, separation, promotions, and transfers.
"""

from datetime import date, datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.people.hr.lifecycle import BoardingStatus, SeparationType


# =============================================================================
# Onboarding Schemas
# =============================================================================


class OnboardingActivityBase(BaseModel):
    """Base onboarding activity schema."""

    activity_name: str = Field(max_length=500)
    assignee_role: Optional[str] = Field(default=None, max_length=100)
    status: Optional[str] = Field(default=None, max_length=50)
    completed_on: Optional[date] = None
    sequence: int = 0


class OnboardingActivityCreate(OnboardingActivityBase):
    """Create onboarding activity request."""

    pass


class OnboardingActivityRead(OnboardingActivityBase):
    """Onboarding activity response."""

    model_config = ConfigDict(from_attributes=True)

    activity_id: UUID
    onboarding_id: UUID


class OnboardingBase(BaseModel):
    """Base onboarding schema."""

    employee_id: UUID
    job_applicant_id: Optional[UUID] = None
    job_offer_id: Optional[UUID] = None
    date_of_joining: Optional[date] = None
    department_id: Optional[UUID] = None
    designation_id: Optional[UUID] = None
    template_name: Optional[str] = None
    notes: Optional[str] = None


class OnboardingCreate(OnboardingBase):
    """Create onboarding request."""

    activities: List[OnboardingActivityCreate] = []


class OnboardingUpdate(BaseModel):
    """Update onboarding request."""

    date_of_joining: Optional[date] = None
    department_id: Optional[UUID] = None
    designation_id: Optional[UUID] = None
    template_name: Optional[str] = None
    notes: Optional[str] = None
    status: Optional[BoardingStatus] = None
    activities: Optional[List[OnboardingActivityCreate]] = None


class OnboardingRead(OnboardingBase):
    """Onboarding response."""

    model_config = ConfigDict(from_attributes=True)

    onboarding_id: UUID
    organization_id: UUID
    status: BoardingStatus
    created_at: datetime
    updated_at: Optional[datetime] = None
    activities: List[OnboardingActivityRead] = []


class OnboardingListResponse(BaseModel):
    """Paginated onboarding list response."""

    items: List[OnboardingRead]
    total: int
    offset: int
    limit: int


# =============================================================================
# Separation Schemas
# =============================================================================


class SeparationActivityBase(BaseModel):
    """Base separation activity schema."""

    activity_name: str = Field(max_length=500)
    assignee_role: Optional[str] = Field(default=None, max_length=100)
    status: Optional[str] = Field(default=None, max_length=50)
    completed_on: Optional[date] = None
    sequence: int = 0


class SeparationActivityCreate(SeparationActivityBase):
    """Create separation activity request."""

    pass


class SeparationActivityRead(SeparationActivityBase):
    """Separation activity response."""

    model_config = ConfigDict(from_attributes=True)

    activity_id: UUID
    separation_id: UUID


class SeparationBase(BaseModel):
    """Base separation schema."""

    employee_id: UUID
    separation_type: Optional[SeparationType] = None
    resignation_letter_date: Optional[date] = None
    separation_date: Optional[date] = None
    department_id: Optional[UUID] = None
    designation_id: Optional[UUID] = None
    reason_for_leaving: Optional[str] = None
    exit_interview: Optional[str] = None
    template_name: Optional[str] = None
    notes: Optional[str] = None


class SeparationCreate(SeparationBase):
    """Create separation request."""

    activities: List[SeparationActivityCreate] = []


class SeparationUpdate(BaseModel):
    """Update separation request."""

    separation_type: Optional[SeparationType] = None
    resignation_letter_date: Optional[date] = None
    separation_date: Optional[date] = None
    department_id: Optional[UUID] = None
    designation_id: Optional[UUID] = None
    reason_for_leaving: Optional[str] = None
    exit_interview: Optional[str] = None
    template_name: Optional[str] = None
    notes: Optional[str] = None
    status: Optional[BoardingStatus] = None
    activities: Optional[List[SeparationActivityCreate]] = None


class SeparationRead(SeparationBase):
    """Separation response."""

    model_config = ConfigDict(from_attributes=True)

    separation_id: UUID
    organization_id: UUID
    status: BoardingStatus
    created_at: datetime
    updated_at: Optional[datetime] = None
    activities: List[SeparationActivityRead] = []


class SeparationListResponse(BaseModel):
    """Paginated separation list response."""

    items: List[SeparationRead]
    total: int
    offset: int
    limit: int


# =============================================================================
# Promotion Schemas
# =============================================================================


class PromotionDetailBase(BaseModel):
    """Base promotion detail schema."""

    property_name: str = Field(max_length=100)
    current_value: Optional[str] = Field(default=None, max_length=255)
    new_value: Optional[str] = Field(default=None, max_length=255)
    sequence: int = 0


class PromotionDetailCreate(PromotionDetailBase):
    """Create promotion detail request."""

    pass


class PromotionDetailRead(PromotionDetailBase):
    """Promotion detail response."""

    model_config = ConfigDict(from_attributes=True)

    detail_id: UUID
    promotion_id: UUID


class PromotionBase(BaseModel):
    """Base promotion schema."""

    employee_id: UUID
    promotion_date: date
    notes: Optional[str] = None


class PromotionCreate(PromotionBase):
    """Create promotion request."""

    details: List[PromotionDetailCreate] = []


class PromotionUpdate(BaseModel):
    """Update promotion request."""

    promotion_date: Optional[date] = None
    notes: Optional[str] = None
    details: Optional[List[PromotionDetailCreate]] = None


class PromotionRead(PromotionBase):
    """Promotion response."""

    model_config = ConfigDict(from_attributes=True)

    promotion_id: UUID
    organization_id: UUID
    created_at: datetime
    updated_at: Optional[datetime] = None
    details: List[PromotionDetailRead] = []


class PromotionListResponse(BaseModel):
    """Paginated promotion list response."""

    items: List[PromotionRead]
    total: int
    offset: int
    limit: int


# =============================================================================
# Transfer Schemas
# =============================================================================


class TransferDetailBase(BaseModel):
    """Base transfer detail schema."""

    property_name: str = Field(max_length=100)
    current_value: Optional[str] = Field(default=None, max_length=255)
    new_value: Optional[str] = Field(default=None, max_length=255)
    sequence: int = 0


class TransferDetailCreate(TransferDetailBase):
    """Create transfer detail request."""

    pass


class TransferDetailRead(TransferDetailBase):
    """Transfer detail response."""

    model_config = ConfigDict(from_attributes=True)

    detail_id: UUID
    transfer_id: UUID


class TransferBase(BaseModel):
    """Base transfer schema."""

    employee_id: UUID
    transfer_date: date
    notes: Optional[str] = None


class TransferCreate(TransferBase):
    """Create transfer request."""

    details: List[TransferDetailCreate] = []


class TransferUpdate(BaseModel):
    """Update transfer request."""

    transfer_date: Optional[date] = None
    notes: Optional[str] = None
    details: Optional[List[TransferDetailCreate]] = None


class TransferRead(TransferBase):
    """Transfer response."""

    model_config = ConfigDict(from_attributes=True)

    transfer_id: UUID
    organization_id: UUID
    created_at: datetime
    updated_at: Optional[datetime] = None
    details: List[TransferDetailRead] = []


class TransferListResponse(BaseModel):
    """Paginated transfer list response."""

    items: List[TransferRead]
    total: int
    offset: int
    limit: int
