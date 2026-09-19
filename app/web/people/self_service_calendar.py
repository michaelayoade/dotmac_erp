"""Permission-gated My Calendar routes inside the People self-service shell."""

from __future__ import annotations

import calendar as month_calendar
import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import cast
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from starlette.datastructures import FormData

from app.db.session_context import prime_tenant_context
from app.models.organization_calendar import (
    CalendarEventScope,
    OrganizationCalendarEvent,
    ParticipantMembershipStatus,
)
from app.services.organization_calendar import (
    CalendarConflictError,
    CalendarError,
    DEFAULT_TIMEZONE,
    OrganizationCalendarService,
    build_event_data,
)
from app.templates import templates
from app.web.deps import (
    WebAuthContext,
    base_context,
    get_db_for_org,
    require_self_service_access,
)

router = APIRouter(prefix="/self/calendar", tags=["people-self-service-calendar"])


def _rollback_and_reprime(db: Session, organization_id: uuid.UUID) -> None:
    db.rollback()
    prime_tenant_context(db, organization_id)


def require_my_calendar_access(
    auth: WebAuthContext = Depends(require_self_service_access),
) -> WebAuthContext:
    if not auth.has_any_permission(
        ["calendar:personal:access", "calendar:events:read_assigned"]
    ):
        raise HTTPException(status_code=403, detail="My Calendar permission required")
    return auth


def require_personal_event_create(
    auth: WebAuthContext = Depends(require_my_calendar_access),
) -> WebAuthContext:
    if not auth.has_permission("calendar:personal:create"):
        raise HTTPException(
            status_code=403, detail="Personal calendar create permission required"
        )
    return auth


def _parse_month(value: str | None) -> date:
    if value:
        try:
            return date.fromisoformat(f"{value}-01")
        except ValueError:
            pass
    return date.today().replace(day=1)


