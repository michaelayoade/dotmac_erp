"""
Recruitment web routes.

Lists job openings, applicants, interviews, and job offers with full CRUD.
"""
from datetime import date, datetime, time
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.models.people.recruit import (
    ApplicantStatus,
    InterviewStatus,
    JobOpeningStatus,
    OfferStatus,
)
from app.services.common import PaginationParams, coerce_uuid
from app.services.people.hr import DepartmentFilters, DesignationFilters, EmployeeFilters, OrganizationService
from app.services.people.recruit import RecruitmentService
from app.templates import templates
from app.web.deps import WebAuthContext, base_context, get_db, require_hr_access


router = APIRouter(prefix="/recruit", tags=["people-recruit-web"])


def _parse_uuid(value: Optional[str]) -> Optional[UUID]:
    if not value:
        return None
    try:
        return coerce_uuid(value)
    except Exception:
        return None


def _parse_date(value: Optional[str], *, end_of_day: bool = False) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = date.fromisoformat(value)
        if end_of_day:
            return datetime.combine(parsed, time.max)
        return datetime.combine(parsed, time.min)
    except ValueError:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None


@router.get("", include_in_schema=False)
def recruit_root() -> RedirectResponse:
    return RedirectResponse(url="/people/recruit/jobs")


@router.get("/jobs", response_class=HTMLResponse)
def list_job_openings(
    request: Request,
    search: Optional[str] = None,
    status: Optional[str] = None,
    department_id: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Job openings list page."""
    org_id = coerce_uuid(auth.organization_id)
    pagination = PaginationParams.from_page(page, per_page=20)
    svc = RecruitmentService(db)

    status_enum = None
    if status:
        try:
            status_enum = JobOpeningStatus(status)
        except ValueError:
            status_enum = None

    result = svc.list_job_openings(
        org_id,
        search=search,
        status=status_enum,
        department_id=_parse_uuid(department_id),
        pagination=pagination,
    )

    org_svc = OrganizationService(db, org_id)
    departments = org_svc.list_departments(
        DepartmentFilters(is_active=True),
        PaginationParams(limit=200),
    ).items

    context = base_context(request, auth, "Job Openings", "recruit", db=db)
    context["request"] = request
    context.update(
        {
            "job_openings": result.items,
            "departments": departments,
            "search": search,
            "status": status,
            "department_id": department_id,
            "statuses": [s.value for s in JobOpeningStatus],
            "page": result.page,
            "total_pages": result.total_pages,
            "total": result.total,
            "has_prev": result.has_prev,
            "has_next": result.has_next,
        }
    )
    return templates.TemplateResponse(request, "people/recruit/job_openings.html", context)


@router.get("/jobs/new", response_class=HTMLResponse)
def new_job_opening_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New job opening form."""
    org_id = coerce_uuid(auth.organization_id)
    org_svc = OrganizationService(db, org_id)

    departments = org_svc.list_departments(
        DepartmentFilters(is_active=True),
        PaginationParams(limit=200),
    ).items
    designations = org_svc.list_designations(
        DesignationFilters(is_active=True),
        PaginationParams(limit=200),
    ).items
    managers = org_svc.list_employees(
        EmployeeFilters(is_active=True),
        PaginationParams(limit=500),
    ).items

    context = base_context(request, auth, "New Job Opening", "recruit", db=db)
    context["request"] = request
    context.update(
        {
            "departments": departments,
            "designations": designations,
            "managers": managers,
            "form_data": {},
        }
    )
    return templates.TemplateResponse(request, "people/recruit/job_opening_form.html", context)


@router.post("/jobs/new", response_class=HTMLResponse)
def create_job_opening(
    request: Request,
    job_code: str = Form(...),
    job_title: str = Form(...),
    department_id: Optional[str] = Form(None),
    designation_id: Optional[str] = Form(None),
    reports_to_id: Optional[str] = Form(None),
    employment_type: str = Form("FULL_TIME"),
    number_of_positions: int = Form(1),
    location: Optional[str] = Form(None),
    is_remote: Optional[str] = Form(None),
    currency_code: str = Form("NGN"),
    min_salary: Optional[str] = Form(None),
    max_salary: Optional[str] = Form(None),
    posted_on: Optional[str] = Form(None),
    closes_on: Optional[str] = Form(None),
    min_experience_years: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    required_skills: Optional[str] = Form(None),
    preferred_skills: Optional[str] = Form(None),
    education_requirements: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create a new job opening."""
    org_id = coerce_uuid(auth.organization_id)
    svc = RecruitmentService(db)

    try:
        opening = svc.create_job_opening(
            org_id,
            job_code=job_code,
            job_title=job_title,
            department_id=coerce_uuid(department_id) if department_id else None,
            designation_id=coerce_uuid(designation_id) if designation_id else None,
            reports_to_id=coerce_uuid(reports_to_id) if reports_to_id else None,
            employment_type=employment_type,
            number_of_positions=number_of_positions,
            location=location or None,
            is_remote=is_remote == "true",
            currency_code=currency_code,
            min_salary=Decimal(min_salary) if min_salary else None,
            max_salary=Decimal(max_salary) if max_salary else None,
            posted_on=date.fromisoformat(posted_on) if posted_on else None,
            closes_on=date.fromisoformat(closes_on) if closes_on else None,
            min_experience_years=int(min_experience_years) if min_experience_years else None,
            description=description or None,
            required_skills=required_skills or None,
            preferred_skills=preferred_skills or None,
            education_requirements=education_requirements or None,
        )
        db.commit()
        return RedirectResponse(
            url=f"/people/recruit/jobs/{opening.job_opening_id}", status_code=303
        )
    except Exception as e:
        db.rollback()
        org_svc = OrganizationService(db, org_id)

        context = base_context(request, auth, "New Job Opening", "recruit", db=db)
        context["request"] = request
        context.update(
            {
                "departments": org_svc.list_departments(
                    DepartmentFilters(is_active=True), PaginationParams(limit=200)
                ).items,
                "designations": org_svc.list_designations(
                    DesignationFilters(is_active=True), PaginationParams(limit=200)
                ).items,
                "managers": org_svc.list_employees(
                    EmployeeFilters(is_active=True), PaginationParams(limit=500)
                ).items,
                "form_data": {
                    "job_code": job_code,
                    "job_title": job_title,
                    "department_id": department_id,
                    "designation_id": designation_id,
                    "reports_to_id": reports_to_id,
                    "employment_type": employment_type,
                    "number_of_positions": number_of_positions,
                    "location": location,
                    "is_remote": is_remote,
                    "currency_code": currency_code,
                    "min_salary": min_salary,
                    "max_salary": max_salary,
                    "posted_on": posted_on,
                    "closes_on": closes_on,
                    "min_experience_years": min_experience_years,
                    "description": description,
                    "required_skills": required_skills,
                    "preferred_skills": preferred_skills,
                    "education_requirements": education_requirements,
                },
                "error": str(e),
            }
        )
        return templates.TemplateResponse(request, "people/recruit/job_opening_form.html", context)


@router.get("/jobs/{job_opening_id}", response_class=HTMLResponse)
def job_opening_detail(
    request: Request,
    job_opening_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Job opening detail page."""
    org_id = coerce_uuid(auth.organization_id)
    svc = RecruitmentService(db)

    try:
        opening = svc.get_job_opening(org_id, coerce_uuid(job_opening_id))
    except Exception:
        return RedirectResponse(url="/people/recruit/jobs", status_code=303)

    # Get applicant count
    applicants = svc.list_applicants(
        org_id, job_opening_id=coerce_uuid(job_opening_id), pagination=PaginationParams(limit=1)
    )

    context = base_context(request, auth, opening.job_title, "recruit", db=db)
    context["request"] = request
    context["opening"] = opening
    context["applicants_count"] = applicants.total
    return templates.TemplateResponse(request, "people/recruit/job_opening_detail.html", context)


@router.get("/jobs/{job_opening_id}/edit", response_class=HTMLResponse)
def edit_job_opening_form(
    request: Request,
    job_opening_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Edit job opening form."""
    org_id = coerce_uuid(auth.organization_id)
    svc = RecruitmentService(db)
    org_svc = OrganizationService(db, org_id)

    try:
        opening = svc.get_job_opening(org_id, coerce_uuid(job_opening_id))
    except Exception:
        return RedirectResponse(url="/people/recruit/jobs", status_code=303)

    context = base_context(request, auth, "Edit Job Opening", "recruit", db=db)
    context["request"] = request
    context.update(
        {
            "opening": opening,
            "departments": org_svc.list_departments(
                DepartmentFilters(is_active=True), PaginationParams(limit=200)
            ).items,
            "designations": org_svc.list_designations(
                DesignationFilters(is_active=True), PaginationParams(limit=200)
            ).items,
            "managers": org_svc.list_employees(
                EmployeeFilters(is_active=True), PaginationParams(limit=500)
            ).items,
            "form_data": {},
        }
    )
    return templates.TemplateResponse(request, "people/recruit/job_opening_form.html", context)


@router.post("/jobs/{job_opening_id}/edit", response_class=HTMLResponse)
def update_job_opening(
    request: Request,
    job_opening_id: str,
    job_code: str = Form(...),
    job_title: str = Form(...),
    department_id: Optional[str] = Form(None),
    designation_id: Optional[str] = Form(None),
    reports_to_id: Optional[str] = Form(None),
    employment_type: str = Form("FULL_TIME"),
    number_of_positions: int = Form(1),
    location: Optional[str] = Form(None),
    is_remote: Optional[str] = Form(None),
    currency_code: str = Form("NGN"),
    min_salary: Optional[str] = Form(None),
    max_salary: Optional[str] = Form(None),
    posted_on: Optional[str] = Form(None),
    closes_on: Optional[str] = Form(None),
    min_experience_years: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    required_skills: Optional[str] = Form(None),
    preferred_skills: Optional[str] = Form(None),
    education_requirements: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Update a job opening."""
    org_id = coerce_uuid(auth.organization_id)
    svc = RecruitmentService(db)

    try:
        svc.update_job_opening(
            org_id,
            coerce_uuid(job_opening_id),
            job_code=job_code,
            job_title=job_title,
            department_id=coerce_uuid(department_id) if department_id else None,
            designation_id=coerce_uuid(designation_id) if designation_id else None,
            reports_to_id=coerce_uuid(reports_to_id) if reports_to_id else None,
            employment_type=employment_type,
            number_of_positions=number_of_positions,
            location=location or None,
            is_remote=is_remote == "true",
            currency_code=currency_code,
            min_salary=Decimal(min_salary) if min_salary else None,
            max_salary=Decimal(max_salary) if max_salary else None,
            posted_on=date.fromisoformat(posted_on) if posted_on else None,
            closes_on=date.fromisoformat(closes_on) if closes_on else None,
            min_experience_years=int(min_experience_years) if min_experience_years else None,
            description=description or None,
            required_skills=required_skills or None,
            preferred_skills=preferred_skills or None,
            education_requirements=education_requirements or None,
        )
        db.commit()
        return RedirectResponse(
            url=f"/people/recruit/jobs/{job_opening_id}", status_code=303
        )
    except Exception as e:
        db.rollback()
        org_svc = OrganizationService(db, org_id)
        opening = svc.get_job_opening(org_id, coerce_uuid(job_opening_id))

        context = base_context(request, auth, "Edit Job Opening", "recruit", db=db)
        context["request"] = request
        context.update(
            {
                "opening": opening,
                "departments": org_svc.list_departments(
                    DepartmentFilters(is_active=True), PaginationParams(limit=200)
                ).items,
                "designations": org_svc.list_designations(
                    DesignationFilters(is_active=True), PaginationParams(limit=200)
                ).items,
                "managers": org_svc.list_employees(
                    EmployeeFilters(is_active=True), PaginationParams(limit=500)
                ).items,
                "form_data": {},
                "error": str(e),
            }
        )
        return templates.TemplateResponse(request, "people/recruit/job_opening_form.html", context)


@router.post("/jobs/{job_opening_id}/publish")
def publish_job_opening(
    request: Request,
    job_opening_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Publish a job opening."""
    org_id = coerce_uuid(auth.organization_id)
    svc = RecruitmentService(db)

    try:
        svc.publish_job_opening(org_id, coerce_uuid(job_opening_id))
        db.commit()
    except Exception:
        db.rollback()

    return RedirectResponse(url=f"/people/recruit/jobs/{job_opening_id}", status_code=303)


@router.post("/jobs/{job_opening_id}/hold")
def hold_job_opening(
    request: Request,
    job_opening_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Put a job opening on hold."""
    org_id = coerce_uuid(auth.organization_id)
    svc = RecruitmentService(db)

    try:
        svc.update_job_opening(org_id, coerce_uuid(job_opening_id), status=JobOpeningStatus.ON_HOLD)
        db.commit()
    except Exception:
        db.rollback()

    return RedirectResponse(url=f"/people/recruit/jobs/{job_opening_id}", status_code=303)


@router.post("/jobs/{job_opening_id}/reopen")
def reopen_job_opening(
    request: Request,
    job_opening_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Reopen a job opening."""
    org_id = coerce_uuid(auth.organization_id)
    svc = RecruitmentService(db)

    try:
        svc.update_job_opening(org_id, coerce_uuid(job_opening_id), status=JobOpeningStatus.OPEN)
        db.commit()
    except Exception:
        db.rollback()

    return RedirectResponse(url=f"/people/recruit/jobs/{job_opening_id}", status_code=303)


@router.post("/jobs/{job_opening_id}/close")
def close_job_opening(
    request: Request,
    job_opening_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Close a job opening."""
    org_id = coerce_uuid(auth.organization_id)
    svc = RecruitmentService(db)

    try:
        svc.update_job_opening(org_id, coerce_uuid(job_opening_id), status=JobOpeningStatus.CLOSED)
        db.commit()
    except Exception:
        db.rollback()

    return RedirectResponse(url=f"/people/recruit/jobs/{job_opening_id}", status_code=303)


@router.get("/applicants", response_class=HTMLResponse)
def list_applicants(
    request: Request,
    search: Optional[str] = None,
    status: Optional[str] = None,
    job_opening_id: Optional[str] = None,
    source: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Job applicants list page."""
    org_id = coerce_uuid(auth.organization_id)
    pagination = PaginationParams.from_page(page, per_page=20)
    svc = RecruitmentService(db)

    status_enum = None
    if status:
        try:
            status_enum = ApplicantStatus(status)
        except ValueError:
            status_enum = None

    result = svc.list_applicants(
        org_id,
        search=search,
        status=status_enum,
        job_opening_id=_parse_uuid(job_opening_id),
        source=source,
        pagination=pagination,
    )

    job_openings = svc.list_job_openings(
        org_id,
        pagination=PaginationParams(limit=200),
    ).items

    context = base_context(request, auth, "Applicants", "recruit", db=db)
    context["request"] = request
    context.update(
        {
            "applicants": result.items,
            "job_openings": job_openings,
            "search": search,
            "status": status,
            "job_opening_id": job_opening_id,
            "source": source,
            "statuses": [s.value for s in ApplicantStatus],
            "page": result.page,
            "total_pages": result.total_pages,
            "total": result.total,
            "has_prev": result.has_prev,
            "has_next": result.has_next,
        }
    )
    return templates.TemplateResponse(request, "people/recruit/applicants.html", context)


@router.get("/applicants/new", response_class=HTMLResponse)
def new_applicant_form(
    request: Request,
    job_opening_id: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New applicant form."""
    org_id = coerce_uuid(auth.organization_id)
    svc = RecruitmentService(db)

    job_openings = svc.list_job_openings(
        org_id, status=JobOpeningStatus.OPEN, pagination=PaginationParams(limit=200)
    ).items

    context = base_context(request, auth, "New Applicant", "recruit", db=db)
    context["request"] = request
    context.update(
        {
            "job_openings": job_openings,
            "form_data": {"job_opening_id": job_opening_id} if job_opening_id else {},
        }
    )
    return templates.TemplateResponse(request, "people/recruit/applicant_form.html", context)


@router.post("/applicants/new", response_class=HTMLResponse)
def create_applicant(
    request: Request,
    job_opening_id: str = Form(...),
    first_name: str = Form(...),
    last_name: str = Form(...),
    email: str = Form(...),
    phone: Optional[str] = Form(None),
    date_of_birth: Optional[str] = Form(None),
    gender: Optional[str] = Form(None),
    city: Optional[str] = Form(None),
    country_code: Optional[str] = Form(None),
    current_employer: Optional[str] = Form(None),
    current_job_title: Optional[str] = Form(None),
    years_of_experience: Optional[str] = Form(None),
    highest_qualification: Optional[str] = Form(None),
    skills: Optional[str] = Form(None),
    source: Optional[str] = Form(None),
    cover_letter: Optional[str] = Form(None),
    resume_url: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create a new applicant."""
    org_id = coerce_uuid(auth.organization_id)
    svc = RecruitmentService(db)

    try:
        applicant = svc.create_applicant(
            org_id,
            job_opening_id=coerce_uuid(job_opening_id),
            first_name=first_name,
            last_name=last_name,
            email=email,
            phone=phone or None,
            date_of_birth=date.fromisoformat(date_of_birth) if date_of_birth else None,
            gender=gender or None,
            city=city or None,
            country_code=country_code or None,
            current_employer=current_employer or None,
            current_job_title=current_job_title or None,
            years_of_experience=int(years_of_experience) if years_of_experience else None,
            highest_qualification=highest_qualification or None,
            skills=skills or None,
            source=source or None,
            cover_letter=cover_letter or None,
            resume_url=resume_url or None,
        )
        db.commit()
        return RedirectResponse(
            url=f"/people/recruit/applicants/{applicant.applicant_id}", status_code=303
        )
    except Exception as e:
        db.rollback()
        job_openings = svc.list_job_openings(
            org_id, status=JobOpeningStatus.OPEN, pagination=PaginationParams(limit=200)
        ).items

        context = base_context(request, auth, "New Applicant", "recruit", db=db)
        context["request"] = request
        context.update(
            {
                "job_openings": job_openings,
                "form_data": {
                    "job_opening_id": job_opening_id,
                    "first_name": first_name,
                    "last_name": last_name,
                    "email": email,
                    "phone": phone,
                    "date_of_birth": date_of_birth,
                    "gender": gender,
                    "city": city,
                    "country_code": country_code,
                    "current_employer": current_employer,
                    "current_job_title": current_job_title,
                    "years_of_experience": years_of_experience,
                    "highest_qualification": highest_qualification,
                    "skills": skills,
                    "source": source,
                    "cover_letter": cover_letter,
                    "resume_url": resume_url,
                },
                "error": str(e),
            }
        )
        return templates.TemplateResponse(request, "people/recruit/applicant_form.html", context)


@router.get("/applicants/{applicant_id}", response_class=HTMLResponse)
def applicant_detail(
    request: Request,
    applicant_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Applicant detail page."""
    org_id = coerce_uuid(auth.organization_id)
    svc = RecruitmentService(db)

    try:
        applicant = svc.get_applicant(org_id, coerce_uuid(applicant_id))
    except Exception:
        return RedirectResponse(url="/people/recruit/applicants", status_code=303)

    # Get interviews and offers for this applicant
    interviews = svc.list_interviews(
        org_id, applicant_id=coerce_uuid(applicant_id), pagination=PaginationParams(limit=50)
    ).items
    offers = svc.list_job_offers(
        org_id, applicant_id=coerce_uuid(applicant_id), pagination=PaginationParams(limit=10)
    ).items

    context = base_context(request, auth, applicant.full_name, "recruit", db=db)
    context["request"] = request
    context.update(
        {
            "applicant": applicant,
            "interviews": interviews,
            "offers": offers,
            "statuses": [s.value for s in ApplicantStatus],
        }
    )
    return templates.TemplateResponse(request, "people/recruit/applicant_detail.html", context)


@router.get("/applicants/{applicant_id}/edit", response_class=HTMLResponse)
def edit_applicant_form(
    request: Request,
    applicant_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Edit applicant form."""
    org_id = coerce_uuid(auth.organization_id)
    svc = RecruitmentService(db)

    try:
        applicant = svc.get_applicant(org_id, coerce_uuid(applicant_id))
    except Exception:
        return RedirectResponse(url="/people/recruit/applicants", status_code=303)

    job_openings = svc.list_job_openings(
        org_id, pagination=PaginationParams(limit=200)
    ).items

    context = base_context(request, auth, "Edit Applicant", "recruit", db=db)
    context["request"] = request
    context.update(
        {
            "applicant": applicant,
            "job_openings": job_openings,
            "form_data": {},
        }
    )
    return templates.TemplateResponse(request, "people/recruit/applicant_form.html", context)


@router.post("/applicants/{applicant_id}/edit", response_class=HTMLResponse)
def update_applicant(
    request: Request,
    applicant_id: str,
    first_name: str = Form(...),
    last_name: str = Form(...),
    email: str = Form(...),
    phone: Optional[str] = Form(None),
    date_of_birth: Optional[str] = Form(None),
    gender: Optional[str] = Form(None),
    city: Optional[str] = Form(None),
    country_code: Optional[str] = Form(None),
    current_employer: Optional[str] = Form(None),
    current_job_title: Optional[str] = Form(None),
    years_of_experience: Optional[str] = Form(None),
    highest_qualification: Optional[str] = Form(None),
    skills: Optional[str] = Form(None),
    source: Optional[str] = Form(None),
    cover_letter: Optional[str] = Form(None),
    resume_url: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    overall_rating: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Update an applicant."""
    org_id = coerce_uuid(auth.organization_id)
    svc = RecruitmentService(db)

    try:
        svc.update_applicant(
            org_id,
            coerce_uuid(applicant_id),
            first_name=first_name,
            last_name=last_name,
            email=email,
            phone=phone or None,
            date_of_birth=date.fromisoformat(date_of_birth) if date_of_birth else None,
            gender=gender or None,
            city=city or None,
            country_code=country_code or None,
            current_employer=current_employer or None,
            current_job_title=current_job_title or None,
            years_of_experience=int(years_of_experience) if years_of_experience else None,
            highest_qualification=highest_qualification or None,
            skills=skills or None,
            source=source or None,
            cover_letter=cover_letter or None,
            resume_url=resume_url or None,
            notes=notes or None,
            overall_rating=int(overall_rating) if overall_rating else None,
        )
        db.commit()
        return RedirectResponse(
            url=f"/people/recruit/applicants/{applicant_id}", status_code=303
        )
    except Exception as e:
        db.rollback()
        applicant = svc.get_applicant(org_id, coerce_uuid(applicant_id))
        job_openings = svc.list_job_openings(
            org_id, pagination=PaginationParams(limit=200)
        ).items

        context = base_context(request, auth, "Edit Applicant", "recruit", db=db)
        context["request"] = request
        context.update(
            {
                "applicant": applicant,
                "job_openings": job_openings,
                "form_data": {},
                "error": str(e),
            }
        )
        return templates.TemplateResponse(request, "people/recruit/applicant_form.html", context)


@router.post("/applicants/{applicant_id}/advance")
def advance_applicant_status(
    request: Request,
    applicant_id: str,
    to_status: str = Form(...),
    notes: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Advance applicant through pipeline."""
    org_id = coerce_uuid(auth.organization_id)
    svc = RecruitmentService(db)

    try:
        status_enum = ApplicantStatus(to_status)
        svc.advance_applicant(org_id, coerce_uuid(applicant_id), status_enum, notes=notes)
        db.commit()
    except Exception:
        db.rollback()

    return RedirectResponse(url=f"/people/recruit/applicants/{applicant_id}", status_code=303)


@router.post("/applicants/{applicant_id}/reject")
def reject_applicant(
    request: Request,
    applicant_id: str,
    reason: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Reject an applicant."""
    org_id = coerce_uuid(auth.organization_id)
    svc = RecruitmentService(db)

    try:
        svc.reject_applicant(org_id, coerce_uuid(applicant_id), reason=reason)
        db.commit()
    except Exception:
        db.rollback()

    return RedirectResponse(url=f"/people/recruit/applicants/{applicant_id}", status_code=303)


@router.post("/applicants/{applicant_id}/delete")
def delete_applicant(
    request: Request,
    applicant_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Delete an applicant."""
    org_id = coerce_uuid(auth.organization_id)
    svc = RecruitmentService(db)

    try:
        svc.delete_applicant(org_id, coerce_uuid(applicant_id))
        db.commit()
        return RedirectResponse(url="/people/recruit/applicants", status_code=303)
    except Exception:
        db.rollback()
        return RedirectResponse(url=f"/people/recruit/applicants/{applicant_id}", status_code=303)


@router.get("/interviews", response_class=HTMLResponse)
def list_interviews(
    request: Request,
    status: Optional[str] = None,
    job_opening_id: Optional[str] = None,
    applicant_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Interviews list page."""
    org_id = coerce_uuid(auth.organization_id)
    pagination = PaginationParams.from_page(page, per_page=20)
    svc = RecruitmentService(db)

    status_enum = None
    if status:
        try:
            status_enum = InterviewStatus(status)
        except ValueError:
            status_enum = None

    result = svc.list_interviews(
        org_id,
        status=status_enum,
        applicant_id=_parse_uuid(applicant_id),
        job_opening_id=_parse_uuid(job_opening_id),
        from_date=_parse_date(start_date),
        to_date=_parse_date(end_date, end_of_day=True),
        pagination=pagination,
    )

    job_openings = svc.list_job_openings(
        org_id,
        pagination=PaginationParams(limit=200),
    ).items
    applicants = svc.list_applicants(
        org_id,
        pagination=PaginationParams(limit=200),
    ).items

    context = base_context(request, auth, "Interviews", "recruit", db=db)
    context["request"] = request
    context.update(
        {
            "interviews": result.items,
            "job_openings": job_openings,
            "applicants": applicants,
            "status": status,
            "job_opening_id": job_opening_id,
            "applicant_id": applicant_id,
            "start_date": start_date,
            "end_date": end_date,
            "statuses": [s.value for s in InterviewStatus],
            "page": result.page,
            "total_pages": result.total_pages,
            "total": result.total,
            "has_prev": result.has_prev,
            "has_next": result.has_next,
        }
    )
    return templates.TemplateResponse(request, "people/recruit/interviews.html", context)


@router.get("/offers", response_class=HTMLResponse)
def list_job_offers(
    request: Request,
    status: Optional[str] = None,
    job_opening_id: Optional[str] = None,
    applicant_id: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Job offers list page."""
    org_id = coerce_uuid(auth.organization_id)
    pagination = PaginationParams.from_page(page, per_page=20)
    svc = RecruitmentService(db)

    status_enum = None
    if status:
        try:
            status_enum = OfferStatus(status)
        except ValueError:
            status_enum = None

    result = svc.list_job_offers(
        org_id,
        status=status_enum,
        applicant_id=_parse_uuid(applicant_id),
        job_opening_id=_parse_uuid(job_opening_id),
        pagination=pagination,
    )

    job_openings = svc.list_job_openings(
        org_id,
        pagination=PaginationParams(limit=200),
    ).items
    applicants = svc.list_applicants(
        org_id,
        pagination=PaginationParams(limit=200),
    ).items

    context = base_context(request, auth, "Job Offers", "recruit", db=db)
    context["request"] = request
    context.update(
        {
            "offers": result.items,
            "job_openings": job_openings,
            "applicants": applicants,
            "status": status,
            "job_opening_id": job_opening_id,
            "applicant_id": applicant_id,
            "statuses": [s.value for s in OfferStatus],
            "page": result.page,
            "total_pages": result.total_pages,
            "total": result.total,
            "has_prev": result.has_prev,
            "has_next": result.has_next,
        }
    )
    return templates.TemplateResponse(request, "people/recruit/offers.html", context)


# Interview CRUD routes


@router.get("/interviews/new", response_class=HTMLResponse)
def new_interview_form(
    request: Request,
    applicant_id: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New interview form."""
    org_id = coerce_uuid(auth.organization_id)
    svc = RecruitmentService(db)
    org_svc = OrganizationService(db, org_id)

    applicants = svc.list_applicants(
        org_id, pagination=PaginationParams(limit=200)
    ).items
    employees = org_svc.list_employees(
        EmployeeFilters(is_active=True),
        PaginationParams(limit=500),
    ).items

    # Get interview rounds from model
    from app.models.people.recruit.interview import InterviewRound

    context = base_context(request, auth, "Schedule Interview", "recruit", db=db)
    context["request"] = request
    context.update(
        {
            "applicants": applicants,
            "employees": employees,
            "rounds": [r.value for r in InterviewRound],
            "interview_types": ["IN_PERSON", "VIDEO", "PHONE"],
            "form_data": {"applicant_id": applicant_id} if applicant_id else {},
        }
    )
    return templates.TemplateResponse(request, "people/recruit/interview_form.html", context)


@router.post("/interviews/new", response_class=HTMLResponse)
def create_interview(
    request: Request,
    applicant_id: str = Form(...),
    round: str = Form(...),
    interview_type: str = Form("IN_PERSON"),
    scheduled_date: str = Form(...),
    scheduled_time_from: str = Form(...),
    scheduled_time_to: str = Form(...),
    interviewer_id: str = Form(...),
    location: Optional[str] = Form(None),
    meeting_link: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Schedule a new interview."""
    org_id = coerce_uuid(auth.organization_id)
    svc = RecruitmentService(db)

    from app.models.people.recruit.interview import InterviewRound

    try:
        # Parse datetime
        scheduled_from = datetime.fromisoformat(f"{scheduled_date}T{scheduled_time_from}")
        scheduled_to = datetime.fromisoformat(f"{scheduled_date}T{scheduled_time_to}")

        interview = svc.schedule_interview(
            org_id,
            applicant_id=coerce_uuid(applicant_id),
            round=InterviewRound(round),
            interview_type=interview_type,
            scheduled_from=scheduled_from,
            scheduled_to=scheduled_to,
            interviewer_id=coerce_uuid(interviewer_id),
            location=location or None,
            meeting_link=meeting_link or None,
        )
        db.commit()
        return RedirectResponse(
            url=f"/people/recruit/interviews/{interview.interview_id}", status_code=303
        )
    except Exception as e:
        db.rollback()
        org_svc = OrganizationService(db, org_id)

        context = base_context(request, auth, "Schedule Interview", "recruit", db=db)
        context["request"] = request
        context.update(
            {
                "applicants": svc.list_applicants(org_id, pagination=PaginationParams(limit=200)).items,
                "employees": org_svc.list_employees(EmployeeFilters(is_active=True), PaginationParams(limit=500)).items,
                "rounds": [r.value for r in InterviewRound],
                "interview_types": ["IN_PERSON", "VIDEO", "PHONE"],
                "form_data": {
                    "applicant_id": applicant_id,
                    "round": round,
                    "interview_type": interview_type,
                    "scheduled_date": scheduled_date,
                    "scheduled_time_from": scheduled_time_from,
                    "scheduled_time_to": scheduled_time_to,
                    "interviewer_id": interviewer_id,
                    "location": location,
                    "meeting_link": meeting_link,
                },
                "error": str(e),
            }
        )
        return templates.TemplateResponse(request, "people/recruit/interview_form.html", context)


@router.get("/interviews/{interview_id}", response_class=HTMLResponse)
def interview_detail(
    request: Request,
    interview_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Interview detail page."""
    org_id = coerce_uuid(auth.organization_id)
    svc = RecruitmentService(db)

    try:
        interview = svc.get_interview(org_id, coerce_uuid(interview_id))
    except Exception:
        return RedirectResponse(url="/people/recruit/interviews", status_code=303)

    context = base_context(request, auth, "Interview Details", "recruit", db=db)
    context["request"] = request
    context.update(
        {
            "interview": interview,
            "statuses": [s.value for s in InterviewStatus],
        }
    )
    return templates.TemplateResponse(request, "people/recruit/interview_detail.html", context)


@router.get("/interviews/{interview_id}/edit", response_class=HTMLResponse)
def edit_interview_form(
    request: Request,
    interview_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Edit interview form."""
    org_id = coerce_uuid(auth.organization_id)
    svc = RecruitmentService(db)
    org_svc = OrganizationService(db, org_id)

    from app.models.people.recruit.interview import InterviewRound

    try:
        interview = svc.get_interview(org_id, coerce_uuid(interview_id))
    except Exception:
        return RedirectResponse(url="/people/recruit/interviews", status_code=303)

    context = base_context(request, auth, "Edit Interview", "recruit", db=db)
    context["request"] = request
    context.update(
        {
            "interview": interview,
            "applicants": svc.list_applicants(org_id, pagination=PaginationParams(limit=200)).items,
            "employees": org_svc.list_employees(EmployeeFilters(is_active=True), PaginationParams(limit=500)).items,
            "rounds": [r.value for r in InterviewRound],
            "interview_types": ["IN_PERSON", "VIDEO", "PHONE"],
            "form_data": {},
        }
    )
    return templates.TemplateResponse(request, "people/recruit/interview_form.html", context)


@router.post("/interviews/{interview_id}/edit", response_class=HTMLResponse)
def update_interview(
    request: Request,
    interview_id: str,
    round: str = Form(...),
    interview_type: str = Form("IN_PERSON"),
    scheduled_date: str = Form(...),
    scheduled_time_from: str = Form(...),
    scheduled_time_to: str = Form(...),
    interviewer_id: str = Form(...),
    location: Optional[str] = Form(None),
    meeting_link: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Update an interview."""
    org_id = coerce_uuid(auth.organization_id)
    svc = RecruitmentService(db)

    from app.models.people.recruit.interview import InterviewRound

    try:
        scheduled_from = datetime.fromisoformat(f"{scheduled_date}T{scheduled_time_from}")
        scheduled_to = datetime.fromisoformat(f"{scheduled_date}T{scheduled_time_to}")

        svc.update_interview(
            org_id,
            coerce_uuid(interview_id),
            round=InterviewRound(round),
            interview_type=interview_type,
            scheduled_from=scheduled_from,
            scheduled_to=scheduled_to,
            interviewer_id=coerce_uuid(interviewer_id),
            location=location or None,
            meeting_link=meeting_link or None,
        )
        db.commit()
        return RedirectResponse(
            url=f"/people/recruit/interviews/{interview_id}", status_code=303
        )
    except Exception as e:
        db.rollback()
        org_svc = OrganizationService(db, org_id)
        interview = svc.get_interview(org_id, coerce_uuid(interview_id))

        context = base_context(request, auth, "Edit Interview", "recruit", db=db)
        context["request"] = request
        context.update(
            {
                "interview": interview,
                "applicants": svc.list_applicants(org_id, pagination=PaginationParams(limit=200)).items,
                "employees": org_svc.list_employees(EmployeeFilters(is_active=True), PaginationParams(limit=500)).items,
                "rounds": [r.value for r in InterviewRound],
                "interview_types": ["IN_PERSON", "VIDEO", "PHONE"],
                "form_data": {},
                "error": str(e),
            }
        )
        return templates.TemplateResponse(request, "people/recruit/interview_form.html", context)


@router.post("/interviews/{interview_id}/cancel")
def cancel_interview(
    request: Request,
    interview_id: str,
    reason: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Cancel an interview."""
    org_id = coerce_uuid(auth.organization_id)
    svc = RecruitmentService(db)

    try:
        svc.cancel_interview(org_id, coerce_uuid(interview_id), reason=reason)
        db.commit()
    except Exception:
        db.rollback()

    return RedirectResponse(url=f"/people/recruit/interviews/{interview_id}", status_code=303)


@router.post("/interviews/{interview_id}/feedback")
def record_interview_feedback(
    request: Request,
    interview_id: str,
    rating: Optional[str] = Form(None),
    recommendation: Optional[str] = Form(None),
    feedback: Optional[str] = Form(None),
    strengths: Optional[str] = Form(None),
    weaknesses: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Record interview feedback."""
    org_id = coerce_uuid(auth.organization_id)
    svc = RecruitmentService(db)

    try:
        svc.update_interview(
            org_id,
            coerce_uuid(interview_id),
            rating=int(rating) if rating else None,
            recommendation=recommendation or None,
            feedback=feedback or None,
            strengths=strengths or None,
            weaknesses=weaknesses or None,
            status=InterviewStatus.COMPLETED,
        )
        db.commit()
    except Exception:
        db.rollback()

    return RedirectResponse(url=f"/people/recruit/interviews/{interview_id}", status_code=303)


# Job Offer CRUD routes


@router.get("/offers/new", response_class=HTMLResponse)
def new_offer_form(
    request: Request,
    applicant_id: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New job offer form."""
    org_id = coerce_uuid(auth.organization_id)
    svc = RecruitmentService(db)
    org_svc = OrganizationService(db, org_id)

    applicants = svc.list_applicants(
        org_id, pagination=PaginationParams(limit=200)
    ).items
    job_openings = svc.list_job_openings(
        org_id, pagination=PaginationParams(limit=200)
    ).items
    departments = org_svc.list_departments(
        DepartmentFilters(is_active=True), PaginationParams(limit=200)
    ).items
    designations = org_svc.list_designations(
        DesignationFilters(is_active=True), PaginationParams(limit=200)
    ).items

    context = base_context(request, auth, "Create Job Offer", "recruit", db=db)
    context["request"] = request
    context.update(
        {
            "applicants": applicants,
            "job_openings": job_openings,
            "departments": departments,
            "designations": designations,
            "employment_types": ["FULL_TIME", "PART_TIME", "CONTRACT", "INTERN"],
            "pay_frequencies": ["MONTHLY", "BI_WEEKLY", "WEEKLY"],
            "form_data": {"applicant_id": applicant_id} if applicant_id else {},
        }
    )
    return templates.TemplateResponse(request, "people/recruit/offer_form.html", context)


@router.post("/offers/new", response_class=HTMLResponse)
def create_offer(
    request: Request,
    applicant_id: str = Form(...),
    job_opening_id: str = Form(...),
    designation_id: str = Form(...),
    department_id: Optional[str] = Form(None),
    offer_date: str = Form(...),
    valid_until: str = Form(...),
    expected_joining_date: str = Form(...),
    base_salary: str = Form(...),
    currency_code: str = Form("NGN"),
    pay_frequency: str = Form("MONTHLY"),
    signing_bonus: Optional[str] = Form(None),
    relocation_allowance: Optional[str] = Form(None),
    other_benefits: Optional[str] = Form(None),
    employment_type: str = Form("FULL_TIME"),
    probation_months: int = Form(3),
    notice_period_days: int = Form(30),
    terms_and_conditions: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create a new job offer."""
    org_id = coerce_uuid(auth.organization_id)
    svc = RecruitmentService(db)

    try:
        offer = svc.create_job_offer(
            org_id,
            applicant_id=coerce_uuid(applicant_id),
            job_opening_id=coerce_uuid(job_opening_id),
            designation_id=coerce_uuid(designation_id),
            department_id=coerce_uuid(department_id) if department_id else None,
            offer_date=date.fromisoformat(offer_date),
            valid_until=date.fromisoformat(valid_until),
            expected_joining_date=date.fromisoformat(expected_joining_date),
            base_salary=Decimal(base_salary),
            currency_code=currency_code,
            pay_frequency=pay_frequency,
            signing_bonus=Decimal(signing_bonus) if signing_bonus else None,
            relocation_allowance=Decimal(relocation_allowance) if relocation_allowance else None,
            other_benefits=other_benefits or None,
            employment_type=employment_type,
            probation_months=probation_months,
            notice_period_days=notice_period_days,
            terms_and_conditions=terms_and_conditions or None,
            notes=notes or None,
        )
        db.commit()
        return RedirectResponse(
            url=f"/people/recruit/offers/{offer.offer_id}", status_code=303
        )
    except Exception as e:
        db.rollback()
        org_svc = OrganizationService(db, org_id)

        context = base_context(request, auth, "Create Job Offer", "recruit", db=db)
        context["request"] = request
        context.update(
            {
                "applicants": svc.list_applicants(org_id, pagination=PaginationParams(limit=200)).items,
                "job_openings": svc.list_job_openings(org_id, pagination=PaginationParams(limit=200)).items,
                "departments": org_svc.list_departments(DepartmentFilters(is_active=True), PaginationParams(limit=200)).items,
                "designations": org_svc.list_designations(DesignationFilters(is_active=True), PaginationParams(limit=200)).items,
                "employment_types": ["FULL_TIME", "PART_TIME", "CONTRACT", "INTERN"],
                "pay_frequencies": ["MONTHLY", "BI_WEEKLY", "WEEKLY"],
                "form_data": {
                    "applicant_id": applicant_id,
                    "job_opening_id": job_opening_id,
                    "designation_id": designation_id,
                    "department_id": department_id,
                    "offer_date": offer_date,
                    "valid_until": valid_until,
                    "expected_joining_date": expected_joining_date,
                    "base_salary": base_salary,
                    "currency_code": currency_code,
                    "pay_frequency": pay_frequency,
                    "signing_bonus": signing_bonus,
                    "relocation_allowance": relocation_allowance,
                    "other_benefits": other_benefits,
                    "employment_type": employment_type,
                    "probation_months": probation_months,
                    "notice_period_days": notice_period_days,
                    "terms_and_conditions": terms_and_conditions,
                    "notes": notes,
                },
                "error": str(e),
            }
        )
        return templates.TemplateResponse(request, "people/recruit/offer_form.html", context)


@router.get("/offers/{offer_id}", response_class=HTMLResponse)
def offer_detail(
    request: Request,
    offer_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Job offer detail page."""
    org_id = coerce_uuid(auth.organization_id)
    svc = RecruitmentService(db)

    try:
        offer = svc.get_job_offer(org_id, coerce_uuid(offer_id))
    except Exception:
        return RedirectResponse(url="/people/recruit/offers", status_code=303)

    context = base_context(request, auth, f"Offer {offer.offer_number}", "recruit", db=db)
    context["request"] = request
    context.update(
        {
            "offer": offer,
            "statuses": [s.value for s in OfferStatus],
        }
    )
    return templates.TemplateResponse(request, "people/recruit/offer_detail.html", context)


@router.get("/offers/{offer_id}/edit", response_class=HTMLResponse)
def edit_offer_form(
    request: Request,
    offer_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Edit job offer form."""
    org_id = coerce_uuid(auth.organization_id)
    svc = RecruitmentService(db)
    org_svc = OrganizationService(db, org_id)

    try:
        offer = svc.get_job_offer(org_id, coerce_uuid(offer_id))
    except Exception:
        return RedirectResponse(url="/people/recruit/offers", status_code=303)

    context = base_context(request, auth, "Edit Job Offer", "recruit", db=db)
    context["request"] = request
    context.update(
        {
            "offer": offer,
            "applicants": svc.list_applicants(org_id, pagination=PaginationParams(limit=200)).items,
            "job_openings": svc.list_job_openings(org_id, pagination=PaginationParams(limit=200)).items,
            "departments": org_svc.list_departments(DepartmentFilters(is_active=True), PaginationParams(limit=200)).items,
            "designations": org_svc.list_designations(DesignationFilters(is_active=True), PaginationParams(limit=200)).items,
            "employment_types": ["FULL_TIME", "PART_TIME", "CONTRACT", "INTERN"],
            "pay_frequencies": ["MONTHLY", "BI_WEEKLY", "WEEKLY"],
            "form_data": {},
        }
    )
    return templates.TemplateResponse(request, "people/recruit/offer_form.html", context)


@router.post("/offers/{offer_id}/edit", response_class=HTMLResponse)
def update_offer(
    request: Request,
    offer_id: str,
    designation_id: str = Form(...),
    department_id: Optional[str] = Form(None),
    offer_date: str = Form(...),
    valid_until: str = Form(...),
    expected_joining_date: str = Form(...),
    base_salary: str = Form(...),
    currency_code: str = Form("NGN"),
    pay_frequency: str = Form("MONTHLY"),
    signing_bonus: Optional[str] = Form(None),
    relocation_allowance: Optional[str] = Form(None),
    other_benefits: Optional[str] = Form(None),
    employment_type: str = Form("FULL_TIME"),
    probation_months: int = Form(3),
    notice_period_days: int = Form(30),
    terms_and_conditions: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Update a job offer."""
    org_id = coerce_uuid(auth.organization_id)
    svc = RecruitmentService(db)

    try:
        svc.update_job_offer(
            org_id,
            coerce_uuid(offer_id),
            designation_id=coerce_uuid(designation_id),
            department_id=coerce_uuid(department_id) if department_id else None,
            offer_date=date.fromisoformat(offer_date),
            valid_until=date.fromisoformat(valid_until),
            expected_joining_date=date.fromisoformat(expected_joining_date),
            base_salary=Decimal(base_salary),
            currency_code=currency_code,
            pay_frequency=pay_frequency,
            signing_bonus=Decimal(signing_bonus) if signing_bonus else None,
            relocation_allowance=Decimal(relocation_allowance) if relocation_allowance else None,
            other_benefits=other_benefits or None,
            employment_type=employment_type,
            probation_months=probation_months,
            notice_period_days=notice_period_days,
            terms_and_conditions=terms_and_conditions or None,
            notes=notes or None,
        )
        db.commit()
        return RedirectResponse(
            url=f"/people/recruit/offers/{offer_id}", status_code=303
        )
    except Exception as e:
        db.rollback()
        org_svc = OrganizationService(db, org_id)
        offer = svc.get_job_offer(org_id, coerce_uuid(offer_id))

        context = base_context(request, auth, "Edit Job Offer", "recruit", db=db)
        context["request"] = request
        context.update(
            {
                "offer": offer,
                "applicants": svc.list_applicants(org_id, pagination=PaginationParams(limit=200)).items,
                "job_openings": svc.list_job_openings(org_id, pagination=PaginationParams(limit=200)).items,
                "departments": org_svc.list_departments(DepartmentFilters(is_active=True), PaginationParams(limit=200)).items,
                "designations": org_svc.list_designations(DesignationFilters(is_active=True), PaginationParams(limit=200)).items,
                "employment_types": ["FULL_TIME", "PART_TIME", "CONTRACT", "INTERN"],
                "pay_frequencies": ["MONTHLY", "BI_WEEKLY", "WEEKLY"],
                "form_data": {},
                "error": str(e),
            }
        )
        return templates.TemplateResponse(request, "people/recruit/offer_form.html", context)


@router.post("/offers/{offer_id}/extend")
def extend_offer(
    request: Request,
    offer_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Extend offer to candidate."""
    org_id = coerce_uuid(auth.organization_id)
    svc = RecruitmentService(db)

    try:
        svc.extend_offer(org_id, coerce_uuid(offer_id))
        db.commit()
    except Exception:
        db.rollback()

    return RedirectResponse(url=f"/people/recruit/offers/{offer_id}", status_code=303)


@router.post("/offers/{offer_id}/accept")
def accept_offer(
    request: Request,
    offer_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Mark offer as accepted."""
    org_id = coerce_uuid(auth.organization_id)
    svc = RecruitmentService(db)

    try:
        svc.accept_offer(org_id, coerce_uuid(offer_id))
        db.commit()
    except Exception:
        db.rollback()

    return RedirectResponse(url=f"/people/recruit/offers/{offer_id}", status_code=303)


@router.post("/offers/{offer_id}/decline")
def decline_offer(
    request: Request,
    offer_id: str,
    reason: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Mark offer as declined."""
    org_id = coerce_uuid(auth.organization_id)
    svc = RecruitmentService(db)

    try:
        svc.decline_offer(org_id, coerce_uuid(offer_id), reason=reason)
        db.commit()
    except Exception:
        db.rollback()

    return RedirectResponse(url=f"/people/recruit/offers/{offer_id}", status_code=303)


@router.post("/offers/{offer_id}/withdraw")
def withdraw_offer(
    request: Request,
    offer_id: str,
    reason: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Withdraw an offer."""
    org_id = coerce_uuid(auth.organization_id)
    svc = RecruitmentService(db)

    try:
        svc.update_job_offer(org_id, coerce_uuid(offer_id), status=OfferStatus.WITHDRAWN)
        db.commit()
    except Exception:
        db.rollback()

    return RedirectResponse(url=f"/people/recruit/offers/{offer_id}", status_code=303)


# ─────────────────────────────────────────────────────────────────────────────
# Recruitment Reports
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/reports/pipeline", response_class=HTMLResponse)
def report_pipeline(
    request: Request,
    job_opening_id: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Recruitment pipeline report."""
    org_id = coerce_uuid(auth.organization_id)
    svc = RecruitmentService(db)

    report = svc.get_recruitment_pipeline_report(
        org_id,
        job_opening_id=_parse_uuid(job_opening_id),
    )

    context = base_context(request, auth, "Pipeline Report", "recruit", db=db)
    context["request"] = request
    context.update(
        {
            "report": report,
            "job_opening_id": job_opening_id,
        }
    )
    return templates.TemplateResponse(request, "people/recruit/reports/pipeline.html", context)


@router.get("/reports/time-to-hire", response_class=HTMLResponse)
def report_time_to_hire(
    request: Request,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Time to hire report."""
    org_id = coerce_uuid(auth.organization_id)
    svc = RecruitmentService(db)

    report = svc.get_time_to_hire_report(
        org_id,
        start_date=date.fromisoformat(start_date) if start_date else None,
        end_date=date.fromisoformat(end_date) if end_date else None,
    )

    context = base_context(request, auth, "Time to Hire", "recruit", db=db)
    context["request"] = request
    context.update(
        {
            "report": report,
            "start_date": start_date or "",
            "end_date": end_date or "",
        }
    )
    return templates.TemplateResponse(request, "people/recruit/reports/time_to_hire.html", context)


@router.get("/reports/sources", response_class=HTMLResponse)
def report_sources(
    request: Request,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Applicant source analysis report."""
    org_id = coerce_uuid(auth.organization_id)
    svc = RecruitmentService(db)

    report = svc.get_source_analysis_report(
        org_id,
        start_date=date.fromisoformat(start_date) if start_date else None,
        end_date=date.fromisoformat(end_date) if end_date else None,
    )

    context = base_context(request, auth, "Source Analysis", "recruit", db=db)
    context["request"] = request
    context.update(
        {
            "report": report,
            "start_date": start_date or "",
            "end_date": end_date or "",
        }
    )
    return templates.TemplateResponse(request, "people/recruit/reports/sources.html", context)


@router.get("/reports/overview", response_class=HTMLResponse)
def report_overview(
    request: Request,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Recruitment overview report."""
    org_id = coerce_uuid(auth.organization_id)
    svc = RecruitmentService(db)

    report = svc.get_recruitment_overview_report(
        org_id,
        start_date=date.fromisoformat(start_date) if start_date else None,
        end_date=date.fromisoformat(end_date) if end_date else None,
    )

    context = base_context(request, auth, "Recruitment Overview", "recruit", db=db)
    context["request"] = request
    context.update(
        {
            "report": report,
            "start_date": start_date or "",
            "end_date": end_date or "",
        }
    )
    return templates.TemplateResponse(request, "people/recruit/reports/overview.html", context)
