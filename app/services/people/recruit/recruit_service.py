"""Recruitment management service implementation.

Handles job openings, applicants, interviews, and job offers.
Adapted from DotMac People for the unified ERP platform.
"""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import Integer, delete, func, or_, select, update
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.models.finance.audit.audit_log import AuditAction
from app.models.finance.core_org.organization import Organization
from app.models.people.hr import EmployeeOnboarding
from app.models.people.recruit import (
    ApplicantStatus,
    Interview,
    InterviewRound,
    InterviewStatus,
    JobApplicant,
    JobOffer,
    JobOpening,
    JobOpeningStatus,
    OfferStatus,
)
from app.models.person import Gender, Person, PersonStatus
from app.services.audit_dispatcher import fire_audit_event
from app.services.careers.candidate_notifications import CandidateNotificationService
from app.services.common import PaginatedResult, PaginationParams, ValidationError
from app.services.people.hr import EmployeeCreateData
from app.services.people.recruit.notifications import send_new_applicant_notification
from app.services.state_machine import StateMachine

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.web.deps import WebAuthContext

__all__ = ["RecruitmentService"]


class RecruitmentServiceError(Exception):
    """Base error for recruitment service."""

    pass


class JobOpeningNotFoundError(RecruitmentServiceError):
    """Job opening not found."""

    def __init__(self, job_opening_id: UUID):
        self.job_opening_id = job_opening_id
        super().__init__(f"Job opening {job_opening_id} not found")


class JobApplicantNotFoundError(RecruitmentServiceError):
    """Job applicant not found."""

    def __init__(self, applicant_id: UUID):
        self.applicant_id = applicant_id
        super().__init__(f"Job applicant {applicant_id} not found")


class InterviewNotFoundError(RecruitmentServiceError):
    """Interview not found."""

    def __init__(self, interview_id: UUID):
        self.interview_id = interview_id
        super().__init__(f"Interview {interview_id} not found")


class JobOfferNotFoundError(RecruitmentServiceError):
    """Job offer not found."""

    def __init__(self, offer_id: UUID):
        self.offer_id = offer_id
        super().__init__(f"Job offer {offer_id} not found")


class ApplicantPipelineError(RecruitmentServiceError):
    """Invalid applicant pipeline transition."""

    def __init__(self, current: str, target: str, reason: str = ""):
        self.current = current
        self.target = target
        super().__init__(f"Cannot move from {current} to {target}. {reason}")


class OfferExpiredError(RecruitmentServiceError):
    """Job offer has expired."""

    def __init__(self, offer_id: UUID, expiry_date: str):
        self.offer_id = offer_id
        self.expiry_date = expiry_date
        super().__init__(f"Offer {offer_id} expired on {expiry_date}")


# Valid pipeline transitions for applicants
PIPELINE_TRANSITIONS = {
    ApplicantStatus.NEW: {
        ApplicantStatus.SCREENING,
        ApplicantStatus.REJECTED,
        ApplicantStatus.WITHDRAWN,
    },
    ApplicantStatus.SCREENING: {
        ApplicantStatus.SHORTLISTED,
        ApplicantStatus.REJECTED,
        ApplicantStatus.WITHDRAWN,
    },
    ApplicantStatus.SHORTLISTED: {
        ApplicantStatus.INTERVIEW_SCHEDULED,
        ApplicantStatus.REJECTED,
        ApplicantStatus.WITHDRAWN,
    },
    ApplicantStatus.INTERVIEW_SCHEDULED: {
        ApplicantStatus.INTERVIEW_COMPLETED,
        ApplicantStatus.REJECTED,
        ApplicantStatus.WITHDRAWN,
    },
    ApplicantStatus.INTERVIEW_COMPLETED: {
        ApplicantStatus.SELECTED,
        ApplicantStatus.REJECTED,
        ApplicantStatus.WITHDRAWN,
    },
    ApplicantStatus.SELECTED: {
        ApplicantStatus.OFFER_EXTENDED,
        ApplicantStatus.REJECTED,
        ApplicantStatus.WITHDRAWN,
    },
    ApplicantStatus.OFFER_EXTENDED: {
        ApplicantStatus.OFFER_ACCEPTED,
        ApplicantStatus.OFFER_DECLINED,
        ApplicantStatus.WITHDRAWN,
    },
    ApplicantStatus.OFFER_ACCEPTED: {
        ApplicantStatus.HIRED,
        ApplicantStatus.WITHDRAWN,
    },
}
_STATE_MACHINE = StateMachine(PIPELINE_TRANSITIONS)


