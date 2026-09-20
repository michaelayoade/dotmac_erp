"""Web response orchestration for the departmental KPI dashboard."""

from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote_plus
from uuid import UUID

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.people.hr import Designation, Employee, EmployeeStatus
from app.models.people.hr.position import Position
from app.models.people.perf import KPI, KPIMeasurementHistory
from app.services.people.perf.departmental_kpi_service import (
    ASSIGNMENT_SCOPES,
    AUTOMATIC_DATA_SOURCES,
    CALCULATION_METHODS,
    KPI_DIRECTIONS,
    KPI_FREQUENCIES,
    MEASUREMENT_TYPES,
    DepartmentalKPIError,
    DepartmentalKPIService,
)
from app.templates import templates
from app.web.deps import WebAuthContext, base_context


def _uuid(value: str | UUID | None) -> UUID | None:
    if isinstance(value, UUID):
        return value
    return UUID(value) if value else None


def _decimal(value: str | None, *, required: bool = False) -> Decimal | None:
    if value is None or value == "":
        if required:
            raise DepartmentalKPIError("A required numeric value is missing")
        return None
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise DepartmentalKPIError(f"Invalid numeric value: {value}") from exc


def _date(value: str | date | None) -> date | None:
    if isinstance(value, date):
        return value
    return date.fromisoformat(value) if value else None


def _month_bounds(today: date | None = None) -> tuple[date, date]:
    current = today or date.today()
    start = current.replace(day=1)
    if current.month == 12:
        next_month = date(current.year + 1, 1, 1)
    else:
        next_month = date(current.year, current.month + 1, 1)
    return start, date.fromordinal(next_month.toordinal() - 1)


