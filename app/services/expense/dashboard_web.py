"""Expense Dashboard Web Service.

Provides dashboard data and chart computations for the Expense module.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from fastapi import Request
from fastapi.responses import HTMLResponse
from sqlalchemy import and_, extract, func, select
from sqlalchemy.orm import Session

from app.models.expense.cash_advance import CashAdvance
from app.models.expense.corporate_card import CardTransaction
from app.models.expense.expense_claim import (
    ExpenseCategory,
    ExpenseClaim,
    ExpenseClaimItem,
)
from app.models.people.hr import Department, Employee
from app.models.person import Person
from app.services.common import coerce_uuid
from app.services.formatters import format_currency
from app.templates import templates

if TYPE_CHECKING:
    from app.web.deps import WebAuthContext

logger = logging.getLogger(__name__)


def _format_currency(amount: Decimal, currency: str = "NGN") -> str:
    """Format amount as currency string."""
    return format_currency(amount, currency, none_value=f"{currency} 0")


class ExpenseDashboardService:
    """Service for Expense module dashboard."""

    def dashboard_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        period: str = "month",
    ) -> HTMLResponse:
        """Render the Expense dashboard page."""
        from app.web.deps import base_context

        org_id = coerce_uuid(auth.organization_id)

        # Determine date range based on period
        today = date.today()
        if period == "month":
            start_date = today.replace(day=1)
        elif period == "quarter":
            quarter_start_month = ((today.month - 1) // 3) * 3 + 1
            start_date = today.replace(month=quarter_start_month, day=1)
        elif period == "year":
            start_date = today.replace(month=1, day=1)
        else:
            start_date = None  # All time

        # Get currency from org settings (simplified)
        currency = "NGN"
        currency_zero = _format_currency(Decimal(0), currency)

        # Gather all dashboard data
        stats = self._get_dashboard_stats(db, org_id, start_date, currency)
        chart_data = self._get_chart_data(db, org_id, start_date)
        recent_claims = self._get_recent_claims(db, org_id, limit=5, currency=currency)

        context = {
            **base_context(request, auth, "Expense Dashboard", "dashboard"),
            "stats": stats,
            "chart_data": chart_data,
            "recent_claims": recent_claims,
            "selected_period": period,
            "currency_zero": currency_zero,
            "presentation_currency_code": currency,
        }

        # Coach insight cards for Expense dashboards
        try:
            from app.services.coach.coach_service import CoachService

            coach_svc = CoachService(db)
            if coach_svc.is_enabled():
                context["coach_insights"] = coach_svc.top_insights_for_module(
                    org_id, ["EFFICIENCY", "COMPLIANCE"]
                )
            else:
                context["coach_insights"] = []
        except Exception:
            context["coach_insights"] = []

        return templates.TemplateResponse(request, "expense/dashboard.html", context)

    def claims_dashboard_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        period: str = "month",
    ) -> HTMLResponse:
        """Render the Expense Claims dashboard page."""
        from app.web.deps import base_context

        org_id = coerce_uuid(auth.organization_id)

        # Determine date range based on period
        today = date.today()
        if period == "month":
            start_date = today.replace(day=1)
        elif period == "quarter":
            quarter_start_month = ((today.month - 1) // 3) * 3 + 1
            start_date = today.replace(month=quarter_start_month, day=1)
        elif period == "year":
            start_date = today.replace(month=1, day=1)
        else:
            start_date = None  # All time

        # Get currency from org settings (simplified)
        currency = "NGN"
        currency_zero = _format_currency(Decimal(0), currency)

        # Gather claims-specific dashboard data
        stats = self._get_claims_stats(db, org_id, start_date, currency)
        chart_data = self._get_claims_chart_data(db, org_id, start_date)
        recent_claims = self._get_recent_claims_detailed(
            db, org_id, limit=8, currency=currency
        )

        context = {
            **base_context(request, auth, "Expense Claims", "claims"),
            "stats": stats,
            "chart_data": chart_data,
            "recent_claims": recent_claims,
            "selected_period": period,
            "currency_zero": currency_zero,
            "presentation_currency_code": currency,
        }

        return templates.TemplateResponse(
            request, "expense/claims_dashboard.html", context
        )

    def _get_claims_stats(
        self, db: Session, org_id: UUID, start_date: date | None, currency: str
    ) -> dict[str, Any]:
        """Get claims-specific statistics."""
        today = date.today()
        month_start = today.replace(day=1)

        # Build base filter
        base_filter = [ExpenseClaim.organization_id == org_id]
        if start_date:
            base_filter.append(ExpenseClaim.claim_date >= start_date)

        # Total claims
        total_claims = (
            db.scalar(
                select(func.count(ExpenseClaim.claim_id)).where(and_(*base_filter))
            )
            or 0
        )

        # This period claims
        this_period_claims = total_claims

        # Total amount claimed
        total_amount = db.scalar(
            select(func.coalesce(func.sum(ExpenseClaim.total_claimed_amount), 0)).where(
                and_(*base_filter)
            )
        ) or Decimal(0)

        # Average claim
        avg_claim = total_amount / total_claims if total_claims > 0 else Decimal(0)

        # Pending review (SUBMITTED or PENDING_APPROVAL)
        pending_count = (
            db.scalar(
                select(func.count(ExpenseClaim.claim_id)).where(
                    and_(
                        *base_filter,
                        ExpenseClaim.status.in_(["SUBMITTED", "PENDING_APPROVAL"]),
                    )
                )
            )
            or 0
        )

        pending_amount = db.scalar(
            select(func.coalesce(func.sum(ExpenseClaim.total_claimed_amount), 0)).where(
                and_(
                    *base_filter,
                    ExpenseClaim.status.in_(["SUBMITTED", "PENDING_APPROVAL"]),
                )
            )
        ) or Decimal(0)

        # Approved count (waiting for payment)
        approved_count = (
            db.scalar(
                select(func.count(ExpenseClaim.claim_id)).where(
                    and_(
                        *base_filter,
                        ExpenseClaim.status == "APPROVED",
                    )
                )
            )
            or 0
        )

        # Paid
        paid_count = (
            db.scalar(
                select(func.count(ExpenseClaim.claim_id)).where(
                    and_(
                        *base_filter,
                        ExpenseClaim.status == "PAID",
                    )
                )
            )
            or 0
        )

        paid_amount = db.scalar(
            select(func.coalesce(func.sum(ExpenseClaim.total_claimed_amount), 0)).where(
                and_(
                    *base_filter,
                    ExpenseClaim.status == "PAID",
                )
            )
        ) or Decimal(0)

        # Rejected count
        rejected_count = (
            db.scalar(
                select(func.count(ExpenseClaim.claim_id)).where(
                    and_(
                        *base_filter,
                        ExpenseClaim.status == "REJECTED",
                    )
                )
            )
            or 0
        )

        # This month's claims
        this_month_count = (
            db.scalar(
                select(func.count(ExpenseClaim.claim_id)).where(
                    and_(
                        ExpenseClaim.organization_id == org_id,
                        ExpenseClaim.claim_date >= month_start,
                    )
                )
            )
            or 0
        )

        # Calculate rates
        completed_claims = paid_count + rejected_count
        approval_rate = (
            round((paid_count / completed_claims) * 100) if completed_claims > 0 else 0
        )
        rejection_rate = (
            round((rejected_count / completed_claims) * 100)
            if completed_claims > 0
            else 0
        )

        # Average processing time (simplified - would need actual date tracking)
        avg_processing_days = 3  # Placeholder

        return {
            "total_claims": total_claims,
            "this_period_claims": this_period_claims,
            "total_amount": _format_currency(total_amount, currency),
            "avg_claim": _format_currency(avg_claim, currency),
            "pending_count": pending_count,
            "pending_amount": _format_currency(pending_amount, currency),
            "approved_count": approved_count,
            "paid_count": paid_count,
            "paid_amount": _format_currency(paid_amount, currency),
            "rejected_count": rejected_count,
            "this_month_count": this_month_count,
            "approval_rate": approval_rate,
            "rejection_rate": rejection_rate,
            "avg_processing_days": avg_processing_days,
        }

    def _get_claims_chart_data(
        self, db: Session, org_id: UUID, start_date: date | None
    ) -> dict[str, Any]:
        """Get chart data for the claims dashboard."""
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
        """Get monthly claims trend (submitted vs paid counts)."""
        today = date.today()
        trend = []

        for i in range(5, -1, -1):
            month_date = today - timedelta(days=i * 30)
            month_name = month_date.strftime("%b")

            # Submitted count
            submitted = (
                db.scalar(
                    select(func.count(ExpenseClaim.claim_id)).where(
                        and_(
                            ExpenseClaim.organization_id == org_id,
                            extract("year", ExpenseClaim.claim_date) == month_date.year,
                            extract("month", ExpenseClaim.claim_date)
                            == month_date.month,
                        )
                    )
                )
                or 0
            )

            # Paid count
            paid = (
                db.scalar(
                    select(func.count(ExpenseClaim.claim_id)).where(
                        and_(
                            ExpenseClaim.organization_id == org_id,
                            ExpenseClaim.status == "PAID",
                            extract("year", ExpenseClaim.claim_date) == month_date.year,
                            extract("month", ExpenseClaim.claim_date)
                            == month_date.month,
                        )
                    )
                )
                or 0
            )

            trend.append(
                {
                    "month": month_name,
                    "submitted": submitted,
                    "paid": paid,
                }
            )

        return trend

    def _get_monthly_amounts(self, db: Session, org_id: UUID) -> list[dict[str, Any]]:
        """Get monthly claimed vs paid amounts."""
        today = date.today()
        amounts = []

        for i in range(5, -1, -1):
            month_date = today - timedelta(days=i * 30)
            month_name = month_date.strftime("%b")

            # Claimed amount
            claimed = db.scalar(
                select(
                    func.coalesce(func.sum(ExpenseClaim.total_claimed_amount), 0)
                ).where(
                    and_(
                        ExpenseClaim.organization_id == org_id,
                        extract("year", ExpenseClaim.claim_date) == month_date.year,
                        extract("month", ExpenseClaim.claim_date) == month_date.month,
                    )
                )
            ) or Decimal(0)

            # Paid amount
            paid = db.scalar(
                select(
                    func.coalesce(func.sum(ExpenseClaim.total_claimed_amount), 0)
                ).where(
                    and_(
                        ExpenseClaim.organization_id == org_id,
                        ExpenseClaim.status == "PAID",
                        extract("year", ExpenseClaim.claim_date) == month_date.year,
                        extract("month", ExpenseClaim.claim_date) == month_date.month,
                    )
                )
            ) or Decimal(0)

            amounts.append(
                {
                    "month": month_name,
                    "claimed": float(claimed),
                    "paid": float(paid),
                }
            )

        return amounts

    def _get_recent_claims_detailed(
        self, db: Session, org_id: UUID, limit: int = 8, currency: str = "NGN"
    ) -> list[dict[str, Any]]:
        """Get recent claims with more details for the claims dashboard."""
        results = db.execute(
            select(ExpenseClaim, Person)
            .join(Employee, Employee.employee_id == ExpenseClaim.employee_id)
            .join(Person, Person.id == Employee.person_id)
            .where(ExpenseClaim.organization_id == org_id)
            .order_by(ExpenseClaim.created_at.desc())
            .limit(limit)
        ).all()

        claims = []
        for claim, person in results:
            claims.append(
                {
                    "id": str(claim.claim_id),
                    "title": claim.purpose or "Expense Claim",
                    "claim_number": claim.claim_number or "",
                    "employee_name": f"{person.first_name or ''} {person.last_name or ''}".strip(),
                    "amount": _format_currency(claim.total_claimed_amount, currency),
                    "status": claim.status.value if claim.status else "DRAFT",
                    "date": claim.claim_date.strftime("%b %d, %Y")
                    if claim.claim_date
                    else "",
                }
            )

        return claims

    def _get_dashboard_stats(
        self, db: Session, org_id: UUID, start_date: date | None, currency: str
    ) -> dict[str, Any]:
        """Get aggregate statistics for the dashboard."""
        today = date.today()

        # Build base filter
        base_filter = [ExpenseClaim.organization_id == org_id]
        if start_date:
            base_filter.append(ExpenseClaim.claim_date >= start_date)

        # Total claims count
        total_claims = (
            db.scalar(
                select(func.count(ExpenseClaim.claim_id)).where(and_(*base_filter))
            )
            or 0
        )

        # Total amount
        total_amount = db.scalar(
            select(func.coalesce(func.sum(ExpenseClaim.total_claimed_amount), 0)).where(
                and_(*base_filter)
            )
        ) or Decimal(0)

        # Average claim amount
        avg_claim = total_amount / total_claims if total_claims > 0 else Decimal(0)

        # Pending approval (SUBMITTED or PENDING_APPROVAL status)
        pending_approval = (
            db.scalar(
                select(func.count(ExpenseClaim.claim_id)).where(
                    and_(
                        *base_filter,
                        ExpenseClaim.status.in_(["SUBMITTED", "PENDING_APPROVAL"]),
                    )
                )
            )
            or 0
        )

        pending_amount = db.scalar(
            select(func.coalesce(func.sum(ExpenseClaim.total_claimed_amount), 0)).where(
                and_(
                    *base_filter,
                    ExpenseClaim.status.in_(["SUBMITTED", "PENDING_APPROVAL"]),
                )
            )
        ) or Decimal(0)

        # Reimbursed (PAID status)
        reimbursed_count = (
            db.scalar(
                select(func.count(ExpenseClaim.claim_id)).where(
                    and_(
                        *base_filter,
                        ExpenseClaim.status == "PAID",
                    )
                )
            )
            or 0
        )

        reimbursed_amount = db.scalar(
            select(func.coalesce(func.sum(ExpenseClaim.total_claimed_amount), 0)).where(
                and_(
                    *base_filter,
                    ExpenseClaim.status == "PAID",
                )
            )
        ) or Decimal(0)

        # Claims to review (for managers)
        claims_to_review = (
            db.scalar(
                select(func.count(ExpenseClaim.claim_id)).where(
                    and_(
                        ExpenseClaim.organization_id == org_id,
                        ExpenseClaim.status.in_(["SUBMITTED", "PENDING_APPROVAL"]),
                    )
                )
            )
            or 0
        )

        # Advances stats
        active_advances = (
            db.scalar(
                select(func.count(CashAdvance.advance_id)).where(
                    and_(
                        CashAdvance.organization_id == org_id,
                        CashAdvance.status.in_(
                            ["PENDING_APPROVAL", "APPROVED", "DISBURSED"]
                        ),
                    )
                )
            )
            or 0
        )

        outstanding_advances = db.scalar(
            select(
                func.coalesce(
                    func.sum(CashAdvance.requested_amount - CashAdvance.amount_settled),
                    0,
                )
            ).where(
                and_(
                    CashAdvance.organization_id == org_id,
                    CashAdvance.status == "DISBURSED",
                )
            )
        ) or Decimal(0)

        advances_to_approve = (
            db.scalar(
                select(func.count(CashAdvance.advance_id)).where(
                    and_(
                        CashAdvance.organization_id == org_id,
                        CashAdvance.status.in_(["SUBMITTED", "PENDING_APPROVAL"]),
                    )
                )
            )
            or 0
        )

        # Ready for payment
        ready_for_payment = (
            db.scalar(
                select(func.count(ExpenseClaim.claim_id)).where(
                    and_(
                        ExpenseClaim.organization_id == org_id,
                        ExpenseClaim.status == "APPROVED",
                    )
                )
            )
            or 0
        )

        # Corporate card stats
        month_start = today.replace(day=1)
        card_spend_month = db.scalar(
            select(func.coalesce(func.sum(CardTransaction.amount), 0)).where(
                and_(
                    CardTransaction.organization_id == org_id,
                    CardTransaction.transaction_date >= month_start,
                )
            )
        ) or Decimal(0)

        card_transactions = (
            db.scalar(
                select(func.count(CardTransaction.transaction_id)).where(
                    and_(
                        CardTransaction.organization_id == org_id,
                        CardTransaction.transaction_date >= month_start,
                    )
                )
            )
            or 0
        )

        unreconciled_count = (
            db.scalar(
                select(func.count(CardTransaction.transaction_id)).where(
                    and_(
                        CardTransaction.organization_id == org_id,
                        CardTransaction.expense_claim_id.is_(None),
                    )
                )
            )
            or 0
        )

        unreconciled_amount = db.scalar(
            select(func.coalesce(func.sum(CardTransaction.amount), 0)).where(
                and_(
                    CardTransaction.organization_id == org_id,
                    CardTransaction.expense_claim_id.is_(None),
                )
            )
        ) or Decimal(0)

        # Compliance stats - count rejected claims as violations
        total_rejected = (
            db.scalar(
                select(func.count(ExpenseClaim.claim_id)).where(
                    and_(
                        *base_filter,
                        ExpenseClaim.status == "REJECTED",
                    )
                )
            )
            or 0
        )

        compliance_rate = (
            round(((total_claims - total_rejected) / total_claims) * 100)
            if total_claims > 0
            else 100
        )

        return {
            "total_claims": total_claims,
            "total_amount": _format_currency(total_amount, currency),
            "avg_claim_amount": _format_currency(avg_claim, currency),
            "pending_approval": pending_approval,
            "pending_amount": _format_currency(pending_amount, currency),
            "reimbursed_count": reimbursed_count,
            "reimbursed_amount": _format_currency(reimbursed_amount, currency),
            "claims_to_review": claims_to_review,
            "advances_to_approve": advances_to_approve,
            "ready_for_payment": ready_for_payment,
            "active_advances": active_advances,
            "outstanding_advances": _format_currency(outstanding_advances, currency),
            "settled_advances": _format_currency(Decimal(0), currency),  # Simplified
            "card_spend_month": _format_currency(card_spend_month, currency),
            "card_transactions": card_transactions,
            "unreconciled_count": unreconciled_count,
            "unreconciled_amount": _format_currency(unreconciled_amount, currency),
            "compliance_rate": compliance_rate,
            "policy_violations": total_rejected,
            "missing_receipts": 0,  # Would need receipt tracking
        }

    def _get_chart_data(
        self, db: Session, org_id: UUID, start_date: date | None
    ) -> dict[str, Any]:
        """Get chart data for the dashboard."""
        chart_data = {}

        # Expense trend (last 6 months)
        chart_data["expense_trend"] = self._get_expense_trend(db, org_id)

        # Category distribution
        chart_data["category_distribution"] = self._get_category_distribution(
            db, org_id, start_date
        )

        # Top spenders
        chart_data["top_spenders"] = self._get_top_spenders(db, org_id, start_date)

        # Status breakdown
        chart_data["status_breakdown"] = self._get_status_breakdown(
            db, org_id, start_date
        )

        # Department spending
        chart_data["department_spending"] = self._get_department_spending(
            db, org_id, start_date
        )

        return chart_data

    def _get_expense_trend(self, db: Session, org_id: UUID) -> list[dict[str, Any]]:
        """Get monthly expense totals for the last 6 months."""
        today = date.today()
        trend = []

        for i in range(5, -1, -1):
            month_date = today - timedelta(days=i * 30)
            month_name = month_date.strftime("%b")

            # Submitted amount
            submitted = db.scalar(
                select(
                    func.coalesce(func.sum(ExpenseClaim.total_claimed_amount), 0)
                ).where(
                    and_(
                        ExpenseClaim.organization_id == org_id,
                        extract("year", ExpenseClaim.claim_date) == month_date.year,
                        extract("month", ExpenseClaim.claim_date) == month_date.month,
                    )
                )
            ) or Decimal(0)

            # Reimbursed amount (approved/paid in that month based on updated_at)
            reimbursed = db.scalar(
                select(
                    func.coalesce(func.sum(ExpenseClaim.total_claimed_amount), 0)
                ).where(
                    and_(
                        ExpenseClaim.organization_id == org_id,
                        ExpenseClaim.status == "PAID",
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
        """Get expense amount by category."""
        base_filter = [
            ExpenseClaimItem.organization_id == org_id,
        ]
        if start_date:
            base_filter.append(ExpenseClaimItem.expense_date >= start_date)

        results = db.execute(
            select(
                ExpenseCategory.category_name,
                func.coalesce(func.sum(ExpenseClaimItem.claimed_amount), 0),
            )
            .join(
                ExpenseCategory,
                ExpenseCategory.category_id == ExpenseClaimItem.category_id,
            )
            .where(and_(*base_filter))
            .group_by(ExpenseCategory.category_name)
            .order_by(func.sum(ExpenseClaimItem.claimed_amount).desc())
            .limit(8)
        ).all()

        return [{"category": name, "amount": float(amount)} for name, amount in results]

    def _get_top_spenders(
        self, db: Session, org_id: UUID, start_date: date | None
    ) -> list[dict[str, Any]]:
        """Get top employees by expense amount."""
        base_filter = [ExpenseClaim.organization_id == org_id]
        if start_date:
            base_filter.append(ExpenseClaim.claim_date >= start_date)

        results = db.execute(
            select(
                Person.first_name,
                Person.last_name,
                func.coalesce(func.sum(ExpenseClaim.total_claimed_amount), 0),
            )
            .join(Employee, Employee.employee_id == ExpenseClaim.employee_id)
            .join(Person, Person.id == Employee.person_id)
            .where(and_(*base_filter))
            .group_by(Person.first_name, Person.last_name)
            .order_by(func.sum(ExpenseClaim.total_claimed_amount).desc())
            .limit(5)
        ).all()

        return [
            {"name": f"{first or ''} {last or ''}".strip(), "amount": float(amount)}
            for first, last, amount in results
        ]

    def _get_status_breakdown(
        self, db: Session, org_id: UUID, start_date: date | None
    ) -> list[dict[str, Any]]:
        """Get claim count by status."""
        base_filter = [ExpenseClaim.organization_id == org_id]
        if start_date:
            base_filter.append(ExpenseClaim.claim_date >= start_date)

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
        """Get expense amount by department."""
        base_filter = [ExpenseClaim.organization_id == org_id]
        if start_date:
            base_filter.append(ExpenseClaim.claim_date >= start_date)

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
        self, db: Session, org_id: UUID, limit: int = 5, currency: str = "NGN"
    ) -> list[dict[str, Any]]:
        """Get most recent expense claims."""
        results = db.execute(
            select(ExpenseClaim, Person)
            .join(Employee, Employee.employee_id == ExpenseClaim.employee_id)
            .join(Person, Person.id == Employee.person_id)
            .where(ExpenseClaim.organization_id == org_id)
            .order_by(ExpenseClaim.created_at.desc())
            .limit(limit)
        ).all()

        claims = []
        for claim, person in results:
            claims.append(
                {
                    "id": str(claim.claim_id),
                    "title": claim.purpose or f"Expense #{claim.claim_number}",
                    "employee_name": f"{person.first_name or ''} {person.last_name or ''}".strip(),
                    "amount": _format_currency(claim.total_claimed_amount, currency),
                    "status": claim.status.value if claim.status else "DRAFT",
                    "date": claim.claim_date.strftime("%b %d, %Y")
                    if claim.claim_date
                    else "",
                }
            )

        return claims


# Singleton instance
expense_dashboard_service = ExpenseDashboardService()
