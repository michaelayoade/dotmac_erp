"""
Recruit Web Service - Job Opening methods.

Provides view-focused data and operations for job opening web routes.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.models.people.hr.employee import EmployeeStatus
from app.models.people.recruit import JobOpeningStatus
from app.services.common import PaginationParams, coerce_uuid
from app.services.people.hr import (
    DepartmentFilters,
    DesignationFilters,
    EmployeeFilters,
    EmployeeService,
    OrganizationService,
)
from app.services.people.recruit import RecruitmentService
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

from .base import (
    logger,
    parse_date_only,
    parse_decimal,
    parse_int,
    parse_status,
    parse_uuid,
)


class JobOpeningWebService:
    """Web service methods for job openings."""

    # ─────────────────────────────────────────────────────────────────────────
    # Context Builders
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def list_job_openings_context(
        db: Session,
        organization_id: UUID,
        search: str | None = None,
        status: str | None = None,
        department_id: str | None = None,
        page: int = 1,
    ) -> dict:
        """Build context for job openings list page."""
        pagination = PaginationParams.from_page(page, per_page=20)
        svc = RecruitmentService(db)

        status_enum = parse_status(status, JobOpeningStatus)
        result = svc.list_job_openings(
            organization_id,
            search=search,
            status=status_enum,
            department_id=parse_uuid(department_id),
            pagination=pagination,
        )

        org_svc = OrganizationService(db, organization_id)
        departments = org_svc.list_departments(
            DepartmentFilters(is_active=True),
            PaginationParams(limit=200),
        ).items

        return {
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

    @staticmethod
    def job_opening_form_context(
        db: Session,
        organization_id: UUID,
        job_opening_id: str | None = None,
    ) -> dict:
        """Build context for job opening create/edit form."""
        org_svc = OrganizationService(db, organization_id)
        emp_svc = EmployeeService(db, organization_id)

        departments = org_svc.list_departments(
            DepartmentFilters(is_active=True),
            PaginationParams(limit=200),
        ).items
        designations = org_svc.list_designations(
            DesignationFilters(is_active=True),
            PaginationParams(limit=200),
        ).items
        managers = emp_svc.list_employees(
            EmployeeFilters(status=EmployeeStatus.ACTIVE),
            PaginationParams(limit=500),
        ).items

        opening = None
        if job_opening_id:
            svc = RecruitmentService(db)
            try:
                opening = svc.get_job_opening(
                    organization_id, coerce_uuid(job_opening_id)
                )
            except Exception:
                opening = None

        return {
            "opening": opening,
            "departments": departments,
            "designations": designations,
            "managers": managers,
            "form_data": {},
        }

    @staticmethod
    def job_opening_detail_context(
        db: Session,
        organization_id: UUID,
        job_opening_id: str,
    ) -> dict:
        """Build context for job opening detail page."""
        svc = RecruitmentService(db)

        try:
            opening = svc.get_job_opening(organization_id, coerce_uuid(job_opening_id))
        except Exception:
            return {"opening": None, "applicants_count": 0}

        applicants = svc.list_applicants(
            organization_id,
            job_opening_id=coerce_uuid(job_opening_id),
            pagination=PaginationParams(limit=1),
        )

        return {
            "opening": opening,
            "applicants_count": applicants.total,
        }

    @staticmethod
    def build_job_opening_input(form_data: dict) -> dict:
        """Build input kwargs for job opening from form data."""
        return {
            "job_code": form_data.get("job_code", ""),
            "job_title": form_data.get("job_title", ""),
            "department_id": coerce_uuid(form_data["department_id"])
            if form_data.get("department_id")
            else None,
            "designation_id": coerce_uuid(form_data["designation_id"])
            if form_data.get("designation_id")
            else None,
            "reports_to_id": coerce_uuid(form_data["reports_to_id"])
            if form_data.get("reports_to_id")
            else None,
            "employment_type": form_data.get("employment_type", "FULL_TIME"),
            "number_of_positions": int(form_data.get("number_of_positions", 1)),
            "location": form_data.get("location") or None,
            "is_remote": form_data.get("is_remote") == "true",
            "currency_code": form_data.get("currency_code", "NGN"),
            "min_salary": parse_decimal(form_data.get("min_salary")),
            "max_salary": parse_decimal(form_data.get("max_salary")),
            "posted_on": parse_date_only(form_data.get("posted_on")),
            "closes_on": parse_date_only(form_data.get("closes_on")),
            "min_experience_years": parse_int(form_data.get("min_experience_years")),
            "description": form_data.get("description") or None,
            "required_skills": form_data.get("required_skills") or None,
            "preferred_skills": form_data.get("preferred_skills") or None,
            "education_requirements": form_data.get("education_requirements") or None,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Response Methods
    # ─────────────────────────────────────────────────────────────────────────

    def list_job_openings_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        search: str | None = None,
        status: str | None = None,
        department_id: str | None = None,
        page: int = 1,
    ) -> HTMLResponse:
        """Render job openings list page."""
        context = base_context(request, auth, "Job Openings", "recruit", db=db)
        context["request"] = request
        context.update(
            self.list_job_openings_context(
                db,
                coerce_uuid(auth.organization_id),
                search=search,
                status=status,
                department_id=department_id,
                page=page,
            )
        )
        return templates.TemplateResponse(
            request, "people/recruit/job_openings.html", context
        )

    def job_opening_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """Render new job opening form."""
        context = base_context(request, auth, "New Job Opening", "recruit", db=db)
        context["request"] = request
        context.update(
            self.job_opening_form_context(db, coerce_uuid(auth.organization_id))
        )
        return templates.TemplateResponse(
            request, "people/recruit/job_opening_form.html", context
        )

    def job_opening_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        job_opening_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Render job opening detail page."""
        ctx = self.job_opening_detail_context(
            db, coerce_uuid(auth.organization_id), job_opening_id
        )

        if not ctx.get("opening"):
            return RedirectResponse(url="/people/recruit/jobs", status_code=303)

        context = base_context(
            request, auth, ctx["opening"].job_title, "recruit", db=db
        )
        context["request"] = request
        context.update(ctx)
        return templates.TemplateResponse(
            request, "people/recruit/job_opening_detail.html", context
        )

    def job_opening_edit_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        job_opening_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Render job opening edit form."""
        ctx = self.job_opening_form_context(
            db, coerce_uuid(auth.organization_id), job_opening_id
        )

        if not ctx.get("opening"):
            return RedirectResponse(url="/people/recruit/jobs", status_code=303)

        context = base_context(request, auth, "Edit Job Opening", "recruit", db=db)
        context["request"] = request
        context.update(ctx)
        return templates.TemplateResponse(
            request, "people/recruit/job_opening_form.html", context
        )

    async def create_job_opening_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Handle job opening creation form submission."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        svc = RecruitmentService(db)

        try:
            input_kwargs = self.build_job_opening_input(dict(form_data))
            opening = svc.create_job_opening(org_id, **input_kwargs)
            db.commit()
            return RedirectResponse(
                url=f"/people/recruit/jobs/{opening.job_opening_id}",
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            logger.exception("create_job_opening_response: failed")
            context = base_context(request, auth, "New Job Opening", "recruit", db=db)
            context["request"] = request
            context.update(self.job_opening_form_context(db, org_id))
            context["form_data"] = dict(form_data)
            context["error"] = str(e)
            return templates.TemplateResponse(
                request, "people/recruit/job_opening_form.html", context
            )

    async def update_job_opening_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        job_opening_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Handle job opening update form submission."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        svc = RecruitmentService(db)

        try:
            input_kwargs = self.build_job_opening_input(dict(form_data))
            svc.update_job_opening(org_id, coerce_uuid(job_opening_id), **input_kwargs)
            db.commit()
            return RedirectResponse(
                url=f"/people/recruit/jobs/{job_opening_id}",
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            logger.exception("update_job_opening_response: failed")
            context = base_context(request, auth, "Edit Job Opening", "recruit", db=db)
            context["request"] = request
            context.update(self.job_opening_form_context(db, org_id, job_opening_id))
            context["error"] = str(e)
            return templates.TemplateResponse(
                request, "people/recruit/job_opening_form.html", context
            )

    def publish_job_opening_response(
        self,
        auth: WebAuthContext,
        db: Session,
        job_opening_id: str,
    ) -> RedirectResponse:
        """Handle job opening publish action."""
        org_id = coerce_uuid(auth.organization_id)
        svc = RecruitmentService(db)

        try:
            svc.publish_job_opening(org_id, coerce_uuid(job_opening_id))
            db.commit()
        except Exception:
            db.rollback()

        return RedirectResponse(
            url=f"/people/recruit/jobs/{job_opening_id}", status_code=303
        )

    def hold_job_opening_response(
        self,
        auth: WebAuthContext,
        db: Session,
        job_opening_id: str,
    ) -> RedirectResponse:
        """Handle job opening hold action."""
        org_id = coerce_uuid(auth.organization_id)
        svc = RecruitmentService(db)

        try:
            svc.update_job_opening(
                org_id, coerce_uuid(job_opening_id), status=JobOpeningStatus.ON_HOLD
            )
            db.commit()
        except Exception:
            db.rollback()

        return RedirectResponse(
            url=f"/people/recruit/jobs/{job_opening_id}", status_code=303
        )

    def reopen_job_opening_response(
        self,
        auth: WebAuthContext,
        db: Session,
        job_opening_id: str,
    ) -> RedirectResponse:
        """Handle job opening reopen action."""
        org_id = coerce_uuid(auth.organization_id)
        svc = RecruitmentService(db)

        try:
            svc.update_job_opening(
                org_id, coerce_uuid(job_opening_id), status=JobOpeningStatus.OPEN
            )
            db.commit()
        except Exception:
            db.rollback()

        return RedirectResponse(
            url=f"/people/recruit/jobs/{job_opening_id}", status_code=303
        )

    def close_job_opening_response(
        self,
        auth: WebAuthContext,
        db: Session,
        job_opening_id: str,
    ) -> RedirectResponse:
        """Handle job opening close action."""
        org_id = coerce_uuid(auth.organization_id)
        svc = RecruitmentService(db)

        try:
            svc.update_job_opening(
                org_id, coerce_uuid(job_opening_id), status=JobOpeningStatus.CLOSED
            )
            db.commit()
        except Exception:
            db.rollback()

        return RedirectResponse(
            url=f"/people/recruit/jobs/{job_opening_id}", status_code=303
        )

    def delete_job_opening_response(
        self,
        auth: WebAuthContext,
        db: Session,
        job_opening_id: str,
    ) -> RedirectResponse:
        """Handle job opening deletion."""
        org_id = coerce_uuid(auth.organization_id)
        svc = RecruitmentService(db)

        try:
            applicants = svc.list_applicants(
                org_id,
                job_opening_id=coerce_uuid(job_opening_id),
                pagination=PaginationParams(limit=1),
            )
            if applicants.total > 0:
                return RedirectResponse(
                    url=f"/people/recruit/jobs/{job_opening_id}?error=Cannot+delete+job+opening+with+applicants",
                    status_code=303,
                )
            svc.delete_job_opening(org_id, coerce_uuid(job_opening_id))
            db.commit()
            return RedirectResponse(url="/people/recruit/jobs", status_code=303)
        except Exception:
            db.rollback()
            return RedirectResponse(
                url=f"/people/recruit/jobs/{job_opening_id}", status_code=303
            )
