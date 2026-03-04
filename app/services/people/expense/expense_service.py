"""Expense management service implementation.

.. deprecated::
    This module is superseded by ``app.services.expense.expense_service``.
    Import via ``app.services.people.expense.ExpenseService`` which now
    re-exports from the canonical module.  This file is kept only for
    reference and will be removed in a future cleanup.

Handles expense categories, claims, cash advances, and corporate cards.
Adapted from DotMac People for the unified ERP platform.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, joinedload

from app.models.people.exp import (
    CardTransaction,
    CardTransactionStatus,
    CashAdvance,
    CashAdvanceStatus,
    CorporateCard,
    ExpenseCategory,
    ExpenseClaim,
    ExpenseClaimAction,
    ExpenseClaimActionStatus,
    ExpenseClaimActionType,
    ExpenseClaimItem,
    ExpenseClaimStatus,
)
from app.services.common import PaginatedResult, PaginationParams

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.web.deps import WebAuthContext

__all__ = ["ExpenseService"]

STALE_ACTION_MINUTES = 5


class ExpenseServiceError(Exception):
    """Base error for expense service."""

    pass


class ExpenseCategoryNotFoundError(ExpenseServiceError):
    """Expense category not found."""

    def __init__(self, category_id: UUID):
        self.category_id = category_id
        super().__init__(f"Expense category {category_id} not found")


class ExpenseClaimNotFoundError(ExpenseServiceError):
    """Expense claim not found."""

    def __init__(self, claim_id: UUID):
        self.claim_id = claim_id
        super().__init__(f"Expense claim {claim_id} not found")


class CashAdvanceNotFoundError(ExpenseServiceError):
    """Cash advance not found."""

    def __init__(self, advance_id: UUID):
        self.advance_id = advance_id
        super().__init__(f"Cash advance {advance_id} not found")


class CorporateCardNotFoundError(ExpenseServiceError):
    """Corporate card not found."""

    def __init__(self, card_id: UUID):
        self.card_id = card_id
        super().__init__(f"Corporate card {card_id} not found")


class CardTransactionNotFoundError(ExpenseServiceError):
    """Card transaction not found."""

    def __init__(self, transaction_id: UUID):
        self.transaction_id = transaction_id
        super().__init__(f"Card transaction {transaction_id} not found")


class ExpenseClaimStatusError(ExpenseServiceError):
    """Invalid expense claim status transition."""

    def __init__(self, current: str, target: str):
        self.current = current
        self.target = target
        super().__init__(f"Cannot transition from {current} to {target}")


# Valid status transitions for expense claims
CLAIM_STATUS_TRANSITIONS = {
    ExpenseClaimStatus.DRAFT: {
        ExpenseClaimStatus.SUBMITTED,
        ExpenseClaimStatus.CANCELLED,
    },
    ExpenseClaimStatus.SUBMITTED: {
        ExpenseClaimStatus.APPROVED,
        ExpenseClaimStatus.REJECTED,
        ExpenseClaimStatus.CANCELLED,
    },
    ExpenseClaimStatus.APPROVED: {
        ExpenseClaimStatus.PAID,
    },
    ExpenseClaimStatus.REJECTED: {
        ExpenseClaimStatus.DRAFT,  # Allow resubmission after rejection
    },
    ExpenseClaimStatus.PAID: set(),  # Terminal state
    ExpenseClaimStatus.CANCELLED: set(),  # Terminal state
}


class ExpenseService:
    """Service for expense management operations.

    Handles:
    - Expense category configuration
    - Expense claim creation and approval workflow
    - Cash advance requests and settlement
    - Corporate card management and transaction tracking
    - Integration with AP for payment processing
    """

    def __init__(
        self,
        db: Session,
        ctx: WebAuthContext | None = None,
    ) -> None:
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
        )
        result = self.db.execute(stmt)
        self.db.flush()
        if (result.rowcount or 0) > 0:
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
        if existing.status == ExpenseClaimActionStatus.STARTED:
            if existing.created_at:
                age = datetime.now(UTC) - existing.created_at
                if age > timedelta(minutes=STALE_ACTION_MINUTES):
                    # Allow retry if previous action got stuck.
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

    def _has_blocking_payment_activity(self, org_id: UUID, claim_id: UUID) -> bool:
        """Return True when claim has active or completed payout activity."""
        from app.models.finance.payments.payment_intent import (
            PaymentIntent,
            PaymentIntentStatus,
        )

        blocking_statuses = (
            PaymentIntentStatus.PENDING,
            PaymentIntentStatus.PROCESSING,
            PaymentIntentStatus.COMPLETED,
        )
        return (
            self.db.scalar(
                select(PaymentIntent.intent_id).where(
                    PaymentIntent.organization_id == org_id,
                    PaymentIntent.source_type == "EXPENSE_CLAIM",
                    PaymentIntent.source_id == claim_id,
                    PaymentIntent.status.in_(blocking_statuses),
                )
            )
            is not None
        )

    def _ensure_editable_in_place(self, claim: ExpenseClaim) -> None:
        """Validate claim can be edited without reopening."""
        if claim.status == ExpenseClaimStatus.PAID:
            raise ExpenseClaimStatusError(claim.status.value, "update")
        if claim.status not in {ExpenseClaimStatus.DRAFT, ExpenseClaimStatus.APPROVED}:
            raise ExpenseClaimStatusError(claim.status.value, "update")
        if claim.status == ExpenseClaimStatus.APPROVED:
            # Approved claims linked to accounting/payment artifacts must not be edited
            # in place to avoid ledger/payout inconsistencies.
            if (
                getattr(claim, "journal_entry_id", None)
                or getattr(claim, "reimbursement_journal_id", None)
                or claim.supplier_invoice_id
                or claim.paid_on
                or claim.payment_reference
            ):
                raise ExpenseServiceError(
                    "Approved claim has accounting/payment records and cannot be edited"
                )
            if self._has_blocking_payment_activity(claim.organization_id, claim.claim_id):
                raise ExpenseServiceError(
                    "Approved claim has payout activity and cannot be edited"
                )

    @staticmethod
    def _recalculate_claim_totals(claim: ExpenseClaim) -> None:
        """Recalculate claim totals from item data."""
        claimed_total: Decimal = sum(
            ((item.claimed_amount or Decimal("0.00")) for item in claim.items),
            Decimal("0.00"),
        )
        claim.total_claimed_amount = claimed_total
        if claim.status == ExpenseClaimStatus.APPROVED:
            approved_total: Decimal = sum(
                (
                    (
                        item.approved_amount
                        if item.approved_amount is not None
                        else (item.claimed_amount or Decimal("0.00"))
                    )
                    for item in claim.items
                ),
                Decimal("0.00"),
            )
            claim.total_approved_amount = approved_total
            claim.net_payable_amount = approved_total - (
                claim.advance_adjusted or Decimal("0.00")
            )

    def reopen_claim_for_edit(self, org_id: UUID, claim_id: UUID) -> ExpenseClaim:
        """Reopen submitted/pending/rejected claim back to draft for editing."""
        claim = self.get_claim(org_id, claim_id)
        reopenable_statuses = {
            ExpenseClaimStatus.SUBMITTED,
            ExpenseClaimStatus.PENDING_APPROVAL,
            ExpenseClaimStatus.REJECTED,
        }
        if claim.status not in reopenable_statuses:
            raise ExpenseClaimStatusError(claim.status.value, ExpenseClaimStatus.DRAFT.value)

        claim.status = ExpenseClaimStatus.DRAFT
        claim.rejection_reason = None
        claim.approver_id = None
        claim.approved_on = None
        claim.total_approved_amount = None
        claim.net_payable_amount = None
        for item in claim.items:
            item.approved_amount = None

        # Clear stale action records so new submit/approve cycle can run.
        from sqlalchemy import delete

        self.db.execute(
            delete(ExpenseClaimAction).where(
                ExpenseClaimAction.organization_id == org_id,
                ExpenseClaimAction.claim_id == claim_id,
            )
        )
        self.db.flush()
        return claim

    def _next_claim_number(self, org_id: UUID) -> str:
        """Generate next expense claim number.

        Delegates to SyncNumberingService for race-condition-safe generation.
        """
        from app.models.finance.core_config.numbering_sequence import SequenceType
        from app.services.finance.common.numbering import SyncNumberingService

        return SyncNumberingService(self.db).generate_next_number(
            org_id, SequenceType.EXPENSE
        )

    # =========================================================================
    # Expense Categories
    # =========================================================================

    def list_categories(
        self,
        org_id: UUID,
        *,
        is_active: bool | None = None,
        search: str | None = None,
        parent_id: UUID | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[ExpenseCategory]:
        """List expense categories."""
        query = select(ExpenseCategory).where(ExpenseCategory.organization_id == org_id)

        if is_active is not None:
            query = query.where(ExpenseCategory.is_active == is_active)

        if search:
            search_term = f"%{search}%"
            query = query.where(
                or_(
                    ExpenseCategory.category_code.ilike(search_term),
                    ExpenseCategory.category_name.ilike(search_term),
                )
            )

        query = query.order_by(ExpenseCategory.category_name)

        # Count total
        count_query = select(func.count()).select_from(query.subquery())
        total = self.db.scalar(count_query) or 0

        # Apply pagination
        if pagination:
            query = query.offset(pagination.offset).limit(pagination.limit)

        items = list(self.db.scalars(query).all())

        return PaginatedResult(
            items=items,
            total=total,
            offset=pagination.offset if pagination else 0,
            limit=pagination.limit if pagination else len(items),
        )

    def get_category(self, org_id: UUID, category_id: UUID) -> ExpenseCategory:
        """Get an expense category by ID."""
        category = self.db.scalar(
            select(ExpenseCategory).where(
                ExpenseCategory.category_id == category_id,
                ExpenseCategory.organization_id == org_id,
            )
        )
        if not category:
            raise ExpenseCategoryNotFoundError(category_id)
        return category

    def create_category(
        self,
        org_id: UUID,
        *,
        category_code: str,
        category_name: str,
        parent_category_id: UUID | None = None,
        expense_account_id: UUID | None = None,
        max_amount: Decimal | None = None,
        max_amount_per_claim: Decimal | None = None,
        requires_receipt: bool = True,
        is_active: bool = True,
        description: str | None = None,
    ) -> ExpenseCategory:
        """Create a new expense category."""
        if max_amount_per_claim is None:
            max_amount_per_claim = max_amount

        category = ExpenseCategory(
            organization_id=org_id,
            category_code=category_code,
            category_name=category_name,
            expense_account_id=expense_account_id,
            max_amount_per_claim=max_amount_per_claim,
            requires_receipt=requires_receipt,
            is_active=is_active,
            description=description,
        )

        self.db.add(category)
        self.db.flush()
        return category

    def update_category(
        self,
        org_id: UUID,
        category_id: UUID,
        **kwargs,
    ) -> ExpenseCategory:
        """Update an expense category."""
        category = self.get_category(org_id, category_id)

        for key, value in kwargs.items():
            if value is not None and hasattr(category, key):
                setattr(category, key, value)

        self.db.flush()
        return category

    def delete_category(self, org_id: UUID, category_id: UUID) -> ExpenseCategory:
        """Deactivate an expense category."""
        category = self.get_category(org_id, category_id)
        category.is_active = False
        self.db.flush()
        return category

    # =========================================================================
    # Expense Claims
    # =========================================================================

    def list_claims(
        self,
        org_id: UUID,
        *,
        employee_id: UUID | None = None,
        status: ExpenseClaimStatus | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
        search: str | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[ExpenseClaim]:
        """List expense claims."""
        query = select(ExpenseClaim).where(ExpenseClaim.organization_id == org_id)

        if employee_id:
            query = query.where(ExpenseClaim.employee_id == employee_id)

        if status:
            query = query.where(ExpenseClaim.status == status)

        if from_date:
            query = query.where(ExpenseClaim.claim_date >= from_date)

        if to_date:
            query = query.where(ExpenseClaim.claim_date <= to_date)

        if search:
            search_term = f"%{search}%"
            query = query.where(
                or_(
                    ExpenseClaim.claim_number.ilike(search_term),
                    ExpenseClaim.purpose.ilike(search_term),
                )
            )

        query = query.options(joinedload(ExpenseClaim.items))
        query = query.order_by(ExpenseClaim.claim_date.desc())

        # Count total
        count_query = select(func.count()).select_from(query.subquery())
        total = self.db.scalar(count_query) or 0

        # Apply pagination
        if pagination:
            query = query.offset(pagination.offset).limit(pagination.limit)

        items = list(self.db.scalars(query).unique().all())

        return PaginatedResult(
            items=items,
            total=total,
            offset=pagination.offset if pagination else 0,
            limit=pagination.limit if pagination else len(items),
        )

    def get_claim(self, org_id: UUID, claim_id: UUID) -> ExpenseClaim:
        """Get an expense claim by ID."""
        claim = self.db.scalar(
            select(ExpenseClaim)
            .options(joinedload(ExpenseClaim.items))
            .where(
                ExpenseClaim.claim_id == claim_id,
                ExpenseClaim.organization_id == org_id,
            )
        )
        if not claim:
            raise ExpenseClaimNotFoundError(claim_id)
        return claim

    def create_claim(
        self,
        org_id: UUID,
        *,
        employee_id: UUID,
        claim_date: date,
        purpose: str,
        expense_period_start: date | None = None,
        expense_period_end: date | None = None,
        project_id: UUID | None = None,
        ticket_id: UUID | None = None,
        task_id: UUID | None = None,
        currency_code: str = "NGN",
        cost_center_id: UUID | None = None,
        recipient_bank_code: str | None = None,
        recipient_bank_name: str | None = None,
        recipient_account_number: str | None = None,
        recipient_name: str | None = None,
        requested_approver_id: UUID | None = None,
        notes: str | None = None,
        items: list[dict] | None = None,
    ) -> ExpenseClaim:
        """Create a new expense claim."""
        if not recipient_bank_code or not recipient_account_number:
            raise ExpenseServiceError("Bank details are required for expense claims")
        if not requested_approver_id:
            raise ExpenseServiceError("Expense approver is required for expense claims")

        # Generate claim number via DB sequence (concurrency-safe)
        claim_number = self._next_claim_number(org_id)

        claim = ExpenseClaim(
            organization_id=org_id,
            employee_id=employee_id,
            claim_number=claim_number,
            claim_date=claim_date,
            purpose=purpose,
            expense_period_start=expense_period_start,
            expense_period_end=expense_period_end,
            project_id=project_id,
            ticket_id=ticket_id,
            task_id=task_id,
            currency_code=currency_code,
            cost_center_id=cost_center_id,
            recipient_bank_code=recipient_bank_code,
            recipient_bank_name=recipient_bank_name,
            recipient_account_number=recipient_account_number,
            recipient_name=recipient_name,
            requested_approver_id=requested_approver_id,
            notes=notes,
            status=ExpenseClaimStatus.DRAFT,
            total_claimed_amount=Decimal("0"),
            advance_adjusted=Decimal("0"),
        )

        self.db.add(claim)
        self.db.flush()

        # Add items
        total_amount = Decimal("0")
        if items:
            for idx, item_data in enumerate(items):
                category = self.db.scalar(
                    select(ExpenseCategory).where(
                        ExpenseCategory.organization_id == org_id,
                        ExpenseCategory.category_id == item_data["category_id"],
                    )
                )
                if not category:
                    raise ExpenseCategoryNotFoundError(item_data["category_id"])
                if (
                    category.max_amount_per_claim is not None
                    and item_data["claimed_amount"] > category.max_amount_per_claim
                ):
                    raise ExpenseServiceError("Claimed amount exceeds category limit")
                item = ExpenseClaimItem(
                    organization_id=org_id,
                    claim_id=claim.claim_id,
                    expense_date=item_data["expense_date"],
                    category_id=item_data["category_id"],
                    description=item_data["description"],
                    claimed_amount=item_data["claimed_amount"],
                    expense_account_id=item_data.get("expense_account_id"),
                    cost_center_id=item_data.get("cost_center_id"),
                    receipt_url=item_data.get("receipt_url"),
                    receipt_number=item_data.get("receipt_number"),
                    vendor_name=item_data.get("vendor_name"),
                    is_travel_expense=item_data.get("is_travel_expense", False),
                    travel_from=item_data.get("travel_from"),
                    travel_to=item_data.get("travel_to"),
                    distance_km=item_data.get("distance_km"),
                    notes=item_data.get("notes"),
                    sequence=idx,
                )
                self.db.add(item)
                total_amount += item_data["claimed_amount"]

        claim.total_claimed_amount = total_amount
        self.db.flush()
        return claim

    def add_claim_item(
        self,
        org_id: UUID,
        claim_id: UUID,
        **item_data,
    ) -> ExpenseClaimItem:
        """Add an item to an expense claim."""
        claim = self.get_claim(org_id, claim_id)

        if claim.status != ExpenseClaimStatus.DRAFT:
            raise ExpenseClaimStatusError(claim.status.value, "add item")

        category = self.db.scalar(
            select(ExpenseCategory).where(
                ExpenseCategory.organization_id == org_id,
                ExpenseCategory.category_id == item_data["category_id"],
            )
        )
        if not category:
            raise ExpenseCategoryNotFoundError(item_data["category_id"])
        if (
            category.max_amount_per_claim is not None
            and item_data["claimed_amount"] > category.max_amount_per_claim
        ):
            raise ExpenseServiceError("Claimed amount exceeds category limit")

        # Get next sequence
        max_seq = self.db.scalar(
            select(func.max(ExpenseClaimItem.sequence)).where(
                ExpenseClaimItem.claim_id == claim_id
            )
        )
        next_seq = (max_seq or 0) + 1

        item = ExpenseClaimItem(
            organization_id=org_id,
            claim_id=claim_id,
            expense_date=item_data["expense_date"],
            category_id=item_data["category_id"],
            description=item_data["description"],
            claimed_amount=item_data["claimed_amount"],
            expense_account_id=item_data.get("expense_account_id"),
            cost_center_id=item_data.get("cost_center_id"),
            receipt_url=item_data.get("receipt_url"),
            receipt_number=item_data.get("receipt_number"),
            vendor_name=item_data.get("vendor_name"),
            is_travel_expense=item_data.get("is_travel_expense", False),
            travel_from=item_data.get("travel_from"),
            travel_to=item_data.get("travel_to"),
            distance_km=item_data.get("distance_km"),
            notes=item_data.get("notes"),
            sequence=next_seq,
        )

        self.db.add(item)

        # Update claim total
        claim.total_claimed_amount += item.claimed_amount

        self.db.flush()
        return item

    def submit_claim(self, org_id: UUID, claim_id: UUID) -> ExpenseClaim:
        """Submit an expense claim for approval."""
        claim = self.get_claim(org_id, claim_id)

        if claim.status in {
            ExpenseClaimStatus.SUBMITTED,
            ExpenseClaimStatus.APPROVED,
            ExpenseClaimStatus.PAID,
        }:
            return claim

        if claim.status != ExpenseClaimStatus.DRAFT:
            raise ExpenseClaimStatusError(
                claim.status.value, ExpenseClaimStatus.SUBMITTED.value
            )

        if not claim.items:
            raise ExpenseServiceError("Cannot submit claim with no items")

        action_started = self._begin_action(
            org_id, claim_id, ExpenseClaimActionType.SUBMIT
        )
        if not action_started:
            if claim.status != ExpenseClaimStatus.DRAFT:
                return claim
            existing = self.db.scalar(
                select(ExpenseClaimAction).where(
                    ExpenseClaimAction.organization_id == org_id,
                    ExpenseClaimAction.claim_id == claim_id,
                    ExpenseClaimAction.action_type == ExpenseClaimActionType.SUBMIT,
                )
            )
            if existing:
                existing.status = ExpenseClaimActionStatus.STARTED
                self.db.flush()

        try:
            claim.status = ExpenseClaimStatus.SUBMITTED
            self.db.flush()
            self._set_action_status(
                org_id,
                claim_id,
                ExpenseClaimActionType.SUBMIT,
                ExpenseClaimActionStatus.COMPLETED,
            )
            return claim
        except Exception:
            self._set_action_status(
                org_id,
                claim_id,
                ExpenseClaimActionType.SUBMIT,
                ExpenseClaimActionStatus.FAILED,
            )
            raise

    def approve_claim(
        self,
        org_id: UUID,
        claim_id: UUID,
        *,
        approver_id: UUID | None = None,
        approved_amounts: list[dict] | None = None,
        notes: str | None = None,
    ) -> ExpenseClaim:
        """Approve an expense claim."""
        claim = self.get_claim(org_id, claim_id)

        if claim.status in {ExpenseClaimStatus.APPROVED, ExpenseClaimStatus.PAID}:
            return claim

        if claim.status != ExpenseClaimStatus.SUBMITTED:
            raise ExpenseClaimStatusError(
                claim.status.value, ExpenseClaimStatus.APPROVED.value
            )

        if not self._begin_action(org_id, claim_id, ExpenseClaimActionType.APPROVE):
            return claim

        try:
            claim.status = ExpenseClaimStatus.APPROVED
            claim.approver_id = approver_id
            claim.approved_on = date.today()

            # Set approved amounts (default to claimed amounts)
            total_approved = Decimal("0")
            if approved_amounts:
                for approval in approved_amounts:
                    item = self.db.get(ExpenseClaimItem, approval["item_id"])
                    if item and item.claim_id == claim_id:
                        item.approved_amount = approval["approved_amount"]
                        total_approved += approval["approved_amount"]
            else:
                for item in claim.items:
                    item.approved_amount = item.claimed_amount
                    total_approved += item.claimed_amount

            claim.total_approved_amount = total_approved
            claim.net_payable_amount = total_approved - claim.advance_adjusted

            self.db.flush()
            self._set_action_status(
                org_id,
                claim_id,
                ExpenseClaimActionType.APPROVE,
                ExpenseClaimActionStatus.COMPLETED,
            )
            return claim
        except Exception:
            self._set_action_status(
                org_id,
                claim_id,
                ExpenseClaimActionType.APPROVE,
                ExpenseClaimActionStatus.FAILED,
            )
            raise

    def reject_claim(
        self,
        org_id: UUID,
        claim_id: UUID,
        *,
        approver_id: UUID | None = None,
        reason: str,
    ) -> ExpenseClaim:
        """Reject an expense claim."""
        claim = self.get_claim(org_id, claim_id)

        if claim.status == ExpenseClaimStatus.REJECTED:
            return claim

        if claim.status != ExpenseClaimStatus.SUBMITTED:
            raise ExpenseClaimStatusError(
                claim.status.value, ExpenseClaimStatus.REJECTED.value
            )

        if not self._begin_action(org_id, claim_id, ExpenseClaimActionType.REJECT):
            return claim

        try:
            claim.status = ExpenseClaimStatus.REJECTED
            claim.approver_id = approver_id
            claim.rejection_reason = reason

            self.db.flush()
            self._set_action_status(
                org_id,
                claim_id,
                ExpenseClaimActionType.REJECT,
                ExpenseClaimActionStatus.COMPLETED,
            )
            return claim
        except Exception:
            self._set_action_status(
                org_id,
                claim_id,
                ExpenseClaimActionType.REJECT,
                ExpenseClaimActionStatus.FAILED,
            )
            raise

    def update_claim(
        self,
        org_id: UUID,
        claim_id: UUID,
        **kwargs,
    ) -> ExpenseClaim:
        """Update an expense claim (allowed in DRAFT and APPROVED statuses)."""
        claim = self.get_claim(org_id, claim_id)
        self._ensure_editable_in_place(claim)

        for key, value in kwargs.items():
            if value is not None and hasattr(claim, key):
                setattr(claim, key, value)

        if not claim.recipient_bank_code or not claim.recipient_account_number:
            raise ExpenseServiceError("Bank details are required for expense claims")

        self.db.flush()
        return claim

    def update_claim_item(
        self,
        org_id: UUID,
        claim_id: UUID,
        item_id: UUID,
        **item_data,
    ) -> ExpenseClaimItem:
        """Update an item on an in-place editable expense claim."""
        claim = self.get_claim(org_id, claim_id)
        self._ensure_editable_in_place(claim)

        item = self.db.scalar(
            select(ExpenseClaimItem).where(
                ExpenseClaimItem.item_id == item_id,
                ExpenseClaimItem.claim_id == claim_id,
            )
        )
        if not item:
            raise ExpenseServiceError(f"Claim item {item_id} not found")

        category_id = item_data.get("category_id")
        claimed_amount = item_data.get("claimed_amount")
        if category_id:
            category = self.db.scalar(
                select(ExpenseCategory).where(
                    ExpenseCategory.organization_id == org_id,
                    ExpenseCategory.category_id == category_id,
                )
            )
            if not category:
                raise ExpenseCategoryNotFoundError(category_id)
            amount_to_check = (
                claimed_amount if claimed_amount is not None else item.claimed_amount
            )
            if (
                category.max_amount_per_claim is not None
                and amount_to_check > category.max_amount_per_claim
            ):
                raise ExpenseServiceError("Claimed amount exceeds category limit")

        old_amount = item.claimed_amount
        for key, value in item_data.items():
            if value is not None and hasattr(item, key):
                setattr(item, key, value)

        if claimed_amount is not None:
            claim.total_claimed_amount += item.claimed_amount - old_amount
            if claim.status == ExpenseClaimStatus.APPROVED:
                item.approved_amount = item.claimed_amount

        if claim.status == ExpenseClaimStatus.APPROVED:
            self._recalculate_claim_totals(claim)

        self.db.flush()
        return item

    def delete_claim(self, org_id: UUID, claim_id: UUID) -> None:
        """Delete an expense claim (only allowed in DRAFT status)."""
        claim = self.get_claim(org_id, claim_id)

        if claim.status != ExpenseClaimStatus.DRAFT:
            raise ExpenseClaimStatusError(claim.status.value, "delete")

        # Delete items first
        self.db.execute(
            select(ExpenseClaimItem).where(ExpenseClaimItem.claim_id == claim_id)
        )
        for item in claim.items:
            self.db.delete(item)

        self.db.delete(claim)
        self.db.flush()

    def remove_claim_item(
        self,
        org_id: UUID,
        claim_id: UUID,
        item_id: UUID,
    ) -> None:
        """Remove an item from an expense claim."""
        claim = self.get_claim(org_id, claim_id)
        self._ensure_editable_in_place(claim)

        item = self.db.scalar(
            select(ExpenseClaimItem).where(
                ExpenseClaimItem.item_id == item_id,
                ExpenseClaimItem.claim_id == claim_id,
            )
        )
        if not item:
            raise ExpenseServiceError(f"Claim item {item_id} not found")

        # Update claim total
        claim.total_claimed_amount -= item.claimed_amount

        self.db.delete(item)
        if claim.status == ExpenseClaimStatus.APPROVED:
            self._recalculate_claim_totals(claim)
        self.db.flush()

    def mark_paid(
        self,
        org_id: UUID,
        claim_id: UUID,
        *,
        payment_reference: str | None = None,
        payment_date: date | None = None,
    ) -> ExpenseClaim:
        """Mark an expense claim as paid."""
        claim = self.get_claim(org_id, claim_id)

        if claim.status == ExpenseClaimStatus.PAID:
            return claim

        if claim.status != ExpenseClaimStatus.APPROVED:
            raise ExpenseClaimStatusError(
                claim.status.value, ExpenseClaimStatus.PAID.value
            )

        if not self._begin_action(org_id, claim_id, ExpenseClaimActionType.MARK_PAID):
            return claim

        try:
            claim.status = ExpenseClaimStatus.PAID
            claim.paid_on = payment_date or date.today()
            claim.payment_reference = payment_reference

            self.db.flush()
            self._set_action_status(
                org_id,
                claim_id,
                ExpenseClaimActionType.MARK_PAID,
                ExpenseClaimActionStatus.COMPLETED,
            )
            return claim
        except Exception:
            self._set_action_status(
                org_id,
                claim_id,
                ExpenseClaimActionType.MARK_PAID,
                ExpenseClaimActionStatus.FAILED,
            )
            raise

    def cancel_claim(
        self,
        org_id: UUID,
        claim_id: UUID,
        *,
        reason: str | None = None,
    ) -> ExpenseClaim:
        """Cancel an expense claim (DRAFT or SUBMITTED only)."""
        claim = self.get_claim(org_id, claim_id)

        if claim.status == ExpenseClaimStatus.CANCELLED:
            return claim

        if claim.status not in {
            ExpenseClaimStatus.DRAFT,
            ExpenseClaimStatus.SUBMITTED,
        }:
            raise ExpenseClaimStatusError(
                claim.status.value, ExpenseClaimStatus.CANCELLED.value
            )

        if not self._begin_action(org_id, claim_id, ExpenseClaimActionType.CANCEL):
            return claim

        try:
            old_status = claim.status.value
            claim.status = ExpenseClaimStatus.CANCELLED
            if reason:
                claim.notes = (
                    f"{claim.notes}\n\nCancelled: {reason}"
                    if claim.notes
                    else f"Cancelled: {reason}"
                )
            self.db.flush()

            self._set_action_status(
                org_id,
                claim_id,
                ExpenseClaimActionType.CANCEL,
                ExpenseClaimActionStatus.COMPLETED,
            )
            logger.info("Cancelled expense claim %s (was %s)", claim_id, old_status)
            return claim
        except Exception:
            self._set_action_status(
                org_id,
                claim_id,
                ExpenseClaimActionType.CANCEL,
                ExpenseClaimActionStatus.FAILED,
            )
            raise

    def resubmit_claim(
        self,
        org_id: UUID,
        claim_id: UUID,
    ) -> ExpenseClaim:
        """Resubmit a previously rejected expense claim (resets to DRAFT)."""
        claim = self.get_claim(org_id, claim_id)

        if claim.status != ExpenseClaimStatus.REJECTED:
            raise ExpenseClaimStatusError(
                claim.status.value, ExpenseClaimStatus.DRAFT.value
            )

        claim.status = ExpenseClaimStatus.DRAFT
        claim.rejection_reason = None
        claim.approver_id = None
        claim.approved_on = None
        claim.total_approved_amount = None
        claim.net_payable_amount = None

        for item in claim.items:
            item.approved_amount = None

        # Clear stale action records so re-submission cycle works
        from sqlalchemy import delete

        self.db.execute(
            delete(ExpenseClaimAction).where(
                ExpenseClaimAction.organization_id == org_id,
                ExpenseClaimAction.claim_id == claim_id,
            )
        )

        self.db.flush()
        logger.info("Resubmit: reset claim %s to DRAFT", claim_id)
        return claim

    def link_advance(
        self,
        org_id: UUID,
        claim_id: UUID,
        advance_id: UUID,
        amount_to_adjust: Decimal,
    ) -> ExpenseClaim:
        """Link a cash advance to an expense claim."""
        claim = self.get_claim(org_id, claim_id)
        self.get_advance(org_id, advance_id)

        if claim.status not in {ExpenseClaimStatus.DRAFT, ExpenseClaimStatus.SUBMITTED}:
            raise ExpenseClaimStatusError(claim.status.value, "link advance")

        if not self._begin_action(
            org_id, claim_id, ExpenseClaimActionType.LINK_ADVANCE
        ):
            return claim

        try:
            claim.cash_advance_id = advance_id
            claim.advance_adjusted = amount_to_adjust

            if claim.total_approved_amount:
                claim.net_payable_amount = (
                    claim.total_approved_amount - amount_to_adjust
                )

            self.db.flush()
            self._set_action_status(
                org_id,
                claim_id,
                ExpenseClaimActionType.LINK_ADVANCE,
                ExpenseClaimActionStatus.COMPLETED,
            )
            return claim
        except Exception:
            self._set_action_status(
                org_id,
                claim_id,
                ExpenseClaimActionType.LINK_ADVANCE,
                ExpenseClaimActionStatus.FAILED,
            )
            raise

    # =========================================================================
    # Cash Advances
    # =========================================================================

    def list_advances(
        self,
        org_id: UUID,
        *,
        employee_id: UUID | None = None,
        status: CashAdvanceStatus | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[CashAdvance]:
        """List cash advances."""
        query = select(CashAdvance).where(CashAdvance.organization_id == org_id)

        if employee_id:
            query = query.where(CashAdvance.employee_id == employee_id)

        if status:
            query = query.where(CashAdvance.status == status)

        if from_date:
            query = query.where(CashAdvance.request_date >= from_date)

        if to_date:
            query = query.where(CashAdvance.request_date <= to_date)

        query = query.order_by(CashAdvance.request_date.desc())

        # Count total
        count_query = select(func.count()).select_from(query.subquery())
        total = self.db.scalar(count_query) or 0

        # Apply pagination
        if pagination:
            query = query.offset(pagination.offset).limit(pagination.limit)

        items = list(self.db.scalars(query).all())

        return PaginatedResult(
            items=items,
            total=total,
            offset=pagination.offset if pagination else 0,
            limit=pagination.limit if pagination else len(items),
        )

    def get_advance(self, org_id: UUID, advance_id: UUID) -> CashAdvance:
        """Get a cash advance by ID."""
        advance = self.db.scalar(
            select(CashAdvance).where(
                CashAdvance.advance_id == advance_id,
                CashAdvance.organization_id == org_id,
            )
        )
        if not advance:
            raise CashAdvanceNotFoundError(advance_id)
        return advance

    def create_advance(
        self,
        org_id: UUID,
        *,
        employee_id: UUID,
        request_date: date,
        purpose: str,
        requested_amount: Decimal,
        currency_code: str = "NGN",
        expected_settlement_date: date | None = None,
        cost_center_id: UUID | None = None,
        advance_account_id: UUID | None = None,
        notes: str | None = None,
    ) -> CashAdvance:
        """Create a new cash advance request."""
        # Generate advance number
        count = (
            self.db.scalar(
                select(func.count(CashAdvance.advance_id)).where(
                    CashAdvance.organization_id == org_id
                )
            )
            or 0
        )
        advance_number = f"ADV-{date.today().year}-{count + 1:05d}"

        advance = CashAdvance(
            organization_id=org_id,
            employee_id=employee_id,
            advance_number=advance_number,
            request_date=request_date,
            purpose=purpose,
            requested_amount=requested_amount,
            currency_code=currency_code,
            expected_settlement_date=expected_settlement_date,
            cost_center_id=cost_center_id,
            advance_account_id=advance_account_id,
            notes=notes,
            status=CashAdvanceStatus.DRAFT,
            amount_settled=Decimal("0"),
            amount_refunded=Decimal("0"),
        )

        self.db.add(advance)
        self.db.flush()
        return advance

    def update_advance(
        self,
        org_id: UUID,
        advance_id: UUID,
        **kwargs,
    ) -> CashAdvance:
        """Update a cash advance (only allowed in DRAFT status)."""
        advance = self.get_advance(org_id, advance_id)

        if advance.status != CashAdvanceStatus.DRAFT:
            raise ExpenseServiceError(
                f"Cannot update advance in {advance.status.value} status"
            )

        for key, value in kwargs.items():
            if value is not None and hasattr(advance, key):
                setattr(advance, key, value)

        self.db.flush()
        return advance

    def delete_advance(self, org_id: UUID, advance_id: UUID) -> None:
        """Delete a cash advance (only allowed in DRAFT status)."""
        advance = self.get_advance(org_id, advance_id)

        if advance.status != CashAdvanceStatus.DRAFT:
            raise ExpenseServiceError(
                f"Cannot delete advance in {advance.status.value} status"
            )

        self.db.delete(advance)
        self.db.flush()

    def submit_advance(self, org_id: UUID, advance_id: UUID) -> CashAdvance:
        """Submit a cash advance for approval."""
        advance = self.get_advance(org_id, advance_id)

        if advance.status != CashAdvanceStatus.DRAFT:
            raise ExpenseServiceError(
                f"Cannot submit advance in {advance.status.value} status"
            )

        advance.status = CashAdvanceStatus.SUBMITTED
        self.db.flush()
        return advance

    def approve_advance(
        self,
        org_id: UUID,
        advance_id: UUID,
        *,
        approver_id: UUID,
        approved_amount: Decimal | None = None,
    ) -> CashAdvance:
        """Approve a cash advance."""
        advance = self.get_advance(org_id, advance_id)

        if advance.status != CashAdvanceStatus.SUBMITTED:
            raise ExpenseServiceError(
                f"Cannot approve advance in {advance.status.value} status"
            )

        advance.status = CashAdvanceStatus.APPROVED
        advance.approver_id = approver_id
        advance.approved_on = date.today()
        advance.approved_amount = approved_amount or advance.requested_amount

        self.db.flush()
        return advance

    def reject_advance(
        self,
        org_id: UUID,
        advance_id: UUID,
        *,
        approver_id: UUID | None = None,
        reason: str,
    ) -> CashAdvance:
        """Reject a cash advance."""
        advance = self.get_advance(org_id, advance_id)

        if advance.status != CashAdvanceStatus.SUBMITTED:
            raise ExpenseServiceError(
                f"Cannot reject advance in {advance.status.value} status"
            )

        advance.status = CashAdvanceStatus.REJECTED
        advance.approver_id = approver_id
        advance.approved_on = date.today()
        advance.rejection_reason = reason

        self.db.flush()
        return advance

    def disburse_advance(
        self,
        org_id: UUID,
        advance_id: UUID,
        *,
        disbursed_amount: Decimal | None = None,
        disbursement_date: date | None = None,
        payment_reference: str | None = None,
    ) -> CashAdvance:
        """Mark a cash advance as disbursed."""
        advance = self.get_advance(org_id, advance_id)

        if advance.status != CashAdvanceStatus.APPROVED:
            raise ExpenseServiceError(
                f"Cannot disburse advance in {advance.status.value} status"
            )

        advance.status = CashAdvanceStatus.DISBURSED
        advance.disbursed_on = disbursement_date or date.today()
        advance.payment_reference = payment_reference

        self.db.flush()
        return advance

    def record_refund(
        self,
        org_id: UUID,
        advance_id: UUID,
        *,
        refund_amount: Decimal,
        payment_reference: str | None = None,
    ) -> CashAdvance:
        """Record a refund from employee for unused advance."""
        advance = self.get_advance(org_id, advance_id)

        if advance.status != CashAdvanceStatus.DISBURSED:
            raise ExpenseServiceError(
                f"Cannot record refund for advance in {advance.status.value} status"
            )

        advance.amount_refunded += refund_amount

        # Check if fully settled
        total_accounted = advance.amount_settled + advance.amount_refunded
        if total_accounted >= (advance.approved_amount or advance.requested_amount):
            advance.status = CashAdvanceStatus.FULLY_SETTLED
            advance.settled_on = date.today()

        self.db.flush()
        return advance

    def settle_advance(
        self,
        org_id: UUID,
        advance_id: UUID,
        *,
        settled_amount: Decimal,
        settlement_date: date | None = None,
        notes: str | None = None,
    ) -> CashAdvance:
        """Settle a cash advance (link to expense claim)."""
        advance = self.get_advance(org_id, advance_id)

        if advance.status != CashAdvanceStatus.DISBURSED:
            raise ExpenseServiceError(
                f"Cannot settle advance in {advance.status.value} status"
            )

        advance.amount_settled = settled_amount
        if notes:
            advance.notes = notes

        # Check if fully settled
        total_accounted = advance.amount_settled + advance.amount_refunded
        if total_accounted >= (advance.approved_amount or advance.requested_amount):
            advance.status = CashAdvanceStatus.FULLY_SETTLED
            advance.settled_on = settlement_date or date.today()

        self.db.flush()
        return advance

    # =========================================================================
    # Corporate Cards
    # =========================================================================

    def list_cards(
        self,
        org_id: UUID,
        *,
        employee_id: UUID | None = None,
        is_active: bool | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[CorporateCard]:
        """List corporate cards."""
        query = select(CorporateCard).where(CorporateCard.organization_id == org_id)

        if employee_id:
            query = query.where(CorporateCard.employee_id == employee_id)

        if is_active is not None:
            query = query.where(CorporateCard.is_active == is_active)

        query = query.order_by(CorporateCard.assigned_date.desc())

        # Count total
        count_query = select(func.count()).select_from(query.subquery())
        total = self.db.scalar(count_query) or 0

        # Apply pagination
        if pagination:
            query = query.offset(pagination.offset).limit(pagination.limit)

        items = list(self.db.scalars(query).all())

        return PaginatedResult(
            items=items,
            total=total,
            offset=pagination.offset if pagination else 0,
            limit=pagination.limit if pagination else len(items),
        )

    def get_card(self, org_id: UUID, card_id: UUID) -> CorporateCard:
        """Get a corporate card by ID."""
        card = self.db.scalar(
            select(CorporateCard).where(
                CorporateCard.card_id == card_id,
                CorporateCard.organization_id == org_id,
            )
        )
        if not card:
            raise CorporateCardNotFoundError(card_id)
        return card

    def create_card(
        self,
        org_id: UUID,
        *,
        card_number_last4: str,
        card_name: str,
        card_type: str,
        employee_id: UUID,
        assigned_date: date,
        issuer: str | None = None,
        expiry_date: date | None = None,
        credit_limit: Decimal | None = None,
        single_transaction_limit: Decimal | None = None,
        monthly_limit: Decimal | None = None,
        currency_code: str = "NGN",
        liability_account_id: UUID | None = None,
    ) -> CorporateCard:
        """Create a new corporate card."""
        card = CorporateCard(
            organization_id=org_id,
            card_number_last4=card_number_last4,
            card_name=card_name,
            card_type=card_type,
            employee_id=employee_id,
            assigned_date=assigned_date,
            issuer=issuer,
            expiry_date=expiry_date,
            credit_limit=credit_limit,
            single_transaction_limit=single_transaction_limit,
            monthly_limit=monthly_limit,
            currency_code=currency_code,
            liability_account_id=liability_account_id,
            is_active=True,
        )

        self.db.add(card)
        self.db.flush()
        return card

    def update_card(
        self,
        org_id: UUID,
        card_id: UUID,
        **kwargs,
    ) -> CorporateCard:
        """Update a corporate card."""
        card = self.get_card(org_id, card_id)

        for key, value in kwargs.items():
            if value is not None and hasattr(card, key):
                setattr(card, key, value)

        self.db.flush()
        return card

    def deactivate_card(
        self,
        org_id: UUID,
        card_id: UUID,
        *,
        reason: str | None = None,
    ) -> CorporateCard:
        """Deactivate a corporate card."""
        card = self.get_card(org_id, card_id)
        card.is_active = False
        card.deactivated_on = date.today()
        if reason:
            card.deactivation_reason = reason
        self.db.flush()
        return card

    # =========================================================================
    # Card Transactions
    # =========================================================================

    def list_transactions(
        self,
        org_id: UUID,
        *,
        card_id: UUID | None = None,
        employee_id: UUID | None = None,
        status: CardTransactionStatus | str | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
        unmatched_only: bool = False,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[CardTransaction]:
        """List card transactions."""
        query = select(CardTransaction).where(CardTransaction.organization_id == org_id)

        if card_id:
            query = query.where(CardTransaction.card_id == card_id)

        if employee_id:
            query = query.join(
                CorporateCard, CorporateCard.card_id == CardTransaction.card_id
            ).where(CorporateCard.employee_id == employee_id)

        if status:
            status_value: CardTransactionStatus | None = (
                status if isinstance(status, CardTransactionStatus) else None
            )
            if isinstance(status, str):
                try:
                    status_value = CardTransactionStatus(status)
                except ValueError:
                    status_value = None
            if status_value:
                query = query.where(CardTransaction.status == status_value)

        if from_date:
            query = query.where(CardTransaction.transaction_date >= from_date)

        if to_date:
            query = query.where(CardTransaction.transaction_date <= to_date)

        if unmatched_only:
            query = query.where(CardTransaction.expense_claim_id.is_(None))

        query = query.order_by(CardTransaction.transaction_date.desc())

        # Count total
        count_query = select(func.count()).select_from(query.subquery())
        total = self.db.scalar(count_query) or 0

        # Apply pagination
        if pagination:
            query = query.offset(pagination.offset).limit(pagination.limit)

        items = list(self.db.scalars(query).all())

        return PaginatedResult(
            items=items,
            total=total,
            offset=pagination.offset if pagination else 0,
            limit=pagination.limit if pagination else len(items),
        )

    def get_transaction(self, org_id: UUID, transaction_id: UUID) -> CardTransaction:
        """Get a card transaction by ID."""
        transaction = self.db.scalar(
            select(CardTransaction).where(
                CardTransaction.transaction_id == transaction_id,
                CardTransaction.organization_id == org_id,
            )
        )
        if not transaction:
            raise CardTransactionNotFoundError(transaction_id)
        return transaction

    def create_transaction(
        self,
        org_id: UUID,
        *,
        card_id: UUID,
        transaction_date: date,
        merchant_name: str,
        amount: Decimal,
        posting_date: date | None = None,
        merchant_category: str | None = None,
        currency_code: str = "NGN",
        original_currency: str | None = None,
        original_amount: Decimal | None = None,
        external_reference: str | None = None,
        description: str | None = None,
        notes: str | None = None,
    ) -> CardTransaction:
        """Create a new card transaction."""
        # Verify card exists
        self.get_card(org_id, card_id)

        transaction = CardTransaction(
            organization_id=org_id,
            card_id=card_id,
            transaction_date=transaction_date,
            posting_date=posting_date,
            merchant_name=merchant_name,
            merchant_category=merchant_category,
            amount=amount,
            currency_code=currency_code,
            original_currency=original_currency,
            original_amount=original_amount,
            external_reference=external_reference,
            description=description,
            notes=notes,
            status=CardTransactionStatus.PENDING,
            is_personal_expense=False,
            personal_deduction_from_salary=False,
        )

        self.db.add(transaction)
        self.db.flush()
        return transaction

    def update_transaction(
        self,
        org_id: UUID,
        transaction_id: UUID,
        **kwargs,
    ) -> CardTransaction:
        """Update a card transaction."""
        transaction = self.get_transaction(org_id, transaction_id)

        for key, value in kwargs.items():
            if value is not None and hasattr(transaction, key):
                setattr(transaction, key, value)

        self.db.flush()
        return transaction

    def match_transaction(
        self,
        org_id: UUID,
        transaction_id: UUID,
        *,
        expense_claim_id: UUID,
    ) -> CardTransaction:
        """Match a card transaction to an expense claim."""
        transaction = self.get_transaction(org_id, transaction_id)

        transaction.expense_claim_id = expense_claim_id
        transaction.matched_on = date.today()
        transaction.status = CardTransactionStatus.MATCHED

        self.db.flush()
        return transaction

    def mark_personal(
        self,
        org_id: UUID,
        transaction_id: UUID,
        *,
        deduct_from_salary: bool = False,
    ) -> CardTransaction:
        """Mark a transaction as personal expense."""
        transaction = self.get_transaction(org_id, transaction_id)

        transaction.is_personal_expense = True
        transaction.personal_deduction_from_salary = deduct_from_salary
        transaction.status = CardTransactionStatus.PERSONAL

        self.db.flush()
        return transaction

    # =========================================================================
    # Reporting
    # =========================================================================

    def get_expense_stats(self, org_id: UUID) -> dict:
        """Get expense statistics for dashboard."""
        today = date.today()
        month_start = today.replace(day=1)

        # Pending claims
        pending_claims = (
            self.db.scalar(
                select(func.count(ExpenseClaim.claim_id)).where(
                    ExpenseClaim.organization_id == org_id,
                    ExpenseClaim.status == ExpenseClaimStatus.SUBMITTED,
                )
            )
            or 0
        )

        # Total pending amount
        total_pending = self.db.scalar(
            select(func.sum(ExpenseClaim.total_claimed_amount)).where(
                ExpenseClaim.organization_id == org_id,
                ExpenseClaim.status == ExpenseClaimStatus.SUBMITTED,
            )
        ) or Decimal("0")

        # Claims this month
        claims_this_month = (
            self.db.scalar(
                select(func.count(ExpenseClaim.claim_id)).where(
                    ExpenseClaim.organization_id == org_id,
                    ExpenseClaim.claim_date >= month_start,
                )
            )
            or 0
        )

        # Amount this month
        amount_this_month = self.db.scalar(
            select(func.sum(ExpenseClaim.total_claimed_amount)).where(
                ExpenseClaim.organization_id == org_id,
                ExpenseClaim.claim_date >= month_start,
            )
        ) or Decimal("0")

        # Outstanding advances
        outstanding_advances = (
            self.db.scalar(
                select(func.count(CashAdvance.advance_id)).where(
                    CashAdvance.organization_id == org_id,
                    CashAdvance.status == CashAdvanceStatus.DISBURSED,
                )
            )
            or 0
        )

        # Outstanding advance amount
        advance_amount = self.db.scalar(
            select(
                func.sum(
                    CashAdvance.approved_amount
                    - CashAdvance.amount_settled
                    - CashAdvance.amount_refunded
                )
            ).where(
                CashAdvance.organization_id == org_id,
                CashAdvance.status == CashAdvanceStatus.DISBURSED,
            )
        ) or Decimal("0")

        return {
            "pending_claims": pending_claims,
            "total_pending_amount": total_pending,
            "claims_this_month": claims_this_month,
            "amount_this_month": amount_this_month,
            "outstanding_advances": outstanding_advances,
            "advance_outstanding_amount": advance_amount,
        }

    def get_employee_expense_summary(
        self,
        org_id: UUID,
        employee_id: UUID,
        *,
        year: int | None = None,
        month: int | None = None,
    ) -> dict:
        """Get expense summary for a specific employee."""
        today = date.today()
        target_year = year or today.year
        target_month = month or today.month

        # Date range for the period
        period_start = date(target_year, target_month, 1)
        if target_month == 12:
            period_end = date(target_year + 1, 1, 1)
        else:
            period_end = date(target_year, target_month + 1, 1)

        # Claims in period
        claims_in_period = (
            self.db.scalar(
                select(func.count(ExpenseClaim.claim_id)).where(
                    ExpenseClaim.organization_id == org_id,
                    ExpenseClaim.employee_id == employee_id,
                    ExpenseClaim.claim_date >= period_start,
                    ExpenseClaim.claim_date < period_end,
                )
            )
            or 0
        )

        # Total claimed in period
        total_claimed = self.db.scalar(
            select(func.sum(ExpenseClaim.total_claimed_amount)).where(
                ExpenseClaim.organization_id == org_id,
                ExpenseClaim.employee_id == employee_id,
                ExpenseClaim.claim_date >= period_start,
                ExpenseClaim.claim_date < period_end,
            )
        ) or Decimal("0")

        # Total approved in period
        total_approved = self.db.scalar(
            select(func.sum(ExpenseClaim.total_approved_amount)).where(
                ExpenseClaim.organization_id == org_id,
                ExpenseClaim.employee_id == employee_id,
                ExpenseClaim.status == ExpenseClaimStatus.APPROVED,
                ExpenseClaim.claim_date >= period_start,
                ExpenseClaim.claim_date < period_end,
            )
        ) or Decimal("0")

        # Pending claims
        pending_claims = (
            self.db.scalar(
                select(func.count(ExpenseClaim.claim_id)).where(
                    ExpenseClaim.organization_id == org_id,
                    ExpenseClaim.employee_id == employee_id,
                    ExpenseClaim.status == ExpenseClaimStatus.SUBMITTED,
                )
            )
            or 0
        )

        # Outstanding advances
        outstanding_advances = self.db.scalar(
            select(
                func.sum(
                    CashAdvance.approved_amount
                    - CashAdvance.amount_settled
                    - CashAdvance.amount_refunded
                )
            ).where(
                CashAdvance.organization_id == org_id,
                CashAdvance.employee_id == employee_id,
                CashAdvance.status == CashAdvanceStatus.DISBURSED,
            )
        ) or Decimal("0")

        return {
            "year": target_year,
            "month": target_month,
            "claims_in_period": claims_in_period,
            "total_claimed": total_claimed,
            "total_approved": total_approved,
            "pending_claims": pending_claims,
            "outstanding_advances": outstanding_advances,
        }
