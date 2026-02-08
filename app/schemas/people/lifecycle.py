"""
Lifecycle Management Pydantic Schemas.

Schemas for onboarding, separation, promotions, and transfers.
"""

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.people.hr.lifecycle import BoardingStatus, SeparationType

# =============================================================================
# Onboarding Schemas
# =============================================================================


class OnboardingActivityBase(BaseModel):
    """Base onboarding activity schema."""

    activity_name: str = Field(max_length=500)
    assignee_role: str | None = Field(default=None, max_length=100)
    status: str | None = Field(default=None, max_length=50)
    completed_on: date | None = None
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
    job_applicant_id: UUID | None = None
    job_offer_id: UUID | None = None
    date_of_joining: date | None = None
    department_id: UUID | None = None
    designation_id: UUID | None = None
    template_name: str | None = None
    notes: str | None = None


class OnboardingCreate(OnboardingBase):
    """Create onboarding request."""

    activities: list[OnboardingActivityCreate] = []


class OnboardingUpdate(BaseModel):
    """Update onboarding request."""

    date_of_joining: date | None = None
    department_id: UUID | None = None
    designation_id: UUID | None = None
    template_name: str | None = None
    notes: str | None = None
    status: BoardingStatus | None = None
    activities: list[OnboardingActivityCreate] | None = None


class OnboardingRead(OnboardingBase):
    """Onboarding response."""

    model_config = ConfigDict(from_attributes=True)

    onboarding_id: UUID
    organization_id: UUID
    status: BoardingStatus
    created_at: datetime
    updated_at: datetime | None = None
    activities: list[OnboardingActivityRead] = []


class OnboardingListResponse(BaseModel):
    """Paginated onboarding list response."""

    items: list[OnboardingRead]
    total: int
    offset: int
    limit: int


# =============================================================================
# Separation Schemas
# =============================================================================


class SeparationActivityBase(BaseModel):
    """Base separation activity schema."""

    activity_name: str = Field(max_length=500)
    assignee_role: str | None = Field(default=None, max_length=100)
    status: str | None = Field(default=None, max_length=50)
    completed_on: date | None = None
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
    separation_type: SeparationType | None = None
    resignation_letter_date: date | None = None
    separation_date: date | None = None
    department_id: UUID | None = None
    designation_id: UUID | None = None
    reason_for_leaving: str | None = None
    exit_interview: str | None = None
    template_name: str | None = None
    notes: str | None = None


class SeparationCreate(SeparationBase):
    """Create separation request."""

    activities: list[SeparationActivityCreate] = []


class SeparationUpdate(BaseModel):
    """Update separation request."""

    separation_type: SeparationType | None = None
    resignation_letter_date: date | None = None
    separation_date: date | None = None
    department_id: UUID | None = None
    designation_id: UUID | None = None
    reason_for_leaving: str | None = None
    exit_interview: str | None = None
    template_name: str | None = None
    notes: str | None = None
    status: BoardingStatus | None = None
    activities: list[SeparationActivityCreate] | None = None


class SeparationRead(SeparationBase):
    """Separation response."""

    model_config = ConfigDict(from_attributes=True)

    separation_id: UUID
    organization_id: UUID
    status: BoardingStatus
    created_at: datetime
    updated_at: datetime | None = None
    activities: list[SeparationActivityRead] = []


class SeparationListResponse(BaseModel):
    """Paginated separation list response."""

    items: list[SeparationRead]
    total: int
    offset: int
    limit: int


# =============================================================================
# Promotion Schemas
# =============================================================================


class PromotionDetailBase(BaseModel):
    """Base promotion detail schema."""

    property_name: str = Field(max_length=100)
    current_value: str | None = Field(default=None, max_length=255)
    new_value: str | None = Field(default=None, max_length=255)
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
    notes: str | None = None


class PromotionCreate(PromotionBase):
    """Create promotion request."""

    details: list[PromotionDetailCreate] = []


class PromotionUpdate(BaseModel):
    """Update promotion request."""

    promotion_date: date | None = None
    notes: str | None = None
    details: list[PromotionDetailCreate] | None = None


class PromotionRead(PromotionBase):
    """Promotion response."""

    model_config = ConfigDict(from_attributes=True)

    promotion_id: UUID
    organization_id: UUID
    created_at: datetime
    updated_at: datetime | None = None
    details: list[PromotionDetailRead] = []


class PromotionListResponse(BaseModel):
    """Paginated promotion list response."""

    items: list[PromotionRead]
    total: int
    offset: int
    limit: int


# =============================================================================
# Transfer Schemas
# =============================================================================


class TransferDetailBase(BaseModel):
    """Base transfer detail schema."""

    property_name: str = Field(max_length=100)
    current_value: str | None = Field(default=None, max_length=255)
    new_value: str | None = Field(default=None, max_length=255)
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
    notes: str | None = None


class TransferCreate(TransferBase):
    """Create transfer request."""

    details: list[TransferDetailCreate] = []


class TransferUpdate(BaseModel):
    """Update transfer request."""

    transfer_date: date | None = None
    notes: str | None = None
    details: list[TransferDetailCreate] | None = None


class TransferRead(TransferBase):
    """Transfer response."""

    model_config = ConfigDict(from_attributes=True)

    transfer_id: UUID
    organization_id: UUID
    created_at: datetime
    updated_at: datetime | None = None
    details: list[TransferDetailRead] = []


class TransferListResponse(BaseModel):
    """Paginated transfer list response."""

    items: list[TransferRead]
    total: int
    offset: int
    limit: int
