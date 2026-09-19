"""Permission-gated ERP Organizational Calendar web routes."""

from __future__ import annotations

import calendar as month_calendar
import uuid
from datetime import UTC, date, datetime, time, timedelta
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.db.session_context import prime_tenant_context
from app.models.organization_calendar import (
    CalendarBusinessStatus,
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
    require_any_web_permission,
    require_web_permission,
)

READ_PERMISSIONS = ["calendar:events:access", "calendar:events:read_all"]
UPDATE_PERMISSIONS = ["calendar:events:update_own", "calendar:events:update_all"]
CANCEL_PERMISSIONS = ["calendar:events:cancel_own", "calendar:events:cancel_all"]

router = APIRouter(prefix="/admin/calendar", tags=["organization-calendar-web"])


def _rollback_and_reprime(db: Session, organization_id: uuid.UUID) -> None:
    db.rollback()
    prime_tenant_context(db, organization_id)


def _parse_month(value: str | None) -> date:
    if value:
        try:
            return date.fromisoformat(f"{value}-01")
        except ValueError:
            pass
    today = date.today()
    return today.replace(day=1)


def _shift_month(value: date, delta: int) -> date:
    month_index = value.year * 12 + value.month - 1 + delta
    return date(month_index // 12, month_index % 12 + 1, 1)


def _local_event_bounds(
    event: OrganizationCalendarEvent,
) -> tuple[date, date, str]:
    if event.all_day:
        assert event.start_date is not None and event.end_date_exclusive is not None
        return event.start_date, event.end_date_exclusive, "All day"
    tz = ZoneInfo(event.timezone or DEFAULT_TIMEZONE)
    assert event.start_at is not None and event.end_at is not None
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
    range_start_local = datetime.combine(weeks[0][0], time.min, tzinfo=tz)
    range_end_local = datetime.combine(
        weeks[-1][-1] + timedelta(days=1), time.min, tzinfo=tz
    )
    service = OrganizationCalendarService(db, auth.organization_id)
    events = service.list_events(
        range_start_local.astimezone(UTC), range_end_local.astimezone(UTC)
    )
    by_day: dict[date, list[dict[str, object]]] = {
        day: [] for week in weeks for day in week
    }
    event_rows: list[dict[str, object]] = []
    for event in events:
        local_start, local_end_exclusive, display_time = _local_event_bounds(event)
        row = {
            "event": event,
            "display_time": display_time,
            "display_date": local_start.strftime("%d %b"),
            "participant_count": sum(
                1
                for item in event.participants
                if item.membership_status == ParticipantMembershipStatus.ACTIVE.value
            ),
        }
        event_rows.append(row)
        day = max(local_start, weeks[0][0])
        final = min(local_end_exclusive, weeks[-1][-1] + timedelta(days=1))
        while day < final:
            if day in by_day:
                by_day[day].append(row)
            day += timedelta(days=1)
    eligible, excluded = service.eligible_participants()
    context = base_context(
        request, auth, "Organizational Calendar", active_module="settings", db=db
    )
    context.update(
        {
            "active_page": "calendar",
            "month_start": month_start,
            "month_label": month_start.strftime("%B %Y"),
            "previous_month": _shift_month(month_start, -1).strftime("%Y-%m"),
            "next_month": _shift_month(month_start, 1).strftime("%Y-%m"),
            "current_month": date.today().strftime("%Y-%m"),
            "today": date.today(),
            "weeks": weeks,
            "events_by_day": by_day,
            "event_rows": event_rows,
            "eligible_count": len(eligible),
            "excluded_count": len(excluded),
            "can_create": auth.has_permission("calendar:events:create"),
            "can_update_all": auth.has_permission("calendar:events:update_all"),
            "can_cancel_all": auth.has_permission("calendar:events:cancel_all"),
            "can_view_sync_issues": (
                auth.has_permission("calendar:audit:read")
                or auth.has_permission("calendar:sync:retry")
            ),
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
    service = OrganizationCalendarService(db, auth.organization_id)
    eligible, excluded = service.eligible_participants()
    selected = (
        {
            str(item.person_id)
            for item in event.participants
            if item.membership_status == ParticipantMembershipStatus.ACTIVE.value
        }
        if event
        else set()
    )
    tz_name = event.timezone if event else DEFAULT_TIMEZONE
    if event and not event.all_day:
        tz = ZoneInfo(tz_name)
        start_local = event.start_at.astimezone(tz) if event.start_at else None
        end_local = event.end_at.astimezone(tz) if event.end_at else None
    else:
        start_local = None
        end_local = None
    initial = {
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
        "color": event.color if event else "#4F46E5",
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
        "Edit Calendar Event" if event else "Create Calendar Event",
        active_module="settings",
        db=db,
    )
    context.update(
        {
            "active_page": "calendar",
            "event": event,
            "form_data": initial,
            "employees": eligible,
            "excluded_employees": excluded,
            "excluded_reason_counts": _excluded_reason_counts(excluded),
            "error": error,
            "can_add_everyone": auth.has_permission("calendar:participants:add_all"),
        }
    )
    return context


def _excluded_reason_counts(excluded: list) -> list[dict[str, object]]:
    counts: dict[str, int] = {}
    for employee in excluded:
        counts[employee.reason] = counts.get(employee.reason, 0) + 1
    return [
        {"reason": reason, "count": count} for reason, count in sorted(counts.items())
    ]


def _can_manage_own(
    auth: WebAuthContext,
    event: OrganizationCalendarEvent,
    *,
    own_permission: str,
    all_permission: str,
) -> bool:
    return auth.has_permission(all_permission) or (
        auth.has_permission(own_permission) and event.created_by_id == auth.person_id
    )


def _require_participant_management(
    auth: WebAuthContext, participant_ids: list[uuid.UUID], add_everyone: bool
) -> None:
    if (participant_ids or add_everyone) and not auth.has_permission(
        "calendar:participants:manage"
    ):
        raise HTTPException(
            status_code=403, detail="Participant management permission required"
        )


async def _read_form(
    request: Request,
) -> tuple[dict[str, object], list[uuid.UUID], list[int], bool]:
    raw = getattr(request.state, "csrf_form", None)
    if raw is None or isinstance(raw, str):
        raw = await request.form()
    participant_ids: list[uuid.UUID] = []
    for value in raw.getlist("participant_ids"):
        try:
            participant_ids.append(uuid.UUID(str(value)))
        except ValueError as exc:
            raise CalendarError("The participant selection is invalid.") from exc
    reminder_offsets: list[int] = []
    for value in raw.getlist("reminder_offsets"):
        if str(value).strip():
            try:
                reminder_offsets.append(int(str(value)))
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
        "color": str(raw.get("color") or "#4F46E5"),
        "selected_participants": {str(value) for value in participant_ids},
        "reminder_offsets": {str(value) for value in reminder_offsets},
    }
    add_everyone = raw.get("add_everyone") == "1"
    return submitted, participant_ids, reminder_offsets, add_everyone


def _data_from_submitted(submitted: dict[str, object]):
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


@router.get("", response_class=HTMLResponse, name="organization_calendar")
def calendar_page(
    request: Request,
    month: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}$"),
    auth: WebAuthContext = Depends(require_any_web_permission(READ_PERMISSIONS)),
    db: Session = Depends(get_db_for_org),
):
    return templates.TemplateResponse(
        request,
        "admin/calendar/index.html",
        _calendar_context(request, auth, db, _parse_month(month)),
    )


@router.get("/sync-issues", response_class=HTMLResponse)
def sync_issues_page(
    request: Request,
    auth: WebAuthContext = Depends(
        require_any_web_permission(["calendar:audit:read", "calendar:sync:retry"])
    ),
    db: Session = Depends(get_db_for_org),
):
    service = OrganizationCalendarService(db, auth.organization_id)
    events = service.list_sync_issues()
    eligible, excluded = service.eligible_participants()
    rows: list[dict[str, object]] = []
    for event in events:
        counts: dict[str, int] = {}
        errors: list[str] = []
        for participant in event.participants:
            counts[participant.sync_status] = counts.get(participant.sync_status, 0) + 1
            if (
                participant.last_error_message
                and participant.last_error_message not in errors
            ):
                errors.append(participant.last_error_message)
        rows.append(
            {
                "event": event,
                "counts": counts,
                "error_messages": errors[:3],
                "last_remote_sync_at": (
                    event.remote_state.last_remote_sync_at
                    if event.remote_state
                    else None
                ),
            }
        )
    context = base_context(
        request,
        auth,
        "Calendar Synchronization Issues",
        active_module="settings",
        db=db,
    )
    context.update(
        {
            "active_page": "calendar",
            "rows": rows,
            "eligible_count": len(eligible),
            "excluded_count": len(excluded),
            "excluded_reasons": _excluded_reason_counts(excluded),
            "can_retry": auth.has_permission("calendar:sync:retry"),
        }
    )
    return templates.TemplateResponse(
        request, "admin/calendar/sync_issues.html", context
    )


@router.get("/events/new", response_class=HTMLResponse)
def new_event_page(
    request: Request,
    auth: WebAuthContext = Depends(require_web_permission("calendar:events:create")),
    db: Session = Depends(get_db_for_org),
):
    return templates.TemplateResponse(
        request, "admin/calendar/form.html", _form_context(request, auth, db)
    )


@router.post("/events/new", response_class=HTMLResponse)
async def create_event(
    request: Request,
    auth: WebAuthContext = Depends(require_web_permission("calendar:events:create")),
    db: Session = Depends(get_db_for_org),
):
    submitted: dict[str, object] = {}
    try:
        submitted, participant_ids, reminders, add_everyone = await _read_form(request)
        _require_participant_management(auth, participant_ids, add_everyone)
        if add_everyone and not auth.has_permission("calendar:participants:add_all"):
            raise HTTPException(
                status_code=403, detail="Add Everyone permission required"
            )
        event = OrganizationCalendarService(db, auth.organization_id).create_event(
            _data_from_submitted(submitted),
            actor_person_id=auth.person_id,
            participant_person_ids=participant_ids,
            reminder_offsets=reminders,
            publish=request.query_params.get("publish") == "1",
            add_everyone=add_everyone,
        )
        return RedirectResponse(
            url=f"/admin/calendar/events/{event.event_id}?created=1", status_code=303
        )
    except CalendarError as exc:
        _rollback_and_reprime(db, auth.organization_id)
        return templates.TemplateResponse(
            request,
            "admin/calendar/form.html",
            _form_context(request, auth, db, error=str(exc), submitted=submitted),
            status_code=422,
        )


@router.get("/events/{event_id}", response_class=HTMLResponse)
def event_detail(
    event_id: uuid.UUID,
    request: Request,
    auth: WebAuthContext = Depends(require_any_web_permission(READ_PERMISSIONS)),
    db: Session = Depends(get_db_for_org),
):
    service = OrganizationCalendarService(db, auth.organization_id)
    try:
        event = service.get_event(event_id)
    except CalendarError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    context = base_context(request, auth, event.title, active_module="settings", db=db)
    context.update(
        {
            "active_page": "calendar",
            "event": event,
            "active_participants": [
                item
                for item in event.participants
                if item.membership_status == ParticipantMembershipStatus.ACTIVE.value
            ],
            "can_edit": _can_manage_own(
                auth,
                event,
                own_permission="calendar:events:update_own",
                all_permission="calendar:events:update_all",
            ),
            "can_cancel": _can_manage_own(
                auth,
                event,
                own_permission="calendar:events:cancel_own",
                all_permission="calendar:events:cancel_all",
            ),
            "created": request.query_params.get("created") == "1",
            "updated": request.query_params.get("updated") == "1",
            "retried": request.query_params.get("retried") == "1",
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
    return templates.TemplateResponse(request, "admin/calendar/detail.html", context)


@router.get("/events/{event_id}/edit", response_class=HTMLResponse)
def edit_event_page(
    event_id: uuid.UUID,
    request: Request,
    auth: WebAuthContext = Depends(require_any_web_permission(UPDATE_PERMISSIONS)),
    db: Session = Depends(get_db_for_org),
):
    service = OrganizationCalendarService(db, auth.organization_id)
    try:
        event = service.get_event(event_id)
    except CalendarError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not _can_manage_own(
        auth,
        event,
        own_permission="calendar:events:update_own",
        all_permission="calendar:events:update_all",
    ):
        raise HTTPException(status_code=403, detail="You can only edit your own events")
    return templates.TemplateResponse(
        request,
        "admin/calendar/form.html",
        _form_context(request, auth, db, event=event),
    )


@router.post("/events/{event_id}/edit", response_class=HTMLResponse)
async def update_event(
    event_id: uuid.UUID,
    request: Request,
    auth: WebAuthContext = Depends(require_any_web_permission(UPDATE_PERMISSIONS)),
    db: Session = Depends(get_db_for_org),
):
    service = OrganizationCalendarService(db, auth.organization_id)
    try:
        event = service.get_event(event_id)
    except CalendarError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not _can_manage_own(
        auth,
        event,
        own_permission="calendar:events:update_own",
        all_permission="calendar:events:update_all",
    ):
        raise HTTPException(status_code=403, detail="You can only edit your own events")
    submitted: dict[str, object] = {}
    try:
        submitted, participant_ids, reminders, add_everyone = await _read_form(request)
        _require_participant_management(auth, participant_ids, add_everyone)
        raw = getattr(request.state, "csrf_form", None)
        expected_version = int(str(raw.get("version")))
        if add_everyone and not auth.has_permission("calendar:participants:add_all"):
            raise HTTPException(
                status_code=403, detail="Add Everyone permission required"
            )
        updated = service.update_event(
            event_id,
            _data_from_submitted(submitted),
            expected_version=expected_version,
            actor_person_id=auth.person_id,
            participant_person_ids=participant_ids,
            reminder_offsets=reminders,
            publish=(
                request.query_params.get("publish") == "1"
                or event.business_status == CalendarBusinessStatus.PUBLISHED.value
            ),
            add_everyone=add_everyone,
        )
        return RedirectResponse(
            url=f"/admin/calendar/events/{updated.event_id}?updated=1", status_code=303
        )
    except (CalendarError, ValueError) as exc:
        _rollback_and_reprime(db, auth.organization_id)
        latest = service.get_event(event_id)
        message = str(exc) if str(exc) else "The submitted event version is invalid."
        status = 409 if isinstance(exc, CalendarConflictError) else 422
        return templates.TemplateResponse(
            request,
            "admin/calendar/form.html",
            _form_context(
                request, auth, db, event=latest, error=message, submitted=submitted
            ),
            status_code=status,
        )


@router.post("/events/{event_id}/delete")
async def cancel_event(
    event_id: uuid.UUID,
    request: Request,
    auth: WebAuthContext = Depends(require_any_web_permission(CANCEL_PERMISSIONS)),
    db: Session = Depends(get_db_for_org),
):
    service = OrganizationCalendarService(db, auth.organization_id)
    try:
        event = service.get_event(event_id)
    except CalendarError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not _can_manage_own(
        auth,
        event,
        own_permission="calendar:events:cancel_own",
        all_permission="calendar:events:cancel_all",
    ):
        raise HTTPException(
            status_code=403, detail="You can only delete your own events"
        )
    raw = getattr(request.state, "csrf_form", None)
    if raw is None or isinstance(raw, str):
        raw = await request.form()
    try:
        service.cancel_event(
            event_id,
            expected_version=int(str(raw.get("version"))),
            actor_person_id=auth.person_id,
        )
    except CalendarConflictError as exc:
        _rollback_and_reprime(db, auth.organization_id)
        return RedirectResponse(
            url=f"/admin/calendar/events/{event_id}?error={quote_plus(str(exc))}",
            status_code=303,
        )
    return RedirectResponse(url="/admin/calendar?cancelled=1", status_code=303)


@router.post("/events/{event_id}/retry")
def retry_event(
    event_id: uuid.UUID,
    auth: WebAuthContext = Depends(require_web_permission("calendar:sync:retry")),
    db: Session = Depends(get_db_for_org),
):
    try:
        OrganizationCalendarService(db, auth.organization_id).request_retry(
            event_id, actor_person_id=auth.person_id
        )
    except CalendarError as exc:
        _rollback_and_reprime(db, auth.organization_id)
        return RedirectResponse(
            url=f"/admin/calendar/events/{event_id}?error={quote_plus(str(exc))}",
            status_code=303,
        )
    return RedirectResponse(
        url=f"/admin/calendar/events/{event_id}?retried=1", status_code=303
    )


__all__ = ["router"]
