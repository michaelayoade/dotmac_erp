"""
Training Management API Router.

Thin API wrapper for Training Management endpoints. All business logic is in services.
"""
from datetime import date, datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import require_organization_id, require_tenant_auth
from app.db import SessionLocal
from app.models.people.training import AttendeeStatus, TrainingEventStatus, TrainingProgramStatus
from app.schemas.people.training import (
    # Training Program
    TrainingProgramCreate,
    TrainingProgramUpdate,
    TrainingProgramRead,
    TrainingProgramListResponse,
    # Training Event
    TrainingEventCreate,
    TrainingEventUpdate,
    TrainingEventRead,
    TrainingEventListResponse,
    # Attendee
    TrainingAttendeeCreate,
    TrainingAttendeeRead,
    TrainingAttendeeListResponse,
    AttendeeFeedbackRequest,
    BulkInviteRequest,
    BulkInviteResponse,
    IssueCertificateRequest,
    TrainingStats,
    CompleteEventRequest,
)
from app.services.people.training import TrainingService
from app.services.common import PaginationParams

router = APIRouter(
    prefix="/training",
    tags=["training"],
    dependencies=[Depends(require_tenant_auth)],
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def parse_enum(value: Optional[str], enum_type, field_name: str):
    if value is None:
        return None
    try:
        return enum_type(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}: {value}") from exc


# =============================================================================
# Training Programs
# =============================================================================


@router.get("/programs", response_model=TrainingProgramListResponse)
def list_training_programs(
    organization_id: UUID = Depends(require_organization_id),
    search: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    is_active: Optional[bool] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List training programs."""
    svc = TrainingService(db)
    status_enum = parse_enum(status, TrainingProgramStatus, "status")
    result = svc.list_programs(
        org_id=organization_id,
        search=search,
        category=category,
        status=status_enum,
        is_active=is_active,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return TrainingProgramListResponse(
        items=[TrainingProgramRead.model_validate(p) for p in result.items],
        total=result.total,
        offset=offset,
        limit=limit,
    )


@router.post("/programs", response_model=TrainingProgramRead, status_code=status.HTTP_201_CREATED)
def create_training_program(
    payload: TrainingProgramCreate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Create a training program."""
    svc = TrainingService(db)
    program = svc.create_program(
        org_id=organization_id,
        program_code=payload.program_code,
        program_name=payload.program_name,
        description=payload.description,
        training_type=payload.training_type,
        category=payload.category,
        duration_hours=payload.duration_hours,
        duration_days=payload.duration_days,
        department_id=payload.department_id,
        cost_per_attendee=payload.cost_per_attendee,
        currency_code=payload.currency_code,
        objectives=payload.objectives,
        prerequisites=payload.prerequisites,
        syllabus=payload.syllabus,
        provider_name=payload.provider_name,
        provider_contact=payload.provider_contact,
        status=payload.status,
    )
    db.commit()
    return TrainingProgramRead.model_validate(program)


@router.get("/programs/{program_id}", response_model=TrainingProgramRead)
def get_training_program(
    program_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get a training program by ID."""
    svc = TrainingService(db)
    return TrainingProgramRead.model_validate(svc.get_program(organization_id, program_id))


@router.patch("/programs/{program_id}", response_model=TrainingProgramRead)
def update_training_program(
    program_id: UUID,
    payload: TrainingProgramUpdate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Update a training program."""
    svc = TrainingService(db)
    update_data = payload.model_dump(exclude_unset=True)
    program = svc.update_program(organization_id, program_id, **update_data)
    db.commit()
    return TrainingProgramRead.model_validate(program)


@router.delete("/programs/{program_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_training_program(
    program_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Delete a training program."""
    svc = TrainingService(db)
    svc.delete_program(organization_id, program_id)
    db.commit()


# =============================================================================
# Training Events
# =============================================================================


@router.get("/events", response_model=TrainingEventListResponse)
def list_training_events(
    organization_id: UUID = Depends(require_organization_id),
    program_id: Optional[UUID] = None,
    status: Optional[str] = None,
    trainer_id: Optional[UUID] = None,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List training events."""
    svc = TrainingService(db)
    status_enum = parse_enum(status, TrainingEventStatus, "status")
    result = svc.list_events(
        org_id=organization_id,
        program_id=program_id,
        status=status_enum,
        trainer_id=trainer_id,
        from_date=from_date,
        to_date=to_date,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return TrainingEventListResponse(
        items=[TrainingEventRead.model_validate(e) for e in result.items],
        total=result.total,
        offset=offset,
        limit=limit,
    )


@router.post("/events", response_model=TrainingEventRead, status_code=status.HTTP_201_CREATED)
def create_training_event(
    payload: TrainingEventCreate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Create a training event."""
    svc = TrainingService(db)
    event = svc.create_event(
        org_id=organization_id,
        program_id=payload.program_id,
        event_name=payload.event_name,
        description=payload.description,
        start_date=payload.start_date,
        end_date=payload.end_date,
        start_time=(
            datetime.combine(payload.start_date, payload.start_time)
            if payload.start_time
            else None
        ),
        end_time=(
            datetime.combine(payload.end_date, payload.end_time)
            if payload.end_time
            else None
        ),
        event_type=payload.event_type,
        location=payload.location,
        meeting_link=payload.meeting_link,
        trainer_name=payload.trainer_name,
        trainer_email=payload.trainer_email,
        trainer_employee_id=payload.trainer_employee_id,
        max_attendees=payload.max_attendees,
        total_cost=payload.total_cost,
        currency_code=payload.currency_code,
    )
    db.commit()
    return TrainingEventRead.model_validate(event)


@router.get("/events/{event_id}", response_model=TrainingEventRead)
def get_training_event(
    event_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get a training event by ID."""
    svc = TrainingService(db)
    return TrainingEventRead.model_validate(svc.get_event(organization_id, event_id))


@router.patch("/events/{event_id}", response_model=TrainingEventRead)
def update_training_event(
    event_id: UUID,
    payload: TrainingEventUpdate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Update a training event."""
    svc = TrainingService(db)
    update_data = payload.model_dump(exclude_unset=True)
    event = svc.update_event(organization_id, event_id, **update_data)
    db.commit()
    return TrainingEventRead.model_validate(event)


@router.delete("/events/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_training_event(
    event_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Delete a training event."""
    svc = TrainingService(db)
    svc.delete_event(organization_id, event_id)
    db.commit()


# Event workflow actions
@router.post("/events/{event_id}/start", response_model=TrainingEventRead)
def start_event(
    event_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Mark event as started (in progress)."""
    svc = TrainingService(db)
    event = svc.start_event(organization_id, event_id)
    db.commit()
    return TrainingEventRead.model_validate(event)


@router.post("/events/{event_id}/complete", response_model=TrainingEventRead)
def complete_event(
    event_id: UUID,
    payload: CompleteEventRequest,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Mark event as completed."""
    svc = TrainingService(db)
    event = svc.complete_event(organization_id, event_id, feedback_notes=payload.feedback_notes)
    db.commit()
    return TrainingEventRead.model_validate(event)


@router.post("/events/{event_id}/cancel", response_model=TrainingEventRead)
def cancel_event(
    event_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    reason: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Cancel a training event."""
    svc = TrainingService(db)
    event = svc.cancel_event(organization_id, event_id, reason=reason)
    db.commit()
    return TrainingEventRead.model_validate(event)


@router.get("/events/summary")
def training_event_summary(
    organization_id: UUID = Depends(require_organization_id),
    program_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
):
    """Get training event summary by status."""
    svc = TrainingService(db)
    return svc.get_event_summary(organization_id, program_id=program_id)


# =============================================================================
# Attendees
# =============================================================================


@router.get("/events/{event_id}/attendees", response_model=TrainingAttendeeListResponse)
def list_attendees(
    event_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    status: Optional[str] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List attendees for a training event."""
    svc = TrainingService(db)
    status_enum = parse_enum(status, AttendeeStatus, "status")
    result = svc.list_attendees(
        org_id=organization_id,
        event_id=event_id,
        status=status_enum,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return TrainingAttendeeListResponse(
        items=[TrainingAttendeeRead.model_validate(a) for a in result.items],
        total=result.total,
        offset=offset,
        limit=limit,
    )


@router.post(
    "/events/{event_id}/attendees",
    response_model=TrainingAttendeeRead,
    status_code=status.HTTP_201_CREATED,
)
def add_attendee(
    event_id: UUID,
    payload: TrainingAttendeeCreate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Add an attendee to a training event."""
    if payload.event_id != event_id:
        raise HTTPException(status_code=400, detail="Event ID mismatch")
    svc = TrainingService(db)
    attendee = svc.invite_attendee(organization_id, event_id, payload.employee_id, notes=payload.notes)
    db.commit()
    return TrainingAttendeeRead.model_validate(attendee)


@router.post("/events/{event_id}/bulk-invite", response_model=BulkInviteResponse)
def bulk_invite_attendees(
    event_id: UUID,
    payload: BulkInviteRequest,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Bulk invite employees to a training event."""
    svc = TrainingService(db)
    attendees = svc.bulk_invite(
        org_id=organization_id,
        event_id=event_id,
        employee_ids=payload.employee_ids,
    )
    db.commit()
    return BulkInviteResponse(
        success_count=len(attendees),
        failed_count=max(0, len(payload.employee_ids) - len(attendees)),
        errors=[],
    )


@router.delete(
    "/events/{event_id}/attendees/{attendee_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_attendee(
    event_id: UUID,
    attendee_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Remove an attendee from a training event."""
    svc = TrainingService(db)
    svc.remove_attendee(organization_id, event_id, attendee_id)
    db.commit()


# Attendee actions
@router.post("/events/{event_id}/attendees/{attendee_id}/confirm", response_model=TrainingAttendeeRead)
def confirm_attendance(
    event_id: UUID,
    attendee_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Confirm attendee attendance."""
    svc = TrainingService(db)
    existing = svc.get_attendee(organization_id, attendee_id)
    if existing.event_id != event_id:
        raise HTTPException(status_code=404, detail="Attendee not found for event")
    attendee = svc.confirm_attendance(organization_id, attendee_id)
    db.commit()
    return TrainingAttendeeRead.model_validate(attendee)


@router.post("/events/{event_id}/attendees/{attendee_id}/mark-attended", response_model=TrainingAttendeeRead)
def mark_attended(
    event_id: UUID,
    attendee_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    attendance_percentage: Optional[float] = None,
    db: Session = Depends(get_db),
):
    """Mark attendee as having attended."""
    svc = TrainingService(db)
    existing = svc.get_attendee(organization_id, attendee_id)
    if existing.event_id != event_id:
        raise HTTPException(status_code=404, detail="Attendee not found for event")
    attendee = svc.mark_attended(organization_id, attendee_id)
    db.commit()
    return TrainingAttendeeRead.model_validate(attendee)


@router.post("/events/{event_id}/attendees/{attendee_id}/feedback", response_model=TrainingAttendeeRead)
def submit_feedback(
    event_id: UUID,
    attendee_id: UUID,
    payload: AttendeeFeedbackRequest,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Submit attendee feedback."""
    svc = TrainingService(db)
    existing = svc.get_attendee(organization_id, attendee_id)
    if existing.event_id != event_id:
        raise HTTPException(status_code=404, detail="Attendee not found for event")
    attendee = svc.submit_feedback(
        org_id=organization_id,
        attendee_id=attendee_id,
        rating=payload.rating,
        feedback=payload.feedback,
    )
    db.commit()
    return TrainingAttendeeRead.model_validate(attendee)


@router.post("/events/{event_id}/attendees/{attendee_id}/certificate", response_model=TrainingAttendeeRead)
def issue_certificate(
    event_id: UUID,
    attendee_id: UUID,
    payload: IssueCertificateRequest,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Issue certificate to attendee."""
    svc = TrainingService(db)
    existing = svc.get_attendee(organization_id, attendee_id)
    if existing.event_id != event_id:
        raise HTTPException(status_code=404, detail="Attendee not found for event")
    attendee = svc.issue_certificate(
        org_id=organization_id,
        attendee_id=attendee_id,
        certificate_number=payload.certificate_number,
    )
    db.commit()
    return TrainingAttendeeRead.model_validate(attendee)


# =============================================================================
# Employee Training Records
# =============================================================================


@router.get("/employees/{employee_id}/history")
def get_employee_training_history(
    employee_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get training history for an employee."""
    svc = TrainingService(db)
    return svc.get_employee_training_history(
        org_id=organization_id,
        employee_id=employee_id,
    )


@router.get("/stats", response_model=TrainingStats)
def get_training_stats(
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get training dashboard statistics."""
    svc = TrainingService(db)
    return TrainingStats(**svc.get_training_stats(organization_id))
