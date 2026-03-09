"""
Training Web Service - Event methods.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.models.people.training import TrainingEventStatus, TrainingProgramStatus
from app.services.common import PaginationParams, coerce_uuid
from app.services.people.hr import EmployeeFilters, EmployeeService
from app.services.people.training import TrainingService
from app.services.people.training.training_service import TrainingEventNotFoundError
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

from .base import (
    logger,
    parse_date,
    parse_decimal,
    parse_event_status,
    parse_int,
    parse_time,
    parse_uuid,
)


class EventWebService:
    """Web service methods for training events."""

    @staticmethod
    def list_events_context(
        db: Session,
        organization_id: UUID,
        search: str | None = None,
        status: str | None = None,
        program_id: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        page: int = 1,
    ) -> dict:
        """Build context for events list page."""
        pagination = PaginationParams.from_page(page, per_page=20)
        svc = TrainingService(db)

        result = svc.list_events(
            organization_id,
            search=search,
            status=parse_event_status(status),
            program_id=parse_uuid(program_id),
            from_date=parse_date(start_date),
            to_date=parse_date(end_date),
            pagination=pagination,
        )

        programs = svc.list_programs(
            organization_id,
            pagination=PaginationParams(limit=200),
        ).items

        active_filters = [
            name
            for name, val in [
                ("status", status),
                ("program_id", program_id),
                ("start_date", start_date),
                ("end_date", end_date),
            ]
            if val
        ]

        return {
            "events": result.items,
            "programs": programs,
            "search": search,
            "status": status,
            "program_id": program_id,
            "start_date": start_date,
            "end_date": end_date,
            "statuses": [s.value for s in TrainingEventStatus],
            "active_filters": active_filters,
            "page": result.page,
            "total_pages": result.total_pages,
            "total": result.total,
            "total_count": result.total,
            "limit": pagination.limit,
            "has_prev": result.has_prev,
            "has_next": result.has_next,
        }

    @staticmethod
    def event_form_context(
        db: Session,
        organization_id: UUID,
        event_id: str | None = None,
        preselected_program_id: str | None = None,
    ) -> dict:
        """Build context for event create/edit form."""
        svc = TrainingService(db)
        emp_svc = EmployeeService(db, organization_id)

        programs = svc.list_programs(
            organization_id,
            status=TrainingProgramStatus.ACTIVE,
            pagination=PaginationParams(limit=200),
        ).items

        employees = emp_svc.list_employees(
            filters=EmployeeFilters(status="ACTIVE"),
            pagination=PaginationParams(limit=500),
            eager_load=True,
        ).items

        event = None
        if event_id:
            try:
                event = svc.get_event(organization_id, coerce_uuid(event_id))
            except (TrainingEventNotFoundError, ValueError):
                event = None

        return {
            "event": event,
            "programs": programs,
            "employees": employees,
            "preselected_program_id": preselected_program_id,
            "form_data": {},
            "error": None,
        }

    @staticmethod
    def event_detail_context(
        db: Session,
        organization_id: UUID,
        event_id: str,
    ) -> dict:
        """Build context for event detail page."""
        svc = TrainingService(db)

        try:
            event = svc.get_event(organization_id, coerce_uuid(event_id))
        except (TrainingEventNotFoundError, ValueError):
            return {"event": None}

        return {
            "event": event,
            "error": None,
        }

    @staticmethod
    def invite_attendees_context(
        db: Session,
        organization_id: UUID,
        event_id: str,
        search: str | None = None,
    ) -> dict:
        """Build context for attendee invitation page."""
        svc = TrainingService(db)
        emp_svc = EmployeeService(db, organization_id)

        try:
            event = svc.get_event(organization_id, coerce_uuid(event_id))
        except (TrainingEventNotFoundError, ValueError):
            return {"event": None, "employees": [], "search": search or ""}

        employees = emp_svc.list_employees(
            filters=EmployeeFilters(status="ACTIVE", search=search),
            pagination=PaginationParams(limit=500),
            eager_load=True,
        ).items
        invited_ids = {attendee.employee_id for attendee in event.attendees}

        return {
            "event": event,
            "employees": [
                employee
                for employee in employees
                if employee.employee_id not in invited_ids
            ],
            "search": search or "",
            "already_invited_count": len(invited_ids),
            "error": None,
        }

    @staticmethod
    def build_event_input(form_data: dict) -> dict:
        """Build input kwargs for event from form data."""
        return {
            "program_id": coerce_uuid(form_data["program_id"])
            if form_data.get("program_id")
            else None,
            "event_name": form_data.get("event_name", ""),
            "event_type": form_data.get("event_type", "IN_PERSON"),
            "start_date": parse_date(form_data.get("start_date")),
            "end_date": parse_date(form_data.get("end_date")),
            "start_time": parse_time(form_data.get("start_time")),
            "end_time": parse_time(form_data.get("end_time")),
            "location": form_data.get("location") or None,
            "meeting_link": form_data.get("meeting_link") or None,
            "trainer_employee_id": coerce_uuid(form_data["trainer_employee_id"])
            if form_data.get("trainer_employee_id")
            else None,
            "trainer_name": form_data.get("trainer_name") or None,
            "trainer_email": form_data.get("trainer_email") or None,
            "max_attendees": parse_int(form_data.get("max_attendees")),
            "total_cost": parse_decimal(form_data.get("total_cost")),
            "currency_code": form_data.get("currency_code") or None,
            "description": form_data.get("description") or None,
        }

    # Response helpers

    def _event_error_context(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        org_id: UUID,
        title: str,
        form_data: dict,
        error: str,
        event_id: str | None = None,
    ) -> dict:
        """Build template context for event form validation errors."""
        context = base_context(request, auth, title, "training", db=db)
        context["request"] = request
        context.update(self.event_form_context(db, org_id, event_id))
        context["form_data"] = form_data
        context["error"] = error
        return context

    # Response methods

    def list_events_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        search: str | None = None,
        status: str | None = None,
        program_id: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        page: int = 1,
    ) -> HTMLResponse:
        """Render events list page."""
        context = base_context(request, auth, "Training Events", "training", db=db)
        context["request"] = request
        context.update(
            self.list_events_context(
                db,
                coerce_uuid(auth.organization_id),
                search,
                status,
                program_id,
                start_date,
                end_date,
                page,
            )
        )
        return templates.TemplateResponse(
            request, "people/training/events.html", context
        )

    def event_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        program_id: str | None = None,
    ) -> HTMLResponse:
        """Render new event form."""
        context = base_context(request, auth, "New Training Event", "training", db=db)
        context["request"] = request
        context.update(
            self.event_form_context(
                db, coerce_uuid(auth.organization_id), preselected_program_id=program_id
            )
        )
        return templates.TemplateResponse(
            request, "people/training/event_form.html", context
        )

    def event_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        event_id: str,
        success: str | None = None,
    ) -> HTMLResponse | RedirectResponse:
        """Render event detail page."""
        ctx = self.event_detail_context(db, coerce_uuid(auth.organization_id), event_id)

        if not ctx.get("event"):
            return RedirectResponse(
                url="/people/training/events?error=Training+event+not+found",
                status_code=303,
            )

        context = base_context(
            request, auth, ctx["event"].event_name, "training", db=db
        )
        context["request"] = request
        context.update(ctx)
        context["success"] = success
        return templates.TemplateResponse(
            request, "people/training/event_detail.html", context
        )

    def event_edit_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        event_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Render event edit form."""
        ctx = self.event_form_context(db, coerce_uuid(auth.organization_id), event_id)

        if not ctx.get("event"):
            return RedirectResponse(
                url="/people/training/events?error=Training+event+not+found",
                status_code=303,
            )

        context = base_context(
            request, auth, f"Edit {ctx['event'].event_name}", "training", db=db
        )
        context["request"] = request
        context.update(ctx)
        return templates.TemplateResponse(
            request, "people/training/event_form.html", context
        )

    async def create_event_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Handle event creation form submission."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        svc = TrainingService(db)

        try:
            input_kwargs = self.build_event_input(dict(form_data))
            event = svc.create_event(org_id, **input_kwargs)
            db.commit()
            return RedirectResponse(
                url=f"/people/training/events/{event.event_id}?success=Event+created+successfully",
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            logger.exception("create_event_response: failed")
            context = self._event_error_context(
                request,
                auth,
                db,
                org_id,
                "New Training Event",
                dict(form_data),
                str(e),
            )
            return templates.TemplateResponse(
                request, "people/training/event_form.html", context
            )

    async def update_event_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        event_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Handle event update form submission."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        svc = TrainingService(db)

        try:
            input_kwargs = self.build_event_input(dict(form_data))
            svc.update_event(org_id, coerce_uuid(event_id), **input_kwargs)
            db.commit()
            return RedirectResponse(
                url=f"/people/training/events/{event_id}?success=Event+updated+successfully",
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            logger.exception("update_event_response: failed")
            context = self._event_error_context(
                request,
                auth,
                db,
                org_id,
                "Edit Event",
                dict(form_data),
                str(e),
                event_id,
            )
            return templates.TemplateResponse(
                request, "people/training/event_form.html", context
            )

    def schedule_event_response(
        self,
        auth: WebAuthContext,
        db: Session,
        event_id: str,
    ) -> RedirectResponse:
        """Handle event scheduling."""
        org_id = coerce_uuid(auth.organization_id)
        svc = TrainingService(db)

        try:
            svc.schedule_event(org_id, coerce_uuid(event_id))
            db.commit()
            return RedirectResponse(
                url=f"/people/training/events/{event_id}?success=Event+scheduled",
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            return RedirectResponse(
                url=f"/people/training/events/{event_id}?error={str(e)}",
                status_code=303,
            )

    def invite_attendees_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        event_id: str,
        search: str | None = None,
    ) -> HTMLResponse | RedirectResponse:
        """Render attendee invitation page."""
        ctx = self.invite_attendees_context(
            db,
            coerce_uuid(auth.organization_id),
            event_id,
            search,
        )

        if not ctx.get("event"):
            return RedirectResponse(
                url="/people/training/events?error=Training+event+not+found",
                status_code=303,
            )

        context = base_context(request, auth, "Invite Attendees", "training", db=db)
        context["request"] = request
        context.update(ctx)
        return templates.TemplateResponse(
            request, "people/training/invite_attendees.html", context
        )

    async def invite_attendees_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        event_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Invite selected employees to a training event."""
        form_data = await request.form()
        employee_ids = [
            employee_id
            for value in form_data.getlist("employee_ids")
            if isinstance(value, str) and (employee_id := parse_uuid(value)) is not None
        ]
        org_id = coerce_uuid(auth.organization_id)
        svc = TrainingService(db)

        if not employee_ids:
            context = base_context(request, auth, "Invite Attendees", "training", db=db)
            context["request"] = request
            context.update(self.invite_attendees_context(db, org_id, event_id))
            context["error"] = "Select at least one employee to invite."
            return templates.TemplateResponse(
                request, "people/training/invite_attendees.html", context
            )

        try:
            attendees = svc.bulk_invite(org_id, coerce_uuid(event_id), employee_ids)
            db.commit()
            return RedirectResponse(
                url=(
                    f"/people/training/events/{event_id}"
                    f"?success={len(attendees)}+attendee(s)+invited"
                ),
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            logger.exception("invite_attendees_response: failed")
            context = base_context(request, auth, "Invite Attendees", "training", db=db)
            context["request"] = request
            context.update(self.invite_attendees_context(db, org_id, event_id))
            context["error"] = str(e)
            return templates.TemplateResponse(
                request, "people/training/invite_attendees.html", context
            )

    def confirm_attendee_response(
        self,
        auth: WebAuthContext,
        db: Session,
        event_id: str,
        attendee_id: str,
    ) -> RedirectResponse:
        """Confirm an invited attendee."""
        org_id = coerce_uuid(auth.organization_id)
        svc = TrainingService(db)

        try:
            svc.confirm_attendance(org_id, coerce_uuid(attendee_id))
            db.commit()
            return RedirectResponse(
                url=f"/people/training/events/{event_id}?success=Attendee+confirmed",
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            return RedirectResponse(
                url=f"/people/training/events/{event_id}?error={str(e)}",
                status_code=303,
            )

    def mark_attended_response(
        self,
        auth: WebAuthContext,
        db: Session,
        event_id: str,
        attendee_id: str,
    ) -> RedirectResponse:
        """Mark an attendee as attended."""
        org_id = coerce_uuid(auth.organization_id)
        svc = TrainingService(db)

        try:
            svc.mark_attended(org_id, coerce_uuid(attendee_id))
            db.commit()
            return RedirectResponse(
                url=f"/people/training/events/{event_id}?success=Attendance+recorded",
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            return RedirectResponse(
                url=f"/people/training/events/{event_id}?error={str(e)}",
                status_code=303,
            )

    def issue_certificate_response(
        self,
        auth: WebAuthContext,
        db: Session,
        event_id: str,
        attendee_id: str,
    ) -> RedirectResponse:
        """Issue a certificate for an attendee."""
        org_id = coerce_uuid(auth.organization_id)
        svc = TrainingService(db)

        try:
            certificate_number = (
                f"TRN-{event_id.replace('-', '')[:8].upper()}-"
                f"{attendee_id.replace('-', '')[:8].upper()}"
            )
            svc.issue_certificate(
                org_id,
                coerce_uuid(attendee_id),
                certificate_number=certificate_number,
            )
            db.commit()
            return RedirectResponse(
                url=f"/people/training/events/{event_id}?success=Certificate+issued",
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            return RedirectResponse(
                url=f"/people/training/events/{event_id}?error={str(e)}",
                status_code=303,
            )

    def remove_attendee_response(
        self,
        auth: WebAuthContext,
        db: Session,
        event_id: str,
        attendee_id: str,
    ) -> RedirectResponse:
        """Remove an attendee from an event."""
        org_id = coerce_uuid(auth.organization_id)
        svc = TrainingService(db)

        try:
            svc.remove_attendee(org_id, coerce_uuid(event_id), coerce_uuid(attendee_id))
            db.commit()
            return RedirectResponse(
                url=f"/people/training/events/{event_id}?success=Attendee+removed",
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            return RedirectResponse(
                url=f"/people/training/events/{event_id}?error={str(e)}",
                status_code=303,
            )

    def start_event_response(
        self,
        auth: WebAuthContext,
        db: Session,
        event_id: str,
    ) -> RedirectResponse:
        """Handle starting an event."""
        org_id = coerce_uuid(auth.organization_id)
        svc = TrainingService(db)

        try:
            svc.start_event(org_id, coerce_uuid(event_id))
            db.commit()
            return RedirectResponse(
                url=f"/people/training/events/{event_id}?success=Event+started",
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            return RedirectResponse(
                url=f"/people/training/events/{event_id}?error={str(e)}",
                status_code=303,
            )

    def complete_event_response(
        self,
        auth: WebAuthContext,
        db: Session,
        event_id: str,
    ) -> RedirectResponse:
        """Handle completing an event."""
        org_id = coerce_uuid(auth.organization_id)
        svc = TrainingService(db)

        try:
            svc.complete_event(org_id, coerce_uuid(event_id))
            db.commit()
            return RedirectResponse(
                url=f"/people/training/events/{event_id}?success=Event+completed",
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            return RedirectResponse(
                url=f"/people/training/events/{event_id}?error={str(e)}",
                status_code=303,
            )

    def cancel_event_response(
        self,
        auth: WebAuthContext,
        db: Session,
        event_id: str,
    ) -> RedirectResponse:
        """Handle cancelling an event."""
        org_id = coerce_uuid(auth.organization_id)
        svc = TrainingService(db)

        try:
            svc.cancel_event(org_id, coerce_uuid(event_id))
            db.commit()
            return RedirectResponse(
                url=f"/people/training/events/{event_id}?success=Event+cancelled",
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            return RedirectResponse(
                url=f"/people/training/events/{event_id}?error={str(e)}",
                status_code=303,
            )
