"""Validated, database-independent configuration for the People KPI dashboard.

This is a presentation contract, not a second KPI calculation authority. Targets,
actuals and lifecycle status continue to belong to ``perf.kpi``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PREFERENCE_KEY = "people_kpi_dashboard_v1"
MAX_SAVED_VIEWS = 10
MAX_DATE_SPAN = 366
MAX_DEPARTMENTS = 100
PAGE_SIZE = 25

WIDGETS = {
    "tracked": ("Tracked KPIs", "KPI records in the selected cohort", "violet"),
    "owners": ("KPI owners", "Distinct employees with a tracked KPI", "blue"),
    "achieved": ("Recorded achieved", "Status explicitly marked Achieved", "emerald"),
    "at_risk": ("At risk or missed", "Recorded At Risk or Missed status", "rose"),
    "overdue": ("Overdue open KPIs", "Deadline passed; KPI is still open", "amber"),
    "coverage": (
        "Actuals recorded",
        "Share of tracked KPIs with an actual value",
        "teal",
    ),
}
DEFAULT_WIDGETS = tuple(WIDGETS)
KPI_STATUSES = frozenset(
    {
        "DRAFT",
        "PENDING",
        "ACTIVE",
        "ON_TRACK",
        "AT_RISK",
        "ACHIEVED",
        "COMPLETED",
        "MISSED",
        "DEFERRED",
        "CANCELLED",
    }
)
DEFAULT_STATUSES = (
    "PENDING",
    "ACTIVE",
    "ON_TRACK",
    "AT_RISK",
    "ACHIEVED",
    "COMPLETED",
    "MISSED",
)
OPEN_STATUSES = ("PENDING", "ACTIVE", "ON_TRACK", "AT_RISK")
ORG_WIDE_ROLES = frozenset({"admin", "hr_manager", "hr_director"})
PERIODS = {
    "this_month": "This month",
    "this_week": "This week",
    "this_quarter": "This quarter",
    "last_month": "Last month",
    "custom": "Custom dates",
}


class DashboardValidationError(ValueError):
    """Invalid dashboard input; safe for display to the authenticated user."""


class DashboardAccessError(PermissionError):
    """An explicit organizational or department scope was not authorized."""


class SavedViewNotFound(LookupError):
    """A requested personal view does not exist for this actor."""


def organization_today(timezone_name: str | None, now: datetime | None = None) -> date:
    """Use the organization's business date, never the server's local date.

    A missing timezone uses UTC explicitly. An invalid configured timezone is an
    error rather than silently changing deadline classifications.
    """
    try:
        zone = ZoneInfo(timezone_name or "UTC")
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise DashboardValidationError(
            "The organization's timezone is invalid. Ask an administrator to correct it."
        ) from exc
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise DashboardValidationError("A timezone-aware current time is required.")
    return current.astimezone(zone).date()


def has_organization_access(roles: Iterable[str]) -> bool:
    return bool({role.strip().lower() for role in roles} & ORG_WIDE_ROLES)


def _strings(value: Any, field: str, maximum: int) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or len(value) > maximum:
        raise DashboardValidationError(f"Invalid {field} selection.")
    if any(not isinstance(item, str) for item in value):
        raise DashboardValidationError(f"Invalid {field} selection.")
    values = tuple(item.strip() for item in value if item.strip())
    if len(values) != len(set(values)):
        raise DashboardValidationError(f"Select each {field} only once.")
    return values


def _date(value: Any, field: str) -> date:
    if not isinstance(value, str):
        raise DashboardValidationError(f"Enter a valid {field} date.")
    try:
        result = date.fromisoformat(value)
    except ValueError as exc:
        raise DashboardValidationError(f"Enter a valid {field} date.") from exc
    # Date-only HTML input contract, not compact or ISO week-date variants.
    if result.isoformat() != value:
        raise DashboardValidationError(f"Enter a valid {field} date.")
    return result


@dataclass(frozen=True)
class DashboardConfig:
    """Bounded configuration stored only in the authenticated person's metadata."""

    period: str = "this_month"
    start_date: str = ""
    end_date: str = ""
    department_ids: tuple[str, ...] = ()
    statuses: tuple[str, ...] = DEFAULT_STATUSES
    widgets: tuple[str, ...] = DEFAULT_WIDGETS
    search: str = ""
    employee_search: str = ""
    cohort: str = "due"
    attention: str = "all"
    show_charts: bool = True

    @classmethod
    def parse(cls, payload: Mapping[str, Any]) -> DashboardConfig:
        permitted = {
            "period",
            "start_date",
            "end_date",
            "department_ids",
            "statuses",
            "widgets",
            "search",
            "employee_search",
            "cohort",
            "attention",
            "show_charts",
        }
        if set(payload) - permitted:
            raise DashboardValidationError("Unsupported dashboard configuration field.")
        period = payload.get("period", "this_month")
        if not isinstance(period, str) or period not in PERIODS:
            raise DashboardValidationError("Choose a supported reporting period.")
        departments = _strings(
            payload.get("department_ids", []), "department", MAX_DEPARTMENTS
        )
        canonical = []
        for value in departments:
            try:
                canonical.append(str(UUID(value)))
            except ValueError as exc:
                raise DashboardValidationError("Invalid department selection.") from exc
        if len(set(canonical)) != len(canonical):
            raise DashboardValidationError("Select each department only once.")
        statuses = _strings(
            payload.get("statuses", list(DEFAULT_STATUSES)), "status", len(KPI_STATUSES)
        )
        if not statuses or not set(statuses) <= KPI_STATUSES:
            raise DashboardValidationError("Choose at least one valid KPI status.")
        widgets = _strings(
            payload.get("widgets", list(DEFAULT_WIDGETS)), "widget", len(WIDGETS)
        )
        if not widgets or not set(widgets) <= WIDGETS.keys():
            raise DashboardValidationError(
                "Choose at least one supported summary widget."
            )
        search = payload.get("search", "")
        if not isinstance(search, str) or len(search) > 100:
            raise DashboardValidationError(
                "KPI search must be no longer than 100 characters."
            )
        employee_search = payload.get("employee_search", "")
        if not isinstance(employee_search, str) or len(employee_search) > 100:
            raise DashboardValidationError(
                "Employee search must be no longer than 100 characters."
            )
        cohort = payload.get("cohort", "due")
        if not isinstance(cohort, str) or cohort not in {"due", "active", "overdue"}:
            raise DashboardValidationError("Choose a valid KPI date scope.")
        attention = payload.get("attention", "all")
        if not isinstance(attention, str) or attention not in {
            "all",
            "needs_attention",
        }:
            raise DashboardValidationError("Choose a valid employee review filter.")
        show_charts = payload.get("show_charts", True)
        if not isinstance(show_charts, bool):
            raise DashboardValidationError("Choose whether to show charts.")
        start_date = end_date = ""
        if period == "custom":
            start = _date(payload.get("start_date"), "start")
            end = _date(payload.get("end_date"), "end")
            _validate_window(start, end)
            start_date, end_date = start.isoformat(), end.isoformat()
        return cls(
            period=period,
            start_date=start_date,
            end_date=end_date,
            department_ids=tuple(canonical),
            statuses=statuses,
            widgets=widgets,
            search=search.strip(),
            employee_search=employee_search.strip(),
            cohort=cohort,
            attention=attention,
            show_charts=show_charts,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "period": self.period,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "department_ids": list(self.department_ids),
            "statuses": list(self.statuses),
            "widgets": list(self.widgets),
            "search": self.search,
            "employee_search": self.employee_search,
            "cohort": self.cohort,
            "attention": self.attention,
            "show_charts": self.show_charts,
        }

    def window(self, today: date) -> tuple[date, date]:
        if self.period == "custom":
            start, end = _date(self.start_date, "start"), _date(self.end_date, "end")
        elif self.period == "this_week":
            start = today - timedelta(days=today.weekday())
            end = start + timedelta(days=6)
        elif self.period == "this_quarter":
            month = 1 + ((today.month - 1) // 3) * 3
            start = today.replace(month=month, day=1)
            next_quarter = (
                date(today.year + 1, 1, 1)
                if month == 10
                else date(today.year, month + 3, 1)
            )
            end = next_quarter - timedelta(days=1)
        elif self.period == "last_month":
            end = today.replace(day=1) - timedelta(days=1)
            start = end.replace(day=1)
        elif self.period == "this_month":
            start = today.replace(day=1)
            following = (start + timedelta(days=32)).replace(day=1)
            end = following - timedelta(days=1)
        else:
            raise DashboardValidationError("Choose a supported reporting period.")
        _validate_window(start, end)
        return start, end


def _validate_window(start: date, end: date) -> None:
    if end < start:
        raise DashboardValidationError(
            "The end date must not be before the start date."
        )
    if (end - start).days + 1 > MAX_DATE_SPAN:
        raise DashboardValidationError("Choose a date range of no more than 366 days.")


def authorize_departments(
    requested: Iterable[str],
    allowed: Iterable[str],
    *,
    organization_wide: bool,
) -> tuple[UUID, ...] | None:
    """None means organization-wide; an empty tuple never means unrestricted."""
    selection, permitted = set(requested), set(allowed)
    if not selection <= permitted:
        raise DashboardAccessError(
            "One or more selected departments are unavailable to you."
        )
    if selection:
        return tuple(UUID(value) for value in sorted(selection))
    if organization_wide:
        return None
    if not permitted:
        raise DashboardAccessError(
            "No department has been assigned to your dashboard access."
        )
    return tuple(UUID(value) for value in sorted(permitted))


def reporting_percentage(reported: int, total: int) -> Decimal | None:
    if total < 0 or reported < 0 or reported > total:
        raise DashboardValidationError("Invalid reporting coverage counts.")
    if total == 0:
        return None
    return (Decimal(reported) * 100 / Decimal(total)).quantize(
        Decimal("0.1"),
        rounding=ROUND_HALF_UP,
    )


def view_name(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 80:
        raise DashboardValidationError("Enter a view name between 1 and 80 characters.")
    return value.strip()
