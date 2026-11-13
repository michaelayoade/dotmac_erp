"""Readable event details shared by calendar notices and reminders."""

from __future__ import annotations

from datetime import timedelta
from zoneinfo import ZoneInfo

from app.services import formatters as _date_formatters
from app.models.organization_calendar import OrganizationCalendarEvent


def calendar_event_message(event: OrganizationCalendarEvent, introduction: str) -> str:
    """Include the event facts a participant needs in every actionable notice."""
    if event.all_day:
        if event.start_date is None or event.end_date_exclusive is None:
            raise ValueError("All-day calendar event dates are incomplete")
        final_day = event.end_date_exclusive - timedelta(days=1)
        when = _date_formatters.format_date_display(event.start_date)
        if final_day != event.start_date:
            when += f" to {_date_formatters.format_date_display(final_day)}"
        when += f" (all day, {event.timezone})"
    else:
        if event.start_at is None or event.end_at is None:
            raise ValueError("Timed calendar event dates are incomplete")
        zone = ZoneInfo(event.timezone)
        start = event.start_at.astimezone(zone)
        end = event.end_at.astimezone(zone)
        when = (
            f"{_date_formatters.format_datetime_display(start, convert_timezone=False)} "
            f"to {_date_formatters.format_datetime_display(end, convert_timezone=False)} "
            f"({event.timezone})"
        )

    lines = [introduction, "", f"Event: {event.title}", f"When: {when}"]
    for label, value in (
        ("Location", event.location),
        ("Description", event.description),
        ("Additional details", event.event_details),
        ("Meeting link", event.meeting_url),
    ):
        if value:
            lines.append(f"{label}: {value}")
    return "\n".join(lines)


def reminder_interval(offset_minutes: int) -> str:
    """Describe a selected reminder offset without losing its unit."""
    if offset_minutes % 10080 == 0 and offset_minutes >= 10080:
        amount = offset_minutes // 10080
        unit = "week"
    elif offset_minutes % 1440 == 0 and offset_minutes >= 1440:
        amount = offset_minutes // 1440
        unit = "day"
    elif offset_minutes % 60 == 0 and offset_minutes >= 60:
        amount = offset_minutes // 60
        unit = "hour"
    else:
        amount = offset_minutes
        unit = "minute"
    return f"{amount} {unit}{'' if amount == 1 else 's'}"