def _shift_month(value: date, delta: int) -> date:
    month_index = value.year * 12 + value.month - 1 + delta
    return date(month_index // 12, month_index % 12 + 1, 1)


def _local_event_bounds(
    event: OrganizationCalendarEvent,
) -> tuple[date, date, str]:
    if event.all_day:
        if event.start_date is None or event.end_date_exclusive is None:
            raise CalendarError("The all-day event has invalid dates.")
        return event.start_date, event.end_date_exclusive, "All day"
    tz = ZoneInfo(event.timezone or DEFAULT_TIMEZONE)
    if event.start_at is None or event.end_at is None:
        raise CalendarError("The timed event has invalid dates.")
    start = event.start_at.astimezone(tz)
    end = event.end_at.astimezone(tz)
    return start.date(), end.date() + timedelta(days=1), start.strftime("%H:%M")


def _calendar_context(
    request: Request,
    auth: WebAuthContext,
    db: Session,
    month_start: date,
) -> dict:
    tz = ZoneInfo(DEFAULT_TIMEZONE)
    weeks = month_calendar.Calendar(firstweekday=0).monthdatescalendar(
        month_start.year, month_start.month
    )
    range_start = datetime.combine(weeks[0][0], time.min, tzinfo=tz).astimezone(UTC)
    range_end = datetime.combine(
        weeks[-1][-1] + timedelta(days=1), time.min, tzinfo=tz
    ).astimezone(UTC)
    events = OrganizationCalendarService(
        db, auth.organization_id
    ).list_visible_events_for_person(auth.person_id, range_start, range_end)
    by_day: dict[date, list[dict[str, object]]] = {
        day: [] for week in weeks for day in week
    }
    rows: list[dict[str, object]] = []
    for event in events:
        local_start, local_end, display_time = _local_event_bounds(event)
        row = {
            "event": event,
            "display_time": display_time,
            "display_date": local_start.strftime("%d %b"),
            "participant_count": sum(
                item.membership_status == ParticipantMembershipStatus.ACTIVE.value
                for item in event.participants
            ),
            "is_owner": event.created_by_id == auth.person_id,
            "is_personal": event.event_scope == CalendarEventScope.PERSONAL.value,
        }
        rows.append(row)
        day = max(local_start, weeks[0][0])
        final = min(local_end, weeks[-1][-1] + timedelta(days=1))
        while day < final:
            if day in by_day:
                by_day[day].append(row)
            day += timedelta(days=1)

    context = base_context(request, auth, "My Calendar", "self", db=db)
    context.update(
        {
            "month_start": month_start,
            "month_label": month_start.strftime("%B %Y"),
            "previous_month": _shift_month(month_start, -1).strftime("%Y-%m"),
            "next_month": _shift_month(month_start, 1).strftime("%Y-%m"),
            "current_month": date.today().strftime("%Y-%m"),
            "today": date.today(),
            "weeks": weeks,
            "events_by_day": by_day,
            "event_rows": rows,
            "can_create": auth.has_permission("calendar:personal:create"),
            "created": request.query_params.get("created") == "1",
            "updated": request.query_params.get("updated") == "1",
            "cancelled": request.query_params.get("cancelled") == "1",
        }
    )
    return context


def _form_context(
    request: Request,
    auth: WebAuthContext,
    db: Session,
    *,
    event: OrganizationCalendarEvent | None = None,
    error: str | None = None,
    submitted: dict[str, object] | None = None,
) -> dict:
    eligible, excluded = OrganizationCalendarService(
        db, auth.organization_id
    ).eligible_participants()
    eligible = [item for item in eligible if item.person_id != auth.person_id]
    selected = (
        {
            str(item.person_id)
            for item in event.participants
            if item.person_id != auth.person_id
            and item.membership_status == ParticipantMembershipStatus.ACTIVE.value
        }
        if event
        else set()
    )
    tz_name = event.timezone if event else DEFAULT_TIMEZONE
    start_local = (
        event.start_at.astimezone(ZoneInfo(tz_name))
        if event and event.start_at
        else None
    )
    end_local = (
        event.end_at.astimezone(ZoneInfo(tz_name)) if event and event.end_at else None
    )
    initial: dict[str, object] = {
        "title": event.title if event else "",
        "description": event.description if event else "",
        "event_details": event.event_details if event else "",
        "location": event.location if event else "",
        "meeting_url": event.meeting_url if event else "",
        "timezone": tz_name,
        "all_day": event.all_day if event else False,
        "start_date": (
            event.start_date.isoformat()
            if event and event.all_day and event.start_date
            else start_local.date().isoformat()
            if start_local
            else date.today().isoformat()
        ),
        "end_date": (
            (event.end_date_exclusive - timedelta(days=1)).isoformat()
            if event and event.all_day and event.end_date_exclusive
            else end_local.date().isoformat()
            if end_local
            else date.today().isoformat()
        ),
        "start_time": start_local.strftime("%H:%M") if start_local else "09:00",
        "end_time": end_local.strftime("%H:%M") if end_local else "10:00",
        "color": event.color if event else "#7C3AED",
        "selected_participants": selected,
        "reminder_offsets": (
            {str(item.offset_minutes) for item in event.reminders} if event else {"60"}
        ),
    }
    if submitted:
        initial.update(submitted)
    context = base_context(
        request,
        auth,
        "Edit Personal Event" if event else "Create Personal Event",
        "self",
        db=db,
    )
    context.update(
        {
            "event": event,
            "form_data": initial,
            "employees": eligible,
            "excluded_count": len(excluded),
            "can_invite": auth.has_permission("calendar:personal:invite"),
            "error": error,
        }
    )
    return context


async def _request_form(request: Request) -> FormData:
    raw = getattr(request.state, "csrf_form", None)
    if isinstance(raw, FormData):
        return raw
    return cast(FormData, await request.form())


async def _read_form(
    request: Request,
) -> tuple[dict[str, object], list[uuid.UUID], list[int]]:
    raw = await _request_form(request)
    participants: list[uuid.UUID] = []
    for value in raw.getlist("participant_ids"):
        try:
            participants.append(uuid.UUID(str(value)))
        except ValueError as exc:
            raise CalendarError("The participant selection is invalid.") from exc
    reminders: list[int] = []
    for value in raw.getlist("reminder_offsets"):
        try:
            reminders.append(int(str(value)))
        except ValueError as exc:
            raise CalendarError("A reminder value is invalid.") from exc
    submitted: dict[str, object] = {
        "title": str(raw.get("title") or ""),
        "description": str(raw.get("description") or ""),
        "event_details": str(raw.get("event_details") or ""),
        "location": str(raw.get("location") or ""),
        "meeting_url": str(raw.get("meeting_url") or ""),
        "timezone": str(raw.get("timezone") or DEFAULT_TIMEZONE),
        "all_day": raw.get("all_day") == "1",
        "start_date": str(raw.get("start_date") or ""),
        "end_date": str(raw.get("end_date") or ""),
        "start_time": str(raw.get("start_time") or ""),
        "end_time": str(raw.get("end_time") or ""),
        "color": str(raw.get("color") or "#7C3AED"),
        "selected_participants": {str(value) for value in participants},
        "reminder_offsets": {str(value) for value in reminders},
    }
    return submitted, participants, reminders


def _event_data(submitted: dict[str, object]):
    try:
        start_date = date.fromisoformat(str(submitted["start_date"]))
        end_date = date.fromisoformat(str(submitted["end_date"]))
        start_time = (
            time.fromisoformat(str(submitted["start_time"]))
            if submitted["start_time"]
            else None
        )
        end_time = (
            time.fromisoformat(str(submitted["end_time"]))
            if submitted["end_time"]
            else None
        )
    except ValueError as exc:
        raise CalendarError("Enter valid event dates and times.") from exc
    return build_event_data(
        title=str(submitted["title"]),
        description=str(submitted["description"]),
        event_details=str(submitted["event_details"]),
        location=str(submitted["location"]),
        meeting_url=str(submitted["meeting_url"]),
        timezone_name=str(submitted["timezone"]),
        all_day=bool(submitted["all_day"]),
        start_date_value=start_date,
        end_date_value=end_date,
        start_time_value=start_time,
        end_time_value=end_time,
        color=str(submitted["color"]),
    )


@router.get("", response_class=HTMLResponse)
def my_calendar(
    request: Request,
    month: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}$"),
    auth: WebAuthContext = Depends(require_my_calendar_access),
    db: Session = Depends(get_db_for_org),
):
    return templates.TemplateResponse(
        request,
        "people/self/calendar/index.html",
        _calendar_context(request, auth, db, _parse_month(month)),
    )


