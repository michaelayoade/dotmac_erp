"""
Recruitment Management API Router.

Thin API wrapper for Recruitment Management endpoints. All business logic is in services.
"""
import csv
import io
from datetime import date, datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import require_organization_id, require_tenant_auth
from app.db import SessionLocal
from app.models.people.recruit import (
    ApplicantStatus,
    InterviewStatus,
    JobOpeningStatus,
    OfferStatus,
)
from app.schemas.people.recruit import (
    # Job Opening
    JobOpeningCreate,
    JobOpeningUpdate,
    JobOpeningRead,
    JobOpeningListResponse,
    JobOpeningStats,
    # Job Applicant
    JobApplicantCreate,
    JobApplicantUpdate,
    JobApplicantRead,
    JobApplicantListResponse,
    ApplicantStatusUpdateRequest,
    ApplicantStats,
    # Interview
    InterviewCreate,
    InterviewUpdate,
    InterviewRead,
    InterviewListResponse,
    InterviewFeedbackRequest,
    # Job Offer
    JobOfferCreate,
    JobOfferUpdate,
    JobOfferRead,
    JobOfferListResponse,
)
from app.services.people.recruit import RecruitmentService
from app.services.people.recruit.recruit_service import JobOpeningNotFoundError
from app.services.common import PaginationParams

