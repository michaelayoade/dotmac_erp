from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, delete, func, select
from sqlalchemy.orm import Session

from app.models.coach.insight import CoachInsight
from app.models.people.hr.employee import Employee, EmployeeStatus

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EmployeeProfileGaps:
    active_employees: int
    missing_department: int
    missing_manager: int
    missing_expense_approver: int


class DataQualityAnalyzer:
    """
    Deterministic data-quality analyzer (no LLM required).

    Generates org-wide HR insights that are safe to show as aggregates.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    def employee_profile_gaps(self, organization_id: UUID) -> EmployeeProfileGaps:
        org_id = organization_id

        base = and_(
            Employee.organization_id == org_id,
            Employee.status == EmployeeStatus.ACTIVE,
        )

        active_employees = int(
            self.db.scalar(select(func.count()).select_from(Employee).where(base)) or 0
        )
        missing_department = int(
            self.db.scalar(
                select(func.count())
                .select_from(Employee)
                .where(base, Employee.department_id.is_(None))
            )
            or 0
        )
        missing_manager = int(
            self.db.scalar(
                select(func.count())
                .select_from(Employee)
                .where(base, Employee.reports_to_id.is_(None))
            )
            or 0
        )
        missing_expense_approver = int(
            self.db.scalar(
                select(func.count())
                .select_from(Employee)
                .where(base, Employee.expense_approver_id.is_(None))
            )
            or 0
        )

        return EmployeeProfileGaps(
            active_employees=active_employees,
            missing_department=missing_department,
            missing_manager=missing_manager,
            missing_expense_approver=missing_expense_approver,
        )

    def generate_employee_profile_gaps_insight(
        self,
        organization_id: UUID,
    ) -> CoachInsight | None:
        gaps = self.employee_profile_gaps(organization_id)
        if gaps.active_employees == 0:
            return None

        title = "Employee profile completeness gaps"
        summary = (
            f"Active employees: {gaps.active_employees}. "
            f"Missing department: {gaps.missing_department}. "
            f"Missing manager: {gaps.missing_manager}. "
            f"Missing expense approver: {gaps.missing_expense_approver}."
        )
        coaching_action = (
            "Prioritize fixing missing department/manager links for active employees. "
            "These gaps reduce reporting accuracy and slow approvals."
        )

        return CoachInsight(
            insight_id=uuid.uuid4(),
            organization_id=organization_id,
            audience="HR",
            target_employee_id=None,
            category="DATA_QUALITY",
            severity="ATTENTION",
            title=title,
            summary=summary,
            detail=None,
            coaching_action=coaching_action,
            confidence=0.95,
            data_sources={"hr.employee": gaps.active_employees},
            evidence={
                "active_employees": gaps.active_employees,
                "missing_department": gaps.missing_department,
                "missing_manager": gaps.missing_manager,
                "missing_expense_approver": gaps.missing_expense_approver,
            },
            status="GENERATED",
            delivered_at=None,
            read_at=None,
            dismissed_at=None,
            feedback=None,
            valid_until=date.today() + timedelta(days=1),
            created_at=datetime.now(UTC),
        )

    def upsert_daily_org_insights(self, organization_id: UUID) -> int:
        """
        Idempotently write today's org-wide insights for this analyzer.

        Deletes existing matching insights for today, then inserts.
        """
        org_id = organization_id
        today = date.today()

        self.db.execute(
            delete(CoachInsight).where(
                CoachInsight.organization_id == org_id,
                CoachInsight.target_employee_id.is_(None),
                CoachInsight.category == "DATA_QUALITY",
                CoachInsight.audience == "HR",
                func.date(CoachInsight.created_at) == today,
                CoachInsight.title == "Employee profile completeness gaps",
            )
        )

        insight = self.generate_employee_profile_gaps_insight(org_id)
        if not insight:
            return 0

        self.db.add(insight)
        self.db.flush()
        return 1
