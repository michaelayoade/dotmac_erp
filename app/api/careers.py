"""
Public Careers API - Job listings and application submission.

These endpoints are PUBLIC and do not require authentication.
Rate limiting is applied to protect against abuse.
"""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.middleware.rate_limit import check_rate_limit
from app.schemas.careers import (
    ApplicationStatusResponse,
    ApplicationSubmitRequest,
    ApplicationSubmitResponse,
    DepartmentWithCount,
    PublicJobBrief,
    PublicJobListResponse,
    PublicJobRead,
    PublicOrganizationInfo,
    ResumeUploadResponse,
    StatusCheckRequest,
    StatusCheckResponse,
)
from app.services.careers.web import CareersWebService
from app.services.upload_utils import read_upload_bytes

router = APIRouter(prefix="/careers", tags=["careers"])
logger = logging.getLogger(__name__)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _get_service(db: Session) -> CareersWebService:
    return CareersWebService(db)


def _require_org(slug: str, db: Session) -> tuple:
    """Get org context or raise 404."""
    service = _get_service(db)
    ctx = service.get_organization_context(slug)
    if not ctx:
        raise HTTPException(status_code=404, detail="Organization not found")
    return ctx, service


def _parse_department_ids(request: Request) -> list[uuid.UUID]:
    raw_values = request.query_params.getlist("department_id")
    if not raw_values:
        return []

    department_ids: list[uuid.UUID] = []
    for raw in raw_values:
        for value in raw.split(","):
            value = value.strip()
            if not value:
                continue
            try:
                department_ids.append(uuid.UUID(value))
            except ValueError:
                logger.warning(
                    "Invalid department_id ignored on careers API: %s", value
                )
    return department_ids