class DepartmentalKPIWebService:
    """Build templates and process forms without placing logic in routes."""

    @staticmethod
    def _can_manage(auth: WebAuthContext) -> bool:
        return auth.has_permission("performance:kpi:manage")

    @staticmethod
    def _can_approve(auth: WebAuthContext) -> bool:
        return auth.has_permission("performance:kpi:approve")

    def _allowed_departments(
        self,
        service: DepartmentalKPIService,
        auth: WebAuthContext,
        org_id: UUID,
    ) -> list:
        departments = service.list_departments(org_id)
        if auth.has_permission("performance:kpi:dashboard:view_all_departments"):
            return departments
        if auth.employee_id is None:
            return []
        employee = service.db.scalar(
            select(Employee).where(
                Employee.organization_id == org_id,
                Employee.employee_id == auth.employee_id,
            )
        )
        if employee is None or employee.department_id is None:
            return []
        return [
            department
            for department in departments
            if department.department_id == employee.department_id
        ]

    def _ensure_department_access(
        self,
        service: DepartmentalKPIService,
        auth: WebAuthContext,
        org_id: UUID,
        department_id: UUID | None,
    ) -> None:
        if self._can_manage(auth) or auth.has_permission(
            "performance:kpi:dashboard:view_all_departments"
        ):
            return
        allowed_ids = {
            department.department_id
            for department in self._allowed_departments(service, auth, org_id)
        }
        if department_id not in allowed_ids:
            raise HTTPException(status_code=403, detail="Department KPI access denied")

    def dashboard_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        *,
        department_id: str | UUID | None,
        period_start: str | date | None,
        period_end: str | date | None,
        employee_id: str | UUID | None,
        category: str | None,
        status: str | None,
    ) -> HTMLResponse:
        org_id = UUID(str(auth.organization_id))
        service = DepartmentalKPIService(db)
        departments = self._allowed_departments(service, auth, org_id)
        selected_department_id = _uuid(department_id)
        allowed_ids = {department.department_id for department in departments}
        if selected_department_id not in allowed_ids:
            selected_department_id = (
                departments[0].department_id if departments else None
            )
        default_start, default_end = _month_bounds()
        selected_start = _date(period_start) or default_start
        selected_end = _date(period_end) or default_end

        context = base_context(
            request, auth, "Departmental KPI Dashboard", "perf", db=db
        )
        context.update(
            {
                "departments": departments,
                "selected_department_id": selected_department_id,
                "period_start": selected_start,
                "period_end": selected_end,
                "selected_employee_id": _uuid(employee_id),
                "selected_category": category,
                "selected_status": status,
                "can_manage_kpis": self._can_manage(auth),
                "can_measure_kpis": auth.has_permission("performance:kpi:measure"),
                "can_approve_kpis": self._can_approve(auth),
                "can_export_kpis": auth.has_permission("performance:kpi:export"),
                "dashboard": None,
                "success": request.query_params.get("success"),
                "error": request.query_params.get("error"),
            }
        )
        if selected_department_id:
            context["dashboard"] = service.dashboard(
                org_id,
                department_id=selected_department_id,
                period_start=selected_start,
                period_end=selected_end,
                employee_id=_uuid(employee_id),
                category=category or None,
                status_filter=status or None,
            )
        return templates.TemplateResponse(
            request, "people/perf/departmental_kpi/dashboard.html", context
        )

    def export_dashboard_response(
        self,
        auth: WebAuthContext,
        db: Session,
        *,
        department_id: str | UUID,
        period_start: str | date | None,
        period_end: str | date | None,
        employee_id: str | UUID | None,
        category: str | None,
        status: str | None,
    ) -> Response:
        """Export the same tenant- and department-scoped KPI view as CSV."""
        org_id = UUID(str(auth.organization_id))
        service = DepartmentalKPIService(db)
        selected_department_id = _uuid(department_id)
        if selected_department_id is None:
            raise DepartmentalKPIError("Department is required")
        self._ensure_department_access(service, auth, org_id, selected_department_id)
        default_start, default_end = _month_bounds()
        selected_start = _date(period_start) or default_start
        selected_end = _date(period_end) or default_end
        dashboard = service.dashboard(
            org_id,
            department_id=selected_department_id,
            period_start=selected_start,
            period_end=selected_end,
            employee_id=_uuid(employee_id),
            category=category or None,
            status_filter=status or None,
        )
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(
            [
                "department",
                "period_start",
                "period_end",
                "kpi_code",
                "kpi",
                "target",
                "unit",
                "actual",
                "score_percent",
                "weight_percent",
                "status",
                "trend_delta",
            ]
        )
        for row in dashboard["rows"]:
            writer.writerow(
                [
                    dashboard["department"].department_name,
                    selected_start.isoformat(),
                    selected_end.isoformat(),
                    row["config"].kpi_code,
                    row["config"].kpi_name,
                    row["target_value"],
                    row["unit_of_measure"] or "",
                    row["actual"] if row["actual"] is not None else "",
                    row["score"] if row["score"] is not None else "",
                    row["weightage"],
                    row["status"],
                    row["trend_delta"] if row["trend_delta"] is not None else "",
                ]
            )
        filename = (
            f"department-kpis-{selected_start.isoformat()}-"
            f"{selected_end.isoformat()}.csv"
        )
        return Response(
            content=output.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    def measurement_queue_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        *,
        period_start: date | None,
        period_end: date | None,
        department_id: UUID | None,
        state: str,
        employee_search: str,
        page: int,
    ) -> HTMLResponse:
        org_id = UUID(str(auth.organization_id))
        service = DepartmentalKPIService(db)
        allowed = self._allowed_departments(service, auth, org_id)
        allowed_ids = {department.department_id for department in allowed}
        selected_department_id = department_id if department_id in allowed_ids else None
        today = date.today()
        selected_start = period_start or date(today.year, 1, 1)
        selected_end = period_end or date(today.year, 12, 31)
        if selected_end < selected_start:
            raise HTTPException(status_code=400, detail="Measurement period is invalid")
        rows = service.measurement_queue(
            org_id,
            period_start=selected_start,
            period_end=selected_end,
            department_ids=(
                {selected_department_id}
                if selected_department_id
                else (None if self._can_manage(auth) else allowed_ids)
            ),
            state="all",
            employee_search=employee_search,
        )
        counts = {key: 0 for key in ("missing", "draft", "awaiting_approval", "returned", "automatic", "recorded")}
        for row in rows:
            counts[row["state"]] += 1
        filtered_rows = rows if state == "all" else [row for row in rows if row["state"] == state]
        per_page = 25
        total = len(filtered_rows)
        total_pages = max(1, (total + per_page - 1) // per_page)
        page = min(max(page, 1), total_pages)
        start = (page - 1) * per_page
        page_rows = filtered_rows[start : start + per_page]
        context = base_context(request, auth, "Measurements", "perf", db=db)
        search_param = quote_plus(employee_search)
        context.update(
            {
                "request": request,
                "rows": page_rows,
                "counts": counts,
                "total": total,
                "page": page,
                "total_pages": total_pages,
                "period_start": selected_start,
                "period_end": selected_end,
                "selected_department_id": selected_department_id,
                "state": state,
                "employee_search": employee_search,
                "departments": allowed,
                "can_measure_kpis": auth.has_permission("performance:kpi:measure"),
                "can_approve_kpis": self._can_approve(auth),
                "previous_url": (
                    f"/people/perf/kpi-dashboard/measurements?period_start={selected_start}&period_end={selected_end}&department_id={selected_department_id or ''}&state={state}&employee_search={search_param}&page={page - 1}"
                    if page > 1
                    else None
                ),
                "next_url": (
                    f"/people/perf/kpi-dashboard/measurements?period_start={selected_start}&period_end={selected_end}&department_id={selected_department_id or ''}&state={state}&employee_search={search_param}&page={page + 1}"
                    if page < total_pages
                    else None
                ),
            }
        )
        response = templates.TemplateResponse(
            request, "people/perf/departmental_kpi/measurements.html", context
        )
        response.headers["Cache-Control"] = "private, no-store"
        return response

    def configuration_list_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        *,
        department_id: str | None,
        include_inactive: bool,
    ) -> HTMLResponse:
        org_id = UUID(str(auth.organization_id))
        service = DepartmentalKPIService(db)
        selected_department = _uuid(department_id)
        configurations = service.list_configurations(
            org_id,
            department_id=selected_department,
            include_inactive=include_inactive,
        )
        weight_summaries = {
            department.department_id: service.weight_summary(
                org_id, department.department_id
            )
            for department in service.list_departments(org_id)
        }
        health_by_template = service.configuration_health(
            org_id,
            configurations,
            weight_summaries=weight_summaries,
        )
        context = base_context(request, auth, "KPI Definitions", "perf", db=db)
        context.update(
            {
                "configurations": configurations,
                "departments": service.list_departments(org_id),
                "selected_department_id": selected_department,
                "include_inactive": include_inactive,
                "weight_summaries": weight_summaries,
                "health_by_template": health_by_template,
                "can_measure_kpis": auth.has_permission("performance:kpi:measure"),
                "can_approve_kpis": self._can_approve(auth),
                "success": request.query_params.get("success"),
                "error": request.query_params.get("error"),
            }
        )
        return templates.TemplateResponse(
            request, "people/perf/departmental_kpi/configurations.html", context
        )

    @staticmethod
    def _form_options(db: Session, org_id: UUID) -> dict:
        employees = list(
            db.scalars(
                select(Employee)
                .where(
                    Employee.organization_id == org_id,
                    Employee.status == EmployeeStatus.ACTIVE,
                )
                .order_by(Employee.employee_code)
            ).all()
        )
        designations = list(
            db.scalars(
                select(Designation)
                .where(
                    Designation.organization_id == org_id,
                    Designation.is_active.is_(True),
                )
                .order_by(Designation.designation_name)
            ).all()
        )
        positions = list(
            db.scalars(
                select(Position)
                .where(
                    Position.organization_id == org_id,
                    Position.is_active.is_(True),
                )
                .order_by(Position.position_name)
            ).all()
        )
        return {
            "employees": employees,
            "designations": designations,
            "positions": positions,
            "measurement_types": MEASUREMENT_TYPES,
            "directions": KPI_DIRECTIONS,
            "frequencies": KPI_FREQUENCIES,
            "assignment_scopes": ASSIGNMENT_SCOPES,
            "calculation_methods": CALCULATION_METHODS,
            "automatic_data_sources": AUTOMATIC_DATA_SOURCES,
        }

    def configuration_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        *,
        template_id: str | None = None,
        form_data: dict | None = None,
        error: str | None = None,
    ) -> HTMLResponse:
        org_id = UUID(str(auth.organization_id))
        service = DepartmentalKPIService(db)
        configuration = (
            service.get_configuration(org_id, UUID(template_id))
            if template_id
            else None
        )
        context = base_context(
            request,
            auth,
            "Edit KPI Configuration" if configuration else "New KPI Configuration",
            "perf",
            db=db,
        )
        context.update(
            {
                "configuration": configuration,
                "departments": service.list_departments(org_id),
                "form_data": form_data or {},
                "error": error,
                **self._form_options(db, org_id),
            }
        )
        return templates.TemplateResponse(
            request, "people/perf/departmental_kpi/configuration_form.html", context
        )

    @staticmethod
    def _configuration_values(form: Any) -> dict:
        direction = str(form.get("direction") or "HIGHER_IS_BETTER")
        return {
            "department_id": UUID(str(form.get("department_id"))),
            "kpi_code": str(form.get("kpi_code") or "").strip(),
            "kra_name": str(form.get("kra_name") or "General Performance").strip(),
            "kpi_name": str(form.get("kpi_name") or "").strip(),
            "description": str(form.get("description") or "").strip() or None,
            "category": str(form.get("category") or "").strip() or None,
            "measurement_type": str(form.get("measurement_type") or "NUMBER"),
            "direction": direction,
            "frequency": str(form.get("frequency") or "MONTHLY"),
            "calculation_method": str(form.get("calculation_method") or "RATIO"),
            "target_value": _decimal(
                str(form.get("target_value") or ""), required=True
            ),
            "unit_of_measure": str(form.get("unit_of_measure") or "").strip() or None,
            "weightage": _decimal(str(form.get("weightage") or ""), required=True),
            "scorecard_perspective": str(
                form.get("scorecard_perspective") or "PROCESS"
            ).upper(),
            "metric_source_key": str(form.get("metric_source_key") or "").strip()
            or None,
            "lower_is_better": direction == "LOWER_IS_BETTER",
            "green_threshold": _decimal(
                str(form.get("green_threshold") or ""), required=True
            ),
            "amber_threshold": _decimal(
                str(form.get("amber_threshold") or ""), required=True
            ),
            "band_min_value": _decimal(str(form.get("band_min_value") or "")),
            "band_max_value": _decimal(str(form.get("band_max_value") or "")),
            "assignment_scope": str(form.get("assignment_scope") or "DEPARTMENT"),
            "designation_id": _uuid(str(form.get("designation_id") or "") or None),
            "position_id": _uuid(str(form.get("position_id") or "") or None),
            "employee_id": _uuid(str(form.get("employee_id") or "") or None),
            "effective_start": _date(str(form.get("effective_start") or "") or None),
            "effective_end": _date(str(form.get("effective_end") or "") or None),
            "is_active": str(form.get("is_active") or "") == "on",
        }

    async def save_configuration_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        *,
        template_id: str | None = None,
    ) -> HTMLResponse | RedirectResponse:
        form = await request.form()
        org_id = UUID(str(auth.organization_id))
        service = DepartmentalKPIService(db)
        try:
            values = self._configuration_values(form)
            if not values["kpi_code"] or not values["kpi_name"]:
                raise DepartmentalKPIError("KPI code and name are required")
            if template_id:
                configuration = service.update_configuration(
                    org_id,
                    UUID(template_id),
                    actor_id=auth.person_id,
                    values=values,
                )
                message = "KPI configuration updated"
            else:
                configuration = service.create_configuration(
                    org_id, actor_id=auth.person_id, values=values
                )
                message = "KPI configuration created"
            db.commit()
            return RedirectResponse(
                url=(
                    "/people/perf/kpi-dashboard/configurations"
                    f"?department_id={configuration.department_id}"
                    f"&success={quote_plus(message)}"
                ),
                status_code=303,
            )
        except (ValueError, DepartmentalKPIError, IntegrityError) as exc:
            db.rollback()
            return self.configuration_form_response(
                request,
                auth,
                db,
                template_id=template_id,
                form_data=dict(form),
                error=str(exc),
            )

    def configuration_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        *,
        template_id: str,
    ) -> HTMLResponse:
        org_id = UUID(str(auth.organization_id))
        detail = DepartmentalKPIService(db).configuration_detail(
            org_id, template_id=UUID(template_id)
        )
        self._ensure_department_access(
            DepartmentalKPIService(db),
            auth,
            org_id,
            detail["config"].department_id,
        )
        context = base_context(request, auth, detail["config"].kpi_name, "perf", db=db)
        context.update(
            {
                **detail,
                "can_manage_kpis": self._can_manage(auth),
                "can_measure_kpis": auth.has_permission("performance:kpi:measure"),
                "can_approve_kpis": self._can_approve(auth),
                "success": request.query_params.get("success"),
                "error": request.query_params.get("error"),
            }
        )
        return templates.TemplateResponse(
            request, "people/perf/departmental_kpi/configuration_detail.html", context
        )

    async def instantiate_period_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        form = await request.form()
        org_id = UUID(str(auth.organization_id))
        try:
            period_start = _date(str(form.get("period_start") or ""))
            period_end = _date(str(form.get("period_end") or ""))
            if period_start is None or period_end is None:
                raise DepartmentalKPIError("Period start and end are required")
            department_id = _uuid(str(form.get("department_id") or "") or None)
            result = DepartmentalKPIService(db).ensure_period_records(
                org_id,
                period_start=period_start,
                period_end=period_end,
                department_id=department_id,
                actor_id=auth.person_id,
            )
            db.commit()
            message = (
                f"KPI period prepared: {result['created']} records created, "
                f"{result['skipped']} already existed"
            )
            query = (
                f"department_id={department_id}&period_start={period_start}"
                f"&period_end={period_end}&success={quote_plus(message)}"
            )
        except (ValueError, DepartmentalKPIError) as exc:
            db.rollback()
            query = f"error={quote_plus(str(exc))}"
        return RedirectResponse(
            url=f"/people/perf/kpi-dashboard/department?{query}", status_code=303
        )

    def measurement_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        *,
        kpi_id: str,
        error: str | None = None,
    ) -> HTMLResponse:
        org_id = UUID(str(auth.organization_id))
        kpi = db.scalar(
            select(KPI).where(
                KPI.organization_id == org_id,
                KPI.kpi_id == UUID(kpi_id),
            )
        )
        if kpi is None:
            raise DepartmentalKPIError("KPI record was not found")
        if kpi.department_template_id is None:
            raise DepartmentalKPIError("KPI configuration was not found")
        service = DepartmentalKPIService(db)
        configuration = service.get_configuration(org_id, kpi.department_template_id)
        self._ensure_department_access(
            service,
            auth,
            org_id,
            configuration.department_id,
        )
        context = base_context(request, auth, "Record KPI Measurement", "perf", db=db)
        context.update(
            {
                "kpi": kpi,
                "error": error,
                "can_approve": self._can_approve(auth),
            }
        )
        return templates.TemplateResponse(
            request, "people/perf/departmental_kpi/measurement_form.html", context
        )

    async def save_measurement_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        *,
        kpi_id: str,
    ) -> HTMLResponse | RedirectResponse:
        form = await request.form()
        org_id = UUID(str(auth.organization_id))
        try:
            actual = _decimal(str(form.get("actual_value") or ""), required=True)
            if actual is None:
                raise DepartmentalKPIError("Actual value is required")
            approve = str(form.get("approve") or "") == "on" and self._can_approve(auth)
            service = DepartmentalKPIService(db)
            existing_kpi = db.scalar(
                select(KPI).where(
                    KPI.organization_id == org_id,
                    KPI.kpi_id == UUID(kpi_id),
                )
            )
            if existing_kpi is None:
                raise DepartmentalKPIError("KPI record was not found")
            if existing_kpi.department_template_id is None:
                raise DepartmentalKPIError("KPI configuration was not found")
            configuration = service.get_configuration(
                org_id, existing_kpi.department_template_id
            )
            self._ensure_department_access(
                service,
                auth,
                org_id,
                configuration.department_id,
            )
            kpi = service.record_measurement(
                org_id,
                kpi_id=UUID(kpi_id),
                actual_value=actual,
                actor_id=auth.person_id,
                evidence=str(form.get("evidence") or "").strip() or None,
                notes=str(form.get("notes") or "").strip() or None,
                approve=approve,
            )
            db.commit()
            return RedirectResponse(
                url=(
                    "/people/perf/kpi-dashboard/configurations/"
                    f"{kpi.department_template_id}?success=Measurement+recorded"
                ),
                status_code=303,
            )
        except (ValueError, DepartmentalKPIError) as exc:
            db.rollback()
            return self.measurement_form_response(
                request, auth, db, kpi_id=kpi_id, error=str(exc)
            )

    def approve_measurement_response(
        self,
        auth: WebAuthContext,
        db: Session,
        *,
        history_id: str,
    ) -> RedirectResponse:
        org_id = UUID(str(auth.organization_id))
        service = DepartmentalKPIService(db)
        template_id: UUID | None = None
        try:
            history = db.scalar(
                select(KPIMeasurementHistory).where(
                    KPIMeasurementHistory.organization_id == org_id,
                    KPIMeasurementHistory.history_id == UUID(history_id),
                )
            )
            if history is None:
                raise DepartmentalKPIError("Measurement revision was not found")
            if history.department_template_id is None:
                raise DepartmentalKPIError("KPI configuration was not found")
            configuration = service.get_configuration(
                org_id, history.department_template_id
            )
            self._ensure_department_access(
                service,
                auth,
                org_id,
                configuration.department_id,
            )
            approved = service.approve_measurement(
                org_id,
                history_id=history.history_id,
                actor_id=auth.person_id,
            )
            db.commit()
            query = "success=Measurement+approved"
            template_id = approved.department_template_id
        except (ValueError, DepartmentalKPIError) as exc:
            db.rollback()
            query = f"error={quote_plus(str(exc))}"
        target = (
            f"/people/perf/kpi-dashboard/configurations/{template_id}"
            if template_id
            else "/people/perf/kpi-dashboard/department"
        )
        return RedirectResponse(url=f"{target}?{query}", status_code=303)

    async def refresh_automatic_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        *,
        template_id: str,
    ) -> RedirectResponse:
        form = await request.form()
        org_id = UUID(str(auth.organization_id))
        try:
            service = DepartmentalKPIService(db)
            config = service.get_configuration(org_id, UUID(template_id))
            self._ensure_department_access(service, auth, org_id, config.department_id)
            result = service.refresh_automatic_measurements(
                org_id,
                template_id=UUID(template_id),
                period_start=date.fromisoformat(str(form.get("period_start"))),
                period_end=date.fromisoformat(str(form.get("period_end"))),
                actor_id=auth.person_id,
            )
            db.commit()
            message = f"{result['updated']} measurements refreshed"
            if result["unavailable"]:
                message += f"; {result['unavailable']} had no source data"
            query = f"success={quote_plus(message)}"
        except (ValueError, DepartmentalKPIError) as exc:
            db.rollback()
            query = f"error={quote_plus(str(exc))}"
        return RedirectResponse(
            url=f"/people/perf/kpi-dashboard/configurations/{template_id}?{query}",
            status_code=303,
        )

    def employee_dashboard_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        *,
        employee_id: str,
        period_start: str | None,
        period_end: str | None,
    ) -> HTMLResponse:
        org_id = UUID(str(auth.organization_id))
        default_start, default_end = _month_bounds()
        selected_start = _date(period_start) or default_start
        selected_end = _date(period_end) or default_end
        dashboard = DepartmentalKPIService(db).employee_dashboard(
            org_id,
            employee_id=UUID(employee_id),
            period_start=selected_start,
            period_end=selected_end,
        )
        self._ensure_department_access(
            DepartmentalKPIService(db),
            auth,
            org_id,
            dashboard["employee"].department_id,
        )
        context = base_context(
            request,
            auth,
            f"{dashboard['employee'].full_name} KPI Performance",
            "perf",
            db=db,
        )
        context.update(
            {
                "dashboard": dashboard,
                "period_start": selected_start,
                "period_end": selected_end,
                "can_measure_kpis": auth.has_permission("performance:kpi:measure"),
            }
        )
        return templates.TemplateResponse(
            request, "people/perf/departmental_kpi/employee_dashboard.html", context
        )


departmental_kpi_web_service = DepartmentalKPIWebService()