@router.get("/events/new", response_class=HTMLResponse)
def new_personal_event(
    request: Request,
    auth: WebAuthContext = Depends(require_personal_event_create),
    db: Session = Depends(get_db_for_org),
):
    return templates.TemplateResponse(
        request, "people/self/calendar/form.html", _form_context(request, auth, db)
    )


@router.post("/events/new", response_class=HTMLResponse)
async def create_personal_event(
    request: Request,
    auth: WebAuthContext = Depends(require_personal_event_create),
    db: Session = Depends(get_db_for_org),
):
    submitted: dict[str, object] = {}
    try:
        submitted, participants, reminders = await _read_form(request)
        if participants and not auth.has_permission("calendar:personal:invite"):
            raise HTTPException(
                status_code=403, detail="Participant invite permission required"
            )
        event = OrganizationCalendarService(
            db, auth.organization_id
        ).create_personal_event(
            _event_data(submitted),
            actor_person_id=auth.person_id,
            participant_person_ids=participants,
            reminder_offsets=reminders,
        )
        return RedirectResponse(
            f"/people/self/calendar/events/{event.event_id}?created=1", status_code=303
        )
    except CalendarError as exc:
        _rollback_and_reprime(db, auth.organization_id)
        return templates.TemplateResponse(
            request,
            "people/self/calendar/form.html",
            _form_context(request, auth, db, error=str(exc), submitted=submitted),
            status_code=422,
        )


@router.get("/events/{event_id}", response_class=HTMLResponse)
def personal_event_detail(
    event_id: uuid.UUID,
    request: Request,
    auth: WebAuthContext = Depends(require_my_calendar_access),
    db: Session = Depends(get_db_for_org),
):
    try:
        event = OrganizationCalendarService(
            db, auth.organization_id
        ).get_visible_event_for_person(event_id, auth.person_id)
    except CalendarError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    context = base_context(request, auth, event.title, "self", db=db)
    context.update(
        {
            "event": event,
            "active_participants": [
                item
                for item in event.participants
                if item.membership_status == ParticipantMembershipStatus.ACTIVE.value
            ],
            "is_personal": event.event_scope == CalendarEventScope.PERSONAL.value,
            "is_owner": event.created_by_id == auth.person_id,
            "created": request.query_params.get("created") == "1",
            "updated": request.query_params.get("updated") == "1",
            "event_start_local": (
                event.start_at.astimezone(ZoneInfo(event.timezone))
                if event.start_at
                else None
            ),
            "event_end_local": (
                event.end_at.astimezone(ZoneInfo(event.timezone))
                if event.end_at
                else None
            ),
            "event_end_date_inclusive": (
                event.end_date_exclusive - timedelta(days=1)
                if event.end_date_exclusive
                else None
            ),
        }
    )
    return templates.TemplateResponse(
        request, "people/self/calendar/detail.html", context
    )


