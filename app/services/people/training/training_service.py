"""Training management service implementation.

Handles training programs, events, and attendees.
Adapted from DotMac People for the unified ERP platform.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, TypedDict
from uuid import UUID

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.models.people.training import (
    AttendeeStatus,
    TrainingAttendee,
    TrainingEvent,
    TrainingEventStatus,
    TrainingProgram,
    TrainingProgramStatus,
)
from app.services.common import PaginatedResult, PaginationParams

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.web.deps import WebAuthContext

__all__ = ["TrainingService"]


class TrainingServiceError(Exception):
    """Base error for training service."""

    pass


class TrainingProgramNotFoundError(TrainingServiceError):
    """Training program not found."""

    def __init__(self, program_id: UUID):
        self.program_id = program_id
        super().__init__(f"Training program {program_id} not found")


class TrainingEventNotFoundError(TrainingServiceError):
    """Training event not found."""

    def __init__(self, event_id: UUID):
        self.event_id = event_id
        super().__init__(f"Training event {event_id} not found")


class ProgramRatingData(TypedDict):
    program_id: UUID
    program_code: str
    program_name: str
    ratings: list[float]


class ProgramSummary(TypedDict):
    program_id: UUID
    program_code: str
    program_name: str
    response_count: int
    average_rating: float


class EventRatingData(TypedDict):
    event_id: UUID
    event_name: str
    program_name: str
    end_date: date
    response_count: int
    average_rating: float


class TrainingAttendeeNotFoundError(TrainingServiceError):
    """Training attendee not found."""

    def __init__(self, attendee_id: UUID):
        self.attendee_id = attendee_id
        super().__init__(f"Training attendee {attendee_id} not found")


class TrainingEventStatusError(TrainingServiceError):
    """Invalid training event status transition."""

    def __init__(self, current: str, target: str):
        self.current = current
        self.target = target
        super().__init__(f"Cannot transition from {current} to {target}")


# Valid status transitions for training events
EVENT_STATUS_TRANSITIONS = {
    TrainingEventStatus.SCHEDULED: {
        TrainingEventStatus.IN_PROGRESS,
        TrainingEventStatus.CANCELLED,
    },
    TrainingEventStatus.IN_PROGRESS: {
        TrainingEventStatus.COMPLETED,
        TrainingEventStatus.CANCELLED,
    },
    TrainingEventStatus.COMPLETED: set(),  # Terminal state
    TrainingEventStatus.CANCELLED: set(),  # Terminal state
}


class TrainingService:
    """Service for training management operations.

    Handles:
    - Training program configuration
    - Training event scheduling
    - Attendee registration and tracking
    - Feedback and certification
    """

    def __init__(
        self,
        db: Session,
        ctx: WebAuthContext | None = None,
    ) -> None:
        self.db = db
        self.ctx = ctx

    # =========================================================================
    # Training Programs
    # =========================================================================

    def list_programs(
        self,
        org_id: UUID,
        *,
        status: TrainingProgramStatus | None = None,
        category: str | None = None,
        is_active: bool | None = None,
        search: str | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[TrainingProgram]:
        """List training programs."""
        query = select(TrainingProgram).where(TrainingProgram.organization_id == org_id)

        status_value = status
        if status_value is None and is_active is not None:
            status_value = (
                TrainingProgramStatus.ACTIVE
                if is_active
                else TrainingProgramStatus.ARCHIVED
            )

        if status_value:
            query = query.where(TrainingProgram.status == status_value)

        if category:
            query = query.where(TrainingProgram.category == category)

        if search:
            search_term = f"%{search}%"
            query = query.where(
                or_(
                    TrainingProgram.program_code.ilike(search_term),
                    TrainingProgram.program_name.ilike(search_term),
                )
            )

        query = query.order_by(TrainingProgram.program_name)

        # Count total
        count_query = select(func.count()).select_from(query.subquery())
        total = self.db.scalar(count_query) or 0

        # Apply pagination
        if pagination:
            query = query.offset(pagination.offset).limit(pagination.limit)

        items = list(self.db.scalars(query).all())

        return PaginatedResult(
            items=items,
            total=total,
            offset=pagination.offset if pagination else 0,
            limit=pagination.limit if pagination else len(items),
        )

    def get_program(self, org_id: UUID, program_id: UUID) -> TrainingProgram:
        """Get a training program by ID."""
        program = self.db.scalar(
            select(TrainingProgram).where(
                TrainingProgram.program_id == program_id,
                TrainingProgram.organization_id == org_id,
            )
        )
        if not program:
            raise TrainingProgramNotFoundError(program_id)
        return program

    def create_program(
        self,
        org_id: UUID,
        *,
        program_code: str,
        program_name: str,
        training_type: str = "INTERNAL",
        category: str | None = None,
        duration_hours: int | None = None,
        duration_days: int | None = None,
        department_id: UUID | None = None,
        cost_per_attendee: Decimal | None = None,
        currency_code: str = "NGN",
        objectives: str | None = None,
        prerequisites: str | None = None,
        syllabus: str | None = None,
        provider_name: str | None = None,
        provider_contact: str | None = None,
        description: str | None = None,
        status: TrainingProgramStatus = TrainingProgramStatus.DRAFT,
    ) -> TrainingProgram:
        """Create a new training program."""
        program = TrainingProgram(
            organization_id=org_id,
            program_code=program_code,
            program_name=program_name,
            training_type=training_type,
            category=category,
            duration_hours=duration_hours,
            duration_days=duration_days,
            department_id=department_id,
            cost_per_attendee=cost_per_attendee,
            currency_code=currency_code,
            objectives=objectives,
            prerequisites=prerequisites,
            syllabus=syllabus,
            provider_name=provider_name,
            provider_contact=provider_contact,
            description=description,
            status=status,
        )

        self.db.add(program)
        self.db.flush()
        return program

    def update_program(
        self,
        org_id: UUID,
        program_id: UUID,
        **kwargs,
    ) -> TrainingProgram:
        """Update a training program."""
        program = self.get_program(org_id, program_id)

        for key, value in kwargs.items():
            if value is not None and hasattr(program, key):
                setattr(program, key, value)

        self.db.flush()
        return program

    def delete_program(self, org_id: UUID, program_id: UUID) -> None:
        """Delete a training program."""
        program = self.get_program(org_id, program_id)
        self.db.delete(program)
        self.db.flush()

    def activate_program(self, org_id: UUID, program_id: UUID) -> TrainingProgram:
        """Activate a training program."""
        program = self.get_program(org_id, program_id)
        program.status = TrainingProgramStatus.ACTIVE
        self.db.flush()
        return program

    def retire_program(self, org_id: UUID, program_id: UUID) -> TrainingProgram:
        """Retire a training program."""
        program = self.get_program(org_id, program_id)
        program.status = TrainingProgramStatus.ARCHIVED
        self.db.flush()
        return program

    # =========================================================================
    # Training Events
    # =========================================================================

    def list_events(
        self,
        org_id: UUID,
        *,
        program_id: UUID | None = None,
        status: TrainingEventStatus | None = None,
        trainer_id: UUID | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
        search: str | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[TrainingEvent]:
        """List training events."""
        query = select(TrainingEvent).where(TrainingEvent.organization_id == org_id)

        if program_id:
            query = query.where(TrainingEvent.program_id == program_id)

        if status:
            query = query.where(TrainingEvent.status == status)

        if trainer_id:
            query = query.where(TrainingEvent.trainer_employee_id == trainer_id)

        if from_date:
            query = query.where(TrainingEvent.start_date >= from_date)

        if to_date:
            query = query.where(TrainingEvent.end_date <= to_date)

        if search:
            search_term = f"%{search}%"
            query = query.where(TrainingEvent.event_name.ilike(search_term))

        query = query.order_by(TrainingEvent.start_date.desc())

        # Count total
        count_query = select(func.count()).select_from(query.subquery())
        total = self.db.scalar(count_query) or 0

        # Apply pagination
        if pagination:
            query = query.offset(pagination.offset).limit(pagination.limit)

        items = list(self.db.scalars(query).all())

        return PaginatedResult(
            items=items,
            total=total,
            offset=pagination.offset if pagination else 0,
            limit=pagination.limit if pagination else len(items),
        )

    def get_event(self, org_id: UUID, event_id: UUID) -> TrainingEvent:
        """Get a training event by ID."""
        event = self.db.scalar(
            select(TrainingEvent)
            .options(joinedload(TrainingEvent.attendees))
            .where(
                TrainingEvent.event_id == event_id,
                TrainingEvent.organization_id == org_id,
            )
        )
        if not event:
            raise TrainingEventNotFoundError(event_id)
        return event

    def create_event(
        self,
        org_id: UUID,
        *,
        program_id: UUID,
        event_name: str,
        start_date: date,
        end_date: date,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        event_type: str = "IN_PERSON",
        location: str | None = None,
        meeting_link: str | None = None,
        trainer_name: str | None = None,
        trainer_email: str | None = None,
        trainer_employee_id: UUID | None = None,
        max_attendees: int | None = None,
        total_cost: Decimal | None = None,
        currency_code: str = "NGN",
        description: str | None = None,
    ) -> TrainingEvent:
        """Create a new training event."""
        # Verify program exists
        self.get_program(org_id, program_id)

        event = TrainingEvent(
            organization_id=org_id,
            program_id=program_id,
            event_name=event_name,
            start_date=start_date,
            end_date=end_date,
            start_time=start_time,
            end_time=end_time,
            event_type=event_type,
            location=location,
            meeting_link=meeting_link,
            trainer_name=trainer_name,
            trainer_email=trainer_email,
            trainer_employee_id=trainer_employee_id,
            max_attendees=max_attendees,
            total_cost=total_cost,
            currency_code=currency_code,
            description=description,
            status=TrainingEventStatus.SCHEDULED,
        )

        self.db.add(event)
        self.db.flush()
        return event

    def update_event(
        self,
        org_id: UUID,
        event_id: UUID,
        **kwargs,
    ) -> TrainingEvent:
        """Update a training event."""
        event = self.get_event(org_id, event_id)

        for key, value in kwargs.items():
            if value is not None and hasattr(event, key):
                setattr(event, key, value)

        self.db.flush()
        return event

    def delete_event(self, org_id: UUID, event_id: UUID) -> None:
        """Delete a training event."""
        event = self.get_event(org_id, event_id)
        self.db.delete(event)
        self.db.flush()

    def start_event(self, org_id: UUID, event_id: UUID) -> TrainingEvent:
        """Start a training event."""
        event = self.get_event(org_id, event_id)

        if event.status != TrainingEventStatus.SCHEDULED:
            raise TrainingEventStatusError(
                event.status.value, TrainingEventStatus.IN_PROGRESS.value
            )

        event.status = TrainingEventStatus.IN_PROGRESS
        self.db.flush()
        return event

    def complete_event(
        self,
        org_id: UUID,
        event_id: UUID,
        *,
        feedback_notes: str | None = None,
    ) -> TrainingEvent:
        """Complete a training event."""
        event = self.get_event(org_id, event_id)

        valid_statuses = {
            TrainingEventStatus.SCHEDULED,
            TrainingEventStatus.IN_PROGRESS,
        }
        if event.status not in valid_statuses:
            raise TrainingEventStatusError(
                event.status.value, TrainingEventStatus.COMPLETED.value
            )

        event.status = TrainingEventStatus.COMPLETED
        if feedback_notes:
            event.feedback_notes = feedback_notes

        # Calculate average rating from attendees
        if event.attendees:
            ratings = [a.rating for a in event.attendees if a.rating is not None]
            if ratings:
                event.average_rating = Decimal(str(sum(ratings) / len(ratings)))

        self.db.flush()
        return event

    def cancel_event(
        self,
        org_id: UUID,
        event_id: UUID,
        *,
        reason: str | None = None,
    ) -> TrainingEvent:
        """Cancel a training event."""
        event = self.get_event(org_id, event_id)

        if event.status in {
            TrainingEventStatus.COMPLETED,
            TrainingEventStatus.CANCELLED,
        }:
            raise TrainingEventStatusError(
                event.status.value, TrainingEventStatus.CANCELLED.value
            )

        event.status = TrainingEventStatus.CANCELLED
        self.db.flush()
        return event

    # =========================================================================
    # Training Attendees
    # =========================================================================

    def list_attendees(
        self,
        org_id: UUID,
        *,
        event_id: UUID | None = None,
        employee_id: UUID | None = None,
        status: AttendeeStatus | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[TrainingAttendee]:
        """List training attendees."""
        query = select(TrainingAttendee).where(
            TrainingAttendee.organization_id == org_id
        )

        if event_id:
            query = query.where(TrainingAttendee.event_id == event_id)

        if employee_id:
            query = query.where(TrainingAttendee.employee_id == employee_id)

        if status:
            query = query.where(TrainingAttendee.status == status)

        query = query.order_by(TrainingAttendee.created_at.desc())

        # Count total
        count_query = select(func.count()).select_from(query.subquery())
        total = self.db.scalar(count_query) or 0

        # Apply pagination
        if pagination:
            query = query.offset(pagination.offset).limit(pagination.limit)

        items = list(self.db.scalars(query).all())

        return PaginatedResult(
            items=items,
            total=total,
            offset=pagination.offset if pagination else 0,
            limit=pagination.limit if pagination else len(items),
        )

    def get_attendee(self, org_id: UUID, attendee_id: UUID) -> TrainingAttendee:
        """Get a training attendee by ID."""
        attendee = self.db.scalar(
            select(TrainingAttendee).where(
                TrainingAttendee.attendee_id == attendee_id,
                TrainingAttendee.organization_id == org_id,
            )
        )
        if not attendee:
            raise TrainingAttendeeNotFoundError(attendee_id)
        return attendee

    def remove_attendee(self, org_id: UUID, event_id: UUID, attendee_id: UUID) -> None:
        """Remove an attendee from a training event."""
        attendee = self.get_attendee(org_id, attendee_id)
        if attendee.event_id != event_id:
            raise TrainingServiceError("Attendee does not belong to this event")
        self.db.delete(attendee)
        self.db.flush()

    def invite_attendee(
        self,
        org_id: UUID,
        event_id: UUID,
        employee_id: UUID,
        *,
        notes: str | None = None,
    ) -> TrainingAttendee:
        """Invite an employee to a training event."""
        # Verify event exists
        event = self.get_event(org_id, event_id)

        # Check if already invited
        existing = self.db.scalar(
            select(TrainingAttendee).where(
                TrainingAttendee.organization_id == org_id,
                TrainingAttendee.event_id == event_id,
                TrainingAttendee.employee_id == employee_id,
            )
        )
        if existing:
            raise TrainingServiceError(f"Employee already invited to event {event_id}")

        # Check max attendees
        if event.max_attendees:
            current_count = (
                self.db.scalar(
                    select(func.count(TrainingAttendee.attendee_id)).where(
                        TrainingAttendee.event_id == event_id
                    )
                )
                or 0
            )
            if current_count >= event.max_attendees:
                raise TrainingServiceError(
                    f"Event has reached maximum attendees ({event.max_attendees})"
                )

        attendee = TrainingAttendee(
            organization_id=org_id,
            event_id=event_id,
            employee_id=employee_id,
            status=AttendeeStatus.INVITED,
            invited_on=date.today(),
            notes=notes,
        )

        self.db.add(attendee)
        self.db.flush()
        return attendee

    def bulk_invite(
        self,
        org_id: UUID,
        event_id: UUID,
        employee_ids: list[UUID],
    ) -> list[TrainingAttendee]:
        """Bulk invite employees to a training event."""
        attendees = []

        for emp_id in employee_ids:
            try:
                attendee = self.invite_attendee(org_id, event_id, emp_id)
                attendees.append(attendee)
            except TrainingServiceError:
                continue  # Skip if already invited

        self.db.flush()
        return attendees

    def confirm_attendance(
        self,
        org_id: UUID,
        attendee_id: UUID,
    ) -> TrainingAttendee:
        """Confirm attendance."""
        attendee = self.get_attendee(org_id, attendee_id)
        attendee.status = AttendeeStatus.CONFIRMED
        attendee.confirmed_on = date.today()
        self.db.flush()
        return attendee

    def mark_attended(
        self,
        org_id: UUID,
        attendee_id: UUID,
    ) -> TrainingAttendee:
        """Mark attendee as attended."""
        attendee = self.get_attendee(org_id, attendee_id)
        attendee.status = AttendeeStatus.ATTENDED
        attendee.attended_on = date.today()
        self.db.flush()
        return attendee

    def submit_feedback(
        self,
        org_id: UUID,
        attendee_id: UUID,
        *,
        rating: int,
        feedback: str | None = None,
    ) -> TrainingAttendee:
        """Submit attendee feedback."""
        attendee = self.get_attendee(org_id, attendee_id)
        attendee.rating = rating
        attendee.feedback = feedback
        self.db.flush()
        return attendee

    def issue_certificate(
        self,
        org_id: UUID,
        attendee_id: UUID,
        *,
        certificate_number: str,
    ) -> TrainingAttendee:
        """Issue a certificate to an attendee."""
        attendee = self.get_attendee(org_id, attendee_id)

        if attendee.status != AttendeeStatus.ATTENDED:
            raise TrainingServiceError(
                "Can only issue certificate to attendees who attended"
            )

        attendee.certificate_issued = True
        attendee.certificate_number = certificate_number
        self.db.flush()
        return attendee

    def get_event_summary(
        self,
        org_id: UUID,
        program_id: UUID | None = None,
    ) -> dict:
        """Get training event summary by status."""
        query = select(TrainingEvent.status, func.count(TrainingEvent.event_id)).where(
            TrainingEvent.organization_id == org_id
        )
        if program_id:
            query = query.where(TrainingEvent.program_id == program_id)

        results = self.db.execute(query.group_by(TrainingEvent.status)).all()
        return {status.value: count for status, count in results}

    # =========================================================================
    # Reporting
    # =========================================================================

    def get_training_stats(self, org_id: UUID) -> dict:
        """Get training statistics for dashboard."""
        # Total programs
        total_programs = (
            self.db.scalar(
                select(func.count(TrainingProgram.program_id)).where(
                    TrainingProgram.organization_id == org_id
                )
            )
            or 0
        )

        # Active programs
        active_programs = (
            self.db.scalar(
                select(func.count(TrainingProgram.program_id)).where(
                    TrainingProgram.organization_id == org_id,
                    TrainingProgram.status == TrainingProgramStatus.ACTIVE,
                )
            )
            or 0
        )

        # Upcoming events
        today = date.today()
        upcoming_events = (
            self.db.scalar(
                select(func.count(TrainingEvent.event_id)).where(
                    TrainingEvent.organization_id == org_id,
                    TrainingEvent.status == TrainingEventStatus.SCHEDULED,
                    TrainingEvent.start_date >= today,
                )
            )
            or 0
        )

        # Completed events (this year)
        year_start = date(today.year, 1, 1)
        completed_events = (
            self.db.scalar(
                select(func.count(TrainingEvent.event_id)).where(
                    TrainingEvent.organization_id == org_id,
                    TrainingEvent.status == TrainingEventStatus.COMPLETED,
                    TrainingEvent.end_date >= year_start,
                )
            )
            or 0
        )

        # Total attendees (this year)
        total_attendees = (
            self.db.scalar(
                select(func.count(TrainingAttendee.attendee_id)).where(
                    TrainingAttendee.organization_id == org_id,
                    TrainingAttendee.status == AttendeeStatus.ATTENDED,
                    TrainingAttendee.attended_on >= year_start,
                )
            )
            or 0
        )

        # Average rating
        avg_rating = self.db.scalar(
            select(func.avg(TrainingAttendee.rating)).where(
                TrainingAttendee.organization_id == org_id,
                TrainingAttendee.rating.isnot(None),
            )
        )

        return {
            "total_programs": total_programs,
            "active_programs": active_programs,
            "upcoming_events": upcoming_events,
            "completed_events": completed_events,
            "total_attendees": total_attendees,
            "average_rating": Decimal(str(avg_rating)).quantize(Decimal("0.1"))
            if avg_rating
            else None,
        }

    def get_employee_training_history(
        self,
        org_id: UUID,
        employee_id: UUID,
    ) -> dict:
        """Get training history for an employee."""
        attendances = (
            self.db.scalars(
                select(TrainingAttendee)
                .options(joinedload(TrainingAttendee.event))
                .where(
                    TrainingAttendee.organization_id == org_id,
                    TrainingAttendee.employee_id == employee_id,
                )
            )
            .unique()
            .all()
        )

        len(attendances)
        attended = [a for a in attendances if a.status == AttendeeStatus.ATTENDED]
        total_attended = len(attended)

        # Calculate total hours from events
        total_hours = 0
        for a in attended:
            if a.event and a.event.program:
                total_hours += a.event.program.duration_hours or 0

        return {
            "employee_id": employee_id,
            "total_trainings_attended": total_attended,
            "total_hours": total_hours,
            "trainings": [
                {
                    "event_id": a.event_id,
                    "event_name": a.event.event_name if a.event else None,
                    "status": a.status.value,
                    "attended_on": a.attended_on,
                    "rating": a.rating,
                    "certificate_issued": a.certificate_issued,
                }
                for a in attendances
            ],
        }

    # ─────────────────────────────────────────────────────────────────────────────
    # Training Reports
    # ─────────────────────────────────────────────────────────────────────────────

    def get_training_completion_report(
        self,
        org_id: UUID,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> dict:
        """Get training completion rates by program.

        Returns completion statistics for each training program including
        invited vs attended rates, certificates issued, and average ratings.
        """
        # Build date filter for events
        date_filters = [
            TrainingEvent.organization_id == org_id,
            TrainingEvent.status.in_(
                [TrainingEventStatus.COMPLETED, TrainingEventStatus.IN_PROGRESS]
            ),
        ]
        if start_date:
            date_filters.append(TrainingEvent.start_date >= start_date)
        if end_date:
            date_filters.append(TrainingEvent.end_date <= end_date)

        # Get programs with their events and attendees
        programs = (
            self.db.scalars(
                select(TrainingProgram)
                .options(
                    joinedload(TrainingProgram.events).joinedload(
                        TrainingEvent.attendees
                    )
                )
                .where(TrainingProgram.organization_id == org_id)
                .order_by(TrainingProgram.program_name)
            )
            .unique()
            .all()
        )

        program_stats = []
        total_invited = 0
        total_attended = 0
        total_certificates = 0

        for program in programs:
            # Filter events by date range
            events = [
                e
                for e in program.events
                if e.status
                in [TrainingEventStatus.COMPLETED, TrainingEventStatus.IN_PROGRESS]
                and (not start_date or e.start_date >= start_date)
                and (not end_date or e.end_date <= end_date)
            ]

            if not events:
                continue

            invited = 0
            attended = 0
            certificates = 0
            ratings = []

            for event in events:
                for attendee in event.attendees:
                    if attendee.status in [
                        AttendeeStatus.INVITED,
                        AttendeeStatus.CONFIRMED,
                        AttendeeStatus.ATTENDED,
                    ]:
                        invited += 1
                    if attendee.status == AttendeeStatus.ATTENDED:
                        attended += 1
                        if attendee.certificate_issued:
                            certificates += 1
                        if attendee.rating:
                            ratings.append(float(attendee.rating))

            if invited > 0:
                completion_rate = round(attended / invited * 100, 1)
            else:
                completion_rate = 0

            avg_rating = round(sum(ratings) / len(ratings), 1) if ratings else None

            program_stats.append(
                {
                    "program_id": program.program_id,
                    "program_code": program.program_code,
                    "program_name": program.program_name,
                    "category": program.category,
                    "event_count": len(events),
                    "invited": invited,
                    "attended": attended,
                    "completion_rate": completion_rate,
                    "certificates_issued": certificates,
                    "average_rating": avg_rating,
                }
            )

            total_invited += invited
            total_attended += attended
            total_certificates += certificates

        overall_completion = (
            round(total_attended / total_invited * 100, 1) if total_invited > 0 else 0
        )

        return {
            "programs": program_stats,
            "total_programs": len(program_stats),
            "total_invited": total_invited,
            "total_attended": total_attended,
            "total_certificates": total_certificates,
            "overall_completion_rate": overall_completion,
        }

    def get_training_by_department_report(
        self,
        org_id: UUID,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> dict:
        """Get training participation by department.

        Returns training metrics grouped by department including attendance
        rates, total hours, and average ratings.
        """
        from app.models.people.hr import Department, Employee

        # Build base query for attendees with department info
        query = (
            select(
                Department.department_id,
                Department.department_name,
                func.count(TrainingAttendee.attendee_id).label("total_enrolled"),
                func.sum(
                    case(
                        (TrainingAttendee.status == AttendeeStatus.ATTENDED, 1), else_=0
                    )
                ).label("total_attended"),
                func.sum(
                    case((TrainingAttendee.certificate_issued == True, 1), else_=0)  # noqa: E712
                ).label("certificates_issued"),
                func.avg(TrainingAttendee.rating).label("avg_rating"),
            )
            .select_from(TrainingAttendee)
            .join(TrainingEvent, TrainingAttendee.event_id == TrainingEvent.event_id)
            .join(Employee, TrainingAttendee.employee_id == Employee.employee_id)
            .join(Department, Employee.department_id == Department.department_id)
            .where(
                TrainingAttendee.organization_id == org_id,
                TrainingEvent.status.in_(
                    [TrainingEventStatus.COMPLETED, TrainingEventStatus.IN_PROGRESS]
                ),
            )
            .group_by(Department.department_id, Department.department_name)
            .order_by(
                func.sum(
                    case(
                        (TrainingAttendee.status == AttendeeStatus.ATTENDED, 1), else_=0
                    )
                ).desc()
            )
        )

        if start_date:
            query = query.where(TrainingEvent.start_date >= start_date)
        if end_date:
            query = query.where(TrainingEvent.end_date <= end_date)

        results = self.db.execute(query).all()

        departments = []
        total_enrolled = 0
        total_attended = 0

        for row in results:
            enrolled = row.total_enrolled or 0
            attended = row.total_attended or 0
            total_enrolled += enrolled
            total_attended += attended

            departments.append(
                {
                    "department_id": row.department_id,
                    "department_name": row.department_name,
                    "total_enrolled": enrolled,
                    "total_attended": attended,
                    "attendance_rate": round(attended / enrolled * 100, 1)
                    if enrolled > 0
                    else 0,
                    "certificates_issued": row.certificates_issued or 0,
                    "average_rating": round(float(row.avg_rating), 1)
                    if row.avg_rating
                    else None,
                }
            )

        # Calculate percentages
        for dept in departments:
            dept["percentage"] = (
                round(dept["total_attended"] / total_attended * 100, 1)
                if total_attended > 0
                else 0
            )

        return {
            "departments": departments,
            "total_departments": len(departments),
            "total_enrolled": total_enrolled,
            "total_attended": total_attended,
            "overall_attendance_rate": round(total_attended / total_enrolled * 100, 1)
            if total_enrolled > 0
            else 0,
        }

    def get_training_cost_report(
        self,
        org_id: UUID,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> dict:
        """Get training cost analysis report.

        Returns cost breakdown by program and category, cost per attendee,
        and ROI indicators like cost per training hour.
        """
        # Build date filter
        date_filters = [
            TrainingEvent.organization_id == org_id,
            TrainingEvent.status.in_(
                [TrainingEventStatus.COMPLETED, TrainingEventStatus.IN_PROGRESS]
            ),
        ]
        if start_date:
            date_filters.append(TrainingEvent.start_date >= start_date)
        if end_date:
            date_filters.append(TrainingEvent.end_date <= end_date)

        # Get events with programs
        events = (
            self.db.scalars(
                select(TrainingEvent)
                .options(
                    joinedload(TrainingEvent.program),
                    joinedload(TrainingEvent.attendees),
                )
                .where(*date_filters)
                .order_by(TrainingEvent.start_date.desc())
            )
            .unique()
            .all()
        )

        # Aggregate by program
        program_costs: dict[UUID, dict[str, Any]] = {}
        category_costs: dict[str, dict[str, Any]] = {}
        total_cost = Decimal("0")
        total_attendees = 0
        total_hours = 0

        for event in events:
            cost = event.total_cost or Decimal("0")
            attended_count = sum(
                1 for a in event.attendees if a.status == AttendeeStatus.ATTENDED
            )

            program = event.program
            if program:
                program_key = program.program_id
                if program_key not in program_costs:
                    program_costs[program_key] = {
                        "program_id": program.program_id,
                        "program_code": program.program_code,
                        "program_name": program.program_name,
                        "category": program.category,
                        "event_count": 0,
                        "total_cost": Decimal("0"),
                        "attendee_count": 0,
                        "total_hours": 0,
                    }
                program_costs[program_key]["event_count"] += 1
                program_costs[program_key]["total_cost"] += cost
                program_costs[program_key]["attendee_count"] += attended_count
                program_costs[program_key]["total_hours"] += (
                    program.duration_hours or 0
                ) * attended_count

                # Category aggregation
                category = program.category or "Uncategorized"
                if category not in category_costs:
                    category_costs[category] = {
                        "category": category,
                        "total_cost": Decimal("0"),
                        "attendee_count": 0,
                    }
                category_costs[category]["total_cost"] += cost
                category_costs[category]["attendee_count"] += attended_count

            total_cost += cost
            total_attendees += attended_count
            if program:
                total_hours += (program.duration_hours or 0) * attended_count

        # Calculate cost per attendee for programs
        programs_list = list(program_costs.values())
        for p in programs_list:
            p["cost_per_attendee"] = (
                float(p["total_cost"] / p["attendee_count"])
                if p["attendee_count"] > 0
                else 0
            )
            p["cost_per_hour"] = (
                float(p["total_cost"] / p["total_hours"]) if p["total_hours"] > 0 else 0
            )
            p["total_cost"] = float(p["total_cost"])

        programs_list.sort(key=lambda x: x["total_cost"], reverse=True)

        # Calculate percentages for categories
        categories_list = list(category_costs.values())
        for c in categories_list:
            c["percentage"] = (
                round(float(c["total_cost"]) / float(total_cost) * 100, 1)
                if total_cost > 0
                else 0
            )
            c["total_cost"] = float(c["total_cost"])

        categories_list.sort(key=lambda x: x["total_cost"], reverse=True)

        return {
            "programs": programs_list,
            "categories": categories_list,
            "total_cost": float(total_cost),
            "total_events": len(events),
            "total_attendees": total_attendees,
            "total_training_hours": total_hours,
            "cost_per_attendee": float(total_cost / total_attendees)
            if total_attendees > 0
            else 0,
            "cost_per_hour": float(total_cost / total_hours) if total_hours > 0 else 0,
        }

    def get_training_effectiveness_report(
        self,
        org_id: UUID,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> dict:
        """Get training effectiveness/feedback report.

        Returns feedback and rating analysis including rating distribution,
        programs with highest/lowest ratings, and feedback trends.
        """
        # Build date filter
        date_filters = [
            TrainingEvent.organization_id == org_id,
            TrainingEvent.status == TrainingEventStatus.COMPLETED,
        ]
        if start_date:
            date_filters.append(TrainingEvent.start_date >= start_date)
        if end_date:
            date_filters.append(TrainingEvent.end_date <= end_date)

        # Get completed events with ratings
        events = (
            self.db.scalars(
                select(TrainingEvent)
                .options(
                    joinedload(TrainingEvent.program),
                    joinedload(TrainingEvent.attendees),
                )
                .where(*date_filters)
                .order_by(TrainingEvent.end_date.desc())
            )
            .unique()
            .all()
        )

        # Rating distribution (1-5 scale)
        rating_distribution = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
        all_ratings = []

        # Program ratings
        program_ratings: dict[UUID, ProgramRatingData] = {}
        event_list: list[EventRatingData] = []

        for event in events:
            event_ratings = []
            for attendee in event.attendees:
                if attendee.rating and attendee.status == AttendeeStatus.ATTENDED:
                    rating = int(attendee.rating)
                    rating_distribution[rating] = rating_distribution.get(rating, 0) + 1
                    all_ratings.append(float(attendee.rating))
                    event_ratings.append(float(attendee.rating))

            if event_ratings:
                avg_event_rating = round(sum(event_ratings) / len(event_ratings), 1)
                event_list.append(
                    {
                        "event_id": event.event_id,
                        "event_name": event.event_name,
                        "program_name": event.program.program_name
                        if event.program
                        else "N/A",
                        "end_date": event.end_date,
                        "response_count": len(event_ratings),
                        "average_rating": avg_event_rating,
                    }
                )

                # Aggregate by program
                if event.program:
                    program_id = event.program.program_id
                    if program_id not in program_ratings:
                        program_ratings[program_id] = {
                            "program_id": program_id,
                            "program_code": event.program.program_code,
                            "program_name": event.program.program_name,
                            "ratings": [],
                        }
                    program_ratings[program_id]["ratings"].extend(event_ratings)

        # Calculate program averages
        programs_list: list[ProgramSummary] = []
        for p in program_ratings.values():
            if p["ratings"]:
                avg = round(sum(p["ratings"]) / len(p["ratings"]), 1)
                programs_list.append(
                    {
                        "program_id": p["program_id"],
                        "program_code": p["program_code"],
                        "program_name": p["program_name"],
                        "response_count": len(p["ratings"]),
                        "average_rating": avg,
                    }
                )

        programs_list.sort(key=lambda x: x["average_rating"], reverse=True)

        # Top and bottom performers
        top_programs = programs_list[:5]
        bottom_programs = (
            list(reversed(programs_list[-5:])) if len(programs_list) > 5 else []
        )

        # Recent events sorted by rating
        event_list.sort(key=lambda x: x["average_rating"], reverse=True)

        # Overall stats
        total_responses = len(all_ratings)
        overall_average = (
            round(sum(all_ratings) / total_responses, 1)
            if total_responses > 0
            else None
        )

        # Calculate distribution percentages
        distribution_list = []
        for rating in range(5, 0, -1):  # 5 to 1
            count = rating_distribution[rating]
            pct = round(count / total_responses * 100, 1) if total_responses > 0 else 0
            distribution_list.append(
                {
                    "rating": rating,
                    "count": count,
                    "percentage": pct,
                }
            )

        return {
            "rating_distribution": distribution_list,
            "total_responses": total_responses,
            "overall_average": overall_average,
            "top_programs": top_programs,
            "bottom_programs": bottom_programs,
            "recent_events": event_list[:10],
            "total_programs_rated": len(programs_list),
        }
