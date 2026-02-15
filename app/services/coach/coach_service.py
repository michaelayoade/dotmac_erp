from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import Select, case, func, or_, select
from sqlalchemy.orm import Session

from app.models.coach.insight import CoachInsight
from app.models.people.hr.employee import Employee
from app.models.rbac import Permission, PersonRole, Role, RolePermission
from app.services.common import coerce_uuid

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CoachInsightScope:
    """
    Scope constraints for insight visibility.

    employee_ids=None means "all employees" (org-wide + any target).
    audiences=None means "all audiences".
    """

    include_org_wide: bool
    employee_ids: set[UUID] | None
    audiences: set[str] | None


class CoachService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def is_enabled(self) -> bool:
        # Feature flag is implemented as config/env. Default is disabled.
        # Keep this lightweight and side-effect free.
        from app.config import settings as app_settings

        return bool(getattr(app_settings, "coach_enabled", False))

    def build_scope_for_user(
        self,
        organization_id: UUID,
        person_id: UUID,
        employee_id: UUID | None,
        roles: set[str],
    ) -> CoachInsightScope:
        """
        Build visibility scope for a user.

        Current implementation focuses on protecting per-employee insights.
        Audience/category filtering is handled at insight generation time.
        """
        org_id = coerce_uuid(organization_id)
        pid = coerce_uuid(person_id)

        can_all = self._has_permission(pid, "coach:insights:read_all")
        can_team = self._has_permission(pid, "coach:insights:read_team")

        if can_all:
            return CoachInsightScope(
                include_org_wide=True,
                employee_ids=None,
                audiences=None,
            )

        audiences = self._audiences_for_roles(roles)
        if not audiences:
            # Defense in depth: if we cannot map the role to an audience, return nothing.
            return CoachInsightScope(
                include_org_wide=False, employee_ids=set(), audiences=set()
            )

        if can_team and employee_id:
            employee_ids = self._collect_report_tree(org_id, coerce_uuid(employee_id))
            return CoachInsightScope(
                include_org_wide=True, employee_ids=employee_ids, audiences=audiences
            )

        if employee_id:
            return CoachInsightScope(
                include_org_wide=True,
                employee_ids={coerce_uuid(employee_id)},
                audiences=audiences,
            )

        # Non-employee accounts: only org-wide insights.
        return CoachInsightScope(
            include_org_wide=True,
            employee_ids=set(),
            audiences=audiences,
        )

    def list_insights(
        self,
        organization_id: UUID,
        scope: CoachInsightScope,
        page: int = 1,
        per_page: int = 50,
        include_expired: bool = False,
    ) -> tuple[list[CoachInsight], int]:
        if page < 1 or per_page < 1:
            raise ValueError("Invalid pagination parameters")

        org_id = coerce_uuid(organization_id)

        stmt = select(CoachInsight).where(CoachInsight.organization_id == org_id)

        if not include_expired:
            stmt = stmt.where(CoachInsight.valid_until >= date.today())

        if scope.audiences is not None:
            if not scope.audiences:
                return [], 0
            stmt = stmt.where(CoachInsight.audience.in_(sorted(scope.audiences)))

        stmt = stmt.where(self._scope_predicate(scope))
        stmt = self._apply_ordering(stmt)

        total = self.db.scalar(select(func.count()).select_from(stmt.subquery()))
        if total is None:
            total = 0

        offset = (page - 1) * per_page
        items = list(self.db.scalars(stmt.limit(per_page).offset(offset)).all())
        return items, int(total)

    def update_feedback(
        self,
        organization_id: UUID,
        insight_id: UUID,
        feedback: str,
    ) -> CoachInsight:
        if feedback not in {"helpful", "not_relevant", "inaccurate"}:
            raise ValueError(
                "Invalid feedback value. Must be: helpful, not_relevant, or inaccurate"
            )

        org_id = coerce_uuid(organization_id)
        iid = coerce_uuid(insight_id)

        insight = self.db.get(CoachInsight, iid)
        if not insight or insight.organization_id != org_id:
            raise ValueError("Insight not found")

        insight = cast(CoachInsight, insight)
        insight.feedback = feedback
        # Keep write minimal; avoid changing status implicitly here.
        if insight.read_at is None:
            insight.read_at = datetime.now(UTC)
            if insight.status == "GENERATED":
                insight.status = "READ"

        self.db.flush()
        return insight

    def _scope_predicate(self, scope: CoachInsightScope):
        clauses = []
        if scope.include_org_wide:
            clauses.append(CoachInsight.target_employee_id.is_(None))

        if scope.employee_ids is None:
            clauses.append(CoachInsight.target_employee_id.is_not(None))
        else:
            if scope.employee_ids:
                clauses.append(CoachInsight.target_employee_id.in_(scope.employee_ids))

        if not clauses:
            # Should never happen, but avoid returning everything on a bug.
            return CoachInsight.insight_id.is_(None)

        return or_(*clauses)

    def _audiences_for_roles(self, roles: set[str]) -> set[str]:
        """
        Map RBAC roles to coach audiences (defense in depth filter).

        This prevents accidental cross-role leakage if an insight was mis-generated
        with an incorrect audience.
        """
        if "admin" in roles:
            return {
                "EMPLOYEE",
                "MANAGER",
                "HR",
                "FINANCE",
                "OPERATIONS",
                "EXECUTIVE",
            }

        audiences: set[str] = set()
        finance_roles = {
            "finance_director",
            "finance_manager",
            "senior_accountant",
            "accountant",
            "junior_accountant",
            "finance_viewer",
            "ap_clerk",
            "ar_clerk",
            "tax_specialist",
            "expense_admin",
            "expense_approver",
            "expense_processor",
            "expense_reimburser",
        }
        hr_roles = {
            "hr_director",
            "hr_manager",
            "hr_officer",
            "hr_assistant",
            "hr_viewer",
            "payroll_manager",
            "payroll_officer",
            "recruiter",
            "training_manager",
        }
        if roles & finance_roles:
            audiences.add("FINANCE")
        if roles & hr_roles:
            audiences.add("HR")
        if "department_manager" in roles:
            audiences.add("MANAGER")
        if "employee" in roles:
            audiences.add("EMPLOYEE")
        if roles & {"operations_manager", "support_agent"}:
            audiences.add("OPERATIONS")

        # Directors also see executive summaries.
        if roles & {"finance_director", "hr_director", "auditor"}:
            audiences.add("EXECUTIVE")

        return audiences

    def _apply_ordering(
        self, stmt: Select[tuple[CoachInsight]]
    ) -> Select[tuple[CoachInsight]]:
        severity_rank = case(
            (CoachInsight.severity == "URGENT", 4),
            (CoachInsight.severity == "WARNING", 3),
            (CoachInsight.severity == "ATTENTION", 2),
            (CoachInsight.severity == "INFO", 1),
            else_=0,
        )
        return stmt.order_by(
            severity_rank.desc(),
            CoachInsight.created_at.desc(),
        )

    def _has_permission(self, person_id: UUID, permission_key: str) -> bool:
        # "admin" role is always allowed.
        stmt_admin = (
            select(func.count())
            .select_from(PersonRole)
            .join(Role, PersonRole.role_id == Role.id)
            .where(
                PersonRole.person_id == person_id,
                Role.name == "admin",
                Role.is_active.is_(True),
            )
        )
        if int(self.db.scalar(stmt_admin) or 0) > 0:
            return True

        perm = self.db.scalar(
            select(Permission)
            .where(Permission.key == permission_key, Permission.is_active.is_(True))
            .limit(1)
        )
        if not perm:
            return False

        stmt = (
            select(func.count())
            .select_from(RolePermission)
            .join(Role, RolePermission.role_id == Role.id)
            .join(PersonRole, PersonRole.role_id == Role.id)
            .where(
                PersonRole.person_id == person_id,
                RolePermission.permission_id == perm.id,
                Role.is_active.is_(True),
            )
        )
        return int(self.db.scalar(stmt) or 0) > 0

    def _collect_report_tree(
        self, organization_id: UUID, root_employee_id: UUID
    ) -> set[UUID]:
        """
        Collect all employees in the manager's reporting tree (including self).

        Uses iterative BFS to avoid recursion limits and keep queries bounded.
        """
        seen: set[UUID] = set()
        frontier: set[UUID] = {root_employee_id}

        while frontier:
            batch = list(frontier)
            frontier.clear()
            for emp_id in batch:
                if emp_id in seen:
                    continue
                seen.add(emp_id)

            # Fetch direct reports for current batch.
            direct = self.db.scalars(
                select(Employee.employee_id).where(
                    Employee.organization_id == organization_id,
                    Employee.reports_to_id.in_(batch),
                )
            ).all()
            for emp_id in direct:
                if emp_id not in seen:
                    frontier.add(emp_id)

        return seen
