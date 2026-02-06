"""Employee lifecycle web service."""

from __future__ import annotations

import logging
from datetime import date as date_type
from typing import Any, Optional, cast
from urllib.parse import quote
from uuid import UUID

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.people.hr import Employee
from app.models.people.hr.checklist_template import (
    ChecklistTemplate,
    ChecklistTemplateType,
)
from app.models.person import Person
from app.services.common import ValidationError, coerce_uuid
from app.services.people.hr import BulkUpdateData, EmployeeService
from app.services.people.hr.lifecycle import LifecycleService
from app.services.people.hr.web import hr_web_service
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

logger = logging.getLogger(__name__)


class LifecycleWebService:
    """Web service for employee lifecycle routes."""

    @staticmethod
    def _parse_bool(value: Optional[str], default: bool = False) -> bool:
        if value is None:
            return default
        return str(value).lower() in {"1", "true", "on", "yes"}

    @staticmethod
    def _form_str(form: Any, key: str) -> str:
        value = form.get(key)
        if value is None:
            return ""
        return str(value).strip()

    @staticmethod
    def new_onboarding_form_response(
        request: Request,
        employee_id: UUID,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        org_id = coerce_uuid(auth.organization_id)
        svc = EmployeeService(db, org_id)
        employee = svc.get_employee(employee_id)
        person = db.get(Person, employee.person_id)

        templates_list = (
            db.query(ChecklistTemplate)
            .filter(
                ChecklistTemplate.organization_id == org_id,
                ChecklistTemplate.template_type == ChecklistTemplateType.ONBOARDING,
                ChecklistTemplate.is_active == True,
            )
            .order_by(ChecklistTemplate.template_name)
            .all()
        )

        context = base_context(request, auth, "New Onboarding", "employees", db=db)
        context["employee"] = employee
        context["person"] = person
        context["templates"] = templates_list
        return templates.TemplateResponse(
            request, "people/hr/onboarding_form.html", context
        )

    @staticmethod
    async def create_onboarding_response(
        request: Request,
        employee_id: UUID,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()

        template_id = LifecycleWebService._form_str(form, "template_id")
        notes = LifecycleWebService._form_str(form, "notes")

        org_id = coerce_uuid(auth.organization_id)
        svc = EmployeeService(db, org_id)
        lifecycle_svc = LifecycleService(db)
        employee = svc.get_employee(employee_id)

        activities = []
        template_name = None
        if template_id:
            template = db.get(ChecklistTemplate, coerce_uuid(template_id))
            if template:
                template_name = template.template_name
                for item in sorted(template.items, key=lambda x: x.sequence):
                    activities.append(
                        {
                            "activity_name": item.item_name,
                            "sequence": item.sequence,
                        }
                    )

        lifecycle_svc.create_onboarding(
            org_id,
            employee_id=employee_id,
            date_of_joining=employee.date_of_joining,
            department_id=employee.department_id,
            designation_id=employee.designation_id,
            template_name=template_name,
            notes=notes or None,
            activities=activities,
        )
        db.commit()

        return RedirectResponse(
            url=f"/people/hr/employees/{employee_id}", status_code=303
        )

    @staticmethod
    async def start_onboarding_response(
        employee_id: UUID,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        org_id = coerce_uuid(auth.organization_id)
        lifecycle_svc = LifecycleService(db)

        onboarding = lifecycle_svc.get_onboarding_for_employee(org_id, employee_id)
        if onboarding:
            lifecycle_svc.start_onboarding(org_id, onboarding.onboarding_id)
            db.commit()

        return RedirectResponse(
            url=f"/people/hr/employees/{employee_id}", status_code=303
        )

    @staticmethod
    async def toggle_onboarding_activity_response(
        request: Request,
        employee_id: UUID,
        activity_id: UUID,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()

        completed = LifecycleWebService._parse_bool(
            LifecycleWebService._form_str(form, "completed"),
            True,
        )

        org_id = coerce_uuid(auth.organization_id)
        lifecycle_svc = LifecycleService(db)

        onboarding = lifecycle_svc.get_onboarding_for_employee(org_id, employee_id)
        if onboarding:
            lifecycle_svc.complete_onboarding_activity(
                org_id, onboarding.onboarding_id, activity_id, completed
            )
            db.commit()

        return RedirectResponse(
            url=f"/people/hr/employees/{employee_id}", status_code=303
        )

    @staticmethod
    async def complete_onboarding_response(
        employee_id: UUID,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        org_id = coerce_uuid(auth.organization_id)
        lifecycle_svc = LifecycleService(db)

        onboarding = lifecycle_svc.get_onboarding_for_employee(org_id, employee_id)
        if onboarding:
            lifecycle_svc.complete_onboarding(org_id, onboarding.onboarding_id)
            db.commit()

        return RedirectResponse(
            url=f"/people/hr/employees/{employee_id}", status_code=303
        )

    @staticmethod
    async def create_employee_user_credentials_response(
        request: Request,
        employee_id: UUID,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()

        username = LifecycleWebService._form_str(form, "username")
        password = LifecycleWebService._form_str(form, "password")
        must_change = LifecycleWebService._parse_bool(
            LifecycleWebService._form_str(form, "must_change_password"),
            False,
        )

        org_id = coerce_uuid(auth.organization_id)
        svc = EmployeeService(db, org_id)

        try:
            svc.create_user_credentials_for_employee(
                employee_id,
                username=username or None,
                password=password or None,
                must_change_password=must_change,
            )
            db.commit()
            return RedirectResponse(
                url=f"/people/hr/employees/{employee_id}", status_code=303
            )
        except ValidationError as exc:
            db.rollback()
            response = hr_web_service.employee_detail_response(
                request, auth, db, str(employee_id)
            )
            context = cast(Any, response).context
            context["user_access_error"] = str(exc)
            return templates.TemplateResponse(
                request, "people/hr/employee_detail.html", context
            )

    @staticmethod
    async def link_employee_user_response(
        request: Request,
        employee_id: UUID,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()

        person_id = LifecycleWebService._form_str(form, "person_id")
        if not person_id:
            response = hr_web_service.employee_detail_response(
                request, auth, db, str(employee_id)
            )
            context = cast(Any, response).context
            context["user_access_error"] = "Person ID is required."
            return templates.TemplateResponse(
                request, "people/hr/employee_detail.html", context
            )

        org_id = coerce_uuid(auth.organization_id)
        svc = EmployeeService(db, org_id)

        try:
            person_uuid = coerce_uuid(person_id, raise_http=False)
        except Exception:
            response = hr_web_service.employee_detail_response(
                request, auth, db, str(employee_id)
            )
            context = cast(Any, response).context
            context["user_access_error"] = "Invalid Person ID."
            return templates.TemplateResponse(
                request, "people/hr/employee_detail.html", context
            )

        try:
            svc.link_employee_to_person(employee_id, person_uuid)
            db.commit()
            return RedirectResponse(
                url=f"/people/hr/employees/{employee_id}", status_code=303
            )
        except ValidationError as exc:
            db.rollback()
            response = hr_web_service.employee_detail_response(
                request, auth, db, str(employee_id)
            )
            context = cast(Any, response).context
            context["user_access_error"] = str(exc)
            return templates.TemplateResponse(
                request, "people/hr/employee_detail.html", context
            )

    @staticmethod
    def search_people_response(
        query: str,
        auth: WebAuthContext,
        db: Session,
    ) -> JSONResponse:
        org_id = coerce_uuid(auth.organization_id)
        search_term = f"%{query.strip()}%"
        linked_people_subq = (
            select(Employee.person_id)
            .where(Employee.organization_id == org_id)
            .where(Employee.is_deleted == False)
        )
        results = (
            db.query(Person)
            .filter(Person.organization_id == org_id)
            .filter(Person.id.not_in(linked_people_subq))
            .filter(
                (Person.first_name.ilike(search_term))
                | (Person.last_name.ilike(search_term))
                | (Person.email.ilike(search_term))
            )
            .order_by(Person.first_name.asc())
            .limit(10)
            .all()
        )
        payload = [
            {
                "id": str(person.id),
                "name": f"{person.first_name or ''} {person.last_name or ''}".strip(),
                "email": person.email,
            }
            for person in results
        ]
        return JSONResponse(payload)

    @staticmethod
    async def bulk_update_employees_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()

        employee_ids = form.getlist("employee_ids")
        department_id = LifecycleWebService._form_str(form, "department_id")
        designation_id = LifecycleWebService._form_str(form, "designation_id")
        reports_to_id = LifecycleWebService._form_str(form, "reports_to_id")
        status = LifecycleWebService._form_str(form, "status")

        if not employee_ids:
            return RedirectResponse(url="/people/hr/employees", status_code=303)

        status_enum = None
        if status:
            from app.models.people.hr import EmployeeStatus as EmpStatus

            try:
                status_enum = EmpStatus(status.upper())
            except ValueError:
                status_enum = None

        org_id = coerce_uuid(auth.organization_id)
        svc = EmployeeService(db, org_id)

        valid_ids = []
        for emp_id in employee_ids:
            try:
                valid_ids.append(coerce_uuid(emp_id))
            except Exception:
                pass

        if not valid_ids:
            return RedirectResponse(
                url="/people/hr/employees?error=No+valid+employees+selected",
                status_code=303,
            )

        data = BulkUpdateData(
            ids=valid_ids,
            department_id=coerce_uuid(department_id) if department_id else None,
            designation_id=coerce_uuid(designation_id) if designation_id else None,
            reports_to_id=coerce_uuid(reports_to_id) if reports_to_id else None,
            status=status_enum,
        )

        svc.bulk_update(data)
        db.commit()

        success_msg = quote(f"Successfully updated {len(valid_ids)} employee(s)")
        return RedirectResponse(
            url=f"/people/hr/employees?success={success_msg}", status_code=303
        )

    @staticmethod
    async def bulk_delete_employees_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()

        employee_ids = form.getlist("employee_ids")
        if not employee_ids:
            return RedirectResponse(url="/people/hr/employees", status_code=303)

        valid_ids = []
        for emp_id in employee_ids:
            try:
                valid_ids.append(coerce_uuid(emp_id))
            except Exception:
                pass

        if not valid_ids:
            return RedirectResponse(
                url="/people/hr/employees?error=No+valid+employees+selected",
                status_code=303,
            )

        org_id = coerce_uuid(auth.organization_id)
        svc = EmployeeService(db, org_id)
        svc.bulk_delete(valid_ids)
        db.commit()

        success_msg = quote(f"Successfully deleted {len(valid_ids)} employee(s)")
        return RedirectResponse(
            url=f"/people/hr/employees?success={success_msg}", status_code=303
        )

    @staticmethod
    def list_promotions_response(
        request: Request,
        employee_id: Optional[str],
        page: int,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        from app.services.common import PaginationParams

        org_id = coerce_uuid(auth.organization_id)
        lifecycle_svc = LifecycleService(db)
        pagination = PaginationParams.from_page(page, per_page=20)

        emp_uuid = coerce_uuid(employee_id) if employee_id else None
        result = lifecycle_svc.list_promotions(
            org_id, employee_id=emp_uuid, pagination=pagination
        )

        context = base_context(request, auth, "Promotions", "employees", db=db)
        context.update(
            {
                "promotions": result.items,
                "employee_id": employee_id,
                "page": result.page,
                "total_pages": result.total_pages,
                "has_prev": result.has_prev,
                "has_next": result.has_next,
            }
        )
        return templates.TemplateResponse(request, "people/hr/promotions.html", context)

    @staticmethod
    def new_promotion_form_response(
        request: Request,
        employee_id: UUID,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        from app.services.common import PaginationParams
        from app.services.people.hr import (
            DepartmentFilters,
            DesignationFilters,
            OrganizationService,
        )

        org_id = coerce_uuid(auth.organization_id)
        svc = EmployeeService(db, org_id)
        org_svc = OrganizationService(db, org_id)
        employee = svc.get_employee(employee_id)

        designations = org_svc.list_designations(
            DesignationFilters(is_active=True),
            PaginationParams(limit=200),
        ).items
        departments = org_svc.list_departments(
            DepartmentFilters(is_active=True),
            PaginationParams(limit=200),
        ).items

        context = base_context(request, auth, "Record Promotion", "employees", db=db)
        context.update(
            {
                "employee": employee,
                "designations": designations,
                "departments": departments,
                "form_data": {},
                "errors": {},
            }
        )
        return templates.TemplateResponse(
            request, "people/hr/promotion_form.html", context
        )

    @staticmethod
    async def create_promotion_response(
        request: Request,
        employee_id: UUID,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()

        promotion_date_str = LifecycleWebService._form_str(form, "promotion_date")
        new_designation_id = LifecycleWebService._form_str(form, "new_designation_id")
        new_department_id = LifecycleWebService._form_str(form, "new_department_id")
        new_reports_to_id = LifecycleWebService._form_str(form, "new_reports_to_id")
        notes = LifecycleWebService._form_str(form, "notes")

        org_id = coerce_uuid(auth.organization_id)
        svc = EmployeeService(db, org_id)
        lifecycle_svc = LifecycleService(db)
        employee = svc.get_employee(employee_id)

        try:
            promotion_date = (
                date_type.fromisoformat(promotion_date_str)
                if promotion_date_str
                else date_type.today()
            )
        except ValueError:
            promotion_date = date_type.today()

        details = []

        if new_designation_id:
            current_designation = (
                employee.designation.designation_name if employee.designation else "-"
            )
            from app.models.people.hr import Designation

            new_desig = db.get(Designation, coerce_uuid(new_designation_id))
            if new_desig:
                details.append(
                    {
                        "property_name": "Designation",
                        "current_value": current_designation,
                        "new_value": new_desig.designation_name,
                    }
                )
                employee.designation_id = coerce_uuid(new_designation_id)

        if new_department_id:
            current_department = (
                employee.department.department_name if employee.department else "-"
            )
            from app.models.people.hr import Department

            new_dept = db.get(Department, coerce_uuid(new_department_id))
            if new_dept:
                details.append(
                    {
                        "property_name": "Department",
                        "current_value": current_department,
                        "new_value": new_dept.department_name,
                    }
                )
                employee.department_id = coerce_uuid(new_department_id)

        if new_reports_to_id:
            current_manager = employee.manager.full_name if employee.manager else "-"
            new_manager = db.get(Employee, coerce_uuid(new_reports_to_id))
            if new_manager:
                details.append(
                    {
                        "property_name": "Reports To",
                        "current_value": current_manager,
                        "new_value": new_manager.full_name,
                    }
                )
                employee.reports_to_id = coerce_uuid(new_reports_to_id)

        lifecycle_svc.create_promotion(
            org_id,
            employee_id=employee_id,
            promotion_date=promotion_date,
            notes=notes or None,
            details=details,
        )
        db.commit()

        return RedirectResponse(
            url=f"/people/hr/employees/{employee_id}?success=Promotion+recorded",
            status_code=303,
        )

    @staticmethod
    def promotion_detail_response(
        request: Request,
        promotion_id: UUID,
        success: Optional[str],
        error: Optional[str],
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        org_id = coerce_uuid(auth.organization_id)
        lifecycle_svc = LifecycleService(db)

        try:
            promotion = lifecycle_svc.get_promotion(org_id, promotion_id)
        except Exception:
            return RedirectResponse(url="/people/hr/promotions", status_code=303)

        employee = db.get(Employee, promotion.employee_id)

        context = base_context(request, auth, "Promotion Details", "employees", db=db)
        context.update(
            {
                "promotion": promotion,
                "employee": employee,
                "success": success,
                "error": error,
            }
        )
        return templates.TemplateResponse(
            request, "people/hr/promotion_detail.html", context
        )

    @staticmethod
    def list_transfers_response(
        request: Request,
        employee_id: Optional[str],
        page: int,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        from app.services.common import PaginationParams

        org_id = coerce_uuid(auth.organization_id)
        lifecycle_svc = LifecycleService(db)
        pagination = PaginationParams.from_page(page, per_page=20)

        emp_uuid = coerce_uuid(employee_id) if employee_id else None
        result = lifecycle_svc.list_transfers(
            org_id, employee_id=emp_uuid, pagination=pagination
        )

        context = base_context(request, auth, "Transfers", "employees", db=db)
        context.update(
            {
                "transfers": result.items,
                "employee_id": employee_id,
                "page": result.page,
                "total_pages": result.total_pages,
                "has_prev": result.has_prev,
                "has_next": result.has_next,
            }
        )
        return templates.TemplateResponse(request, "people/hr/transfers.html", context)

    @staticmethod
    def new_transfer_form_response(
        request: Request,
        employee_id: UUID,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        from app.services.common import PaginationParams
        from app.services.people.hr import (
            DepartmentFilters,
            DesignationFilters,
            OrganizationService,
        )

        org_id = coerce_uuid(auth.organization_id)
        svc = EmployeeService(db, org_id)
        org_svc = OrganizationService(db, org_id)
        employee = svc.get_employee(employee_id)

        designations = org_svc.list_designations(
            DesignationFilters(is_active=True),
            PaginationParams(limit=200),
        ).items
        departments = org_svc.list_departments(
            DepartmentFilters(is_active=True),
            PaginationParams(limit=200),
        ).items

        context = base_context(request, auth, "Record Transfer", "employees", db=db)
        context.update(
            {
                "employee": employee,
                "designations": designations,
                "departments": departments,
                "form_data": {},
                "errors": {},
            }
        )
        return templates.TemplateResponse(
            request, "people/hr/transfer_form.html", context
        )

    @staticmethod
    async def create_transfer_response(
        request: Request,
        employee_id: UUID,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()

        transfer_date_str = LifecycleWebService._form_str(form, "transfer_date")
        new_department_id = LifecycleWebService._form_str(form, "new_department_id")
        new_designation_id = LifecycleWebService._form_str(form, "new_designation_id")
        new_reports_to_id = LifecycleWebService._form_str(form, "new_reports_to_id")
        new_branch = LifecycleWebService._form_str(form, "new_branch")
        notes = LifecycleWebService._form_str(form, "notes")

        org_id = coerce_uuid(auth.organization_id)
        svc = EmployeeService(db, org_id)
        lifecycle_svc = LifecycleService(db)
        employee = svc.get_employee(employee_id)

        try:
            transfer_date = (
                date_type.fromisoformat(transfer_date_str)
                if transfer_date_str
                else date_type.today()
            )
        except ValueError:
            transfer_date = date_type.today()

        details = []

        if new_department_id:
            current_department = (
                employee.department.department_name if employee.department else "-"
            )
            from app.models.people.hr import Department

            new_dept = db.get(Department, coerce_uuid(new_department_id))
            if new_dept:
                details.append(
                    {
                        "property_name": "Department",
                        "current_value": current_department,
                        "new_value": new_dept.department_name,
                    }
                )
                employee.department_id = coerce_uuid(new_department_id)

        if new_designation_id:
            current_designation = (
                employee.designation.designation_name if employee.designation else "-"
            )
            from app.models.people.hr import Designation

            new_desig = db.get(Designation, coerce_uuid(new_designation_id))
            if new_desig:
                details.append(
                    {
                        "property_name": "Designation",
                        "current_value": current_designation,
                        "new_value": new_desig.designation_name,
                    }
                )
                employee.designation_id = coerce_uuid(new_designation_id)

        if new_reports_to_id:
            current_manager = employee.manager.full_name if employee.manager else "-"
            new_manager = db.get(Employee, coerce_uuid(new_reports_to_id))
            if new_manager:
                details.append(
                    {
                        "property_name": "Reports To",
                        "current_value": current_manager,
                        "new_value": new_manager.full_name,
                    }
                )
                employee.reports_to_id = coerce_uuid(new_reports_to_id)

        if new_branch:
            current_branch = (
                employee.assigned_location.location_name
                if employee.assigned_location
                else "-"
            )
            details.append(
                {
                    "property_name": "Branch",
                    "current_value": current_branch,
                    "new_value": new_branch,
                }
            )
            from app.models.finance.core_org.location import Location

            new_location = db.scalar(
                select(Location).where(
                    Location.organization_id == org_id,
                    Location.location_name == new_branch,
                )
            )
            if new_location:
                employee.assigned_location_id = new_location.location_id

        lifecycle_svc.create_transfer(
            org_id,
            employee_id=employee_id,
            transfer_date=transfer_date,
            notes=notes or None,
            details=details,
        )
        db.commit()

        return RedirectResponse(
            url=f"/people/hr/employees/{employee_id}?success=Transfer+recorded",
            status_code=303,
        )

    @staticmethod
    def transfer_detail_response(
        request: Request,
        transfer_id: UUID,
        success: Optional[str],
        error: Optional[str],
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        org_id = coerce_uuid(auth.organization_id)
        lifecycle_svc = LifecycleService(db)

        try:
            transfer = lifecycle_svc.get_transfer(org_id, transfer_id)
        except Exception:
            return RedirectResponse(url="/people/hr/transfers", status_code=303)

        employee = db.get(Employee, transfer.employee_id)

        context = base_context(request, auth, "Transfer Details", "employees", db=db)
        context.update(
            {
                "transfer": transfer,
                "employee": employee,
                "success": success,
                "error": error,
            }
        )
        return templates.TemplateResponse(
            request, "people/hr/transfer_detail.html", context
        )


lifecycle_web_service = LifecycleWebService()
