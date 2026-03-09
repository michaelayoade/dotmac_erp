"""
Expense Integration Service - PM Module.

Business logic for linking expense claims to projects.
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from typing import TYPE_CHECKING, cast

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.config import settings

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.auth import Principal

__all__ = ["ProjectExpenseService"]


class ProjectExpenseService:
    """
    Service for project expense integration.

    Links expense claims to projects for cost tracking and reporting.
    """

    def __init__(
        self,
        db: Session,
        organization_id: uuid.UUID,
        principal: Principal | None = None,
    ) -> None:
        self.db = db
        self.organization_id = organization_id
        self.principal = principal

    def get_project_expenses(self, project_id: uuid.UUID) -> list[dict]:
        """
        Get all expense claims linked to a project.

        Returns list of expense claim summaries.
        """
        # Import here to avoid circular imports
        try:
            from app.models.expense.expense_claim import ExpenseClaim
        except ImportError:
            # Expense module not available
            return []

        stmt = select(ExpenseClaim).where(
            ExpenseClaim.project_id == project_id,
            ExpenseClaim.organization_id == self.organization_id,
        )

        expenses = self.db.scalars(stmt).all()

        result = []
        for e in expenses:
            employee_name = None
            emp_id = getattr(e, "employee_id", None)
            if emp_id:
                try:
                    from app.models.people.hr.employee import Employee

                    emp = self.db.get(Employee, emp_id)
                    if emp:
                        employee_name = (
                            f"{emp.first_name} {emp.last_name}"
                            if emp.first_name
                            else emp.last_name
                        )
                except ImportError:
                    pass

            result.append(
                {
                    "expense_id": e.claim_id,
                    "claim_number": e.claim_number,
                    "description": getattr(e, "purpose", None),
                    "amount": getattr(e, "total_claimed_amount", Decimal("0")),
                    "status": getattr(e, "status", "UNKNOWN"),
                    "expense_date": getattr(e, "claim_date", None),
                    "employee_name": employee_name,
                    "category_name": None,
                }
            )
        return result

    def get_expense_summary(self, project_id: uuid.UUID) -> dict:
        """
        Get expense summary for a project.

        Returns aggregated expense data.
        """
        try:
            from app.models.expense.expense_claim import (
                ExpenseClaim,
                ExpenseClaimStatus,
            )
        except ImportError:
            return {
                "project_id": project_id,
                "total_expenses": Decimal("0"),
                "expense_count": 0,
                "approved_amount": Decimal("0"),
                "pending_amount": Decimal("0"),
                "expenses_by_category": {},
            }

        base_where = and_(
            ExpenseClaim.project_id == project_id,
            ExpenseClaim.organization_id == self.organization_id,
        )

        # Total expenses
        total_count = (
            self.db.scalar(select(func.count(ExpenseClaim.claim_id)).where(base_where))
            or 0
        )

        total_amount = self.db.scalar(
            select(func.sum(ExpenseClaim.total_claimed_amount)).where(base_where)
        ) or Decimal("0")

        # Approved expenses
        approved_amount = Decimal("0")
        pending_amount = Decimal("0")

        if hasattr(ExpenseClaimStatus, "APPROVED"):
            approved_amount = self.db.scalar(
                select(func.sum(ExpenseClaim.total_claimed_amount)).where(
                    base_where,
                    ExpenseClaim.status == ExpenseClaimStatus.APPROVED,
                )
            ) or Decimal("0")

        if hasattr(ExpenseClaimStatus, "PENDING_APPROVAL"):
            pending_amount = self.db.scalar(
                select(func.sum(ExpenseClaim.total_claimed_amount)).where(
                    base_where,
                    ExpenseClaim.status == ExpenseClaimStatus.PENDING_APPROVAL,
                )
            ) or Decimal("0")

        # By category
        expenses_by_category: dict[str, Decimal] = {}
        if hasattr(ExpenseClaim, "category"):
            expenses_by_category = {}

        return {
            "project_id": project_id,
            "total_amount": total_amount,
            "total_expenses": total_amount,
            "expense_count": total_count,
            "approved_amount": approved_amount,
            "pending_amount": pending_amount,
            "currency": settings.default_functional_currency_code,
            "expenses_by_category": expenses_by_category,
        }

    def get_expense_by_category(self, project_id: uuid.UUID) -> dict[str, Decimal]:
        """Get expenses grouped by category."""
        summary = self.get_expense_summary(project_id)
        return cast(dict[str, Decimal], summary.get("expenses_by_category", {}))
