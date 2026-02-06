"""
Training Management Pydantic Schemas.

Pydantic schemas for Training APIs including:
- Training Program
- Training Event
- Training Attendee
"""

from datetime import date, datetime, time
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.people.training import (
    TrainingProgramStatus,
    TrainingEventStatus,
    AttendeeStatus,
)


# =============================================================================
# Training Program Schemas
# =============================================================================


class TrainingProgramBase(BaseModel):
    """Base training program schema."""

    program_code: str = Field(max_length=30)
    program_name: str = Field(max_length=200)
    description: Optional[str] = None
    training_type: str = "INTERNAL"
    category: Optional[str] = Field(default=None, max_length=50)
    duration_hours: Optional[int] = None
    duration_days: Optional[int] = None
    department_id: Optional[UUID] = None
    cost_per_attendee: Optional[Decimal] = None
    currency_code: str = "NGN"
    objectives: Optional[str] = None
    prerequisites: Optional[str] = None
    syllabus: Optional[str] = None
    provider_name: Optional[str] = Field(default=None, max_length=200)
    provider_contact: Optional[str] = Field(default=None, max_length=200)
    status: TrainingProgramStatus = TrainingProgramStatus.DRAFT


class TrainingProgramCreate(TrainingProgramBase):
    """Create training program request."""

    pass


class TrainingProgramUpdate(BaseModel):
    """Update training program request."""

    program_code: Optional[str] = Field(default=None, max_length=30)
    program_name: Optional[str] = Field(default=None, max_length=200)
    description: Optional[str] = None
    training_type: Optional[str] = None
    category: Optional[str] = Field(default=None, max_length=50)
    duration_hours: Optional[int] = None
    duration_days: Optional[int] = None
    department_id: Optional[UUID] = None
    cost_per_attendee: Optional[Decimal] = None
    currency_code: Optional[str] = None
    objectives: Optional[str] = None
    prerequisites: Optional[str] = None
    syllabus: Optional[str] = None
    provider_name: Optional[str] = Field(default=None, max_length=200)
    provider_contact: Optional[str] = Field(default=None, max_length=200)
    status: Optional[TrainingProgramStatus] = None


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
    updated_at: Optional[datetime] = None

    department: Optional[DepartmentBrief] = None


class TrainingProgramListResponse(BaseModel):
    """Paginated training program list response."""

    items: List[TrainingProgramRead]
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
    description: Optional[str] = None
    start_date: date
    end_date: date
    start_time: Optional[time] = None
    end_time: Optional[time] = None
    event_type: str = "IN_PERSON"
    location: Optional[str] = Field(default=None, max_length=200)
    meeting_link: Optional[str] = Field(default=None, max_length=500)
    trainer_name: Optional[str] = Field(default=None, max_length=200)
    trainer_email: Optional[str] = Field(default=None, max_length=255)
    trainer_employee_id: Optional[UUID] = None
    max_attendees: Optional[int] = None
    total_cost: Optional[Decimal] = None
    currency_code: str = "NGN"


class TrainingEventCreate(TrainingEventBase):
    """Create training event request."""

    pass


class TrainingEventUpdate(BaseModel):
    """Update training event request."""

    event_name: Optional[str] = Field(default=None, max_length=200)
    description: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    start_time: Optional[time] = None
    end_time: Optional[time] = None
    event_type: Optional[str] = None
    location: Optional[str] = Field(default=None, max_length=200)
    meeting_link: Optional[str] = Field(default=None, max_length=500)
    trainer_name: Optional[str] = Field(default=None, max_length=200)
    trainer_email: Optional[str] = Field(default=None, max_length=255)
    trainer_employee_id: Optional[UUID] = None
    max_attendees: Optional[int] = None
    total_cost: Optional[Decimal] = None
    currency_code: Optional[str] = None
    status: Optional[TrainingEventStatus] = None


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
    average_rating: Optional[Decimal] = None
    feedback_notes: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    program: Optional[TrainingProgramBrief] = None
    trainer: Optional[EmployeeBrief] = None


class TrainingEventListResponse(BaseModel):
    """Paginated training event list response."""

    items: List[TrainingEventRead]
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

    reason: Optional[str] = None


class CompleteEventRequest(BaseModel):
    """Mark event as completed."""

    feedback_notes: Optional[str] = None


# =============================================================================
# Training Attendee Schemas
# =============================================================================


class TrainingAttendeeBase(BaseModel):
    """Base training attendee schema."""

    event_id: UUID
    employee_id: UUID
    notes: Optional[str] = None


class TrainingAttendeeCreate(TrainingAttendeeBase):
    """Create training attendee request."""

    pass


class TrainingAttendeeUpdate(BaseModel):
    """Update training attendee request."""

    status: Optional[AttendeeStatus] = None
    notes: Optional[str] = None


class TrainingAttendeeRead(BaseModel):
    """Training attendee response."""

    model_config = ConfigDict(from_attributes=True)

    attendee_id: UUID
    organization_id: UUID
    event_id: UUID
    employee_id: UUID
    status: AttendeeStatus
    invited_on: Optional[date] = None
    confirmed_on: Optional[date] = None
    attended_on: Optional[date] = None
    rating: Optional[int] = None
    feedback: Optional[str] = None
    certificate_issued: bool
    certificate_number: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    event: Optional[TrainingEventBrief] = None
    employee: Optional[EmployeeBrief] = None


class TrainingAttendeeListResponse(BaseModel):
    """Paginated training attendee list response."""

    items: List[TrainingAttendeeRead]
    total: int
    offset: int
    limit: int


class BulkInviteRequest(BaseModel):
    """Bulk invite employees to training event."""

    employee_ids: List[UUID]
    registration_type: Optional[str] = "voluntary"


class BulkInviteResponse(BaseModel):
    """Bulk invite response."""

    success_count: int
    failed_count: int
    errors: List[str] = []


class AttendeeConfirmRequest(BaseModel):
    """Confirm attendance."""

    pass


class AttendeeMarkAttendedRequest(BaseModel):
    """Mark attendee as attended."""

    pass


class AttendeeFeedbackRequest(BaseModel):
    """Submit attendee feedback."""

    rating: int = Field(ge=1, le=5)
    feedback: Optional[str] = None


class IssueCertificateRequest(BaseModel):
    """Issue certificate to attendee."""

    certificate_number: str = Field(max_length=50)
    certificate_url: Optional[str] = None


class TrainingStats(BaseModel):
    """Training statistics."""

    total_programs: int
    active_programs: int
    upcoming_events: int
    completed_events: int
    total_attendees: int
    average_rating: Optional[Decimal] = None


class EmployeeTrainingHistory(BaseModel):
    """Employee training history summary."""

    employee_id: UUID
    total_trainings_attended: int
    total_hours: int
    trainings: List[TrainingAttendeeRead]
