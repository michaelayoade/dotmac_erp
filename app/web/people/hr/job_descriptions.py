"""Job Descriptions routes."""

from typing import Optional

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.models.people.hr import JobDescriptionStatus
from app.services.people.hr import (
    OrganizationService,
    CompetencyService,
    JobDescriptionService,
    DepartmentFilters,
    DesignationFilters,
)
from app.services.common import PaginationParams, coerce_uuid
from app.services.people.hr.web.employee_web import DEFAULT_PAGE_SIZE
from app.templates import templates
from app.web.deps import base_context, get_db, require_hr_access, WebAuthContext

from ._common import _parse_bool


router = APIRouter()


# =============================================================================
# Job Descriptions
# =============================================================================


@router.get("/job-descriptions", response_class=HTMLResponse)
def list_job_descriptions(
    request: Request,
    status: Optional[str] = None,
    department_id: Optional[str] = None,
    search: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    success: Optional[str] = None,
    error: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Job description list page."""
    org_id = coerce_uuid(auth.organization_id)
    jd_svc = JobDescriptionService(db, org_id)
    org_svc = OrganizationService(db, org_id)

    jd_status = JobDescriptionStatus(status) if status else None
    dept_id = coerce_uuid(department_id) if department_id else None

    pagination = PaginationParams.from_page(page, per_page=DEFAULT_PAGE_SIZE)
    result = jd_svc.list_job_descriptions(
        status=jd_status,
        department_id=dept_id,
        search=search,
        pagination=pagination,
    )

    departments = org_svc.list_departments(
        DepartmentFilters(is_active=True),
        PaginationParams(limit=200),
    ).items

    context = base_context(request, auth, "Job Descriptions", "job-descriptions", db=db)
    context.update(
        {
            "job_descriptions": result.items,
            "pagination": result,
            "statuses": list(JobDescriptionStatus),
            "departments": departments,
            "selected_status": status,
            "selected_department_id": department_id,
            "search": search,
            "success": success,
            "error": error,
        }
    )
    return templates.TemplateResponse(
        request, "people/hr/job_descriptions.html", context
    )


@router.get("/job-descriptions/new", response_class=HTMLResponse)
def new_job_description_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New job description form."""
    org_id = coerce_uuid(auth.organization_id)
    org_svc = OrganizationService(db, org_id)

    designations = org_svc.list_designations(
        DesignationFilters(is_active=True),
        PaginationParams(limit=200),
    ).items
    departments = org_svc.list_departments(
        DepartmentFilters(is_active=True),
        PaginationParams(limit=200),
    ).items

    context = base_context(
        request, auth, "New Job Description", "job-descriptions", db=db
    )
    context.update(
        {
            "designations": designations,
            "departments": departments,
            "statuses": list(JobDescriptionStatus),
            "form_data": {},
        }
    )
    return templates.TemplateResponse(
        request, "people/hr/job_description_form.html", context
    )


@router.post("/job-descriptions/new", response_class=HTMLResponse)
def create_job_description(
    request: Request,
    jd_code: str = Form(...),
    job_title: str = Form(...),
    designation_id: str = Form(...),
    department_id: Optional[str] = Form(None),
    summary: Optional[str] = Form(None),
    purpose: Optional[str] = Form(None),
    key_responsibilities: Optional[str] = Form(None),
    education_requirements: Optional[str] = Form(None),
    experience_requirements: Optional[str] = Form(None),
    min_years_experience: Optional[int] = Form(None),
    max_years_experience: Optional[int] = Form(None),
    technical_skills: Optional[str] = Form(None),
    certifications_required: Optional[str] = Form(None),
    certifications_preferred: Optional[str] = Form(None),
    work_location: Optional[str] = Form(None),
    travel_requirements: Optional[str] = Form(None),
    reports_to: Optional[str] = Form(None),
    direct_reports: Optional[str] = Form(None),
    status: str = Form("draft"),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create a new job description."""
    org_id = coerce_uuid(auth.organization_id)
    jd_svc = JobDescriptionService(db, org_id, auth.principal)
    org_svc = OrganizationService(db, org_id)

    try:
        jd_svc.create_job_description(
            jd_code=jd_code,
            job_title=job_title,
            designation_id=coerce_uuid(designation_id),
            department_id=coerce_uuid(department_id) if department_id else None,
            summary=summary or None,
            purpose=purpose or None,
            key_responsibilities=key_responsibilities or None,
            education_requirements=education_requirements or None,
            experience_requirements=experience_requirements or None,
            min_years_experience=min_years_experience,
            max_years_experience=max_years_experience,
            technical_skills=technical_skills or None,
            certifications_required=certifications_required or None,
            certifications_preferred=certifications_preferred or None,
            work_location=work_location or None,
            travel_requirements=travel_requirements or None,
            reports_to=reports_to or None,
            direct_reports=direct_reports or None,
            status=JobDescriptionStatus(status),
        )
        db.commit()
        return RedirectResponse(
            url="/people/hr/job-descriptions?success=Job+description+created",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        designations = org_svc.list_designations(
            DesignationFilters(is_active=True),
            PaginationParams(limit=200),
        ).items
        departments = org_svc.list_departments(
            DepartmentFilters(is_active=True),
            PaginationParams(limit=200),
        ).items

        context = base_context(
            request, auth, "New Job Description", "job-descriptions", db=db
        )
        context.update(
            {
                "designations": designations,
                "departments": departments,
                "statuses": list(JobDescriptionStatus),
                "form_data": {
                    "jd_code": jd_code,
                    "job_title": job_title,
                    "designation_id": designation_id,
                    "department_id": department_id,
                    "summary": summary,
                    "purpose": purpose,
                    "key_responsibilities": key_responsibilities,
                    "education_requirements": education_requirements,
                    "experience_requirements": experience_requirements,
                    "min_years_experience": min_years_experience,
                    "max_years_experience": max_years_experience,
                    "technical_skills": technical_skills,
                    "certifications_required": certifications_required,
                    "certifications_preferred": certifications_preferred,
                    "work_location": work_location,
                    "travel_requirements": travel_requirements,
                    "reports_to": reports_to,
                    "direct_reports": direct_reports,
                    "status": status,
                },
                "error": str(e),
            }
        )
        return templates.TemplateResponse(
            request, "people/hr/job_description_form.html", context
        )


@router.get("/job-descriptions/{jd_id}", response_class=HTMLResponse)
def view_job_description(
    request: Request,
    jd_id: str,
    success: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """View job description detail."""
    org_id = coerce_uuid(auth.organization_id)
    jd_svc = JobDescriptionService(db, org_id)
    comp_svc = CompetencyService(db, org_id)

    jd = jd_svc.get_job_description(coerce_uuid(jd_id), load_competencies=True)
    if not jd:
        return RedirectResponse(
            url="/people/hr/job-descriptions?error=Job+description+not+found",
            status_code=303,
        )

    # Get available competencies for adding
    all_competencies = comp_svc.list_competencies(is_active=True).items

    context = base_context(request, auth, jd.job_title, "job-descriptions", db=db)
    context.update(
        {
            "jd": jd,
            "all_competencies": all_competencies,
            "success": success,
        }
    )
    return templates.TemplateResponse(
        request, "people/hr/job_description_detail.html", context
    )


@router.get("/job-descriptions/{jd_id}/edit", response_class=HTMLResponse)
def edit_job_description_form(
    request: Request,
    jd_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Edit job description form."""
    org_id = coerce_uuid(auth.organization_id)
    jd_svc = JobDescriptionService(db, org_id)
    org_svc = OrganizationService(db, org_id)

    jd = jd_svc.get_job_description(coerce_uuid(jd_id))
    if not jd:
        return RedirectResponse(
            url="/people/hr/job-descriptions?error=Job+description+not+found",
            status_code=303,
        )

    designations = org_svc.list_designations(
        DesignationFilters(is_active=True),
        PaginationParams(limit=200),
    ).items
    departments = org_svc.list_departments(
        DepartmentFilters(is_active=True),
        PaginationParams(limit=200),
    ).items

    context = base_context(
        request, auth, f"Edit {jd.job_title}", "job-descriptions", db=db
    )
    context.update(
        {
            "jd": jd,
            "designations": designations,
            "departments": departments,
            "statuses": list(JobDescriptionStatus),
            "form_data": {},
        }
    )
    return templates.TemplateResponse(
        request, "people/hr/job_description_form.html", context
    )


@router.post("/job-descriptions/{jd_id}/edit", response_class=HTMLResponse)
def update_job_description(
    request: Request,
    jd_id: str,
    jd_code: str = Form(...),
    job_title: str = Form(...),
    designation_id: str = Form(...),
    department_id: Optional[str] = Form(None),
    summary: Optional[str] = Form(None),
    purpose: Optional[str] = Form(None),
    key_responsibilities: Optional[str] = Form(None),
    education_requirements: Optional[str] = Form(None),
    experience_requirements: Optional[str] = Form(None),
    min_years_experience: Optional[int] = Form(None),
    max_years_experience: Optional[int] = Form(None),
    technical_skills: Optional[str] = Form(None),
    certifications_required: Optional[str] = Form(None),
    certifications_preferred: Optional[str] = Form(None),
    work_location: Optional[str] = Form(None),
    travel_requirements: Optional[str] = Form(None),
    reports_to: Optional[str] = Form(None),
    direct_reports: Optional[str] = Form(None),
    status: str = Form("draft"),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Update a job description."""
    org_id = coerce_uuid(auth.organization_id)
    jd_svc = JobDescriptionService(db, org_id, auth.principal)
    org_svc = OrganizationService(db, org_id)

    try:
        jd_svc.update_job_description(
            coerce_uuid(jd_id),
            {
                "jd_code": jd_code,
                "job_title": job_title,
                "designation_id": coerce_uuid(designation_id),
                "department_id": coerce_uuid(department_id) if department_id else None,
                "summary": summary or None,
                "purpose": purpose or None,
                "key_responsibilities": key_responsibilities or None,
                "education_requirements": education_requirements or None,
                "experience_requirements": experience_requirements or None,
                "min_years_experience": min_years_experience,
                "max_years_experience": max_years_experience,
                "technical_skills": technical_skills or None,
                "certifications_required": certifications_required or None,
                "certifications_preferred": certifications_preferred or None,
                "work_location": work_location or None,
                "travel_requirements": travel_requirements or None,
                "reports_to": reports_to or None,
                "direct_reports": direct_reports or None,
                "status": JobDescriptionStatus(status),
            },
        )
        db.commit()
        return RedirectResponse(
            url=f"/people/hr/job-descriptions/{jd_id}?success=Job+description+updated",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        jd = jd_svc.get_job_description(coerce_uuid(jd_id))
        designations = org_svc.list_designations(
            DesignationFilters(is_active=True),
            PaginationParams(limit=200),
        ).items
        departments = org_svc.list_departments(
            DepartmentFilters(is_active=True),
            PaginationParams(limit=200),
        ).items

        context = base_context(
            request, auth, "Edit Job Description", "job-descriptions", db=db
        )
        context.update(
            {
                "jd": jd,
                "designations": designations,
                "departments": departments,
                "statuses": list(JobDescriptionStatus),
                "form_data": {
                    "jd_code": jd_code,
                    "job_title": job_title,
                },
                "error": str(e),
            }
        )
        return templates.TemplateResponse(
            request, "people/hr/job_description_form.html", context
        )


@router.post("/job-descriptions/{jd_id}/activate", response_class=HTMLResponse)
def activate_job_description(
    request: Request,
    jd_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Activate a job description."""
    org_id = coerce_uuid(auth.organization_id)
    jd_svc = JobDescriptionService(db, org_id, auth.principal)

    try:
        jd_svc.activate_job_description(coerce_uuid(jd_id))
        db.commit()
        return RedirectResponse(
            url=f"/people/hr/job-descriptions/{jd_id}?success=Job+description+activated",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        return RedirectResponse(
            url=f"/people/hr/job-descriptions/{jd_id}?error={str(e)}", status_code=303
        )


@router.post("/job-descriptions/{jd_id}/archive", response_class=HTMLResponse)
def archive_job_description(
    request: Request,
    jd_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Archive a job description."""
    org_id = coerce_uuid(auth.organization_id)
    jd_svc = JobDescriptionService(db, org_id, auth.principal)

    try:
        jd_svc.archive_job_description(coerce_uuid(jd_id))
        db.commit()
        return RedirectResponse(
            url=f"/people/hr/job-descriptions/{jd_id}?success=Job+description+archived",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        return RedirectResponse(
            url=f"/people/hr/job-descriptions/{jd_id}?error={str(e)}", status_code=303
        )


@router.post("/job-descriptions/{jd_id}/competencies", response_class=HTMLResponse)
def add_competency_to_jd(
    request: Request,
    jd_id: str,
    competency_id: str = Form(...),
    required_level: int = Form(3),
    is_mandatory: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Add a competency to a job description."""
    org_id = coerce_uuid(auth.organization_id)
    jd_svc = JobDescriptionService(db, org_id, auth.principal)

    try:
        jd_svc.add_competency(
            coerce_uuid(jd_id),
            coerce_uuid(competency_id),
            required_level=required_level,
            is_mandatory=_parse_bool(is_mandatory, True),
            notes=notes or None,
        )
        db.commit()
        return RedirectResponse(
            url=f"/people/hr/job-descriptions/{jd_id}?success=Competency+added",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        return RedirectResponse(
            url=f"/people/hr/job-descriptions/{jd_id}?error={str(e)}", status_code=303
        )


@router.post(
    "/job-descriptions/{jd_id}/competencies/{competency_id}/delete",
    response_class=HTMLResponse,
)
def remove_competency_from_jd(
    request: Request,
    jd_id: str,
    competency_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Remove a competency from a job description."""
    org_id = coerce_uuid(auth.organization_id)
    jd_svc = JobDescriptionService(db, org_id, auth.principal)

    try:
        jd_svc.remove_competency(coerce_uuid(jd_id), coerce_uuid(competency_id))
        db.commit()
        return RedirectResponse(
            url=f"/people/hr/job-descriptions/{jd_id}?success=Competency+removed",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        return RedirectResponse(
            url=f"/people/hr/job-descriptions/{jd_id}?error={str(e)}", status_code=303
        )