router = APIRouter(
    prefix="/recruit",
    tags=["recruitment"],
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


def csv_response(rows: list[list[str]], filename: str) -> Response:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerows(rows)
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# =============================================================================
# Job Openings
# =============================================================================


@router.get("/job-openings/new", include_in_schema=False)
def job_opening_new_redirect() -> Response:
    return Response(
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        headers={"Location": "/people/recruit/jobs/new"},
    )


@router.get("/job-openings", response_model=JobOpeningListResponse)
def list_job_openings(
    organization_id: UUID = Depends(require_organization_id),
    search: Optional[str] = None,
    status: Optional[str] = None,
    department_id: Optional[UUID] = None,
    designation_id: Optional[UUID] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List job openings."""
    svc = RecruitmentService(db)
    status_enum = parse_enum(status, JobOpeningStatus, "status")
    result = svc.list_job_openings(
        org_id=organization_id,
        search=search,
        status=status_enum,
        department_id=department_id,
        designation_id=designation_id,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return JobOpeningListResponse(
        items=[JobOpeningRead.model_validate(jo) for jo in result.items],
        total=result.total,
        offset=offset,
        limit=limit,
    )


@router.post("/job-openings", response_model=JobOpeningRead, status_code=status.HTTP_201_CREATED)
def create_job_opening(
    payload: JobOpeningCreate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Create a job opening."""
    svc = RecruitmentService(db)
    job_opening = svc.create_job_opening(
        org_id=organization_id,
        job_code=payload.job_code,
        job_title=payload.job_title,
        description=payload.description,
        department_id=payload.department_id,
        designation_id=payload.designation_id,
        reports_to_id=payload.reports_to_id,
        number_of_positions=payload.number_of_positions,
        posted_on=payload.posted_on,
        closes_on=payload.closes_on,
        employment_type=payload.employment_type,
        location=payload.location,
        is_remote=payload.is_remote,
        min_salary=payload.min_salary,
        max_salary=payload.max_salary,
        currency_code=payload.currency_code,
        min_experience_years=payload.min_experience_years,
        required_skills=payload.required_skills,
        preferred_skills=payload.preferred_skills,
        education_requirements=payload.education_requirements,
        status=payload.status,
    )
    db.commit()
    return JobOpeningRead.model_validate(job_opening)


@router.get("/job-openings/{job_opening_id}", response_model=JobOpeningRead)
def get_job_opening(
    job_opening_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get a job opening by ID."""
    svc = RecruitmentService(db)
    try:
        job_opening = svc.get_job_opening(organization_id, job_opening_id)
    except JobOpeningNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job opening not found")
    return JobOpeningRead.model_validate(job_opening)


@router.patch("/job-openings/{job_opening_id}", response_model=JobOpeningRead)
def update_job_opening(
    job_opening_id: UUID,
    payload: JobOpeningUpdate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Update a job opening."""
    svc = RecruitmentService(db)
    update_data = payload.model_dump(exclude_unset=True)
    job_opening = svc.update_job_opening(organization_id, job_opening_id, **update_data)
    db.commit()
    return JobOpeningRead.model_validate(job_opening)


@router.delete("/job-openings/{job_opening_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job_opening(
    job_opening_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Delete a job opening."""
    svc = RecruitmentService(db)
    svc.delete_job_opening(organization_id, job_opening_id)
    db.commit()


# Job opening actions
@router.post("/job-openings/{job_opening_id}/publish", response_model=JobOpeningRead)
def publish_job_opening(
    job_opening_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Publish a job opening."""
    svc = RecruitmentService(db)
    job_opening = svc.publish_job_opening(organization_id, job_opening_id)
    db.commit()
    return JobOpeningRead.model_validate(job_opening)


@router.post("/job-openings/{job_opening_id}/close", response_model=JobOpeningRead)
def close_job_opening(
    job_opening_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Close a job opening."""
    svc = RecruitmentService(db)
    job_opening = svc.close_job_opening(organization_id, job_opening_id)
    db.commit()
    return JobOpeningRead.model_validate(job_opening)


@router.post("/job-openings/{job_opening_id}/hold", response_model=JobOpeningRead)
def hold_job_opening(
    job_opening_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Put a job opening on hold."""
    svc = RecruitmentService(db)
    job_opening = svc.hold_job_opening(organization_id, job_opening_id)
    db.commit()
    return JobOpeningRead.model_validate(job_opening)


@router.post("/job-openings/{job_opening_id}/reopen", response_model=JobOpeningRead)
def reopen_job_opening(
    job_opening_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Reopen a job opening."""
    svc = RecruitmentService(db)
    job_opening = svc.reopen_job_opening(organization_id, job_opening_id)
    db.commit()
    return JobOpeningRead.model_validate(job_opening)


@router.get("/job-openings/summary", response_model=JobOpeningStats)
def job_opening_summary(
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get job opening summary statistics."""
    svc = RecruitmentService(db)
    stats = svc.get_job_opening_stats(organization_id)
    return JobOpeningStats(**stats)


# =============================================================================
# Job Applicants
# =============================================================================


@router.get("/applicants", response_model=JobApplicantListResponse)
def list_applicants(
    organization_id: UUID = Depends(require_organization_id),
    search: Optional[str] = None,
    job_opening_id: Optional[UUID] = None,
    status: Optional[str] = None,
    source: Optional[str] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List job applicants."""
    svc = RecruitmentService(db)
    status_enum = parse_enum(status, ApplicantStatus, "status")
    result = svc.list_applicants(
        org_id=organization_id,
        search=search,
        job_opening_id=job_opening_id,
        status=status_enum,
        source=source,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return JobApplicantListResponse(
        items=[JobApplicantRead.model_validate(a) for a in result.items],
        total=result.total,
        offset=offset,
        limit=limit,
    )


@router.post("/applicants", response_model=JobApplicantRead, status_code=status.HTTP_201_CREATED)
def create_applicant(
    payload: JobApplicantCreate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Create a job applicant."""
    svc = RecruitmentService(db)
    applicant = svc.create_applicant(
        org_id=organization_id,
        job_opening_id=payload.job_opening_id,
        first_name=payload.first_name,
        last_name=payload.last_name,
        email=payload.email,
        phone=payload.phone,
        date_of_birth=payload.date_of_birth,
        gender=payload.gender,
        city=payload.city,
        country_code=payload.country_code,
        current_employer=payload.current_employer,
        current_job_title=payload.current_job_title,
        years_of_experience=payload.years_of_experience,
        highest_qualification=payload.highest_qualification,
        skills=payload.skills,
        source=payload.source,
        referral_employee_id=payload.referral_employee_id,
        cover_letter=payload.cover_letter,
        resume_url=payload.resume_url,
    )
    db.commit()
    return JobApplicantRead.model_validate(applicant)


@router.get("/applicants/{applicant_id}", response_model=JobApplicantRead)
def get_applicant(
    applicant_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get a job applicant by ID."""
    svc = RecruitmentService(db)
    return JobApplicantRead.model_validate(svc.get_applicant(organization_id, applicant_id))


@router.patch("/applicants/{applicant_id}", response_model=JobApplicantRead)
def update_applicant(
    applicant_id: UUID,
    payload: JobApplicantUpdate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Update a job applicant."""
    svc = RecruitmentService(db)
    update_data = payload.model_dump(exclude_unset=True)
    applicant = svc.update_applicant(organization_id, applicant_id, **update_data)
    db.commit()
    return JobApplicantRead.model_validate(applicant)


@router.delete("/applicants/{applicant_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_applicant(
    applicant_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Delete a job applicant."""
    svc = RecruitmentService(db)
    svc.delete_applicant(organization_id, applicant_id)
    db.commit()


# Pipeline actions
@router.post("/applicants/{applicant_id}/advance", response_model=JobApplicantRead)
def advance_applicant(
    applicant_id: UUID,
    payload: ApplicantStatusUpdateRequest,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Advance applicant to next pipeline stage."""
    svc = RecruitmentService(db)
    applicant = svc.advance_applicant(
        org_id=organization_id,
        applicant_id=applicant_id,
        to_status=payload.status,
        notes=payload.notes,
    )
    db.commit()
    return JobApplicantRead.model_validate(applicant)


@router.post("/applicants/{applicant_id}/reject", response_model=JobApplicantRead)
def reject_applicant(
    applicant_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    reason: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Reject a job applicant."""
    svc = RecruitmentService(db)
    applicant = svc.reject_applicant(
        org_id=organization_id,
        applicant_id=applicant_id,
        reason=reason,
    )
    db.commit()
    return JobApplicantRead.model_validate(applicant)


# =============================================================================
# Interviews
# =============================================================================


@router.get("/interviews", response_model=InterviewListResponse)
def list_interviews(
    organization_id: UUID = Depends(require_organization_id),
    applicant_id: Optional[UUID] = None,
    job_opening_id: Optional[UUID] = None,
    interviewer_id: Optional[UUID] = None,
    status: Optional[str] = None,
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List interviews."""
    svc = RecruitmentService(db)
    status_enum = parse_enum(status, InterviewStatus, "status")
    result = svc.list_interviews(
        org_id=organization_id,
        applicant_id=applicant_id,
        job_opening_id=job_opening_id,
        interviewer_id=interviewer_id,
        status=status_enum,
        from_date=from_date,
        to_date=to_date,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return InterviewListResponse(
        items=[InterviewRead.model_validate(i) for i in result.items],
        total=result.total,
        offset=offset,
        limit=limit,
    )


@router.post("/interviews", response_model=InterviewRead, status_code=status.HTTP_201_CREATED)
def schedule_interview(
    payload: InterviewCreate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Schedule an interview."""
    svc = RecruitmentService(db)
    interview = svc.schedule_interview(
        org_id=organization_id,
        applicant_id=payload.applicant_id,
        round=payload.round,
        interview_type=payload.interview_type,
        scheduled_from=payload.scheduled_from,
        scheduled_to=payload.scheduled_to,
        interviewer_id=payload.interviewer_id,
        location=payload.location,
        meeting_link=payload.meeting_link,
    )
    db.commit()
    return InterviewRead.model_validate(interview)


@router.get("/interviews/{interview_id}", response_model=InterviewRead)
def get_interview(
    interview_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get an interview by ID."""
    svc = RecruitmentService(db)
    return InterviewRead.model_validate(svc.get_interview(organization_id, interview_id))


@router.patch("/interviews/{interview_id}", response_model=InterviewRead)
def update_interview(
    interview_id: UUID,
    payload: InterviewUpdate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Update an interview."""
    svc = RecruitmentService(db)
    update_data = payload.model_dump(exclude_unset=True)
    interview = svc.update_interview(organization_id, interview_id, **update_data)
    db.commit()
    return InterviewRead.model_validate(interview)


@router.delete("/interviews/{interview_id}", status_code=status.HTTP_204_NO_CONTENT)
def cancel_interview(
    interview_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Cancel an interview."""
    svc = RecruitmentService(db)
    svc.cancel_interview(organization_id, interview_id)
    db.commit()


# Interview actions
@router.post("/interviews/{interview_id}/feedback", response_model=InterviewRead)
def submit_interview_feedback(
    interview_id: UUID,
    payload: InterviewFeedbackRequest,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Submit interview feedback."""
    svc = RecruitmentService(db)
    interview = svc.record_interview_feedback(
        org_id=organization_id,
        interview_id=interview_id,
        rating=payload.rating,
        feedback=payload.feedback,
        recommendation=payload.recommendation,
        strengths=payload.strengths,
        weaknesses=payload.weaknesses,
    )
    db.commit()
    return InterviewRead.model_validate(interview)


# =============================================================================
# Job Offers
# =============================================================================


@router.get("/offers", response_model=JobOfferListResponse)
def list_job_offers(
    organization_id: UUID = Depends(require_organization_id),
    applicant_id: Optional[UUID] = None,
    job_opening_id: Optional[UUID] = None,
    status: Optional[str] = None,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List job offers."""
    svc = RecruitmentService(db)
    status_enum = parse_enum(status, OfferStatus, "status")
    result = svc.list_job_offers(
        org_id=organization_id,
        applicant_id=applicant_id,
        job_opening_id=job_opening_id,
        status=status_enum,
        from_date=from_date,
        to_date=to_date,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return JobOfferListResponse(
        items=[JobOfferRead.model_validate(o) for o in result.items],
        total=result.total,
        offset=offset,
        limit=limit,
    )


@router.post("/offers", response_model=JobOfferRead, status_code=status.HTTP_201_CREATED)
def create_job_offer(
    payload: JobOfferCreate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Create a job offer."""
    svc = RecruitmentService(db)
    offer = svc.create_job_offer(
        org_id=organization_id,
        applicant_id=payload.applicant_id,
        job_opening_id=payload.job_opening_id,
        designation_id=payload.designation_id,
        department_id=payload.department_id,
        offer_date=payload.offer_date,
        valid_until=payload.valid_until,
        expected_joining_date=payload.expected_joining_date,
        base_salary=payload.base_salary,
        currency_code=payload.currency_code,
        pay_frequency=payload.pay_frequency,
        signing_bonus=payload.signing_bonus,
        relocation_allowance=payload.relocation_allowance,
        other_benefits=payload.other_benefits,
        employment_type=payload.employment_type,
        probation_months=payload.probation_months,
        notice_period_days=payload.notice_period_days,
        terms_and_conditions=payload.terms_and_conditions,
        notes=payload.notes,
    )
    db.commit()
    return JobOfferRead.model_validate(offer)


@router.get("/offers/{offer_id}", response_model=JobOfferRead)
def get_job_offer(
    offer_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get a job offer by ID."""
    svc = RecruitmentService(db)
    return JobOfferRead.model_validate(svc.get_job_offer(organization_id, offer_id))


@router.patch("/offers/{offer_id}", response_model=JobOfferRead)
def update_job_offer(
    offer_id: UUID,
    payload: JobOfferUpdate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Update a job offer."""
    svc = RecruitmentService(db)
    update_data = payload.model_dump(exclude_unset=True)
    offer = svc.update_job_offer(organization_id, offer_id, **update_data)
    db.commit()
    return JobOfferRead.model_validate(offer)


# Offer actions
@router.post("/offers/{offer_id}/extend", response_model=JobOfferRead)
def extend_offer(
    offer_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Extend/send a job offer."""
    svc = RecruitmentService(db)
    offer = svc.extend_offer(organization_id, offer_id)
    db.commit()
    return JobOfferRead.model_validate(offer)


@router.post("/offers/{offer_id}/accept", response_model=JobOfferRead)
def accept_offer(
    offer_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Accept a job offer."""
    svc = RecruitmentService(db)
    offer = svc.accept_offer(organization_id, offer_id)
    db.commit()
    return JobOfferRead.model_validate(offer)


@router.delete("/offers/{offer_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job_offer(
    offer_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Delete a job offer."""
    svc = RecruitmentService(db)
    svc.delete_job_offer(organization_id, offer_id)
    db.commit()


@router.post("/offers/{offer_id}/decline", response_model=JobOfferRead)
def decline_offer(
    offer_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    reason: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Decline a job offer."""
    svc = RecruitmentService(db)
    offer = svc.decline_offer(organization_id, offer_id, reason=reason)
    db.commit()
    return JobOfferRead.model_validate(offer)


@router.post("/offers/{offer_id}/convert-to-employee", response_model=dict)
def convert_to_employee(
    offer_id: UUID,
    date_of_joining: date = Query(...),
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Convert accepted offer to employee."""
    svc = RecruitmentService(db)
    employee_id = svc.convert_to_employee(
        organization_id,
        offer_id,
        date_of_joining=date_of_joining,
    )
    db.commit()
    return {
        "message": "Employee created successfully",
        "employee_id": str(employee_id),
    }


# =============================================================================
# Pipeline Statistics
# =============================================================================


@router.get("/pipeline/stats")
def get_pipeline_stats(
    organization_id: UUID = Depends(require_organization_id),
    job_opening_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
):
    """Get recruitment pipeline statistics."""
    svc = RecruitmentService(db)
    stats = svc.get_pipeline_stats(
        org_id=organization_id,
        job_opening_id=job_opening_id,
    )
    return stats


@router.get("/applicants/summary", response_model=ApplicantStats)
def get_applicant_summary(
    organization_id: UUID = Depends(require_organization_id),
    job_opening_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
):
    """Get applicant summary statistics."""
    svc = RecruitmentService(db)
    summary = svc.get_pipeline_summary(
        org_id=organization_id,
        job_opening_id=job_opening_id,
    )
    interviewing = summary.get("interviewing", 0)
    return ApplicantStats(
        job_opening_id=job_opening_id,
        total=summary.get("total", 0),
        new=summary.get("new", 0),
        screening=summary.get("screening", 0),
        shortlisted=summary.get("shortlisted", 0),
        interviewing=interviewing,
        selected=summary.get("selected", 0),
        rejected=summary.get("rejected", 0),
    )


@router.get("/applicants/export")
def export_applicants(
    organization_id: UUID = Depends(require_organization_id),
    search: Optional[str] = None,
    job_opening_id: Optional[UUID] = None,
    status: Optional[str] = None,
    source: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Export job applicants to CSV."""
    svc = RecruitmentService(db)
    status_enum = parse_enum(status, ApplicantStatus, "status")
    result = svc.list_applicants(
        org_id=organization_id,
        search=search,
        job_opening_id=job_opening_id,
        status=status_enum,
        source=source,
        pagination=None,
    )
    rows = [
        [
            "application_number",
            "first_name",
            "last_name",
            "email",
            "phone",
            "status",
            "job_opening_id",
            "applied_on",
            "source",
        ]
    ]
    for applicant in result.items:
        rows.append(
            [
                applicant.application_number,
                applicant.first_name,
                applicant.last_name,
                applicant.email,
                applicant.phone or "",
                applicant.status.value,
                str(applicant.job_opening_id),
                applicant.applied_on.isoformat(),
                applicant.source or "",
            ]
        )
    return csv_response(rows, "job_applicants.csv")


@router.get("/stats")
def get_recruitment_stats(
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get recruitment dashboard statistics."""
    svc = RecruitmentService(db)
    return svc.get_recruitment_stats(organization_id)


@router.get("/offers/summary")
def get_offer_summary(
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get offer summary by status."""
    svc = RecruitmentService(db)
    return svc.get_offer_summary(organization_id)