def _owned_personal_event(
    service: OrganizationCalendarService, event_id: uuid.UUID, person_id: uuid.UUID
) -> OrganizationCalendarEvent:
    event = service.get_event(event_id, event_scope=CalendarEventScope.PERSONAL.value)
    if event.created_by_id != person_id:
        raise CalendarError("Only the creator can manage this personal event.")
    return event


@router.get("/events/{event_id}/edit", response_class=HTMLResponse)
def edit_personal_event(
    event_id: uuid.UUID,
    request: Request,
    auth: WebAuthContext = Depends(require_personal_event_create),
    db: Session = Depends(get_db_for_org),
):
    try:
        event = _owned_personal_event(
            OrganizationCalendarService(db, auth.organization_id),
            event_id,
            auth.person_id,
        )
    except CalendarError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return templates.TemplateResponse(
        request,
        "people/self/calendar/form.html",
        _form_context(request, auth, db, event=event),
    )


@router.post("/events/{event_id}/edit", response_class=HTMLResponse)
async def update_personal_event(
    event_id: uuid.UUID,
    request: Request,
    auth: WebAuthContext = Depends(require_personal_event_create),
    db: Session = Depends(get_db_for_org),
):
    service = OrganizationCalendarService(db, auth.organization_id)
    try:
        event = _owned_personal_event(service, event_id, auth.person_id)
    except CalendarError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    submitted: dict[str, object] = {}
    try:
        submitted, participants, reminders = await _read_form(request)
        if participants and not auth.has_permission("calendar:personal:invite"):
            raise HTTPException(
                status_code=403, detail="Participant invite permission required"
            )
        if not auth.has_permission("calendar:personal:invite"):
            participants = [
                participant.person_id
                for participant in event.participants
                if participant.person_id != auth.person_id
                and participant.membership_status
                == ParticipantMembershipStatus.ACTIVE.value
            ]
        raw = await _request_form(request)
        expected_version = int(str(raw.get("version")))
        updated = service.update_personal_event(
            event_id,
            _event_data(submitted),
            expected_version=expected_version,
            actor_person_id=auth.person_id,
            participant_person_ids=participants,
            reminder_offsets=reminders,
        )
        return RedirectResponse(
            f"/people/self/calendar/events/{updated.event_id}?updated=1",
            status_code=303,
        )
    except (CalendarError, ValueError) as exc:
        _rollback_and_reprime(db, auth.organization_id)
        latest = _owned_personal_event(service, event_id, auth.person_id)
        return templates.TemplateResponse(
            request,
            "people/self/calendar/form.html",
            _form_context(
                request, auth, db, event=latest, error=str(exc), submitted=submitted
            ),
            status_code=409 if isinstance(exc, CalendarConflictError) else 422,
        )


@router.post("/events/{event_id}/delete")
async def delete_personal_event(
    event_id: uuid.UUID,
    request: Request,
    auth: WebAuthContext = Depends(require_personal_event_create),
    db: Session = Depends(get_db_for_org),
):
    raw = await _request_form(request)
    try:
        OrganizationCalendarService(db, auth.organization_id).cancel_personal_event(
            event_id,
            expected_version=int(str(raw.get("version"))),
            actor_person_id=auth.person_id,
        )
    except (CalendarError, ValueError) as exc:
        _rollback_and_reprime(db, auth.organization_id)
        return RedirectResponse(
            f"/people/self/calendar/events/{event_id}?error={quote_plus(str(exc))}",
            status_code=303,
        )
    return RedirectResponse("/people/self/calendar?cancelled=1", status_code=303)


__all__ = ["router"]
