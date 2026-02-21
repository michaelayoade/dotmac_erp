"""
Public Careers Web Routes - Server-rendered job portal pages.

These routes serve HTML pages for the public careers portal.
No authentication required.
"""

import logging
import secrets
import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.middleware.rate_limit import check_rate_limit
from app.models.person import Person, PersonStatus
from app.models.rbac import PersonRole, Role
from app.services.careers.captcha import get_captcha_site_key, is_captcha_enabled
from app.services.careers.web import CareersWebService
from app.services.people.recruit.offer_letter_service import OfferLetterService
from app.templates import templates
from app.web.csrf import CSRF_COOKIE_NAME, _is_secure_request

router = APIRouter(prefix="/careers", tags=["careers-web"])
short_router = APIRouter(prefix="/c", tags=["careers-web"])
logger = logging.getLogger(__name__)


def get_db():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _get_service(db: Session) -> CareersWebService:
    return CareersWebService(db)


def _ensure_csrf_token(request: Request) -> tuple[str, bool]:
    token = getattr(request.state, "csrf_token", "") or request.cookies.get(
        CSRF_COOKIE_NAME, ""
    )
    if token:
        return token, False
    return secrets.token_urlsafe(32), True


def _render_with_csrf(
    request: Request, template_name: str, context: dict
) -> HTMLResponse:
    token, set_cookie = _ensure_csrf_token(request)
    response = templates.TemplateResponse(
        template_name,
        {
            **context,
            "csrf_token": token,
        },
    )
    if set_cookie:
        response.set_cookie(
            CSRF_COOKIE_NAME,
            token,
            httponly=True,
            secure=_is_secure_request(request),
            samesite="Lax",
            path="/",
        )
    return response


def _require_org_ctx(slug: str, db: Session) -> tuple:
    """Get org context or raise 404."""
    service = _get_service(db)
    ctx = service.get_organization_context(slug)
    if not ctx:
        raise HTTPException(status_code=404, detail="Organization not found")
    return ctx, service


def _resolve_offer_letter_user_id(db: Session, org_id: uuid.UUID) -> uuid.UUID:
    """Best-effort user id for offer letter generation."""
    stmt = (
        select(Person.id)
        .join(PersonRole, PersonRole.person_id == Person.id)
        .join(Role, PersonRole.role_id == Role.id)
        .where(Person.organization_id == org_id)
        .where(Person.status == PersonStatus.active)
        .where(Person.is_active.is_(True))
        .where(Role.name == "admin")
        .limit(1)
    )
    person_id = db.scalar(stmt)
    return person_id or org_id


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
                    "Invalid department_id ignored on careers page: %s", value
                )
    return department_ids


# ═══════════════════════════════════════════════════════════════════════════
# Job Listings
# ═══════════════════════════════════════════════════════════════════════════


@short_router.get("/{org_slug}", include_in_schema=False)
def careers_short_link_redirect(request: Request, org_slug: str) -> RedirectResponse:
    """Short link redirect to public careers portal."""
    url = f"/careers/{org_slug}"
    if request.url.query:
        url = f"{url}?{request.url.query}"
    return RedirectResponse(url=url, status_code=302)


