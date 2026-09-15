"""Tenant- and department-scoped reads over the existing Performance KPI records."""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import String, and_, case, cast, func, or_, select
from sqlalchemy.orm import Session

from app.models.finance.core_org.organization import Organization
from app.models.people.hr.department import Department
from app.models.people.hr.employee import Employee, EmployeeStatus
from app.models.people.perf.kpi import KPI, KPIStatus
from app.models.people.perf.kpi_measurement import (
    LOWER_SUPPORT_METRIC_KEYS,
    achievement,
)
from app.models.person import Person
from app.services.people.perf.kpi_dashboard_contract import (
    MAX_SAVED_VIEWS,
    OPEN_STATUSES,
    PAGE_SIZE,
    PREFERENCE_KEY,
    DashboardAccessError,
    DashboardConfig,
    DashboardValidationError,
    SavedViewNotFound,
    authorize_departments,
    has_organization_access,
    organization_today,
    reporting_percentage,
    view_name,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DashboardScope:
    organization_id: UUID
    person_id: UUID
    organization_wide: bool
    departments: tuple[dict[str, Any], ...]
    timezone_name: str

    def selected_departments(self, config: DashboardConfig) -> tuple[UUID, ...] | None:
        return authorize_departments(
            config.department_ids,
            (item["id"] for item in self.departments),
            organization_wide=self.organization_wide,
        )


class KPIDashboardService:
    """Read projections and personal view settings; never mutates KPI records."""

    def __init__(self, db: Session):
        self.db = db

    def resolve_scope(
        self,
        organization_id: UUID,
        person_id: UUID,
        roles: list[str],
    ) -> DashboardScope:
        org = self.db.scalars(
            select(Organization).where(Organization.organization_id == organization_id)
        ).one_or_none()
        if org is None:
            raise DashboardAccessError("Organization context is unavailable.")
        organization_wide = has_organization_access(roles)
        departments = select(Department).where(
            Department.organization_id == organization_id,
        )
        if not organization_wide:
            employee = self.db.scalars(
                select(Employee).where(
                    Employee.organization_id == organization_id,
                    Employee.person_id == person_id,
                    Employee.status == EmployeeStatus.ACTIVE,
                )
            ).one_or_none()
            if employee is None:
                raise DashboardAccessError(
                    "An active department-head assignment is required."
                )
            departments = departments.where(
                Department.head_id == employee.employee_id,
                Department.is_active.is_(True),
            )
        rows = self.db.scalars(
            departments.order_by(Department.department_name, Department.department_id)
        ).all()
        if not organization_wide and not rows:
            raise DashboardAccessError(
                "An active department-head assignment is required."
            )
        return DashboardScope(
            organization_id=organization_id,
            person_id=person_id,
            organization_wide=organization_wide,
            departments=tuple(
                {
                    "id": str(row.department_id),
                    "name": row.department_name,
                    "active": row.is_active,
                }
                for row in rows
            ),
            timezone_name=org.timezone or "UTC",
        )

    def _base_query(self, scope: DashboardScope, config: DashboardConfig, today: date):
        selected = scope.selected_departments(config)
        start, end = config.window(today)
        # Every join is tenant constrained, not just the driving KPI table.
        source_text = func.lower(
            func.coalesce(KPI.kpi_name, "")
            + " "
            + func.coalesce(KPI.description, "")
            + " "
            + func.coalesce(KPI.notes, "")
        )
        direction = func.coalesce(
            KPI.lower_is_better,
            or_(
                *(
                    source_text.contains(key, autoescape=True)
                    for key in sorted(LOWER_SUPPORT_METRIC_KEYS)
                )
            ),
        )
        stmt = (
            select(
                KPI.kpi_id,
                KPI.employee_id,
                KPI.kpi_name,
                KPI.period_start,
                KPI.period_end,
                KPI.target_value,
                KPI.actual_value,
                KPI.unit_of_measure,
                KPI.status,
                direction.label("lower_is_better"),
                Person.name_expr().label("employee_name"),
                func.coalesce(KPI.updated_at, KPI.created_at).label(
                    "record_updated_at"
                ),
                Employee.employee_code,
                Department.department_id,
                Department.department_name,
            )
            .select_from(KPI)
            .join(
                Employee,
                and_(
                    Employee.employee_id == KPI.employee_id,
                    Employee.organization_id == scope.organization_id,
                ),
            )
            .outerjoin(
                Person,
                and_(
                    Person.id == Employee.person_id,
                    Person.organization_id == scope.organization_id,
                ),
            )
            .outerjoin(
                Department,
                and_(
                    Department.department_id == Employee.department_id,
                    Department.organization_id == scope.organization_id,
                ),
            )
            .where(
                KPI.organization_id == scope.organization_id,
                KPI.status.in_([KPIStatus(status) for status in config.statuses]),
            )
        )
        if config.cohort == "active":
            stmt = stmt.where(KPI.period_start <= end, KPI.period_end >= start)
        elif config.cohort == "overdue":
            stmt = stmt.where(
                KPI.period_end < today,
                KPI.status.in_([KPIStatus(status) for status in OPEN_STATUSES]),
            )
        else:
            stmt = stmt.where(KPI.period_end >= start, KPI.period_end <= end)
        if config.employee_search:
            stmt = stmt.where(
                or_(
                    Employee.employee_code.icontains(
                        config.employee_search, autoescape=True
                    ),
                    Person.name_expr().icontains(
                        config.employee_search, autoescape=True
                    ),
                )
            )
        if selected is not None:
            # Empty restricted scope MUST NOT fall through to an unrestricted query.
            stmt = stmt.where(Employee.department_id.in_(selected))
        if config.search:
            # Literal substring, not user-controlled LIKE wildcard syntax or SQL.
            stmt = stmt.where(KPI.kpi_name.icontains(config.search, autoescape=True))
        return stmt

    @staticmethod
    def _aggregates(rows, today: date):
        scoreable = and_(
            rows.c.actual_value.isnot(None),
            rows.c.actual_value >= 0,
            cast(rows.c.actual_value, String).notin_(["NaN", "Infinity", "-Infinity"]),
            cast(rows.c.target_value, String).notin_(["NaN", "Infinity", "-Infinity"]),
            or_(
                rows.c.target_value > 0,
                and_(rows.c.lower_is_better.is_(True), rows.c.target_value == 0),
            ),
        )
        below = or_(
            and_(
                rows.c.lower_is_better.is_(True),
                rows.c.actual_value > rows.c.target_value,
            ),
            and_(
                rows.c.lower_is_better.is_(False),
                rows.c.actual_value < rows.c.target_value,
            ),
        )
        assessable = rows.c.status.notin_(
            [KPIStatus.DRAFT, KPIStatus.CANCELLED, KPIStatus.DEFERRED]
        )
        return (
            func.count(rows.c.kpi_id).label("tracked"),
            func.count(func.distinct(rows.c.employee_id)).label("owners"),
            func.coalesce(
                func.sum(case((rows.c.status == KPIStatus.ACHIEVED, 1), else_=0)),
                0,
            ).label("achieved"),
            func.coalesce(
                func.sum(
                    case(
                        (rows.c.status.in_([KPIStatus.AT_RISK, KPIStatus.MISSED]), 1),
                        else_=0,
                    )
                ),
                0,
            ).label("at_risk"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            and_(
                                rows.c.period_end < today,
                                rows.c.status.in_(
                                    [KPIStatus(status) for status in OPEN_STATUSES]
                                ),
                            ),
                            1,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("overdue"),
            func.count(rows.c.actual_value).label("reported"),
            func.coalesce(
                func.sum(case((rows.c.status == KPIStatus.ON_TRACK, 1), else_=0)), 0
            ).label("on_track"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            and_(
                                assessable,
                                rows.c.period_start <= today,
                                rows.c.actual_value.is_(None),
                            ),
                            1,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("missing"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            and_(
                                assessable, rows.c.period_end < today, scoreable, below
                            ),
                            1,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("below_target"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            and_(
                                assessable, rows.c.actual_value.isnot(None), ~scoreable
                            ),
                            1,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("unscorable"),
            func.max(rows.c.record_updated_at).label("latest_record_update"),
        )

    @staticmethod
    def _summary(mapping) -> dict[str, Any]:
        result = dict(mapping)
        result["coverage"] = reporting_percentage(result["reported"], result["tracked"])
        return result

    def dashboard(
        self,
        scope: DashboardScope,
        config: DashboardConfig,
        *,
        page: int = 1,
        employee_page: int = 1,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if not isinstance(page, int) or not 1 <= page <= 100_000:
            raise DashboardValidationError("Choose a valid results page.")
        if not isinstance(employee_page, int) or not 1 <= employee_page <= 100_000:
            raise DashboardValidationError("Choose a valid employee page.")
        config = DashboardConfig.parse(config.to_dict())
        current = now or datetime.now(timezone.utc)
        today = organization_today(scope.timezone_name, current)
        rows = self._base_query(scope, config, today).subquery("scoped_kpis")
        columns = self._aggregates(rows, today)
        summary = self._summary(self.db.execute(select(*columns)).mappings().one())
        department_rows = (
            self.db.execute(
                select(rows.c.department_id, rows.c.department_name, *columns)
                .group_by(rows.c.department_id, rows.c.department_name)
                .order_by(rows.c.department_name, rows.c.department_id)
            )
            .mappings()
            .all()
        )
        total_pages = max(1, (summary["tracked"] + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(page, total_pages)
        details = (
            self.db.execute(
                select(rows)
                .order_by(rows.c.period_end, rows.c.kpi_name, rows.c.kpi_id)
                .limit(PAGE_SIZE)
                .offset((page - 1) * PAGE_SIZE)
            )
            .mappings()
            .all()
        )
        employee_data = self._employee_review(scope, config, rows, today, employee_page)
        chart_rows = department_rows[:20]
        department_chart = {
            "labels": [row["department_name"] or "Unassigned" for row in chart_rows],
            "datasets": [
                {"label": label, "data": [int(row[key]) for row in chart_rows]}
                for key, label in (
                    ("achieved", "Recorded achieved"),
                    ("on_track", "Recorded on track"),
                    ("at_risk", "Recorded at risk / missed"),
                )
            ]
            + [
                {
                    "label": "Other recorded statuses",
                    "data": [
                        int(
                            row["tracked"]
                            - row["achieved"]
                            - row["on_track"]
                            - row["at_risk"]
                        )
                        for row in chart_rows
                    ],
                }
            ],
        }
        return {
            **employee_data,
            "department_chart": department_chart,
            "coverage_chart": {
                "labels": ["Actual recorded", "Missing actual"],
                "data": [
                    int(summary["reported"]),
                    int(summary["tracked"] - summary["reported"]),
                ],
            },
            "chart_department_limit": len(department_rows) > 20,
            "summary": summary,
            "department_rows": [self._summary(row) for row in department_rows],
            "kpis": [self._record(dict(row), today) for row in details],
            "page": page,
            "total_pages": total_pages,
            "period_start": config.window(today)[0],
            "period_end": config.window(today)[1],
            "business_date": today,
            "queried_at": current.astimezone(timezone.utc),
        }

    @staticmethod
    def _record(row: dict[str, Any], today: date) -> dict[str, Any]:
        score = achievement(
            row["actual_value"], row["target_value"], lower=bool(row["lower_is_better"])
        )
        excluded = row["status"] in {
            KPIStatus.DRAFT,
            KPIStatus.CANCELLED,
            KPIStatus.DEFERRED,
        }
        if excluded:
            label = "Not assessed"
        elif row["actual_value"] is None:
            label = "Missing measurement"
        elif score is None:
            label = "Review measurement setup"
        elif score >= 100:
            label = "Target met"
        elif row["period_end"] < today:
            label = "Below target after deadline"
        else:
            label = "In progress — period not ended"
        row.update(
            achievement=score,
            assessment=label,
            employee_name=row["employee_name"] or row["employee_code"],
        )
        return row

    def _employee_review(
        self,
        scope: DashboardScope,
        config: DashboardConfig,
        rows,
        today: date,
        page: int,
    ) -> dict[str, Any]:
        """Roster-first review; absence of matching data is never a failing score.

        Assignment coverage ignores status/KPI-name filters, but retains the date
        and department scope. Only active employees are included in this roster.
        Employee rows and charts are aggregated before independent pagination.
        """
        selected = scope.selected_departments(config)
        metrics = (
            select(rows.c.employee_id, *self._aggregates(rows, today))
            .group_by(rows.c.employee_id)
            .subquery("employee_metrics")
        )
        assignment_config = replace(
            config, search="", statuses=tuple(status.value for status in KPIStatus)
        )
        assigned_rows = self._base_query(scope, assignment_config, today).subquery(
            "assigned_kpis"
        )
        assignments = (
            select(assigned_rows.c.employee_id, func.count().label("assigned"))
            .group_by(assigned_rows.c.employee_id)
            .subquery("assignment_counts")
        )
        roster = (
            select(
                Employee.employee_id,
                Employee.employee_code,
                Person.name_expr().label("employee_name"),
                Department.department_name,
                func.coalesce(assignments.c.assigned, 0).label("assigned"),
                *(
                    func.coalesce(metrics.c[key], 0).label(key)
                    for key in (
                        "tracked",
                        "achieved",
                        "at_risk",
                        "overdue",
                        "reported",
                        "below_target",
                        "missing",
                        "unscorable",
                    )
                ),
            )
            .select_from(Employee)
            .outerjoin(
                Person,
                and_(
                    Person.id == Employee.person_id,
                    Person.organization_id == scope.organization_id,
                ),
            )
            .outerjoin(
                Department,
                and_(
                    Department.department_id == Employee.department_id,
                    Department.organization_id == scope.organization_id,
                ),
            )
            .outerjoin(metrics, metrics.c.employee_id == Employee.employee_id)
            .outerjoin(assignments, assignments.c.employee_id == Employee.employee_id)
            .where(
                Employee.organization_id == scope.organization_id,
                Employee.status == EmployeeStatus.ACTIVE,
            )
        )
        if selected is not None:
            roster = roster.where(Employee.department_id.in_(selected))
        if config.employee_search:
            roster = roster.where(
                or_(
                    Employee.employee_code.icontains(
                        config.employee_search, autoescape=True
                    ),
                    Person.name_expr().icontains(
                        config.employee_search, autoescape=True
                    ),
                )
            )
        roster_rows = roster.subquery("roster")
        needs_review = or_(
            roster_rows.c.below_target > 0,
            roster_rows.c.overdue > 0,
            roster_rows.c.missing > 0,
            roster_rows.c.unscorable > 0,
            (roster_rows.c.assigned == 0) if config.cohort != "overdue" else False,
        )
        roster_counts = (
            self.db.execute(
                select(
                    func.count().label("total"),
                    func.coalesce(func.sum(case((needs_review, 1), else_=0)), 0).label(
                        "needs_review"
                    ),
                    func.coalesce(
                        func.sum(case((roster_rows.c.assigned == 0, 1), else_=0)), 0
                    ).label("no_kpis"),
                ).select_from(roster_rows)
            )
            .mappings()
            .one()
        )
        filtered = select(roster_rows)
        total = roster_counts["total"]
        if config.attention == "needs_attention":
            filtered = filtered.where(needs_review)
            total = roster_counts["needs_review"]
        total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(page, total_pages)
        records = (
            self.db.execute(
                filtered.order_by(
                    roster_rows.c.below_target.desc(),
                    roster_rows.c.overdue.desc(),
                    roster_rows.c.employee_code,
                    roster_rows.c.employee_id,
                )
                .limit(PAGE_SIZE)
                .offset((page - 1) * PAGE_SIZE)
            )
            .mappings()
            .all()
        )
        return {
            "employee_rows": [dict(row) for row in records],
            "employee_counts": dict(roster_counts),
            "employee_page": page,
            "employee_total_pages": total_pages,
            "employee_total": total,
        }

    def _person(self, scope: DashboardScope, *, lock: bool = False) -> Person:
        stmt = select(Person).where(
            Person.id == scope.person_id,
            Person.organization_id == scope.organization_id,
            Person.is_active.is_(True),
        )
        if lock:
            # Reload after obtaining the lock so concurrent saves preserve other keys.
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        person = self.db.scalars(stmt).one_or_none()
        if person is None:
            raise DashboardAccessError("Your dashboard profile is unavailable.")
        return person

    @staticmethod
    def _views(person: Person) -> list[dict[str, Any]]:
        metadata = person.metadata_ or {}
        if not isinstance(metadata, dict):
            raise DashboardValidationError(
                "Saved dashboard settings need administrator review."
            )
        stored = metadata.get(PREFERENCE_KEY, {"version": 1, "views": []})
        if (
            not isinstance(stored, dict)
            or stored.get("version") != 1
            or not isinstance(stored.get("views"), list)
            or len(stored["views"]) > MAX_SAVED_VIEWS
        ):
            raise DashboardValidationError(
                "Saved dashboard settings need administrator review."
            )
        views = stored["views"]
        if any(
            not isinstance(item, dict)
            or not isinstance(item.get("id"), str)
            or not isinstance(item.get("name"), str)
            or not isinstance(item.get("config"), dict)
            for item in views
        ):
            raise DashboardValidationError(
                "Saved dashboard settings need administrator review."
            )
        return [dict(item) for item in views]

    def list_views(self, scope: DashboardScope) -> list[dict[str, Any]]:
        # Never returns another person's views, even when the viewer is an admin.
        return self._views(self._person(scope))

    def get_view(
        self, scope: DashboardScope, view_id: str
    ) -> tuple[str, DashboardConfig]:
        for item in self.list_views(scope):
            if item["id"] == view_id:
                config = DashboardConfig.parse(item["config"])
                scope.selected_departments(config)  # Reauthorize on every load.
                return item["name"], config
        raise SavedViewNotFound("This saved view is unavailable.")

    def save_view(
        self,
        scope: DashboardScope,
        config: DashboardConfig,
        name: str,
        *,
        view_id: str | None = None,
    ) -> str:
        name = view_name(name)
        # Never trust a caller-created dataclass or a previously saved filter.
        config = DashboardConfig.parse(config.to_dict())
        scope.selected_departments(config)
        person = self._person(scope, lock=True)
        views = self._views(person)
        if view_id and not any(item["id"] == view_id for item in views):
            raise SavedViewNotFound("This saved view is unavailable.")
        if not view_id and len(views) >= MAX_SAVED_VIEWS:
            raise DashboardValidationError(
                "Delete a saved view before adding another (maximum 10)."
            )
        if any(
            item["name"].casefold() == name.casefold() and item["id"] != view_id
            for item in views
        ):
            raise DashboardValidationError("A saved view already uses that name.")
        identifier = view_id or str(uuid4())
        entry = {
            "id": identifier,
            "name": name,
            "config": config.to_dict(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        views = [entry if item["id"] == identifier else item for item in views]
        if not view_id:
            views.append(entry)
        person.metadata_ = {
            **(person.metadata_ or {}),
            PREFERENCE_KEY: {"version": 1, "views": views},
        }
        self.db.flush()
        logger.info("Saved personal KPI dashboard view for person %s", scope.person_id)
        return identifier

    def delete_view(self, scope: DashboardScope, view_id: str) -> None:
        person = self._person(scope, lock=True)
        views = self._views(person)
        remaining = [item for item in views if item["id"] != view_id]
        if len(remaining) == len(views):
            raise SavedViewNotFound("This saved view is unavailable.")
        person.metadata_ = {
            **(person.metadata_ or {}),
            PREFERENCE_KEY: {"version": 1, "views": remaining},
        }
        self.db.flush()
        logger.info(
            "Deleted personal KPI dashboard view for person %s", scope.person_id
        )
