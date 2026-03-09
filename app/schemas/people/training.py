"""
Training Management Pydantic Schemas.

Pydantic schemas for Training APIs including:
- Training Program
- Training Event
- Training Attendee
"""

from datetime import date, datetime, time
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.config import settings
from app.models.people.training import (
    AttendeeStatus,
    TrainingEventStatus,
    TrainingProgramStatus,
)

# =============================================================================
# Training Program Schemas
# =============================================================================


class TrainingProgramBase(BaseModel):
    """Base training program schema."""

    program_code: str = Field(max_length=30)
    program_name: str = Field(max_length=200)
    description: str | None = None
    training_type: str = "INTERNAL"
    category: str | None = Field(default=None, max_length=50)
    duration_hours: int | None = None
    duration_days: int | None = None
    department_id: UUID | None = None
    cost_per_attendee: Decimal | None = None
    currency_code: str = settings.default_functional_currency_code
    objectives: str | None = None
    prerequisites: str | None = None
    syllabus: str | None = None
    provider_name: str | None = Field(default=None, max_length=200)
    provider_contact: str | None = Field(default=None, max_length=200)
    status: TrainingProgramStatus = TrainingProgramStatus.DRAFT


class TrainingProgramCreate(TrainingProgramBase):
    """Create training program request."""

    pass


class TrainingProgramUpdate(BaseModel):
    """Update training program request."""

    program_code: str | None = Field(default=None, max_length=30)
    program_name: str | None = Field(default=None, max_length=200)
    description: str | None = None
    training_type: str | None = None
    category: str | None = Field(default=None, max_length=50)
    duration_hours: int | None = None
    duration_days: int | None = None
    department_id: UUID | None = None
    cost_per_attendee: Decimal | None = None
    currency_code: str | None = None
    objectives: str | None = None
    prerequisites: str | None = None
    syllabus: str | None = None
    provider_name: str | None = Field(default=None, max_length=200)
    provider_contact: str | None = Field(default=None, max_length=200)
    status: TrainingProgramStatus | None = None


class DepartmentBrief(BaseModel):
    """Brief department info."""

    model_config = ConfigDict(from_attributes=True)

    department_id: UUID
    department_code: str
    department_name: str


class TrainingProgramRead(TrainingProgramBase):
    """Training program response."""

    model_config = ConfigDict(from_attributes=True)

    program_id: UUID
    organization_id: UUID
    created_at: datetime
    updated_at: datetime | None = None

    department: DepartmentBrief | None = None


class TrainingProgramListResponse(BaseModel):
    """Paginated training program list response."""

    items: list[TrainingProgramRead]
    total: int
    offset: int
    limit: int


class TrainingProgramBrief(BaseModel):
    """Brief training program info."""

    model_config = ConfigDict(from_attributes=True)

    program_id: UUID
    program_code: str
    program_name: str
    training_type: str


# =============================================================================
# Training Event Schemas
# =============================================================================


class TrainingEventBase(BaseModel):
    """Base training event schema."""

    program_id: UUID
    event_name: str = Field(max_length=200)
    description: str | None = None
    start_date: date
    end_date: date
    start_time: time | None = None
    end_time: time | None = None
    event_type: str = "IN_PERSON"
    location: str | None = Field(default=None, max_length=200)
    meeting_link: str | None = Field(default=None, max_length=500)
    trainer_name: str | None = Field(default=None, max_length=200)
    trainer_email: str | None = Field(default=None, max_length=255)
    trainer_employee_id: UUID | None = None
    max_attendees: int | None = None
    total_cost: Decimal | None = None
    currency_code: str = settings.default_functional_currency_code


class TrainingEventCreate(TrainingEventBase):
    """Create training event request."""

    pass


class TrainingEventUpdate(BaseModel):
    """Update training event request."""

    event_name: str | None = Field(default=None, max_length=200)
    description: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    start_time: time | None = None
    end_time: time | None = None
    event_type: str | None = None
    location: str | None = Field(default=None, max_length=200)
    meeting_link: str | None = Field(default=None, max_length=500)
    trainer_name: str | None = Field(default=None, max_length=200)
    trainer_email: str | None = Field(default=None, max_length=255)
    trainer_employee_id: UUID | None = None
    max_attendees: int | None = None
    total_cost: Decimal | None = None
    currency_code: str | None = None
    status: TrainingEventStatus | None = None