class RecruitmentService:
    """Service for recruitment management operations.

    Handles:
    - Job openings management
    - Applicant tracking through pipeline
    - Interview scheduling and feedback
    - Job offer management
    - Conversion to employee
    """

    def __init__(
        self,
        db: Session,
        ctx: WebAuthContext | None = None,
    ) -> None:
        self.db = db
        self.ctx = ctx

    def _validate_pipeline_transition(
        self,
        current_status: ApplicantStatus,
        new_status: ApplicantStatus,
    ) -> None:
        try:
            _STATE_MACHINE.validate(current_status, new_status)
        except ValidationError:
            raise ApplicantPipelineError(
                current_status.value,
                new_status.value,
                "Invalid pipeline transition",
            ) from None

    # =========================================================================
    # Job Openings
    # =========================================================================

    def list_job_openings(
        self,
        org_id: UUID,
        *,
        status: JobOpeningStatus | None = None,
        department_id: UUID | None = None,
        designation_id: UUID | None = None,
        search: str | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[JobOpening]:
        """List job openings."""
        query = (
            select(JobOpening)
            .where(JobOpening.organization_id == org_id)
            .options(joinedload(JobOpening.department))
        )

        if status:
            query = query.where(JobOpening.status == status)

        if department_id:
            query = query.where(JobOpening.department_id == department_id)

        if designation_id:
            query = query.where(JobOpening.designation_id == designation_id)

        if search:
            search_term = f"%{search}%"
            query = query.where(
                or_(
                    JobOpening.job_code.ilike(search_term),
                    JobOpening.job_title.ilike(search_term),
                )
            )

        query = query.order_by(JobOpening.created_at.desc())

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

    def get_job_opening(self, org_id: UUID, job_opening_id: UUID) -> JobOpening:
        """Get a job opening by ID."""
        opening = self.db.scalar(
            select(JobOpening).where(
                JobOpening.job_opening_id == job_opening_id,
                JobOpening.organization_id == org_id,
            )
        )
        if not opening:
            raise JobOpeningNotFoundError(job_opening_id)
        return opening

    def create_job_opening(
        self,
        org_id: UUID,
        *,
        job_code: str,
        job_title: str,
        department_id: UUID | None = None,
        designation_id: UUID | None = None,
        reports_to_id: UUID | None = None,
        number_of_positions: int = 1,
        posted_on: date | None = None,
        closes_on: date | None = None,
        employment_type: str = "FULL_TIME",
        location: str | None = None,
        is_remote: bool = False,
        min_salary: Decimal | None = None,
        max_salary: Decimal | None = None,
        currency_code: str = "NGN",
        min_experience_years: int | None = None,
        description: str | None = None,
        required_skills: str | None = None,
        preferred_skills: str | None = None,
        education_requirements: str | None = None,
        status: JobOpeningStatus = JobOpeningStatus.DRAFT,
    ) -> JobOpening:
        """Create a new job opening."""
        opening = JobOpening(
            organization_id=org_id,
            job_code=job_code,
            job_title=job_title,
            department_id=department_id,
            designation_id=designation_id,
            reports_to_id=reports_to_id,
            number_of_positions=number_of_positions,
            posted_on=posted_on,
            closes_on=closes_on,
            employment_type=employment_type,
            location=location,
            is_remote=is_remote,
            min_salary=min_salary,
            max_salary=max_salary,
            currency_code=currency_code,
            min_experience_years=min_experience_years,
            description=description,
            required_skills=required_skills,
            preferred_skills=preferred_skills,
            education_requirements=education_requirements,
            status=status,
            positions_filled=0,
        )

        self.db.add(opening)
        self.db.flush()
        fire_audit_event(
            self.db,
            org_id,
            "recruit",
            "job_opening",
            str(opening.job_opening_id),
            AuditAction.INSERT,
            new_values={
                "job_code": job_code,
                "job_title": job_title,
                "status": status.value,
            },
        )
        return opening

    def update_job_opening(
        self,
        org_id: UUID,
        job_opening_id: UUID,
        **kwargs,
    ) -> JobOpening:
        """Update a job opening."""
        opening = self.get_job_opening(org_id, job_opening_id)

        for key, value in kwargs.items():
            if value is not None and hasattr(opening, key):
                setattr(opening, key, value)

        self.db.flush()
        return opening

    def publish_job_opening(self, org_id: UUID, job_opening_id: UUID) -> JobOpening:
        """Publish a job opening."""
        opening = self.get_job_opening(org_id, job_opening_id)
        opening.status = JobOpeningStatus.OPEN
        opening.posted_on = date.today()
        self.db.flush()
        fire_audit_event(
            self.db,
            org_id,
            "recruit",
            "job_opening",
            str(opening.job_opening_id),
            AuditAction.UPDATE,
            old_values={"status": "DRAFT"},
            new_values={"status": "OPEN", "posted_on": str(opening.posted_on)},
        )
        return opening

    def close_job_opening(self, org_id: UUID, job_opening_id: UUID) -> JobOpening:
        """Close a job opening."""
        opening = self.get_job_opening(org_id, job_opening_id)
        opening.status = JobOpeningStatus.CLOSED
        self.db.flush()
        fire_audit_event(
            self.db,
            org_id,
            "recruit",
            "job_opening",
            str(opening.job_opening_id),
            AuditAction.UPDATE,
            new_values={"status": "CLOSED"},
            reason="Job opening closed",
        )

        try:
            from app.services.finance.automation.event_dispatcher import (
                fire_workflow_event,
            )

            fire_workflow_event(
                db=self.db,
                organization_id=org_id,
                entity_type="RECRUITMENT",
                entity_id=opening.job_opening_id,
                event="ON_STATUS_CHANGE",
                old_values={},
                new_values={"status": "CLOSED"},
            )
        except Exception:
            logger.exception("Ignored exception")

        return opening

    def hold_job_opening(self, org_id: UUID, job_opening_id: UUID) -> JobOpening:
        """Put a job opening on hold."""
        opening = self.get_job_opening(org_id, job_opening_id)
        opening.status = JobOpeningStatus.ON_HOLD
        self.db.flush()
        return opening

    def reopen_job_opening(self, org_id: UUID, job_opening_id: UUID) -> JobOpening:
        """Reopen a job opening."""
        opening = self.get_job_opening(org_id, job_opening_id)
        opening.status = JobOpeningStatus.OPEN
        self.db.flush()
        return opening

    def delete_job_opening(self, org_id: UUID, job_opening_id: UUID) -> None:
        """Delete a job opening."""
        opening = self.get_job_opening(org_id, job_opening_id)
        self.db.delete(opening)
        self.db.flush()

    # =========================================================================
    # Job Applicants
    # =========================================================================

    def list_applicants(
        self,
        org_id: UUID,
        *,
        job_opening_id: UUID | None = None,
        status: ApplicantStatus | None = None,
        search: str | None = None,
        source: str | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[JobApplicant]:
        """List job applicants."""
        query = select(JobApplicant).where(JobApplicant.organization_id == org_id)

        if job_opening_id:
            query = query.where(JobApplicant.job_opening_id == job_opening_id)

        if status:
            query = query.where(JobApplicant.status == status)

        if search:
            search_term = f"%{search}%"
            query = query.where(
                or_(
                    JobApplicant.first_name.ilike(search_term),
                    JobApplicant.last_name.ilike(search_term),
                    JobApplicant.email.ilike(search_term),
                    JobApplicant.application_number.ilike(search_term),
                )
            )

        if source:
            query = query.where(JobApplicant.source == source)

        query = query.order_by(JobApplicant.applied_on.desc())

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

    def get_applicant(self, org_id: UUID, applicant_id: UUID) -> JobApplicant:
        """Get an applicant by ID."""
        applicant = self.db.scalar(
            select(JobApplicant).where(
                JobApplicant.applicant_id == applicant_id,
                JobApplicant.organization_id == org_id,
            )
        )
        if not applicant:
            raise JobApplicantNotFoundError(applicant_id)
        return applicant

    def create_applicant(
        self,
        org_id: UUID,
        *,
        job_opening_id: UUID,
        first_name: str,
        last_name: str,
        email: str,
        phone: str | None = None,
        date_of_birth: date | None = None,
        gender: str | None = None,
        city: str | None = None,
        country_code: str | None = None,
        current_employer: str | None = None,
        current_job_title: str | None = None,
        years_of_experience: int | None = None,
        highest_qualification: str | None = None,
        skills: str | None = None,
        source: str | None = None,
        referral_employee_id: UUID | None = None,
        cover_letter: str | None = None,
        resume_url: str | None = None,
    ) -> JobApplicant:
        """Create a new job applicant."""
        # Verify job opening exists
        self.get_job_opening(org_id, job_opening_id)

        # Generate application number
        count = (
            self.db.scalar(
                select(func.count(JobApplicant.applicant_id)).where(
                    JobApplicant.organization_id == org_id
                )
            )
            or 0
        )
        application_number = f"APP-{date.today().year}-{count + 1:05d}"

        applicant = JobApplicant(
            organization_id=org_id,
            job_opening_id=job_opening_id,
            application_number=application_number,
            first_name=first_name,
            last_name=last_name,
            email=email,
            phone=phone,
            date_of_birth=date_of_birth,
            gender=gender,
            city=city,
            country_code=country_code,
            current_employer=current_employer,
            current_job_title=current_job_title,
            years_of_experience=years_of_experience,
            highest_qualification=highest_qualification,
            skills=skills,
            source=source,
            referral_employee_id=referral_employee_id,
            cover_letter=cover_letter,
            resume_url=resume_url,
            applied_on=date.today(),
            status=ApplicantStatus.NEW,
        )

        self.db.add(applicant)
        self.db.flush()
        fire_audit_event(
            self.db,
            org_id,
            "recruit",
            "job_applicant",
            str(applicant.applicant_id),
            AuditAction.INSERT,
            new_values={
                "application_number": application_number,
                "job_opening_id": str(job_opening_id),
                "name": f"{first_name} {last_name}",
                "email": email,
            },
        )
        opening = self.get_job_opening(org_id, job_opening_id)
        send_new_applicant_notification(self.db, org_id, applicant, opening)
        return applicant

    def update_applicant(
        self,
        org_id: UUID,
        applicant_id: UUID,
        **kwargs,
    ) -> JobApplicant:
        """Update an applicant."""
        applicant = self.get_applicant(org_id, applicant_id)

        for key, value in kwargs.items():
            if value is not None and hasattr(applicant, key):
                setattr(applicant, key, value)

        self.db.flush()
        return applicant

    def delete_applicant(self, org_id: UUID, applicant_id: UUID) -> None:
        """Delete a job applicant."""
        applicant = self.get_applicant(org_id, applicant_id)

        offer_ids = [
            row[0]
            for row in self.db.execute(
                select(JobOffer.offer_id).where(JobOffer.applicant_id == applicant_id)
            ).all()
        ]

        if offer_ids:
            self.db.execute(
                update(EmployeeOnboarding)
                .where(EmployeeOnboarding.job_offer_id.in_(offer_ids))
                .values(job_offer_id=None)
            )

        self.db.execute(
            update(EmployeeOnboarding)
            .where(EmployeeOnboarding.job_applicant_id == applicant_id)
            .values(job_applicant_id=None)
        )

        self.db.execute(
            delete(Interview).where(
                Interview.applicant_id == applicant_id,
            )
        )
        self.db.execute(
            delete(JobOffer).where(
                JobOffer.applicant_id == applicant_id,
            )
        )

        self.db.delete(applicant)
        self.db.flush()

    def advance_applicant(
        self,
        org_id: UUID,
        applicant_id: UUID,
        to_status: ApplicantStatus,
        *,
        notes: str | None = None,
    ) -> JobApplicant:
        """Move an applicant through the hiring pipeline."""
        applicant = self.get_applicant(org_id, applicant_id)

        self._validate_pipeline_transition(applicant.status, to_status)

        old_status = applicant.status
        applicant.status = to_status
        if notes:
            applicant.notes = notes

        self.db.flush()
        fire_audit_event(
            self.db,
            org_id,
            "recruit",
            "job_applicant",
            str(applicant.applicant_id),
            AuditAction.UPDATE,
            old_values={"status": old_status.value},
            new_values={"status": to_status.value},
        )
        return applicant

    def reject_applicant(
        self,
        org_id: UUID,
        applicant_id: UUID,
        *,
        reason: str | None = None,
    ) -> JobApplicant:
        """Reject an applicant."""
        applicant = self.get_applicant(org_id, applicant_id)
        applicant.status = ApplicantStatus.REJECTED
        if reason:
            applicant.notes = reason
        self.db.flush()
        fire_audit_event(
            self.db,
            org_id,
            "recruit",
            "job_applicant",
            str(applicant.applicant_id),
            AuditAction.UPDATE,
            new_values={"status": "REJECTED"},
            reason=reason,
        )
        return applicant

    # =========================================================================
    # Interviews
    # =========================================================================

    def list_interviews(
        self,
        org_id: UUID,
        *,
        applicant_id: UUID | None = None,
        job_opening_id: UUID | None = None,
        interviewer_id: UUID | None = None,
        status: InterviewStatus | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[Interview]:
        """List interviews."""
        query = select(Interview).where(Interview.organization_id == org_id)

        if applicant_id:
            query = query.where(Interview.applicant_id == applicant_id)

        if job_opening_id:
            query = query.join(
                JobApplicant, JobApplicant.applicant_id == Interview.applicant_id
            ).where(JobApplicant.job_opening_id == job_opening_id)

        if interviewer_id:
            query = query.where(Interview.interviewer_id == interviewer_id)

        if status:
            query = query.where(Interview.status == status)

        if from_date:
            query = query.where(Interview.scheduled_from >= from_date)

        if to_date:
            query = query.where(Interview.scheduled_to <= to_date)

        query = query.order_by(Interview.scheduled_from.desc())

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

    def get_interview(self, org_id: UUID, interview_id: UUID) -> Interview:
        """Get an interview by ID."""
        interview = self.db.scalar(
            select(Interview).where(
                Interview.interview_id == interview_id,
                Interview.organization_id == org_id,
            )
        )
        if not interview:
            raise InterviewNotFoundError(interview_id)
        return interview

    def schedule_interview(
        self,
        org_id: UUID,
        *,
        applicant_id: UUID,
        round: InterviewRound,
        interview_type: str = "IN_PERSON",
        scheduled_from: datetime,
        scheduled_to: datetime,
        interviewer_id: UUID,
        location: str | None = None,
        meeting_link: str | None = None,
    ) -> Interview:
        """Schedule a new interview."""
        # Verify applicant exists
        applicant = self.get_applicant(org_id, applicant_id)

        interview = Interview(
            organization_id=org_id,
            applicant_id=applicant_id,
            round=round,
            interview_type=interview_type,
            scheduled_from=scheduled_from,
            scheduled_to=scheduled_to,
            interviewer_id=interviewer_id,
            location=location,
            meeting_link=meeting_link,
            status=InterviewStatus.SCHEDULED,
        )

        self.db.add(interview)

        # Move applicant to interviewing stage if not already
        if applicant.status in [
            ApplicantStatus.NEW,
            ApplicantStatus.SCREENING,
            ApplicantStatus.SHORTLISTED,
        ]:
            applicant.status = ApplicantStatus.INTERVIEW_SCHEDULED

        self.db.flush()
        fire_audit_event(
            self.db,
            org_id,
            "recruit",
            "interview",
            str(interview.interview_id),
            AuditAction.INSERT,
            new_values={
                "applicant_id": str(applicant_id),
                "round": round.value,
                "interviewer_id": str(interviewer_id),
                "scheduled_from": str(scheduled_from),
            },
        )
        return interview

    def reschedule_interview(
        self,
        org_id: UUID,
        interview_id: UUID,
        *,
        scheduled_from: datetime,
        scheduled_to: datetime,
        reason: str | None = None,
    ) -> Interview:
        """Reschedule an interview."""
        interview = self.get_interview(org_id, interview_id)
        interview.scheduled_from = scheduled_from
        interview.scheduled_to = scheduled_to
        interview.status = InterviewStatus.RESCHEDULED
        self.db.flush()
        return interview

    def update_interview(
        self,
        org_id: UUID,
        interview_id: UUID,
        **kwargs,
    ) -> Interview:
        """Update interview details."""
        interview = self.get_interview(org_id, interview_id)
        schedule_changed = False
        for key, value in kwargs.items():
            if value is None or not hasattr(interview, key):
                continue
            if key in ("scheduled_from", "scheduled_to") and value != getattr(
                interview, key
            ):
                schedule_changed = True
            setattr(interview, key, value)

        if schedule_changed and "status" not in kwargs:
            interview.status = InterviewStatus.RESCHEDULED

        self.db.flush()
        return interview

    def send_interview_invitation(self, org_id: UUID, interview: Interview) -> None:
        """Send interview invitation email to the applicant (best-effort)."""
        applicant = self.db.get(JobApplicant, interview.applicant_id)
        if not applicant or not applicant.email:
            return

        job_opening = (
            self.db.get(JobOpening, applicant.job_opening_id)
            if applicant.job_opening_id
            else None
        )
        org = self.db.get(Organization, org_id)
        org_name = (org.legal_name or org.trading_name) if org else "Our Company"
        applicant_name = f"{applicant.first_name} {applicant.last_name}".strip()

        interview_date = interview.scheduled_from.strftime("%Y-%m-%d")
        interview_time = interview.scheduled_from.strftime("%I:%M %p")
        interview_type = str(interview.interview_type)
        location_or_link = interview.meeting_link or interview.location or "TBD"

        candidate_notifications = CandidateNotificationService()
        candidate_notifications.send_interview_invitation(
            db=self.db,
            applicant_email=applicant.email,
            applicant_name=applicant_name or "Candidate",
            job_title=job_opening.job_title if job_opening else "Position",
            interview_date=interview_date,
            interview_time=interview_time,
            interview_type=interview_type,
            location_or_link=location_or_link,
            org_name=org_name or "Our Company",
            organization_id=org_id,
        )

    def record_interview_feedback(
        self,
        org_id: UUID,
        interview_id: UUID,
        *,
        rating: int,
        recommendation: str,
        feedback: str | None = None,
        strengths: str | None = None,
        weaknesses: str | None = None,
    ) -> Interview:
        """Record feedback for a completed interview."""
        interview = self.get_interview(org_id, interview_id)
        interview.status = InterviewStatus.COMPLETED
        interview.actual_start = interview.scheduled_from
        interview.actual_end = interview.scheduled_to
        interview.rating = rating
        interview.recommendation = recommendation
        interview.feedback = feedback
        interview.strengths = strengths
        interview.weaknesses = weaknesses
        self.db.flush()
        return interview

    def cancel_interview(
        self,
        org_id: UUID,
        interview_id: UUID,
        *,
        reason: str | None = None,
    ) -> Interview:
        """Cancel an interview."""
        interview = self.get_interview(org_id, interview_id)
        interview.status = InterviewStatus.CANCELLED
        self.db.flush()
        return interview

    def delete_interview(self, org_id: UUID, interview_id: UUID) -> None:
        """Delete an interview."""
        interview = self.get_interview(org_id, interview_id)
        self.db.delete(interview)
        self.db.flush()

    # =========================================================================
    # Job Offers
    # =========================================================================

    def list_job_offers(
        self,
        org_id: UUID,
        *,
        applicant_id: UUID | None = None,
        job_opening_id: UUID | None = None,
        status: OfferStatus | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[JobOffer]:
        """List job offers."""
        query = select(JobOffer).where(JobOffer.organization_id == org_id)

        if applicant_id:
            query = query.where(JobOffer.applicant_id == applicant_id)

        if job_opening_id:
            query = query.where(JobOffer.job_opening_id == job_opening_id)

        if status:
            query = query.where(JobOffer.status == status)

        if from_date:
            query = query.where(JobOffer.offer_date >= from_date)

        if to_date:
            query = query.where(JobOffer.offer_date <= to_date)

        query = query.order_by(JobOffer.offer_date.desc())

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

    def get_job_offer(self, org_id: UUID, offer_id: UUID) -> JobOffer:
        """Get a job offer by ID."""
        offer = self.db.scalar(
            select(JobOffer).where(
                JobOffer.offer_id == offer_id,
                JobOffer.organization_id == org_id,
            )
        )
        if not offer:
            raise JobOfferNotFoundError(offer_id)
        return offer

    def update_job_offer(
        self,
        org_id: UUID,
        offer_id: UUID,
        **kwargs,
    ) -> JobOffer:
        """Update a job offer."""
        offer = self.get_job_offer(org_id, offer_id)
        for key, value in kwargs.items():
            if value is not None and hasattr(offer, key):
                setattr(offer, key, value)
        self.db.flush()
        return offer

    def delete_job_offer(self, org_id: UUID, offer_id: UUID) -> None:
        """Delete a job offer."""
        offer = self.get_job_offer(org_id, offer_id)
        self.db.delete(offer)
        self.db.flush()

    def create_job_offer(
        self,
        org_id: UUID,
        *,
        applicant_id: UUID,
        job_opening_id: UUID,
        designation_id: UUID,
        department_id: UUID | None = None,
        offer_date: date,
        valid_until: date,
        expected_joining_date: date,
        base_salary: Decimal,
        currency_code: str = "NGN",
        pay_frequency: str = "MONTHLY",
        signing_bonus: Decimal | None = None,
        relocation_allowance: Decimal | None = None,
        other_benefits: str | None = None,
        employment_type: str = "FULL_TIME",
        probation_months: int = 3,
        notice_period_days: int = 30,
        terms_and_conditions: str | None = None,
        notes: str | None = None,
    ) -> JobOffer:
        """Create a new job offer."""
        # Verify applicant and job opening exist
        applicant = self.get_applicant(org_id, applicant_id)
        self.get_job_opening(org_id, job_opening_id)

        # Generate offer number
        count = (
            self.db.scalar(
                select(func.count(JobOffer.offer_id)).where(
                    JobOffer.organization_id == org_id
                )
            )
            or 0
        )
        offer_number = f"OFR-{date.today().year}-{count + 1:05d}"

        offer = JobOffer(
            organization_id=org_id,
            applicant_id=applicant_id,
            job_opening_id=job_opening_id,
            offer_number=offer_number,
            designation_id=designation_id,
            department_id=department_id,
            offer_date=offer_date,
            valid_until=valid_until,
            expected_joining_date=expected_joining_date,
            base_salary=base_salary,
            currency_code=currency_code,
            pay_frequency=pay_frequency,
            signing_bonus=signing_bonus,
            relocation_allowance=relocation_allowance,
            other_benefits=other_benefits,
            employment_type=employment_type,
            probation_months=probation_months,
            notice_period_days=notice_period_days,
            terms_and_conditions=terms_and_conditions,
            notes=notes,
            status=OfferStatus.DRAFT,
        )

        self.db.add(offer)

        # Update applicant status
        applicant.status = ApplicantStatus.SELECTED

        self.db.flush()
        fire_audit_event(
            self.db,
            org_id,
            "recruit",
            "job_offer",
            str(offer.offer_id),
            AuditAction.INSERT,
            new_values={
                "offer_number": offer_number,
                "applicant_id": str(applicant_id),
                "base_salary": str(base_salary),
                "status": "DRAFT",
            },
        )
        return offer

    def extend_offer(self, org_id: UUID, offer_id: UUID) -> JobOffer:
        """Extend/send the offer to the candidate."""
        offer = self.get_job_offer(org_id, offer_id)
        offer.status = OfferStatus.EXTENDED
        offer.extended_on = date.today()

        # Ensure candidate portal token is available
        if (
            not offer.candidate_access_token
            or not offer.candidate_access_expires
            or offer.candidate_access_expires < datetime.now(UTC)
        ):
            offer.candidate_access_token = secrets.token_urlsafe(32)
            offer.candidate_access_expires = datetime.now(UTC) + timedelta(days=30)

        # Update applicant status
        if offer.applicant_id:
            applicant = self.db.get(JobApplicant, offer.applicant_id)
            if applicant:
                applicant.status = ApplicantStatus.OFFER_EXTENDED

        self.db.flush()

        # Send offer portal email to candidate
        if offer.applicant_id:
            applicant = self.db.get(JobApplicant, offer.applicant_id)
            if applicant and applicant.email:
                org = self.db.get(Organization, org_id)
                org_name = org.legal_name or org.trading_name if org else "Our Company"
                org_slug = (
                    (org.slug if org and org.slug else str(org_id))
                    if org
                    else str(org_id)
                )
                portal_url = (
                    f"{settings.app_url.rstrip('/')}/careers/{org_slug}/offer/"
                    f"{offer.candidate_access_token}"
                )
                pdf_url = f"{portal_url}/pdf"
                accept_url = f"{portal_url}/accept"
                decline_url = f"{portal_url}/decline"

                job_opening = self.db.get(JobOpening, offer.job_opening_id)
                candidate_notifications = CandidateNotificationService()
                candidate_notifications.send_offer_portal_email(
                    db=self.db,
                    applicant_email=applicant.email,
                    applicant_name=applicant.first_name,
                    job_title=job_opening.job_title if job_opening else "Position",
                    org_name=org_name or "Our Company",
                    portal_url=portal_url,
                    pdf_url=pdf_url,
                    accept_url=accept_url,
                    decline_url=decline_url,
                    organization_id=org_id,
                )
        return offer

    def accept_offer(self, org_id: UUID, offer_id: UUID) -> JobOffer:
        """Mark an offer as accepted by the candidate."""
        offer = self.get_job_offer(org_id, offer_id)

        # Check if expired
        if offer.valid_until and offer.valid_until < date.today():
            raise OfferExpiredError(offer_id, str(offer.valid_until))

        offer.status = OfferStatus.ACCEPTED
        offer.responded_on = date.today()
        self.db.flush()
        fire_audit_event(
            self.db,
            org_id,
            "recruit",
            "job_offer",
            str(offer.offer_id),
            AuditAction.UPDATE,
            old_values={"status": "EXTENDED"},
            new_values={"status": "ACCEPTED"},
        )
        return offer

    def decline_offer(
        self,
        org_id: UUID,
        offer_id: UUID,
        *,
        reason: str | None = None,
    ) -> JobOffer:
        """Mark an offer as declined by the candidate."""
        offer = self.get_job_offer(org_id, offer_id)
        offer.status = OfferStatus.DECLINED
        offer.responded_on = date.today()
        offer.decline_reason = reason

        # Update applicant status
        if offer.applicant_id:
            applicant = self.db.get(JobApplicant, offer.applicant_id)
            if applicant:
                applicant.status = ApplicantStatus.OFFER_DECLINED

        self.db.flush()
        return offer

    def withdraw_offer(self, org_id: UUID, offer_id: UUID) -> JobOffer:
        """Withdraw an offer."""
        offer = self.get_job_offer(org_id, offer_id)
        offer.status = OfferStatus.WITHDRAWN

        # Update applicant status
        if offer.applicant_id:
            applicant = self.db.get(JobApplicant, offer.applicant_id)
            if applicant:
                applicant.status = ApplicantStatus.WITHDRAWN

        self.db.flush()
        return offer

    def convert_to_employee(
        self,
        org_id: UUID,
        offer_id: UUID,
        *,
        date_of_joining: date,
        create_onboarding: bool = True,
        onboarding_template_id: UUID | None = None,
        buddy_employee_id: UUID | None = None,
        manager_id: UUID | None = None,
        send_welcome_email: bool = True,
    ) -> UUID:
        """
        Convert an accepted offer to an employee record.

        Args:
            org_id: Organization UUID
            offer_id: Job offer UUID
            date_of_joining: Employee start date
            create_onboarding: If True, automatically create an onboarding record
            onboarding_template_id: Specific template to use (uses default if None)
            buddy_employee_id: Assigned buddy/mentor for onboarding
            manager_id: Manager for onboarding approvals (defaults to reports_to)
            send_welcome_email: If True, send welcome email with portal link

        Returns:
            UUID of the created employee
        """
        from app.services.people.hr import EmployeeService

        offer = self.get_job_offer(org_id, offer_id)

        if offer.status != OfferStatus.ACCEPTED:
            raise ApplicantPipelineError(
                offer.status.value,
                "hire",
                "Offer must be accepted before converting to employee",
            )

        applicant = self.get_applicant(org_id, offer.applicant_id)

        # Ensure a Person exists for the applicant
        person = self.db.scalar(select(Person).where(Person.email == applicant.email))
        if person:
            if person.organization_id != org_id:
                raise RecruitmentServiceError(
                    "Applicant email belongs to a different organization"
                )
        else:
            gender_value = Gender.unknown
            if applicant.gender:
                try:
                    gender_value = Gender(applicant.gender.lower())
                except ValueError:
                    gender_value = Gender.unknown

            person = Person(
                organization_id=org_id,
                first_name=applicant.first_name,
                last_name=applicant.last_name,
                email=applicant.email,
                phone=applicant.phone,
                date_of_birth=applicant.date_of_birth,
                gender=gender_value,
                city=applicant.city,
                country_code=applicant.country_code,
                status=PersonStatus.active,
                is_active=True,
            )
            self.db.add(person)
            self.db.flush()

        # Create employee via EmployeeService
        employee_service = EmployeeService(self.db, org_id)
        employee_data = EmployeeCreateData(
            department_id=offer.department_id,
            designation_id=offer.designation_id,
            date_of_joining=date_of_joining,
        )
        employee = employee_service.create_employee(person.id, employee_data)

        # Update offer and applicant status
        offer.status = OfferStatus.CONVERTED
        offer.converted_to_employee_id = employee.employee_id
        applicant.status = ApplicantStatus.HIRED

        # Increment positions filled on job opening
        if offer.job_opening_id:
            opening = self.db.get(JobOpening, offer.job_opening_id)
            if opening:
                opening.positions_filled += 1

        self.db.flush()
        fire_audit_event(
            self.db,
            org_id,
            "recruit",
            "job_offer",
            str(offer.offer_id),
            AuditAction.UPDATE,
            new_values={
                "status": "CONVERTED",
                "converted_to_employee_id": str(employee.employee_id),
            },
            reason="Offer converted to employee",
        )

        # Auto-create onboarding record
        onboarding_id = None
        if create_onboarding:
            from app.services.people.hr.onboarding import OnboardingService

            onboarding_service = OnboardingService(self.db)

            # Use manager_id from parameter, or fall back to reports_to
            effective_manager_id = manager_id or employee.reports_to_id

            onboarding = onboarding_service.create_onboarding_from_template(
                org_id,
                employee_id=employee.employee_id,
                template_id=onboarding_template_id,
                date_of_joining=date_of_joining,
                department_id=offer.department_id,
                designation_id=offer.designation_id,
                job_applicant_id=applicant.applicant_id,
                job_offer_id=offer.offer_id,
                buddy_employee_id=buddy_employee_id,
                manager_id=effective_manager_id,
                generate_self_service_token=send_welcome_email,
            )
            onboarding_id = onboarding.onboarding_id

            # Queue welcome email task
            if send_welcome_email and onboarding.self_service_token:
                from app.tasks.hr import send_welcome_email as send_welcome_email_task

                # Use delay() to queue the task asynchronously
                send_welcome_email_task.delay(str(onboarding_id))

        return employee.employee_id

    # =========================================================================
    # Reporting
    # =========================================================================

    def get_recruitment_stats(self, org_id: UUID) -> dict:
        """Get recruitment statistics for dashboard."""
        # Open positions
        open_positions = (
            self.db.scalar(
                select(
                    func.sum(
                        JobOpening.number_of_positions - JobOpening.positions_filled
                    )
                ).where(
                    JobOpening.organization_id == org_id,
                    JobOpening.status == JobOpeningStatus.OPEN,
                )
            )
            or 0
        )

        # Total applicants
        total_applicants = (
            self.db.scalar(
                select(func.count(JobApplicant.applicant_id)).where(
                    JobApplicant.organization_id == org_id
                )
            )
            or 0
        )

        # Interviews scheduled
        interviews_scheduled = (
            self.db.scalar(
                select(func.count(Interview.interview_id)).where(
                    Interview.organization_id == org_id,
                    Interview.status == InterviewStatus.SCHEDULED,
                )
            )
            or 0
        )

        # Offers pending
        offers_pending = (
            self.db.scalar(
                select(func.count(JobOffer.offer_id)).where(
                    JobOffer.organization_id == org_id,
                    JobOffer.status == OfferStatus.EXTENDED,
                )
            )
            or 0
        )

        # Offers accepted
        offers_accepted = (
            self.db.scalar(
                select(func.count(JobOffer.offer_id)).where(
                    JobOffer.organization_id == org_id,
                    JobOffer.status == OfferStatus.ACCEPTED,
                )
            )
            or 0
        )

        # Recent hires (this month)
        month_start = date.today().replace(day=1)
        recent_hires = (
            self.db.scalar(
                select(func.count(JobOffer.offer_id)).where(
                    JobOffer.organization_id == org_id,
                    JobOffer.status == OfferStatus.CONVERTED,
                    JobOffer.responded_on >= month_start,
                )
            )
            or 0
        )

        return {
            "open_positions": open_positions,
            "total_applicants": total_applicants,
            "interviews_scheduled": interviews_scheduled,
            "offers_pending": offers_pending,
            "offers_accepted": offers_accepted,
            "recent_hires": recent_hires,
        }

    def get_job_opening_stats(self, org_id: UUID) -> dict:
        """Get job opening summary stats."""
        total = (
            self.db.scalar(
                select(func.count(JobOpening.job_opening_id)).where(
                    JobOpening.organization_id == org_id
                )
            )
            or 0
        )
        open_count = (
            self.db.scalar(
                select(func.count(JobOpening.job_opening_id)).where(
                    JobOpening.organization_id == org_id,
                    JobOpening.status == JobOpeningStatus.OPEN,
                )
            )
            or 0
        )
        filled = (
            self.db.scalar(
                select(func.count(JobOpening.job_opening_id)).where(
                    JobOpening.organization_id == org_id,
                    JobOpening.status == JobOpeningStatus.FILLED,
                )
            )
            or 0
        )
        closed = (
            self.db.scalar(
                select(func.count(JobOpening.job_opening_id)).where(
                    JobOpening.organization_id == org_id,
                    JobOpening.status == JobOpeningStatus.CLOSED,
                )
            )
            or 0
        )
        total_applicants = (
            self.db.scalar(
                select(func.count(JobApplicant.applicant_id)).where(
                    JobApplicant.organization_id == org_id
                )
            )
            or 0
        )

        return {
            "total": total,
            "open": open_count,
            "filled": filled,
            "closed": closed,
            "total_applicants": total_applicants,
        }

    def get_offer_summary(self, org_id: UUID) -> dict:
        """Get job offer summary by status."""
        results = self.db.execute(
            select(JobOffer.status, func.count(JobOffer.offer_id))
            .where(JobOffer.organization_id == org_id)
            .group_by(JobOffer.status)
        ).all()
        return {status.value: count for status, count in results}

    def get_pipeline_summary(
        self,
        org_id: UUID,
        job_opening_id: UUID | None = None,
    ) -> dict:
        """Get applicant pipeline summary."""
        query = select(JobApplicant).where(JobApplicant.organization_id == org_id)

        if job_opening_id:
            query = query.where(JobApplicant.job_opening_id == job_opening_id)

        applicants = self.db.scalars(query).all()

        summary = {
            "total": len(applicants),
            "new": sum(1 for a in applicants if a.status == ApplicantStatus.NEW),
            "screening": sum(
                1 for a in applicants if a.status == ApplicantStatus.SCREENING
            ),
            "shortlisted": sum(
                1 for a in applicants if a.status == ApplicantStatus.SHORTLISTED
            ),
            "interviewing": sum(
                1 for a in applicants if a.status == ApplicantStatus.INTERVIEW_SCHEDULED
            ),
            "selected": sum(
                1 for a in applicants if a.status == ApplicantStatus.SELECTED
            ),
            "offer_sent": sum(
                1 for a in applicants if a.status == ApplicantStatus.OFFER_EXTENDED
            ),
            "hired": sum(1 for a in applicants if a.status == ApplicantStatus.HIRED),
            "rejected": sum(
                1 for a in applicants if a.status == ApplicantStatus.REJECTED
            ),
            "withdrawn": sum(
                1 for a in applicants if a.status == ApplicantStatus.WITHDRAWN
            ),
            "declined": sum(
                1 for a in applicants if a.status == ApplicantStatus.OFFER_DECLINED
            ),
        }

        return summary

    def get_pipeline_stats(
        self,
        org_id: UUID,
        job_opening_id: UUID | None = None,
    ) -> dict:
        """Compatibility wrapper for pipeline stats."""
        return self.get_pipeline_summary(org_id, job_opening_id=job_opening_id)

    # ─────────────────────────────────────────────────────────────────────────────
    # Recruitment Reports
    # ─────────────────────────────────────────────────────────────────────────────

    def get_recruitment_pipeline_report(
        self,
        org_id: UUID,
        *,
        job_opening_id: UUID | None = None,
    ) -> dict:
        """Get detailed recruitment pipeline report.

        Returns applicant flow through pipeline stages with conversion rates.
        """
        # Build base filters
        filters = [JobApplicant.organization_id == org_id]
        if job_opening_id:
            filters.append(JobApplicant.job_opening_id == job_opening_id)

        # Get applicant counts by status
        results = self.db.execute(
            select(JobApplicant.status, func.count(JobApplicant.applicant_id))
            .where(*filters)
            .group_by(JobApplicant.status)
        ).all()

        status_counts = {status: count for status, count in results}
        total = sum(status_counts.values())

        # Define pipeline stages in order
        pipeline_stages = [
            ("NEW", "New Applications"),
            ("SCREENING", "Screening"),
            ("SHORTLISTED", "Shortlisted"),
            ("INTERVIEW_SCHEDULED", "Interview Scheduled"),
            ("INTERVIEW_COMPLETED", "Interview Completed"),
            ("SELECTED", "Selected"),
            ("OFFER_EXTENDED", "Offer Extended"),
            ("OFFER_ACCEPTED", "Offer Accepted"),
            ("HIRED", "Hired"),
        ]

        pipeline = []
        prev_count = total

        for status_value, label in pipeline_stages:
            try:
                status = ApplicantStatus(status_value)
                count = status_counts.get(status, 0)
            except ValueError:
                count = 0

            # Calculate conversion rate from previous stage
            if prev_count > 0:
                conversion_rate = round(count / prev_count * 100, 1)
            else:
                conversion_rate = 0

            pipeline.append(
                {
                    "status": status_value,
                    "label": label,
                    "count": count,
                    "percentage": round(count / total * 100, 1) if total > 0 else 0,
                    "conversion_rate": conversion_rate,
                }
            )

            if count > 0:
                prev_count = count

        # Rejected and withdrawn
        rejected = status_counts.get(ApplicantStatus.REJECTED, 0)
        withdrawn = status_counts.get(ApplicantStatus.WITHDRAWN, 0)
        declined = status_counts.get(ApplicantStatus.OFFER_DECLINED, 0)

        # Get job openings for filter dropdown
        openings = self.db.scalars(
            select(JobOpening)
            .where(JobOpening.organization_id == org_id)
            .order_by(JobOpening.created_at.desc())
        ).all()

        # Hired rate
        hired = status_counts.get(ApplicantStatus.HIRED, 0)
        hired_rate = round(hired / total * 100, 1) if total > 0 else 0

        return {
            "pipeline": pipeline,
            "total_applicants": total,
            "rejected": rejected,
            "withdrawn": withdrawn,
            "declined": declined,
            "hired": hired,
            "hired_rate": hired_rate,
            "openings": openings,
            "selected_opening_id": job_opening_id,
        }

    def get_time_to_hire_report(
        self,
        org_id: UUID,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> dict:
        """Get time to hire metrics.

        Returns average time from application to hire, broken down by stage.
        """
        # Filter for hired applicants
        filters = [
            JobApplicant.organization_id == org_id,
            JobApplicant.status == ApplicantStatus.HIRED,
        ]
        if start_date:
            filters.append(JobApplicant.applied_on >= start_date)
        if end_date:
            filters.append(JobApplicant.applied_on <= end_date)

        # Get hired applicants with their application and hire dates
        hired_applicants = (
            self.db.scalars(
                select(JobApplicant)
                .options(joinedload(JobApplicant.job_opening))
                .where(*filters)
            )
            .unique()
            .all()
        )

        if not hired_applicants:
            return {
                "total_hires": 0,
                "average_days_to_hire": None,
                "fastest_hire_days": None,
                "slowest_hire_days": None,
                "by_opening": [],
                "by_month": [],
            }

        # Calculate days to hire for each
        days_to_hire_list: list[int] = []
        opening_stats: dict[UUID | None, dict[str, Any]] = {}

        for applicant in hired_applicants:
            if applicant.updated_at and applicant.applied_on:
                days = (applicant.updated_at.date() - applicant.applied_on).days
            else:
                days = 0

            days_to_hire_list.append(days)

            # Aggregate by opening
            opening_id = applicant.job_opening_id
            if opening_id not in opening_stats:
                opening_stats[opening_id] = {
                    "job_opening_id": opening_id,
                    "job_title": applicant.job_opening.job_title
                    if applicant.job_opening
                    else "Unknown",
                    "hires": [],
                }
            opening_stats[opening_id]["hires"].append(days)

        # Calculate overall stats
        avg_days = (
            round(sum(days_to_hire_list) / len(days_to_hire_list), 1)
            if days_to_hire_list
            else 0
        )
        fastest = min(days_to_hire_list) if days_to_hire_list else 0
        slowest = max(days_to_hire_list) if days_to_hire_list else 0

        # By opening
        by_opening = []
        for stats in opening_stats.values():
            hire_days = stats["hires"]
            by_opening.append(
                {
                    "job_opening_id": stats["job_opening_id"],
                    "job_title": stats["job_title"],
                    "hire_count": len(hire_days),
                    "average_days": round(sum(hire_days) / len(hire_days), 1)
                    if hire_days
                    else 0,
                    "fastest_days": min(hire_days) if hire_days else 0,
                    "slowest_days": max(hire_days) if hire_days else 0,
                }
            )

        by_opening.sort(key=lambda x: x["average_days"])

        return {
            "total_hires": len(hired_applicants),
            "average_days_to_hire": avg_days,
            "fastest_hire_days": fastest,
            "slowest_hire_days": slowest,
            "by_opening": by_opening[:10],
            "by_month": [],  # Could add monthly breakdown here
        }

    def get_source_analysis_report(
        self,
        org_id: UUID,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> dict:
        """Get applicant source analysis report.

        Returns applicant statistics by source with conversion rates.
        """
        # Build filters
        filters = [JobApplicant.organization_id == org_id]
        if start_date:
            filters.append(JobApplicant.applied_on >= start_date)
        if end_date:
            filters.append(JobApplicant.applied_on <= end_date)

        # Get applicants grouped by source
        source_bucket = func.coalesce(JobApplicant.source, "Unknown")
        results = self.db.execute(
            select(
                source_bucket.label("source"),
                func.count(JobApplicant.applicant_id).label("total"),
                func.sum(
                    func.cast(JobApplicant.status == ApplicantStatus.HIRED, Integer)
                ).label("hired"),
                func.sum(
                    func.cast(
                        JobApplicant.status == ApplicantStatus.SHORTLISTED, Integer
                    )
                ).label("shortlisted"),
            )
            .where(*filters)
            .group_by(source_bucket)
            .order_by(func.count(JobApplicant.applicant_id).desc())
        ).all()

        sources = []
        total_applicants = sum(r.total for r in results)
        total_hired = sum((r.hired or 0) for r in results)

        for row in results:
            hire_rate = (
                round((row.hired or 0) / row.total * 100, 1) if row.total > 0 else 0
            )
            sources.append(
                {
                    "source": row.source,
                    "total_applicants": row.total,
                    "hired": row.hired or 0,
                    "shortlisted": row.shortlisted or 0,
                    "hire_rate": hire_rate,
                    "percentage": round(row.total / total_applicants * 100, 1)
                    if total_applicants > 0
                    else 0,
                }
            )

        # Find best performing source
        best_source = max(sources, key=lambda x: x["hire_rate"]) if sources else None

        return {
            "sources": sources,
            "total_applicants": total_applicants,
            "total_hired": total_hired,
            "overall_hire_rate": round(total_hired / total_applicants * 100, 1)
            if total_applicants > 0
            else 0,
            "best_source": best_source["source"] if best_source else None,
            "source_count": len(sources),
        }

    def get_recruitment_overview_report(
        self,
        org_id: UUID,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> dict:
        """Get recruitment overview/dashboard report.

        Returns high-level recruitment metrics and trends.
        """
        # Base filters
        applicant_filters = [JobApplicant.organization_id == org_id]
        opening_filters = [JobOpening.organization_id == org_id]

        if start_date:
            applicant_filters.append(JobApplicant.applied_on >= start_date)
            opening_filters.append(
                JobOpening.created_at
                >= datetime.combine(start_date, datetime.min.time())
            )
        if end_date:
            applicant_filters.append(JobApplicant.applied_on <= end_date)
            opening_filters.append(
                JobOpening.created_at <= datetime.combine(end_date, datetime.max.time())
            )

        # Job opening stats
        total_openings = (
            self.db.scalar(
                select(func.count(JobOpening.job_opening_id)).where(*opening_filters)
            )
            or 0
        )

        open_positions = (
            self.db.scalar(
                select(func.count(JobOpening.job_opening_id)).where(
                    *opening_filters,
                    JobOpening.status == JobOpeningStatus.OPEN,
                )
            )
            or 0
        )

        filled_positions = (
            self.db.scalar(
                select(func.count(JobOpening.job_opening_id)).where(
                    *opening_filters,
                    JobOpening.status == JobOpeningStatus.FILLED,
                )
            )
            or 0
        )

        # Applicant stats
        total_applicants = (
            self.db.scalar(
                select(func.count(JobApplicant.applicant_id)).where(*applicant_filters)
            )
            or 0
        )

        hired = (
            self.db.scalar(
                select(func.count(JobApplicant.applicant_id)).where(
                    *applicant_filters,
                    JobApplicant.status == ApplicantStatus.HIRED,
                )
            )
            or 0
        )

        pending_review = (
            self.db.scalar(
                select(func.count(JobApplicant.applicant_id)).where(
                    *applicant_filters,
                    JobApplicant.status.in_(
                        [ApplicantStatus.NEW, ApplicantStatus.SCREENING]
                    ),
                )
            )
            or 0
        )

        in_interview = (
            self.db.scalar(
                select(func.count(JobApplicant.applicant_id)).where(
                    *applicant_filters,
                    JobApplicant.status.in_(
                        [
                            ApplicantStatus.INTERVIEW_SCHEDULED,
                            ApplicantStatus.INTERVIEW_COMPLETED,
                        ]
                    ),
                )
            )
            or 0
        )

        # Status breakdown for chart
        status_results = self.db.execute(
            select(JobApplicant.status, func.count(JobApplicant.applicant_id))
            .where(*applicant_filters)
            .group_by(JobApplicant.status)
        ).all()

        status_breakdown = [
            {
                "status": status.value,
                "count": count,
                "percentage": round(count / total_applicants * 100, 1)
                if total_applicants > 0
                else 0,
            }
            for status, count in status_results
        ]

        # Top openings by applicant count
        top_openings_results = self.db.execute(
            select(
                JobOpening.job_opening_id,
                JobOpening.job_title,
                func.count(JobApplicant.applicant_id).label("applicant_count"),
            )
            .select_from(JobOpening)
            .outerjoin(
                JobApplicant, JobApplicant.job_opening_id == JobOpening.job_opening_id
            )
            .where(
                JobOpening.organization_id == org_id,
                JobOpening.status == JobOpeningStatus.OPEN,
            )
            .group_by(JobOpening.job_opening_id, JobOpening.job_title)
            .order_by(func.count(JobApplicant.applicant_id).desc())
            .limit(5)
        ).all()

        top_openings = [
            {
                "job_opening_id": row.job_opening_id,
                "job_title": row.job_title,
                "applicant_count": row.applicant_count or 0,
            }
            for row in top_openings_results
        ]

        return {
            "total_openings": total_openings,
            "open_positions": open_positions,
            "filled_positions": filled_positions,
            "total_applicants": total_applicants,
            "hired": hired,
            "pending_review": pending_review,
            "in_interview": in_interview,
            "hire_rate": round(hired / total_applicants * 100, 1)
            if total_applicants > 0
            else 0,
            "status_breakdown": status_breakdown,
            "top_openings": top_openings,
        }