# ═══════════════════════════════════════════════════════════════════════════
# Organization Info
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/{org_slug}/org", response_model=PublicOrganizationInfo)
def get_organization_info(org_slug: str, db: Session = Depends(get_db)):
    ctx, _ = _require_org(org_slug, db)
    return PublicOrganizationInfo(
        name=ctx.org_name,
        logo_url=ctx.org_logo,
        website_url=ctx.org.website_url,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Job Listings
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/{org_slug}/jobs", response_model=PublicJobListResponse)
def list_jobs(
    org_slug: str,
    request: Request,
    search: str | None = None,
    location: str | None = None,
    employment_type: str | None = None,
    is_remote: bool | None = None,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
):
    department_ids = _parse_department_ids(request) if request else []
    ctx, service = _require_org(org_slug, db)
    result = service.list_jobs(
        ctx.org_id,
        search=search,
        department_id=department_ids or None,
        location=location,
        employment_type=employment_type,
        is_remote=is_remote,
        page=page,
        page_size=min(page_size, 50),
    )

    return PublicJobListResponse(
        jobs=[
            PublicJobBrief(
                job_code=j.job_code,
                job_title=j.job_title,
                department_name=j.department.department_name if j.department else None,
                location=j.location,
                employment_type=j.employment_type,
                is_remote=j.is_remote,
                posted_on=j.posted_on,
                closes_on=j.closes_on,
            )
            for j in result.jobs
        ],
        total=result.total,
        page=result.page,
        page_size=result.page_size,
        total_pages=result.total_pages,
    )


@router.get("/{org_slug}/jobs/{job_code}", response_model=PublicJobRead)
def get_job_detail(org_slug: str, job_code: str, db: Session = Depends(get_db)):
    ctx, service = _require_org(org_slug, db)
    job = service.get_job_by_code(ctx.org_id, job_code)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return PublicJobRead(
        job_code=job.job_code,
        job_title=job.job_title,
        description=job.description,
        department_name=job.department.department_name if job.department else None,
        location=job.location,
        employment_type=job.employment_type,
        is_remote=job.is_remote,
        min_experience_years=job.min_experience_years,
        required_skills=job.required_skills,
        preferred_skills=job.preferred_skills,
        education_requirements=job.education_requirements,
        posted_on=job.posted_on,
        closes_on=job.closes_on,
        positions_remaining=job.positions_remaining,
    )


@router.get("/{org_slug}/departments", response_model=list[DepartmentWithCount])
def list_departments(org_slug: str, db: Session = Depends(get_db)):
    ctx, service = _require_org(org_slug, db)
    result = service.list_jobs(ctx.org_id, page_size=1)  # Just to get departments
    return [
        DepartmentWithCount(department_id=d[0], department_name=d[1], job_count=d[2])
        for d in result.departments
    ]


@router.get("/{org_slug}/locations", response_model=list[str])
def list_locations(org_slug: str, db: Session = Depends(get_db)):
    ctx, service = _require_org(org_slug, db)
    result = service.list_jobs(ctx.org_id, page_size=1)
    return result.locations


# ═══════════════════════════════════════════════════════════════════════════
# Application Submission
# ═══════════════════════════════════════════════════════════════════════════


@router.post("/{org_slug}/upload/resume", response_model=ResumeUploadResponse)
async def upload_resume(
    request: Request,
    org_slug: str,
    file: UploadFile,
    db: Session = Depends(get_db),
):
    check_rate_limit(request, max_requests=5, window_seconds=60)
    ctx, service = _require_org(org_slug, db)

    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    max_mb = settings.resume_max_size_bytes // 1024 // 1024
    content = await read_upload_bytes(
        file,
        settings.resume_max_size_bytes,
        error_detail=f"File too large. Maximum size: {max_mb}MB",
    )
    file_id, error = await service.upload_resume(ctx.org_id, file.filename, content)

    if error:
        raise HTTPException(status_code=400, detail=error)

    return ResumeUploadResponse(file_id=file_id, filename=file.filename)


@router.post(
    "/{org_slug}/jobs/{job_code}/apply", response_model=ApplicationSubmitResponse
)
async def submit_application(
    request: Request,
    org_slug: str,
    job_code: str,
    data: ApplicationSubmitRequest,
    db: Session = Depends(get_db),
):
    check_rate_limit(request, max_requests=3, window_seconds=300)
    ctx, service = _require_org(org_slug, db)

    client_ip = request.client.host if request.client else None
    result = await service.submit_application(
        ctx.org_id,
        job_code,
        first_name=data.first_name,
        last_name=data.last_name,
        email=data.email,
        phone=data.phone,
        resume_file_id=data.resume_file_id,
        cover_letter=data.cover_letter,
        current_employer=data.current_employer,
        current_job_title=data.current_job_title,
        years_of_experience=data.years_of_experience,
        highest_qualification=data.highest_qualification,
        skills=data.skills,
        city=data.city,
        country_code=data.country_code,
        captcha_token=data.captcha_token,
        client_ip=client_ip,
    )

    if not result.success:
        raise HTTPException(status_code=400, detail=result.error)

    return ApplicationSubmitResponse(
        application_number=result.application_number or "",
        message=f"Application submitted successfully. Reference: {result.application_number}",
    )


# ═══════════════════════════════════════════════════════════════════════════
# Status Checking
# ═══════════════════════════════════════════════════════════════════════════


@router.post("/{org_slug}/status/request", response_model=StatusCheckResponse)
async def request_status_check(
    request: Request,
    org_slug: str,
    data: StatusCheckRequest,
    db: Session = Depends(get_db),
):
    check_rate_limit(request, max_requests=3, window_seconds=60)
    ctx, service = _require_org(org_slug, db)

    service.request_status_check(ctx.org_id, data.email, data.application_number)

    return StatusCheckResponse(
        message="If an application exists with this email, you will receive a verification email shortly."
    )


@router.get("/{org_slug}/status/{token}", response_model=ApplicationStatusResponse)
def get_application_status(org_slug: str, token: str, db: Session = Depends(get_db)):
    ctx, service = _require_org(org_slug, db)
    status_info = service.verify_status_token(ctx.org_id, token)

    if not status_info:
        raise HTTPException(
            status_code=404, detail="Invalid or expired verification link"
        )

    return ApplicationStatusResponse(**status_info)


@router.get("/{org_slug}/captcha-config")
def get_captcha_config(org_slug: str, db: Session = Depends(get_db)):
    ctx, service = _require_org(org_slug, db)
    return service.get_captcha_config()
