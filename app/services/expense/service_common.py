"""Shared expense-service types, constants, and base helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.expense import (
    ExpenseClaim,
    ExpenseClaimAction,
    ExpenseClaimActionStatus,
    ExpenseClaimActionType,
    ExpenseClaimStatus,
)

if TYPE_CHECKING:
    from app.models.expense import ExpenseLimitRule
    from app.services.expense.limit_service import EligibleApprover, EvaluationResult
    from app.web.deps import WebAuthContext

__all__ = [
    "ApproverAuthorityError",
    "CLAIM_STATUS_TRANSITIONS",
    "CashAdvanceNotFoundError",
    "CardTransactionNotFoundError",
    "CorporateCardNotFoundError",
    "ExpenseCategoryNotFoundError",
    "ExpenseClaimNotFoundError",
    "ExpenseClaimStatusError",
    "ExpenseLimitBlockedError",
    "ExpenseServiceBase",
    "ExpenseServiceError",
    "REPORTABLE_EXPENSE_CLAIM_STATUSES",
    "STALE_ACTION_MINUTES",
    "SubmitClaimResult",
]

STALE_ACTION_MINUTES = 5


@dataclass
class SubmitClaimResult:
    """Result of submitting an expense claim."""

    claim: ExpenseClaim
    evaluation_result: EvaluationResult | None = None
    requires_approval: bool = False
    eligible_approvers: list[EligibleApprover] = field(default_factory=list)
    warning_message: str | None = None

    @property
    def success(self) -> bool:
        return self.claim.status in [
            ExpenseClaimStatus.SUBMITTED,
            ExpenseClaimStatus.PENDING_APPROVAL,
        ]

    @property
    def has_warning(self) -> bool:
        return bool(self.warning_message)


class ExpenseServiceError(Exception):
    """Base error for expense service."""


class ExpenseCategoryNotFoundError(ExpenseServiceError):
    def __init__(self, category_id: UUID):
        self.category_id = category_id
        super().__init__(f"Expense category {category_id} not found")


class ExpenseClaimNotFoundError(ExpenseServiceError):
    def __init__(self, claim_id: UUID):
        self.claim_id = claim_id
        super().__init__(f"Expense claim {claim_id} not found")


class CashAdvanceNotFoundError(ExpenseServiceError):
    def __init__(self, advance_id: UUID):
        self.advance_id = advance_id
        super().__init__(f"Cash advance {advance_id} not found")


class CorporateCardNotFoundError(ExpenseServiceError):
    def __init__(self, card_id: UUID):
        self.card_id = card_id
        super().__init__(f"Corporate card {card_id} not found")


class CardTransactionNotFoundError(ExpenseServiceError):
    def __init__(self, transaction_id: UUID):
        self.transaction_id = transaction_id
        super().__init__(f"Card transaction {transaction_id} not found")


class ExpenseClaimStatusError(ExpenseServiceError):
    def __init__(self, current: str, target: str):
        self.current = current
        self.target = target
        super().__init__(f"Cannot transition from {current} to {target}")


class ExpenseLimitBlockedError(ExpenseServiceError):
    def __init__(self, message: str, rule: ExpenseLimitRule | None = None):
        self.rule = rule
        super().__init__(message)


class ApproverAuthorityError(ExpenseServiceError):
    def __init__(self, claim_amount: Decimal, max_approval_amount: Decimal):
        self.claim_amount = claim_amount
        self.max_approval_amount = max_approval_amount
        super().__init__(
            f"Your approval limit ({max_approval_amount:,.2f}) is below "
            f"the claim amount ({claim_amount:,.2f}). "
            f"Please escalate to a higher authority."
        )


CLAIM_STATUS_TRANSITIONS = {
    ExpenseClaimStatus.DRAFT: {
        ExpenseClaimStatus.SUBMITTED,
        ExpenseClaimStatus.PENDING_APPROVAL,
        ExpenseClaimStatus.CANCELLED,
    },
    ExpenseClaimStatus.SUBMITTED: {
        ExpenseClaimStatus.PENDING_APPROVAL,
        ExpenseClaimStatus.APPROVED,
        ExpenseClaimStatus.REJECTED,
        ExpenseClaimStatus.CANCELLED,
    },
    ExpenseClaimStatus.PENDING_APPROVAL: {
        ExpenseClaimStatus.APPROVED,
        ExpenseClaimStatus.REJECTED,
        ExpenseClaimStatus.CANCELLED,
    },
    ExpenseClaimStatus.APPROVED: {
        ExpenseClaimStatus.PAID,
    },
    ExpenseClaimStatus.REJECTED: {
        ExpenseClaimStatus.DRAFT,
    },
    ExpenseClaimStatus.PAID: set(),
    ExpenseClaimStatus.CANCELLED: set(),
}

REPORTABLE_EXPENSE_CLAIM_STATUSES = (
    ExpenseClaimStatus.SUBMITTED,
    ExpenseClaimStatus.PENDING_APPROVAL,
    ExpenseClaimStatus.APPROVED,
    ExpenseClaimStatus.PAID,
)


class ExpenseServiceBase:
    """Base state and idempotency helpers shared across expense mixins."""

    db: Session
    ctx: WebAuthContext | None

    def __init__(self, db: Session, ctx: WebAuthContext | None = None) -> None:
        self.db = db
        self.ctx = ctx

    @staticmethod
    def _action_key(claim_id: UUID, action: ExpenseClaimActionType) -> str:
        return f"EXPENSE:{claim_id}:{action.value}:v1"

    def _begin_action(
        self,
        org_id: UUID,
        claim_id: UUID,
        action: ExpenseClaimActionType,
    ) -> bool:
        action_key = self._action_key(claim_id, action)
        stmt = (
            insert(ExpenseClaimAction)
            .values(
                organization_id=org_id,
                claim_id=claim_id,
                action_type=action,
                action_key=action_key,
                status=ExpenseClaimActionStatus.STARTED,
            )
            .on_conflict_do_nothing(
                index_elements=["organization_id", "claim_id", "action_type"],
            )
            .returning(ExpenseClaimAction.action_id)
        )
        result = self.db.execute(stmt)
        inserted_action_id = result.scalar_one_or_none()
        self.db.flush()
        if inserted_action_id is not None:
            return True

        existing = self.db.scalar(
            select(ExpenseClaimAction).where(
                ExpenseClaimAction.organization_id == org_id,
                ExpenseClaimAction.claim_id == claim_id,
                ExpenseClaimAction.action_type == action,
            )
        )
        if not existing:
            return False
        if existing.status == ExpenseClaimActionStatus.FAILED:
            existing.status = ExpenseClaimActionStatus.STARTED
            self.db.flush()
            return True
        if existing.status == ExpenseClaimActionStatus.STARTED and existing.created_at:
            age = datetime.now(UTC) - existing.created_at
            if age > timedelta(minutes=STALE_ACTION_MINUTES):
                self.db.flush()
                return True
        return False

    def _set_action_status(
        self,
        org_id: UUID,
        claim_id: UUID,
        action: ExpenseClaimActionType,
        status: ExpenseClaimActionStatus,
    ) -> None:
        record = self.db.scalar(
            select(ExpenseClaimAction).where(
                ExpenseClaimAction.organization_id == org_id,
                ExpenseClaimAction.claim_id == claim_id,
                ExpenseClaimAction.action_type == action,
            )
        )
        if record:
            record.status = status
            self.db.flush()

    def _reset_workflow_action_markers(self, org_id: UUID, claim_id: UUID) -> None:
        records = list(
            self.db.scalars(
                select(ExpenseClaimAction).where(
                    ExpenseClaimAction.organization_id == org_id,
                    ExpenseClaimAction.claim_id == claim_id,
                    ExpenseClaimAction.action_type.in_(
                        [
                            ExpenseClaimActionType.SUBMIT,
                            ExpenseClaimActionType.APPROVE,
                            ExpenseClaimActionType.REJECT,
                        ]
                    ),
                )
            ).all()
        )
        for record in records:
            record.status = ExpenseClaimActionStatus.FAILED
        if records:
            self.db.flush()

    def _next_claim_number(self, org_id: UUID) -> str:
        from app.models.finance.core_config.numbering_sequence import SequenceType
        from app.services.finance.common.numbering import SyncNumberingService

        return SyncNumberingService(self.db).generate_next_number(
            org_id, SequenceType.EXPENSE
        )