class EmployeeBrief(BaseModel):
    """Brief employee info."""

    model_config = ConfigDict(from_attributes=True)

    employee_id: UUID
    employee_code: str


class TrainingEventRead(TrainingEventBase):
    """Training event response."""

    model_config = ConfigDict(from_attributes=True)

    event_id: UUID
    organization_id: UUID
    status: TrainingEventStatus
    average_rating: Decimal | None = None
    feedback_notes: str | None = None
    created_at: datetime
    updated_at: datetime | None = None

    program: TrainingProgramBrief | None = None
    trainer: EmployeeBrief | None = None


class TrainingEventListResponse(BaseModel):
    """Paginated training event list response."""

    items: list[TrainingEventRead]
    total: int
    offset: int
    limit: int


class TrainingEventBrief(BaseModel):
    """Brief training event info."""

    model_config = ConfigDict(from_attributes=True)

    event_id: UUID
    event_name: str
    start_date: date
    end_date: date
    status: TrainingEventStatus


class ScheduleEventRequest(BaseModel):
    """Schedule event request."""

    pass


class CancelEventRequest(BaseModel):
    """Cancel event request."""

    reason: str | None = None


class CompleteEventRequest(BaseModel):
    """Mark event as completed."""

    feedback_notes: str | None = None


# =============================================================================
# Training Attendee Schemas
# =============================================================================


class TrainingAttendeeBase(BaseModel):
    """Base training attendee schema."""

    event_id: UUID
    employee_id: UUID
    notes: str | None = None


class TrainingAttendeeCreate(TrainingAttendeeBase):
    """Create training attendee request."""

    pass


class TrainingAttendeeUpdate(BaseModel):
    """Update training attendee request."""

    status: AttendeeStatus | None = None
    notes: str | None = None


class TrainingAttendeeRead(BaseModel):
    """Training attendee response."""

    model_config = ConfigDict(from_attributes=True)

    attendee_id: UUID
    organization_id: UUID
    event_id: UUID
    employee_id: UUID
    status: AttendeeStatus
    invited_on: date | None = None
    confirmed_on: date | None = None
    attended_on: date | None = None
    rating: int | None = None
    feedback: str | None = None
    certificate_issued: bool
    certificate_number: str | None = None
    notes: str | None = None
    created_at: datetime
    updated_at: datetime | None = None

    event: TrainingEventBrief | None = None
    employee: EmployeeBrief | None = None


class TrainingAttendeeListResponse(BaseModel):
    """Paginated training attendee list response."""

    items: list[TrainingAttendeeRead]
    total: int
    offset: int
    limit: int


class BulkInviteRequest(BaseModel):
    """Bulk invite employees to training event."""

    employee_ids: list[UUID]
    registration_type: str | None = "voluntary"


class BulkInviteResponse(BaseModel):
    """Bulk invite response."""

    success_count: int
    failed_count: int
    errors: list[str] = []


class AttendeeConfirmRequest(BaseModel):
    """Confirm attendance."""

    pass


class AttendeeMarkAttendedRequest(BaseModel):
    """Mark attendee as attended."""

    pass


class AttendeeFeedbackRequest(BaseModel):
    """Submit attendee feedback."""

    rating: int = Field(ge=1, le=5)
    feedback: str | None = None


class IssueCertificateRequest(BaseModel):
    """Issue certificate to attendee."""

    certificate_number: str = Field(max_length=50)
    certificate_url: str | None = None


class TrainingStats(BaseModel):
    """Training statistics."""

    total_programs: int
    active_programs: int
    upcoming_events: int
    completed_events: int
    total_attendees: int
    average_rating: Decimal | None = None


class EmployeeTrainingHistory(BaseModel):
    """Employee training history summary."""

    employee_id: UUID
    total_trainings_attended: int
    total_hours: int
    trainings: list[TrainingAttendeeRead]
