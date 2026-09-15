"""HTML presentation for the configurable People / Performance KPI workspace."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.services.people.perf.kpi_dashboard_contract import (
    DEFAULT_STATUSES,
    DEFAULT_WIDGETS,
    KPI_STATUSES,
    PERIODS,
    WIDGETS,
    DashboardAccessError,
    DashboardConfig,
    DashboardValidationError,
    SavedViewNotFound,
    view_name,
)
from app.services.people.perf.kpi_dashboard_service import KPIDashboardService
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

DASHBOARD_URL = "/people/perf/kpi-dashboard"


def _read_configuration(values) -> DashboardConfig:
    explicit = values.get("configured") == "1"
    return DashboardConfig.parse(
        {
            "period": values.get("period", "this_month"),
            "start_date": values.get("start_date", ""),
            "end_date": values.get("end_date", ""),
            "department_ids": values.getlist("department_id"),
            "statuses": values.getlist("status")
            if explicit or "status" in values
            else list(DEFAULT_STATUSES),
            "widgets": values.getlist("widget")
            if explicit or "widget" in values
            else list(DEFAULT_WIDGETS),
            "search": values.get("search", ""),
        }
    )


def _identifier(value: Any) -> str:
    if not isinstance(value, str):
        raise DashboardValidationError("This saved view is unavailable.")
    try:
        return str(UUID(value))
    except ValueError as exc:
        raise DashboardValidationError("This saved view is unavailable.") from exc


def _pairs(config: DashboardConfig) -> list[tuple[str, str]]:
    return [
        ("configured", "1"),
        ("period", config.period),
        ("start_date", config.start_date),
        ("end_date", config.end_date),
        ("search", config.search),
        *(("department_id", value) for value in config.department_ids),
        *(("status", value) for value in config.statuses),
        *(("widget", value) for value in config.widgets),
    ]


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, DashboardAccessError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, SavedViewNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


class KPIDashboardWebService:
    @staticmethod
    def _scope(
        service: KPIDashboardService, auth: WebAuthContext, *, write: bool = False
    ):
        if not auth.organization_id or not auth.person_id:
            raise DashboardAccessError(
                "An authenticated organization context is required."
            )
        if write and auth.leave_write_restricted:
            raise DashboardAccessError("Your account currently has read-only access.")
        return service.resolve_scope(auth.organization_id, auth.person_id, auth.roles)

    def dashboard_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        *,
        page: int = 1,
    ) -> HTMLResponse:
        service = KPIDashboardService(db)
        try:
            scope = self._scope(service, auth)
            selected_id = request.query_params.get("view_id", "")
            selected_name = ""
            if selected_id:
                selected_id = _identifier(selected_id)
                selected_name, saved_config = service.get_view(scope, selected_id)
                config = (
                    _read_configuration(request.query_params)
                    if request.query_params.get("configured") == "1"
                    else saved_config
                )
            else:
                config = _read_configuration(request.query_params)
            data = service.dashboard(scope, config, page=page)
            saved_views = service.list_views(scope)
        except (
            DashboardValidationError,
            DashboardAccessError,
            SavedViewNotFound,
        ) as exc:
            raise _http_error(exc) from exc

        context = base_context(request, auth, "KPI Dashboard", "perf", db=db)
        pairs = _pairs(config)
        navigation_pairs = pairs + ([("view_id", selected_id)] if selected_id else [])

        def page_url(number: int) -> str:
            return f"{DASHBOARD_URL}?{urlencode(navigation_pairs + [('page', str(number))])}"

        cards = []
        for key in config.widgets:
            label, subtitle, color = WIDGETS[key]
            value = data["summary"][key]
            if key == "coverage":
                display = f"{value}%" if value is not None else "—"
            else:
                display = f"{value:,}"
            cards.append(
                {"label": label, "subtitle": subtitle, "color": color, "value": display}
            )
        context.update(
            {
                **data,
                "config": config,
                "dashboard_url": DASHBOARD_URL,
                "departments": scope.departments,
                "organization_wide": scope.organization_wide,
                "organization_timezone": scope.timezone_name,
                "cards": cards,
                "period_options": PERIODS,
                "status_options": sorted(KPI_STATUSES),
                "widget_options": WIDGETS,
                "widget_slots": list(config.widgets)
                + [""] * (len(WIDGETS) - len(config.widgets)),
                "saved_views": saved_views,
                "selected_view_id": selected_id,
                "selected_view_name": selected_name,
                "configuration_pairs": pairs,
                "previous_page_url": page_url(data["page"] - 1)
                if data["page"] > 1
                else None,
                "next_page_url": page_url(data["page"] + 1)
                if data["page"] < data["total_pages"]
                else None,
                "can_save_view": not auth.leave_write_restricted,
                "can_manage_kpis": scope.organization_wide
                and not auth.leave_write_restricted,
            }
        )
        response = templates.TemplateResponse(
            request, "people/perf/kpi_dashboard.html", context
        )
        response.headers["Cache-Control"] = "private, no-store"
        return response

    async def save_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        service = KPIDashboardService(db)
        try:
            scope = self._scope(service, auth, write=True)
            form = await request.form()
            config = _read_configuration(form)
            mode = form.get("save_mode", "new")
            if mode not in {"new", "update"}:
                raise DashboardValidationError("Choose a valid save action.")
            identifier = (
                _identifier(form.get("view_id", "")) if mode == "update" else None
            )
            identifier = service.save_view(
                scope, config, view_name(form.get("name", "")), view_id=identifier
            )
        except (
            DashboardValidationError,
            DashboardAccessError,
            SavedViewNotFound,
        ) as exc:
            raise _http_error(exc) from exc
        return RedirectResponse(
            f"{DASHBOARD_URL}?{urlencode({'view_id': identifier})}",
            status_code=303,
            headers={"Cache-Control": "private, no-store"},
        )

    def delete_response(
        self,
        auth: WebAuthContext,
        db: Session,
        view_id: UUID,
    ) -> RedirectResponse:
        service = KPIDashboardService(db)
        try:
            scope = self._scope(service, auth, write=True)
            service.delete_view(scope, str(view_id))
        except (
            DashboardValidationError,
            DashboardAccessError,
            SavedViewNotFound,
        ) as exc:
            raise _http_error(exc) from exc
        return RedirectResponse(
            DASHBOARD_URL,
            status_code=303,
            headers={"Cache-Control": "private, no-store"},
        )


kpi_dashboard_web_service = KPIDashboardWebService()
