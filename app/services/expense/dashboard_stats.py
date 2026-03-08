"""Expense dashboard KPI/stat computations."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.models.expense.cash_advance import CashAdvance, CashAdvanceStatus
from app.models.expense.corporate_card import CardTransaction
from app.models.expense.expense_claim import ExpenseClaim, ExpenseClaimStatus
from app.services.expense.dashboard_common import _format_currency


class ExpenseDashboardStatsMixin:
    def _get_claims_stats(
        self, db: Session, org_id: UUID, start_date: date | None, currency: str
    ) -> dict[str, Any]:
        today = date.today()
        month_start = today.replace(day=1)

        base_filter = [ExpenseClaim.organization_id == org_id]
        if start_date:
            base_filter.append(ExpenseClaim.claim_date >= start_date)

        total_claims = (
            db.scalar(
                select(func.count(ExpenseClaim.claim_id)).where(and_(*base_filter))
            )
            or 0
        )
        total_amount = db.scalar(
            select(func.coalesce(func.sum(ExpenseClaim.total_claimed_amount), 0)).where(
                and_(*base_filter)
            )
        ) or Decimal(0)
        avg_claim = total_amount / total_claims if total_claims > 0 else Decimal(0)

        pending_statuses = [
            ExpenseClaimStatus.SUBMITTED,
            ExpenseClaimStatus.PENDING_APPROVAL,
        ]
        pending_count = (
            db.scalar(
                select(func.count(ExpenseClaim.claim_id)).where(
                    and_(*base_filter, ExpenseClaim.status.in_(pending_statuses))
                )
            )
            or 0
        )
        pending_amount = db.scalar(
            select(func.coalesce(func.sum(ExpenseClaim.total_claimed_amount), 0)).where(
                and_(*base_filter, ExpenseClaim.status.in_(pending_statuses))
            )
        ) or Decimal(0)

        approved_count = (
            db.scalar(
                select(func.count(ExpenseClaim.claim_id)).where(
                    and_(
                        *base_filter, ExpenseClaim.status == ExpenseClaimStatus.APPROVED
                    )
                )
            )
            or 0
        )
        paid_count = (
            db.scalar(
                select(func.count(ExpenseClaim.claim_id)).where(
                    and_(*base_filter, ExpenseClaim.status == ExpenseClaimStatus.PAID)
                )
            )
            or 0
        )
        paid_amount = db.scalar(
            select(func.coalesce(func.sum(ExpenseClaim.total_claimed_amount), 0)).where(
                and_(*base_filter, ExpenseClaim.status == ExpenseClaimStatus.PAID)
            )
        ) or Decimal(0)
        rejected_count = (
            db.scalar(
                select(func.count(ExpenseClaim.claim_id)).where(
                    and_(
                        *base_filter, ExpenseClaim.status == ExpenseClaimStatus.REJECTED
                    )
                )
            )
            or 0
        )
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

        completed_claims = paid_count + rejected_count
        approval_rate = (
            round((paid_count / completed_claims) * 100) if completed_claims > 0 else 0
        )
        rejection_rate = (
            round((rejected_count / completed_claims) * 100)
            if completed_claims > 0
            else 0
        )

        return {
            "total_claims": total_claims,
            "this_period_claims": total_claims,
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
            "avg_processing_days": 3,
        }

    def _get_dashboard_stats(
        self, db: Session, org_id: UUID, start_date: date | None, currency: str
    ) -> dict[str, Any]:
        today = date.today()
        month_start = today.replace(day=1)

        base_filter = [ExpenseClaim.organization_id == org_id]
        if start_date:
            base_filter.append(ExpenseClaim.claim_date >= start_date)

        total_claims = (
            db.scalar(
                select(func.count(ExpenseClaim.claim_id)).where(and_(*base_filter))
            )
            or 0
        )
        total_amount = db.scalar(
            select(func.coalesce(func.sum(ExpenseClaim.total_claimed_amount), 0)).where(
                and_(*base_filter)
            )
        ) or Decimal(0)
        avg_claim = total_amount / total_claims if total_claims > 0 else Decimal(0)

        pending_statuses = [
            ExpenseClaimStatus.SUBMITTED,
            ExpenseClaimStatus.PENDING_APPROVAL,
        ]
        pending_approval = (
            db.scalar(
                select(func.count(ExpenseClaim.claim_id)).where(
                    and_(*base_filter, ExpenseClaim.status.in_(pending_statuses))
                )
            )
            or 0
        )
        pending_amount = db.scalar(
            select(func.coalesce(func.sum(ExpenseClaim.total_claimed_amount), 0)).where(
                and_(*base_filter, ExpenseClaim.status.in_(pending_statuses))
            )
        ) or Decimal(0)
        reimbursed_count = (
            db.scalar(
                select(func.count(ExpenseClaim.claim_id)).where(
                    and_(*base_filter, ExpenseClaim.status == ExpenseClaimStatus.PAID)
                )
            )
            or 0
        )
        reimbursed_amount = db.scalar(
            select(func.coalesce(func.sum(ExpenseClaim.total_claimed_amount), 0)).where(
                and_(*base_filter, ExpenseClaim.status == ExpenseClaimStatus.PAID)
            )
        ) or Decimal(0)
        claims_to_review = (
            db.scalar(
                select(func.count(ExpenseClaim.claim_id)).where(
                    and_(
                        ExpenseClaim.organization_id == org_id,
                        ExpenseClaim.status.in_(pending_statuses),
                    )
                )
            )
            or 0
        )

        active_advances = (
            db.scalar(
                select(func.count(CashAdvance.advance_id)).where(
                    and_(
                        CashAdvance.organization_id == org_id,
                        CashAdvance.status.in_(
                            [
                                CashAdvanceStatus.PENDING_APPROVAL,
                                CashAdvanceStatus.APPROVED,
                                CashAdvanceStatus.DISBURSED,
                            ]
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
                    CashAdvance.status == CashAdvanceStatus.DISBURSED,
                )
            )
        ) or Decimal(0)
        advances_to_approve = (
            db.scalar(
                select(func.count(CashAdvance.advance_id)).where(
                    and_(
                        CashAdvance.organization_id == org_id,
                        CashAdvance.status.in_(
                            [
                                CashAdvanceStatus.SUBMITTED,
                                CashAdvanceStatus.PENDING_APPROVAL,
                            ]
                        ),
                    )
                )
            )
            or 0
        )
        ready_for_payment = (
            db.scalar(
                select(func.count(ExpenseClaim.claim_id)).where(
                    and_(
                        ExpenseClaim.organization_id == org_id,
                        ExpenseClaim.status == ExpenseClaimStatus.APPROVED,
                    )
                )
            )
            or 0
        )
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
        total_rejected = (
            db.scalar(
                select(func.count(ExpenseClaim.claim_id)).where(
                    and_(
                        *base_filter, ExpenseClaim.status == ExpenseClaimStatus.REJECTED
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
            "settled_advances": _format_currency(Decimal(0), currency),
            "card_spend_month": _format_currency(card_spend_month, currency),
            "card_transactions": card_transactions,
            "unreconciled_count": unreconciled_count,
            "unreconciled_amount": _format_currency(unreconciled_amount, currency),
            "compliance_rate": compliance_rate,
            "policy_violations": total_rejected,
            "missing_receipts": 0,
        }
