"""
Public Careers Web Routes - Server-rendered job portal pages.

These routes serve HTML pages for the public careers portal.
No authentication required.
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.middleware.rate_limit import check_rate_limit
from app.services.careers.captcha import get_captcha_site_key, is_captcha_enabled
from app.services.careers.web import CareersWebService
from app.templates import templates

router = APIRouter(prefix="/careers", tags=["careers-web"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _get_service(db: Session) -> CareersWebService:
    return CareersWebService(db)


def _require_org_ctx(slug: str, db: Session) -> tuple:
    """Get org context or raise 404."""
    service = _get_service(db)
    ctx = service.get_organization_context(slug)
    if not ctx:
        raise HTTPException(status_code=404, detail="Organization not found")
    return ctx, service


# ═══════════════════════════════════════════════════════════════════════════
# Job Listings
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/{org_slug}", response_class=HTMLResponse)
@router.get("/{org_slug}/jobs", response_class=HTMLResponse)
def job_list_page(
    request: Request,
    org_slug: str,
    search: Optional[str] = None,
    department_id: Optional[uuid.UUID] = None,
    location: Optional[str] = None,
    employment_type: Optional[str] = None,
    is_remote: Optional[bool] = None,
    page: int = 1,
    db: Session = Depends(get_db),
):
    ctx, service = _require_org_ctx(org_slug, db)
    result = service.list_jobs(
        ctx.org_id,
        search=search,
        department_id=department_id,
        location=location,
        employment_type=employment_type,
        is_remote=is_remote,
        page=page,
    )

    return templates.TemplateResponse(
        "careers/job_list.html",
        {
            "request": request,
            "org": ctx.org,
            "org_slug": ctx.org_slug,
            "org_name": ctx.org_name,
            "org_logo": ctx.org_logo,
            "brand": ctx.brand,
            "jobs": result.jobs,
            "total": result.total,
            "page": result.page,
            "page_size": result.page_size,
            "total_pages": result.total_pages,
            "search": search or "",
            "department_id": department_id,
            "location": location or "",
            "employment_type": employment_type or "",
            "is_remote": is_remote,
            "departments": result.departments,
            "locations": result.locations,
            "employment_types": [
                ("FULL_TIME", "Full Time"),
                ("PART_TIME", "Part Time"),
                ("CONTRACT", "Contract"),
                ("INTERNSHIP", "Internship"),
            ],
        },
    )


@router.get("/{org_slug}/jobs/{job_code}", response_class=HTMLResponse)
def job_detail_page(
    request: Request,
    org_slug: str,
    job_code: str,
    db: Session = Depends(get_db),
):
    ctx, service = _require_org_ctx(org_slug, db)
    job = service.get_job_by_code(ctx.org_id, job_code)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return templates.TemplateResponse(
        "careers/job_detail.html",
        {
            "request": request,
            "org": ctx.org,
            "org_slug": ctx.org_slug,
            "org_name": ctx.org_name,
            "org_logo": ctx.org_logo,
            "brand": ctx.brand,
            "job": job,
        },
    )


# ═══════════════════════════════════════════════════════════════════════════
# Application Form
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/{org_slug}/apply/{job_code}", response_class=HTMLResponse)
def apply_form_page(
    request: Request,
    org_slug: str,
    job_code: str,
    db: Session = Depends(get_db),
):
    ctx, service = _require_org_ctx(org_slug, db)
    job = service.get_job_by_code(ctx.org_id, job_code)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return templates.TemplateResponse(
        "careers/apply.html",
        {
            "request": request,
            "org": ctx.org,
            "org_slug": ctx.org_slug,
            "org_name": ctx.org_name,
            "org_logo": ctx.org_logo,
            "brand": ctx.brand,
            "job": job,
            "captcha_enabled": is_captcha_enabled(),
            "captcha_site_key": get_captcha_site_key(),
            "error": None,
            "form_data": None,
        },
    )


@router.post("/{org_slug}/apply/{job_code}", response_class=HTMLResponse)
async def submit_application(
    request: Request,
    org_slug: str,
    job_code: str,
    first_name: str = Form(...),
    last_name: str = Form(...),
    email: str = Form(...),
    phone: Optional[str] = Form(None),
    cover_letter: Optional[str] = Form(None),
    current_employer: Optional[str] = Form(None),
    current_job_title: Optional[str] = Form(None),
    years_of_experience: Optional[int] = Form(None),
    highest_qualification: Optional[str] = Form(None),
    skills: Optional[str] = Form(None),
    city: Optional[str] = Form(None),
    country_code: Optional[str] = Form(None),
    resume: Optional[UploadFile] = None,
    captcha_token: Optional[str] = Form(None, alias="cf-turnstile-response"),
    db: Session = Depends(get_db),
):
    check_rate_limit(request, max_requests=3, window_seconds=300)
    ctx, service = _require_org_ctx(org_slug, db)

    job = service.get_job_by_code(ctx.org_id, job_code)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Handle resume upload
    resume_file_id = None
    error = None
    if resume and resume.filename:
        content = await resume.read()
        resume_file_id, error = await service.upload_resume(ctx.org_id, resume.filename, content)

    # Submit if no upload error
    if not error:
        client_ip = request.client.host if request.client else None
        result = await service.submit_application(
            ctx.org_id,
            job_code,
            first_name=first_name,
            last_name=last_name,
            email=email,
            phone=phone,
            resume_file_id=resume_file_id,
            cover_letter=cover_letter,
            current_employer=current_employer,
            current_job_title=current_job_title,
            years_of_experience=years_of_experience,
            highest_qualification=highest_qualification,
            skills=skills,
            city=city,
            country_code=country_code,
            captcha_token=captcha_token,
            client_ip=client_ip,
        )

        if result.success:
            return RedirectResponse(
                url=f"/careers/{org_slug}/confirm/{result.application_number}",
                status_code=303,
            )
        error = result.error

    # Re-render form with error
    return templates.TemplateResponse(
        "careers/apply.html",
        {
            "request": request,
            "org": ctx.org,
            "org_slug": ctx.org_slug,
            "org_name": ctx.org_name,
            "org_logo": ctx.org_logo,
            "brand": ctx.brand,
            "job": job,
            "captcha_enabled": is_captcha_enabled(),
            "captcha_site_key": get_captcha_site_key(),
            "error": error,
            "form_data": {
                "first_name": first_name,
                "last_name": last_name,
                "email": email,
                "phone": phone,
                "cover_letter": cover_letter,
                "current_employer": current_employer,
                "current_job_title": current_job_title,
                "years_of_experience": years_of_experience,
                "highest_qualification": highest_qualification,
                "skills": skills,
                "city": city,
                "country_code": country_code,
            },
        },
    )


@router.get("/{org_slug}/confirm/{application_number}", response_class=HTMLResponse)
def confirmation_page(
    request: Request,
    org_slug: str,
    application_number: str,
    db: Session = Depends(get_db),
):
    ctx, _ = _require_org_ctx(org_slug, db)

    return templates.TemplateResponse(
        "careers/confirmation.html",
        {
            "request": request,
            "org": ctx.org,
            "org_slug": ctx.org_slug,
            "org_name": ctx.org_name,
            "org_logo": ctx.org_logo,
            "brand": ctx.brand,
            "application_number": application_number,
        },
    )


# ═══════════════════════════════════════════════════════════════════════════
# Status Checking
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/{org_slug}/status", response_class=HTMLResponse)
def status_form_page(
    request: Request,
    org_slug: str,
    db: Session = Depends(get_db),
):
    ctx, _ = _require_org_ctx(org_slug, db)

    return templates.TemplateResponse(
        "careers/status_form.html",
        {
            "request": request,
            "org": ctx.org,
            "org_slug": ctx.org_slug,
            "org_name": ctx.org_name,
            "org_logo": ctx.org_logo,
            "brand": ctx.brand,
            "message": None,
            "error": None,
        },
    )


@router.post("/{org_slug}/status", response_class=HTMLResponse)
async def request_status_check(
    request: Request,
    org_slug: str,
    email: str = Form(...),
    application_number: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    check_rate_limit(request, max_requests=3, window_seconds=60)
    ctx, service = _require_org_ctx(org_slug, db)

    service.request_status_check(ctx.org_id, email, application_number or None)

    return templates.TemplateResponse(
        "careers/status_form.html",
        {
            "request": request,
            "org": ctx.org,
            "org_slug": ctx.org_slug,
            "org_name": ctx.org_name,
            "org_logo": ctx.org_logo,
            "brand": ctx.brand,
            "message": "If an application exists with this email, you will receive a verification email shortly.",
            "error": None,
        },
    )


@router.get("/{org_slug}/status/{token}", response_class=HTMLResponse)
def status_detail_page(
    request: Request,
    org_slug: str,
    token: str,
    db: Session = Depends(get_db),
):
    ctx, service = _require_org_ctx(org_slug, db)
    status_info = service.verify_status_token(ctx.org_id, token)

    if not status_info:
        return templates.TemplateResponse(
            "careers/status_expired.html",
            {
                "request": request,
                "org": ctx.org,
                "org_slug": ctx.org_slug,
                "org_name": ctx.org_name,
                "org_logo": ctx.org_logo,
                "brand": ctx.brand,
            },
        )

    return templates.TemplateResponse(
        "careers/status_detail.html",
        {
            "request": request,
            "org": ctx.org,
            "org_slug": ctx.org_slug,
            "org_name": ctx.org_name,
            "org_logo": ctx.org_logo,
            "brand": ctx.brand,
            "status": status_info,
        },
    )
