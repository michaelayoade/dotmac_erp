"""Expense dashboard chart and recent-item queries."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import and_, extract, func, select
from sqlalchemy.orm import Session

from app.models.expense.expense_claim import (
    ExpenseCategory,
    ExpenseClaim,
    ExpenseClaimItem,
    ExpenseClaimStatus,
)
from app.models.people.hr import Department, Employee
from app.models.person import Person
from app.services.expense.dashboard_common import _format_currency
from app.services.expense.expense_service import REPORTABLE_EXPENSE_CLAIM_STATUSES


class ExpenseDashboardChartsMixin:
    def _get_claims_chart_data(
        self, db: Session, org_id: UUID, start_date: date | None
    ) -> dict[str, Any]:
        return {
            "claims_trend": self._get_claims_trend(db, org_id),
            "status_distribution": self._get_status_breakdown(db, org_id, start_date),
            "category_breakdown": self._get_category_distribution(
                db, org_id, start_date
            ),
            "department_breakdown": self._get_department_spending(
                db, org_id, start_date
            ),
            "top_claimants": self._get_top_spenders(db, org_id, start_date),
            "monthly_amounts": self._get_monthly_amounts(db, org_id),
        }

    def _get_claims_trend(self, db: Session, org_id: UUID) -> list[dict[str, Any]]:
        today = date.today()
        trend = []
        for i in range(5, -1, -1):
            month_date = today - timedelta(days=i * 30)
            month_name = month_date.strftime("%b")
            submitted = (
                db.scalar(
                    select(func.count(ExpenseClaim.claim_id)).where(
                        and_(
                            ExpenseClaim.organization_id == org_id,
                            ExpenseClaim.status.in_(REPORTABLE_EXPENSE_CLAIM_STATUSES),
                            extract("year", ExpenseClaim.claim_date) == month_date.year,
                            extract("month", ExpenseClaim.claim_date)
                            == month_date.month,
                        )
                    )
                )
                or 0
            )
            paid = (
                db.scalar(
                    select(func.count(ExpenseClaim.claim_id)).where(
                        and_(
                            ExpenseClaim.organization_id == org_id,
                            ExpenseClaim.status == ExpenseClaimStatus.PAID,
                            extract("year", ExpenseClaim.claim_date) == month_date.year,
                            extract("month", ExpenseClaim.claim_date)
                            == month_date.month,
                        )
                    )
                )
                or 0
            )
            trend.append({"month": month_name, "submitted": submitted, "paid": paid})
        return trend

    def _get_monthly_amounts(self, db: Session, org_id: UUID) -> list[dict[str, Any]]:
        today = date.today()
        amounts = []
        for i in range(5, -1, -1):
            month_date = today - timedelta(days=i * 30)
            month_name = month_date.strftime("%b")
            claimed = db.scalar(
                select(
                    func.coalesce(func.sum(ExpenseClaim.total_claimed_amount), 0)
                ).where(
                    and_(
                        ExpenseClaim.organization_id == org_id,
                        ExpenseClaim.status.in_(REPORTABLE_EXPENSE_CLAIM_STATUSES),
                        extract("year", ExpenseClaim.claim_date) == month_date.year,
                        extract("month", ExpenseClaim.claim_date) == month_date.month,
                    )
                )
            ) or Decimal(0)
            paid = db.scalar(
                select(
                    func.coalesce(func.sum(ExpenseClaim.total_claimed_amount), 0)
                ).where(
                    and_(
                        ExpenseClaim.organization_id == org_id,
                        ExpenseClaim.status == ExpenseClaimStatus.PAID,
                        extract("year", ExpenseClaim.claim_date) == month_date.year,
                        extract("month", ExpenseClaim.claim_date) == month_date.month,
                    )
                )
            ) or Decimal(0)
            amounts.append(
                {"month": month_name, "claimed": float(claimed), "paid": float(paid)}
            )
        return amounts

    def _get_recent_claims_detailed(
        self,
        db: Session,
        org_id: UUID,
        limit: int = 8,
        currency: str | None = None,
    ) -> list[dict[str, Any]]:
        currency = currency or self._resolve_currency(db, org_id)
        results = db.execute(
            select(ExpenseClaim, Person)
            .join(Employee, Employee.employee_id == ExpenseClaim.employee_id)
            .join(Person, Person.id == Employee.person_id)
            .where(ExpenseClaim.organization_id == org_id)
            .order_by(ExpenseClaim.created_at.desc())
            .limit(limit)
        ).all()

        return [
            {
                "id": str(claim.claim_id),
                "title": claim.purpose or "Expense Claim",
                "claim_number": claim.claim_number or "",
                "employee_name": person.name,
                "amount": _format_currency(claim.total_claimed_amount, currency),
                "status": claim.status.value if claim.status else "DRAFT",
                "date": claim.claim_date.strftime("%b %d, %Y")
                if claim.claim_date
                else "",
            }
            for claim, person in results
        ]

    def _get_chart_data(
        self, db: Session, org_id: UUID, start_date: date | None
    ) -> dict[str, Any]:
        return {
            "expense_trend": self._get_expense_trend(db, org_id),
            "category_distribution": self._get_category_distribution(
                db, org_id, start_date
            ),
            "top_spenders": self._get_top_spenders(db, org_id, start_date),
            "status_breakdown": self._get_status_breakdown(db, org_id, start_date),
            "department_spending": self._get_department_spending(
                db, org_id, start_date
            ),
        }

    def _get_expense_trend(self, db: Session, org_id: UUID) -> list[dict[str, Any]]:
        today = date.today()
        trend = []
        for i in range(5, -1, -1):
            month_date = today - timedelta(days=i * 30)
            month_name = month_date.strftime("%b")
            submitted = db.scalar(
                select(
                    func.coalesce(func.sum(ExpenseClaim.total_claimed_amount), 0)
                ).where(
                    and_(
                        ExpenseClaim.organization_id == org_id,
                        ExpenseClaim.status.in_(REPORTABLE_EXPENSE_CLAIM_STATUSES),
                        extract("year", ExpenseClaim.claim_date) == month_date.year,
                        extract("month", ExpenseClaim.claim_date) == month_date.month,
                    )
                )
            ) or Decimal(0)
            reimbursed = db.scalar(
                select(
                    func.coalesce(func.sum(ExpenseClaim.total_claimed_amount), 0)
                ).where(
                    and_(
                        ExpenseClaim.organization_id == org_id,
                        ExpenseClaim.status == ExpenseClaimStatus.PAID,
                        extract("year", ExpenseClaim.claim_date) == month_date.year,
                        extract("month", ExpenseClaim.claim_date) == month_date.month,
                    )
                )
            ) or Decimal(0)
            trend.append(
                {
                    "month": month_name,
                    "submitted": float(submitted),
                    "reimbursed": float(reimbursed),
                }
            )
        return trend

    def _get_category_distribution(
        self, db: Session, org_id: UUID, start_date: date | None
    ) -> list[dict[str, Any]]:
        base_filter = [ExpenseClaimItem.organization_id == org_id]
        if start_date:
            base_filter.append(ExpenseClaimItem.expense_date >= start_date)
        results = db.execute(
            select(
                ExpenseCategory.category_name,
                func.coalesce(func.sum(ExpenseClaimItem.claimed_amount), 0),
            )
            .join(ExpenseClaim, ExpenseClaim.claim_id == ExpenseClaimItem.claim_id)
            .join(
                ExpenseCategory,
                ExpenseCategory.category_id == ExpenseClaimItem.category_id,
            )
            .where(
                and_(
                    *base_filter,
                    ExpenseClaim.status.in_(REPORTABLE_EXPENSE_CLAIM_STATUSES),
                )
            )
            .group_by(ExpenseCategory.category_name)
            .order_by(func.sum(ExpenseClaimItem.claimed_amount).desc())
            .limit(8)
        ).all()
        return [{"category": name, "amount": float(amount)} for name, amount in results]

    def _get_top_spenders(
        self, db: Session, org_id: UUID, start_date: date | None
    ) -> list[dict[str, Any]]:
        base_filter = [ExpenseClaim.organization_id == org_id]
        if start_date:
            base_filter.append(ExpenseClaim.claim_date >= start_date)
        base_filter.append(ExpenseClaim.status.in_(REPORTABLE_EXPENSE_CLAIM_STATUSES))
        employee_name_col = Person.name_expr().label("employee_name")
        results = db.execute(
            select(
                employee_name_col,
                func.coalesce(func.sum(ExpenseClaim.total_claimed_amount), 0).label(
                    "total"
                ),
            )
            .join(Employee, Employee.employee_id == ExpenseClaim.employee_id)
            .join(Person, Person.id == Employee.person_id)
            .where(and_(*base_filter))
            .group_by(employee_name_col)
            .order_by(func.sum(ExpenseClaim.total_claimed_amount).desc())
            .limit(5)
        ).all()
        return [
            {"name": row.employee_name, "amount": float(row.total)} for row in results
        ]

    def _get_status_breakdown(
        self, db: Session, org_id: UUID, start_date: date | None
    ) -> list[dict[str, Any]]:
        base_filter = [ExpenseClaim.organization_id == org_id]
        if start_date:
            base_filter.append(ExpenseClaim.claim_date >= start_date)
        base_filter.append(ExpenseClaim.status.in_(REPORTABLE_EXPENSE_CLAIM_STATUSES))
        results = db.execute(
            select(ExpenseClaim.status, func.count(ExpenseClaim.claim_id))
            .where(and_(*base_filter))
            .group_by(ExpenseClaim.status)
        ).all()
        status_labels = {
            "DRAFT": "Draft",
            "SUBMITTED": "Submitted",
            "PENDING_APPROVAL": "Pending",
            "APPROVED": "Approved",
            "REJECTED": "Rejected",
            "PAID": "Paid",
            "CANCELLED": "Cancelled",
        }
        return [
            {
                "status": status_labels.get(str(status), str(status).title()),
                "count": count,
            }
            for status, count in results
        ]

    def _get_department_spending(
        self, db: Session, org_id: UUID, start_date: date | None
    ) -> list[dict[str, Any]]:
        base_filter = [ExpenseClaim.organization_id == org_id]
        if start_date:
            base_filter.append(ExpenseClaim.claim_date >= start_date)
        base_filter.append(ExpenseClaim.status.in_(REPORTABLE_EXPENSE_CLAIM_STATUSES))
        results = db.execute(
            select(
                Department.department_name,
                func.coalesce(func.sum(ExpenseClaim.total_claimed_amount), 0),
            )
            .join(Employee, Employee.employee_id == ExpenseClaim.employee_id)
            .join(Department, Department.department_id == Employee.department_id)
            .where(and_(*base_filter))
            .group_by(Department.department_name)
            .order_by(func.sum(ExpenseClaim.total_claimed_amount).desc())
            .limit(5)
        ).all()
        return [
            {"department": name, "amount": float(amount)} for name, amount in results
        ]

    def _get_recent_claims(
        self,
        db: Session,
        org_id: UUID,
        limit: int = 5,
        currency: str | None = None,
    ) -> list[dict[str, Any]]:
        currency = currency or self._resolve_currency(db, org_id)
        results = db.execute(
            select(ExpenseClaim, Person)
            .join(Employee, Employee.employee_id == ExpenseClaim.employee_id)
            .join(Person, Person.id == Employee.person_id)
            .where(ExpenseClaim.organization_id == org_id)
            .order_by(ExpenseClaim.created_at.desc())
            .limit(limit)
        ).all()
        return [
            {
                "id": str(claim.claim_id),
                "title": claim.purpose or f"Expense #{claim.claim_number}",
                "employee_name": person.name,
                "amount": _format_currency(claim.total_claimed_amount, currency),
                "status": claim.status.value if claim.status else "DRAFT",
                "date": claim.claim_date.strftime("%b %d, %Y")
                if claim.claim_date
                else "",
            }
            for claim, person in results
        ]
