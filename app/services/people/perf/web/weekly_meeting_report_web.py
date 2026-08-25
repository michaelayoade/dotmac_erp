"""Web orchestration for weekly meeting reports."""

from __future__ import annotations

import json
import logging
from datetime import date, time
from typing import Any
from urllib.parse import quote_plus
from uuid import UUID

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.people.perf.weekly_meeting_report import (
    MeetingActionStatus,
    MeetingAttendanceStatus,
    MeetingParticipantSource,
    ReportEmailStatus,
    WeeklyMeetingReport,
    WeeklyMeetingReportStatus,
)
from app.services.common import coerce_uuid
from app.services.people.perf.weekly_meeting_report_service import (
    ActionItemInput,
    ParticipantInput,
    WeeklyMeetingReportError,
    WeeklyMeetingReportInput,
    WeeklyMeetingReportNotFoundError,
    WeeklyMeetingReportService,
)
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

logger = logging.getLogger(__name__)

BASE_URL = "/people/perf/weekly-meeting-reports"


class WeeklyMeetingReportWebService:
    """Build report view contexts and own HTTP transaction boundaries."""

    def list_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        *,
        search: str = "",
        status: str = "",
        department_id: str = "",
        page: int = 1,
    ) -> HTMLResponse:
        org_id = self._org_id(auth)
        service = WeeklyMeetingReportService(db)
        filter_error = None
        try:
            parsed_status = self._optional_status(status)
            parsed_department = coerce_uuid(department_id) if department_id else None
        except (ValueError, TypeError):
            parsed_status = None
            parsed_department = None
            filter_error = "One or more report filters were invalid and were ignored."
        reports, total = service.list_reports(
            org_id,
            search=search,
            status=parsed_status,
            department_id=parsed_department,
            page=page,
        )
        total_pages = max(1, (total + 19) // 20)
        context = base_context(
            request, auth, "Weekly Meeting Reports", "perf-weekly-reports", db=db
        )
        context.update(
            {
                "reports": reports,
                "departments": service.list_departments(org_id),
                "search": search,
                "status_filter": status,
                "department_filter": department_id,
                "page": page,
                "total": total,
                "total_pages": total_pages,
                "has_prev": page > 1,
                "has_next": page < total_pages,
                "can_write": auth.has_any_permission(
                    ["performance:weekly_reports:write"]
                ),
                "success": request.query_params.get("success"),
                "error": request.query_params.get("error") or filter_error,
            }
        )
        return templates.TemplateResponse(
            request, "people/perf/weekly_meeting_reports/list.html", context
        )

    def new_form_response(
        self, request: Request, auth: WebAuthContext, db: Session
    ) -> HTMLResponse:
        return self._form_response(request, auth, db, report=None)

    def edit_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        report_id: str,
    ) -> Response:
        service = WeeklyMeetingReportService(db)
        report = self._get_report_or_404(service, self._org_id(auth), report_id)
        if report.status != WeeklyMeetingReportStatus.DRAFT:
            return RedirectResponse(
                f"{BASE_URL}/{report.report_id}?error="
                + quote_plus("Submitted reports must be reopened before editing"),
                status_code=303,
            )
        return self._form_response(request, auth, db, report=report)

    async def save_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        *,
        report_id: str | None = None,
        submit: bool = False,
    ) -> Response:
        org_id = self._org_id(auth)
        person_id = self._person_id(auth)
        service = WeeklyMeetingReportService(db)
        raw_form = await request.form()
        try:
            data = self._parse_report_input(raw_form)
            report = service.save_draft(
                org_id,
                person_id,
                coerce_uuid(auth.employee_id) if auth.employee_id else None,
                data,
                report_id=coerce_uuid(report_id) if report_id else None,
            )
            if submit:
                report = service.submit(org_id, report.report_id, person_id)
            db.commit()
        except (
            WeeklyMeetingReportError,
            ValueError,
            TypeError,
            json.JSONDecodeError,
        ) as exc:
            db.rollback()
            logger.info("Weekly meeting report validation failed: %s", exc)
            existing_report = self._reload_report_for_form(db, org_id, report_id)
            return self._form_response(
                request,
                auth,
                db,
                report=existing_report,
                form_values=self._form_values_from_raw(raw_form),
                error=str(exc),
                status_code=422,
            )
        except IntegrityError:
            db.rollback()
            existing_report = self._reload_report_for_form(db, org_id, report_id)
            return self._form_response(
                request,
                auth,
                db,
                report=existing_report,
                form_values=self._form_values_from_raw(raw_form),
                error="A report already exists for this division and week ending.",
                status_code=409,
            )

        if submit:
            self._queue_notification(report)
            message = "Weekly meeting report submitted"
        else:
            message = "Draft saved"
        return RedirectResponse(
            f"{BASE_URL}/{report.report_id}?success={quote_plus(message)}",
            status_code=303,
        )

    def detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        report_id: str,
        *,
        print_view: bool = False,
    ) -> HTMLResponse:
        service = WeeklyMeetingReportService(db)
        report = self._get_report_or_404(
            service,
            self._org_id(auth),
            report_id,
        )
        context = base_context(
            request,
            auth,
            report.report_number,
            "perf-weekly-reports",
            db=db,
        )
        context.update(
            {
                "report": report,
                "can_edit": auth.has_any_permission(
                    ["performance:weekly_reports:write"]
                ),
                "can_reopen": auth.has_permission("performance:weekly_reports:reopen"),
                "success": request.query_params.get("success"),
                "error": request.query_params.get("error"),
            }
        )
        template_name = (
            "people/perf/weekly_meeting_reports/print.html"
            if print_view
            else "people/perf/weekly_meeting_reports/detail.html"
        )
        return templates.TemplateResponse(request, template_name, context)

    @staticmethod
    def _reload_report_for_form(
        db: Session, organization_id: UUID, report_id: str | None
    ) -> WeeklyMeetingReport | None:
        if not report_id:
            return None
        try:
            return WeeklyMeetingReportService(db).get_report(
                organization_id, coerce_uuid(report_id)
            )
        except (WeeklyMeetingReportError, ValueError, TypeError):
            return None

    @staticmethod
    def _get_report_or_404(
        service: WeeklyMeetingReportService,
        organization_id: UUID,
        report_id: str,
    ) -> WeeklyMeetingReport:
        try:
            return service.get_report(organization_id, coerce_uuid(report_id))
        except (WeeklyMeetingReportNotFoundError, ValueError, TypeError) as exc:
            raise HTTPException(
                status_code=404, detail="Weekly meeting report not found"
            ) from exc

    def roster_response(
        self,
        auth: WebAuthContext,
        db: Session,
        department_id: str,
    ) -> JSONResponse:
        try:
            roster = WeeklyMeetingReportService(db).department_roster(
                self._org_id(auth), coerce_uuid(department_id)
            )
        except (WeeklyMeetingReportError, ValueError, TypeError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return JSONResponse(roster)

    def refresh_response(
        self,
        auth: WebAuthContext,
        db: Session,
        report_id: str,
    ) -> RedirectResponse:
        try:
            report = WeeklyMeetingReportService(db).refresh_from_hr(
                self._org_id(auth), coerce_uuid(report_id), self._person_id(auth)
            )
            db.commit()
            return RedirectResponse(
                f"{BASE_URL}/{report.report_id}/edit?success="
                + quote_plus("Current HR information merged into the draft"),
                status_code=303,
            )
        except (WeeklyMeetingReportError, ValueError, TypeError) as exc:
            db.rollback()
            return RedirectResponse(
                f"{BASE_URL}/{report_id}?error={quote_plus(str(exc))}",
                status_code=303,
            )

    def reopen_response(
        self,
        auth: WebAuthContext,
        db: Session,
        report_id: str,
    ) -> RedirectResponse:
        try:
            report = WeeklyMeetingReportService(db).reopen(
                self._org_id(auth), coerce_uuid(report_id), self._person_id(auth)
            )
            db.commit()
            return RedirectResponse(
                f"{BASE_URL}/{report.report_id}/edit?success="
                + quote_plus("Report reopened as a draft"),
                status_code=303,
            )
        except (WeeklyMeetingReportError, ValueError, TypeError) as exc:
            db.rollback()
            return RedirectResponse(
                f"{BASE_URL}?error={quote_plus(str(exc))}", status_code=303
            )

    def retry_notification_response(
        self,
        auth: WebAuthContext,
        db: Session,
        report_id: str,
    ) -> RedirectResponse:
        try:
            report = WeeklyMeetingReportService(db).mark_notification_pending(
                self._org_id(auth), coerce_uuid(report_id)
            )
            db.commit()
            self._queue_notification(report)
            return RedirectResponse(
                f"{BASE_URL}/{report.report_id}?success="
                + quote_plus("HR email queued for retry"),
                status_code=303,
            )
        except (WeeklyMeetingReportError, ValueError, TypeError) as exc:
            db.rollback()
            return RedirectResponse(
                f"{BASE_URL}?error={quote_plus(str(exc))}", status_code=303
            )

    def _form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        *,
        report: WeeklyMeetingReport | None,
        form_values: dict[str, Any] | None = None,
        error: str | None = None,
        status_code: int = 200,
    ) -> HTMLResponse:
        org_id = self._org_id(auth)
        service = WeeklyMeetingReportService(db)
        context = base_context(
            request,
            auth,
            "Weekly Meeting Report",
            "perf-weekly-reports",
            db=db,
        )
        context.update(
            {
                "report": report,
                "departments": service.list_departments(org_id),
                "employee_options": service.list_employee_options(org_id),
                "form_values": form_values or self._report_form_values(report),
                "attendance_statuses": [item.value for item in MeetingAttendanceStatus],
                "action_statuses": [item.value for item in MeetingActionStatus],
                "error": error or request.query_params.get("error"),
                "success": request.query_params.get("success"),
            }
        )
        return templates.TemplateResponse(
            request,
            "people/perf/weekly_meeting_reports/form.html",
            context,
            status_code=status_code,
        )

    @staticmethod
    def _parse_report_input(form: Any) -> WeeklyMeetingReportInput:
        participants_raw = json.loads(str(form.get("participants_json") or "[]"))
        actions_raw = json.loads(str(form.get("action_items_json") or "[]"))
        participants = [
            ParticipantInput(
                employee_id=coerce_uuid(row["employee_id"])
                if row.get("employee_id")
                else None,
                name=str(row.get("name") or "").strip(),
                role=str(row.get("role") or "").strip(),
                attendance_status=MeetingAttendanceStatus(
                    str(row.get("attendance_status") or "INVITED")
                ),
                source=MeetingParticipantSource(str(row.get("source") or "EMPLOYEE")),
                role_overridden=bool(row.get("role_overridden", False)),
            )
            for row in participants_raw
            if isinstance(row, dict)
        ]
        action_items = [
            ActionItemInput(
                action_text=str(row.get("action_text") or "").strip(),
                owner_employee_id=coerce_uuid(row["owner_employee_id"])
                if row.get("owner_employee_id")
                else None,
                owner_name=str(row.get("owner_name") or "").strip(),
                due_date=date.fromisoformat(str(row["due_date"]))
                if row.get("due_date")
                else None,
                status=MeetingActionStatus(str(row.get("status") or "NOT_STARTED")),
            )
            for row in actions_raw
            if isinstance(row, dict)
        ]
        return WeeklyMeetingReportInput(
            department_id=coerce_uuid(form.get("department_id")),
            division_head_employee_id=coerce_uuid(form.get("division_head_employee_id"))
            if form.get("division_head_employee_id")
            else None,
            week_ending=date.fromisoformat(str(form.get("week_ending") or "")),
            meeting_date=date.fromisoformat(str(form.get("meeting_date") or "")),
            meeting_time=time.fromisoformat(str(form.get("meeting_time") or "")),
            purpose_context=str(form.get("purpose_context") or "").strip(),
            matters_discussed=str(form.get("matters_discussed") or "").strip(),
            key_decisions=str(form.get("key_decisions") or "").strip(),
            issues_risks_support=str(form.get("issues_risks_support") or "").strip(),
            carry_forward=str(form.get("carry_forward") or "").strip(),
            participants=participants,
            action_items=action_items,
        )

    @staticmethod
    def _report_form_values(report: WeeklyMeetingReport | None) -> dict[str, Any]:
        today = date.today()
        if report is None:
            return {
                "department_id": "",
                "division_head_employee_id": "",
                "week_ending": today.isoformat(),
                "meeting_date": today.isoformat(),
                "meeting_time": "09:00",
                "purpose_context": "",
                "matters_discussed": "",
                "key_decisions": "",
                "issues_risks_support": "",
                "carry_forward": "",
                "participants": [],
                "action_items": [],
            }
        return {
            "department_id": str(report.department_id),
            "division_head_employee_id": str(report.division_head_employee_id or ""),
            "week_ending": report.week_ending.isoformat(),
            "meeting_date": report.meeting_date.isoformat(),
            "meeting_time": report.meeting_time.strftime("%H:%M"),
            "purpose_context": report.purpose_context or "",
            "matters_discussed": report.matters_discussed or "",
            "key_decisions": report.key_decisions or "",
            "issues_risks_support": report.issues_risks_support or "",
            "carry_forward": report.carry_forward or "",
            "participants": [
                {
                    "employee_id": str(item.employee_id or ""),
                    "name": item.name_snapshot,
                    "role": item.role_snapshot or "",
                    "attendance_status": item.attendance_status.value,
                    "source": item.source.value,
                    "role_overridden": item.role_overridden,
                }
                for item in report.participants
            ],
            "action_items": [
                {
                    "action_text": item.action_text,
                    "owner_employee_id": str(item.owner_employee_id or ""),
                    "owner_name": item.owner_name_snapshot or "",
                    "due_date": item.due_date.isoformat() if item.due_date else "",
                    "status": item.status.value,
                }
                for item in report.action_items
            ],
        }

    @staticmethod
    def _form_values_from_raw(form: Any) -> dict[str, Any]:
        def decoded(key: str) -> list[dict[str, Any]]:
            try:
                value = json.loads(str(form.get(key) or "[]"))
                return value if isinstance(value, list) else []
            except json.JSONDecodeError:
                return []

        return {
            "department_id": str(form.get("department_id") or ""),
            "division_head_employee_id": str(
                form.get("division_head_employee_id") or ""
            ),
            "week_ending": str(form.get("week_ending") or ""),
            "meeting_date": str(form.get("meeting_date") or ""),
            "meeting_time": str(form.get("meeting_time") or ""),
            "purpose_context": str(form.get("purpose_context") or ""),
            "matters_discussed": str(form.get("matters_discussed") or ""),
            "key_decisions": str(form.get("key_decisions") or ""),
            "issues_risks_support": str(form.get("issues_risks_support") or ""),
            "carry_forward": str(form.get("carry_forward") or ""),
            "participants": decoded("participants_json"),
            "action_items": decoded("action_items_json"),
        }

    @staticmethod
    def _optional_status(value: str) -> WeeklyMeetingReportStatus | None:
        return WeeklyMeetingReportStatus(value) if value else None

    @staticmethod
    def _org_id(auth: WebAuthContext) -> UUID:
        if auth.organization_id is None:
            raise WeeklyMeetingReportError("Organization context is required")
        return UUID(str(auth.organization_id))

    @staticmethod
    def _person_id(auth: WebAuthContext) -> UUID:
        if auth.person_id is None:
            raise WeeklyMeetingReportError("Authenticated person is required")
        return UUID(str(auth.person_id))

    @staticmethod
    def _queue_notification(report: WeeklyMeetingReport) -> None:
        if report.notification_status != ReportEmailStatus.PENDING:
            return
        try:
            from app.tasks.weekly_meeting_reports import (
                send_weekly_meeting_report_hr_email,
            )

            send_weekly_meeting_report_hr_email.delay(
                str(report.report_id), str(report.organization_id)
            )
        except Exception:
            logger.exception(
                "Could not queue HR email for weekly report %s", report.report_id
            )


weekly_meeting_report_web_service = WeeklyMeetingReportWebService()
