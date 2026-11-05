"""Organization-specific weekly expense budget boundaries."""

from datetime import UTC, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain_settings import (
    DomainSetting,
    SettingDomain,
    SettingScope,
    SettingValueType,
)

KEY = "expense_weekly_reset_schedule"
DEFAULT = {"weekday": 0, "time": "00:00", "timezone": "UTC"}
WEEKDAYS = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)


def schedule_row(db: Session, org_id: UUID) -> DomainSetting | None:
    return db.scalar(
        select(DomainSetting).where(
            DomainSetting.organization_id == org_id,
            DomainSetting.domain == SettingDomain.expense,
            DomainSetting.key == KEY,
        )
    )


def boundary(config: dict, at: datetime, *, upcoming: bool = False) -> datetime:
    zone = ZoneInfo(config["timezone"])
    local = at.astimezone(zone)
    day = local.date() - timedelta(days=(local.weekday() - config["weekday"]) % 7)
    clock = time.fromisoformat(config["time"])
    candidate = datetime.combine(day, clock, zone).astimezone(UTC)
    if upcoming and candidate <= at:
        candidate = datetime.combine(day + timedelta(days=7), clock, zone).astimezone(
            UTC
        )
    elif not upcoming and candidate > at:
        candidate = datetime.combine(day - timedelta(days=7), clock, zone).astimezone(
            UTC
        )
    return candidate


def schedule_history(db: Session, org_id: UUID) -> list[dict]:
    row = schedule_row(db, org_id)
    return row.value_json.get("history", []) if row and row.value_json else []


def active_schedule(history: list[dict], at: datetime) -> dict:
    return next(
        (
            entry
            for entry in reversed(history)
            if not entry.get("superseded_at")
            and datetime.fromisoformat(entry["effective_at"]) <= at
        ),
        DEFAULT,
    )


def next_boundary(history: list[dict], at: datetime) -> datetime:
    current = boundary(active_schedule(history, at), at, upcoming=True)
    return min(
        (
            current,
            *(
                datetime.fromisoformat(entry["effective_at"])
                for entry in history
                if not entry.get("superseded_at")
                and datetime.fromisoformat(entry["effective_at"]) > at
            ),
        )
    )


def parse_schedule(form) -> dict:
    weekday = int(str(form.get("weekday", "")))
    clock = time.fromisoformat(str(form.get("time", "")))
    timezone_name = str(form.get("timezone", "")).strip()
    ZoneInfo(timezone_name)
    if not 0 <= weekday < 7 or clock.tzinfo or clock.second or clock.microsecond:
        raise ValueError("Choose a weekday and a time in hours and minutes.")
    return {
        "weekday": weekday,
        "time": clock.isoformat(timespec="minutes"),
        "timezone": timezone_name,
    }


def save_schedule(db: Session, org_id: UUID, actor_id: UUID, config: dict) -> None:
    # Serialize first-time saves as well as edits to an existing schedule.
    from app.models.finance.core_org.organization import Organization

    db.execute(
        select(Organization.organization_id)
        .where(Organization.organization_id == org_id)
        .with_for_update()
    )
    row = schedule_row(db, org_id)
    now = datetime.now(UTC)
    history = list((row.value_json or {}).get("history", [])) if row else []
    effective_at = boundary(config, now, upcoming=True)
    # Keep prior scheduled changes as audit evidence, including superseded ones.
    history = [
        {**entry, "superseded_at": now.isoformat()}
        if not entry.get("superseded_at")
        and datetime.fromisoformat(entry["effective_at"]) > now
        else entry
        for entry in history
    ]
    history.append(
        {
            **config,
            "effective_at": effective_at.isoformat(),
            "changed_at": now.isoformat(),
            "changed_by": str(actor_id),
        }
    )
    if row is None:
        row = DomainSetting(
            domain=SettingDomain.expense,
            key=KEY,
            organization_id=org_id,
            scope=SettingScope.ORG_SPECIFIC,
            value_type=SettingValueType.json,
            is_active=True,
        )
        db.add(row)
    row.value_json = {"history": history}