@router.get("/{org_slug}", response_class=HTMLResponse)
@router.get("/{org_slug}/jobs", response_class=HTMLResponse)
def job_list_page(
    request: Request,
    org_slug: str,
    search: str | None = None,
    department_id: str | None = None,
    location: str | None = None,
    employment_type: str | None = None,
    is_remote: bool | None = None,
    page: int = 1,
    db: Session = Depends(get_db),
):
    department_ids = _parse_department_ids(request)
    ctx, service = _require_org_ctx(org_slug, db)
    result = service.list_jobs(
        ctx.org_id,
        search=search,
        department_id=department_ids or None,
        location=location,
        employment_type=employment_type,
        is_remote=is_remote,
        page=page,
    )
    department_query = "".join(
        f"&department_id={department_id}" for department_id in department_ids
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
            "selected_department_ids": department_ids,
            "department_query": department_query,
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

    return _render_with_csrf(
        request,
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
    phone: str | None = Form(None),
    cover_letter: str | None = Form(None),
    current_employer: str | None = Form(None),
    current_job_title: str | None = Form(None),
    years_of_experience: int | None = Form(None),
    highest_qualification: str | None = Form(None),
    skills: str | None = Form(None),
    city: str | None = Form(None),
    country_code: str | None = Form(None),
    resume: UploadFile | None = None,
    captcha_token: str | None = Form(None, alias="cf-turnstile-response"),
    db: Session = Depends(get_db),
):
    check_rate_limit(request, max_requests=10, window_seconds=300, key_suffix=email)
    ctx, service = _require_org_ctx(org_slug, db)

    job = service.get_job_by_code(ctx.org_id, job_code)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Handle resume upload
    resume_file_id = None
    error = None
    if resume and resume.filename:
        content = await resume.read()
        resume_file_id, error = await service.upload_resume(
            ctx.org_id, resume.filename, content
        )

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
                url=f"/careers/{org_slug}/confirm/{result.application_number}?saved=1",
                status_code=303,
            )
        error = result.error

    # Re-render form with error
    return _render_with_csrf(
        request,
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

    return _render_with_csrf(
        request,
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
    application_number: str | None = Form(None),
    db: Session = Depends(get_db),
):
    check_rate_limit(request, max_requests=3, window_seconds=60)
    ctx, service = _require_org_ctx(org_slug, db)

    service.request_status_check(ctx.org_id, email, application_number or None)

    return _render_with_csrf(
        request,
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


# ═══════════════════════════════════════════════════════════════════════════
# Offer Portal
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/{org_slug}/offer/{token}", response_class=HTMLResponse)
def offer_portal_page(
    request: Request,
    org_slug: str,
    token: str,
    db: Session = Depends(get_db),
):
    ctx, service = _require_org_ctx(org_slug, db)
    offer = service._careers_service.get_offer_by_token(ctx.org_id, token)
    if not offer:
        return templates.TemplateResponse(
            "careers/offer_expired.html",
            {
                "request": request,
                "org": ctx.org,
                "org_slug": ctx.org_slug,
                "org_name": ctx.org_name,
                "org_logo": ctx.org_logo,
                "brand": ctx.brand,
            },
        )

    status_message = request.query_params.get("message")
    return _render_with_csrf(
        request,
        "careers/offer_portal.html",
        {
            "request": request,
            "org": ctx.org,
            "org_slug": ctx.org_slug,
            "org_name": ctx.org_name,
            "org_logo": ctx.org_logo,
            "brand": ctx.brand,
            "offer": offer,
            "message": status_message,
        },
    )


@router.get("/{org_slug}/offer/{token}/pdf")
def offer_portal_pdf(
    request: Request,
    org_slug: str,
    token: str,
    db: Session = Depends(get_db),
):
    ctx, service = _require_org_ctx(org_slug, db)
    offer = service._careers_service.get_offer_by_token(ctx.org_id, token)
    if not offer:
        raise HTTPException(status_code=404, detail="Offer not found or expired")

    user_id = offer.created_by_id or _resolve_offer_letter_user_id(db, ctx.org_id)
    letter_service = OfferLetterService(db)
    try:
        letter_service._ensure_default_template(ctx.org_id, user_id)
    except Exception:
        pass
    pdf_bytes, doc = letter_service.generate_offer_letter(offer.offer_id, user_id)
    filename = doc.document_number or f"OFFER-{offer.offer_number}"

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}.pdf"',
        },
    )


@router.post("/{org_slug}/offer/{token}/accept", response_class=HTMLResponse)
def offer_portal_accept(
    request: Request,
    org_slug: str,
    token: str,
    db: Session = Depends(get_db),
):
    check_rate_limit(request, max_requests=5, window_seconds=300, key_suffix=token)
    ctx, service = _require_org_ctx(org_slug, db)
    existing = service._careers_service.get_offer_by_token(ctx.org_id, token)
    if not existing:
        raise HTTPException(status_code=404, detail="Offer not found or expired")
    if existing.status.value == "WITHDRAWN":
        return RedirectResponse(
            url=(
                f"/careers/{org_slug}/offer/{token}"
                "?message=This+offer+has+been+withdrawn+by+the+company"
            ),
            status_code=303,
        )
    try:
        offer = service._careers_service.accept_offer_by_token(ctx.org_id, token)
    except Exception as exc:
        error_msg = str(exc).lower()
        if "expired" in error_msg:
            return RedirectResponse(
                url=(f"/careers/{org_slug}/offer/{token}?message=Offer+has+expired"),
                status_code=303,
            )
        raise
    if not offer:
        raise HTTPException(status_code=404, detail="Offer not found or expired")
    return RedirectResponse(
        url=(
            f"/careers/{org_slug}/offer/{token}?message=Your+response+has+been+recorded"
        ),
        status_code=303,
    )


@router.post("/{org_slug}/offer/{token}/decline", response_class=HTMLResponse)
def offer_portal_decline(
    request: Request,
    org_slug: str,
    token: str,
    reason: str | None = Form(None),
    db: Session = Depends(get_db),
):
    check_rate_limit(request, max_requests=5, window_seconds=300, key_suffix=token)
    ctx, service = _require_org_ctx(org_slug, db)
    existing = service._careers_service.get_offer_by_token(ctx.org_id, token)
    if not existing:
        raise HTTPException(status_code=404, detail="Offer not found or expired")
    if existing.status.value == "WITHDRAWN":
        return RedirectResponse(
            url=(
                f"/careers/{org_slug}/offer/{token}"
                "?message=This+offer+has+been+withdrawn+by+the+company"
            ),
            status_code=303,
        )
    offer = service._careers_service.decline_offer_by_token(
        ctx.org_id, token, reason=reason
    )
    if not offer:
        raise HTTPException(status_code=404, detail="Offer not found or expired")
    return RedirectResponse(
        url=(
            f"/careers/{org_slug}/offer/{token}?message=Your+response+has+been+recorded"
        ),
        status_code=303,
    )
