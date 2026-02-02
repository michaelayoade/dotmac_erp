"""Expense management service implementation.

Handles expense categories, claims, cash advances, and corporate cards.
Independent module with HR integration for employee tracking.
Includes expense limit enforcement on claim submission.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any, List, Optional
from uuid import UUID

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, joinedload

from app.models.expense import (
    CardTransaction,
    CardTransactionStatus,
    CashAdvance,
    CashAdvanceStatus,
    CorporateCard,
    ExpenseCategory,
    ExpenseClaim,
    ExpenseClaimItem,
    ExpenseClaimStatus,
    ExpenseClaimAction,
    ExpenseClaimActionType,
    ExpenseClaimActionStatus,
)
from app.services.common import PaginatedResult, PaginationParams

if TYPE_CHECKING:
    from app.models.expense import ExpenseLimitRule
    from app.services.expense.limit_service import EligibleApprover, EvaluationResult
    from app.web.deps import WebAuthContext

__all__ = ["ExpenseService", "SubmitClaimResult"]

STALE_ACTION_MINUTES = 5


@dataclass
class SubmitClaimResult:
    """Result of submitting an expense claim.

    Contains the claim and optional limit evaluation details.
    """

    claim: ExpenseClaim
    evaluation_result: Optional["EvaluationResult"] = None
    requires_approval: bool = False
    eligible_approvers: List["EligibleApprover"] = field(default_factory=list)
    warning_message: Optional[str] = None

    @property
    def success(self) -> bool:
        """Check if submission was successful (not blocked)."""
        return self.claim.status in [
            ExpenseClaimStatus.SUBMITTED,
        ]

    @property
    def has_warning(self) -> bool:
        """Check if there's a warning message."""
        return bool(self.warning_message)


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


class ExpenseLimitBlockedError(ExpenseServiceError):
    """Expense limit exceeded and blocks submission."""

    def __init__(self, message: str, rule: Optional["ExpenseLimitRule"] = None):
        self.rule = rule
        super().__init__(message)


# Valid status transitions for expense claims
CLAIM_STATUS_TRANSITIONS = {
    ExpenseClaimStatus.DRAFT: {
        ExpenseClaimStatus.SUBMITTED,
    },
    ExpenseClaimStatus.SUBMITTED: {
        ExpenseClaimStatus.APPROVED,
        ExpenseClaimStatus.REJECTED,
    },
    ExpenseClaimStatus.APPROVED: {
        ExpenseClaimStatus.PAID,
    },
    ExpenseClaimStatus.REJECTED: set(),  # Terminal state
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
        ctx: Optional["WebAuthContext"] = None,
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
        if existing.status == ExpenseClaimActionStatus.STARTED:
            if existing.created_at:
                age = datetime.now(timezone.utc) - existing.created_at
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
        status: "ExpenseClaimActionStatus",
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

    def _next_claim_number(self) -> str:
        if self.db.bind and self.db.bind.dialect.name == "postgresql":
            seq = self.db.scalar(text("select nextval('expense.expense_claim_number_seq')"))
            return f"EXP-{date.today().year}-{int(seq):05d}"
        count = self.db.scalar(
            select(func.count(ExpenseClaim.claim_id))
        ) or 0
        return f"EXP-{date.today().year}-{count + 1:05d}"

    # =========================================================================
    # Expense Categories
    # =========================================================================

    def list_categories(
        self,
        org_id: UUID,
        *,
        is_active: Optional[bool] = None,
        search: Optional[str] = None,
        pagination: Optional[PaginationParams] = None,
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
        expense_account_id: Optional[UUID] = None,
        max_amount_per_claim: Optional[Decimal] = None,
        requires_receipt: bool = True,
        is_active: bool = True,
        description: Optional[str] = None,
    ) -> ExpenseCategory:
        """Create a new expense category."""
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
        employee_id: Optional[UUID] = None,
        status: Optional[ExpenseClaimStatus] = None,
        from_date: Optional[date] = None,
        to_date: Optional[date] = None,
        search: Optional[str] = None,
        pagination: Optional[PaginationParams] = None,
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
        employee_id: Optional[UUID] = None,
        claim_date: date,
        purpose: str,
        expense_period_start: Optional[date] = None,
        expense_period_end: Optional[date] = None,
        project_id: Optional[UUID] = None,
        task_id: Optional[UUID] = None,
        currency_code: str = "NGN",
        cost_center_id: Optional[UUID] = None,
        notes: Optional[str] = None,
        items: Optional[List[dict]] = None,
    ) -> ExpenseClaim:
        """Create a new expense claim."""
        # Generate claim number via DB sequence (concurrency-safe)
        claim_number = self._next_claim_number()

        claim = ExpenseClaim(
            organization_id=org_id,
            employee_id=employee_id,
            claim_number=claim_number,
            claim_date=claim_date,
            purpose=purpose,
            expense_period_start=expense_period_start,
            expense_period_end=expense_period_end,
            project_id=project_id,
            task_id=task_id,
            currency_code=currency_code,
            cost_center_id=cost_center_id,
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
                    raise ExpenseServiceError(
                        "Claimed amount exceeds category limit"
                    )
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

    def submit_claim(
        self,
        org_id: UUID,
        claim_id: UUID,
        *,
        skip_limit_check: bool = False,
        skip_receipt_validation: bool = False,
        notify_approvers: bool = True,
    ) -> "SubmitClaimResult":
        """
        Submit an expense claim for approval.

        Evaluates expense limits and receipt requirements before submission:
        - Validates receipts are attached for categories that require them
        - Block submission if limits are exceeded with BLOCK action
        - Route to SUBMITTED if approval is required
        - Allow submission with WARNING if limits are soft

        Args:
            org_id: Organization ID
            claim_id: Claim to submit
            skip_limit_check: If True, skip limit evaluation (admin override)
            skip_receipt_validation: If True, skip receipt requirement check
            notify_approvers: If True, send notification to eligible approvers

        Returns:
            SubmitClaimResult with claim and evaluation details
        """
        from app.services.expense.limit_service import ExpenseLimitService
        from app.services.expense.approval_service import ExpenseApprovalService
        from app.models.expense import LimitResultType

        claim = self.get_claim(org_id, claim_id)

        if claim.status in {
            ExpenseClaimStatus.SUBMITTED,
            ExpenseClaimStatus.APPROVED,
            ExpenseClaimStatus.PAID,
        }:
            return SubmitClaimResult(claim=claim)

        if claim.status != ExpenseClaimStatus.DRAFT:
            raise ExpenseClaimStatusError(
                claim.status.value, ExpenseClaimStatus.SUBMITTED.value
            )

        if not claim.items:
            raise ExpenseServiceError("Cannot submit claim with no items")

        action_started = self._begin_action(org_id, claim_id, ExpenseClaimActionType.SUBMIT)
        if not action_started:
            return SubmitClaimResult(claim=claim)

        try:
            # Validate receipt requirements
            receipt_warnings = []
            if not skip_receipt_validation:
                approval_service = ExpenseApprovalService(self.db, self.ctx)
                validation_result = approval_service.validate_receipt_requirements(claim)

                if not validation_result.is_valid:
                    # Block submission if required receipts are missing
                    raise ExpenseServiceError(
                        f"Missing required receipts: {'; '.join(validation_result.missing_receipts)}"
                    )

                # Collect warnings (will be included in result)
                receipt_warnings = validation_result.warnings

            # Evaluate limits (unless skipped)
            evaluation_result = None
            if not skip_limit_check:
                limit_service = ExpenseLimitService(self.db, self.ctx)
                evaluation_result = limit_service.evaluate_claim(claim)

                # Handle based on result
                if evaluation_result.result == LimitResultType.BLOCKED:
                    raise ExpenseLimitBlockedError(
                        evaluation_result.message,
                        evaluation_result.triggered_rule,
                    )

                elif evaluation_result.result in [
                    LimitResultType.APPROVAL_REQUIRED,
                    LimitResultType.MULTI_APPROVAL_REQUIRED,
                    LimitResultType.ESCALATED,
                ]:
                    # Strict state machine: DRAFT -> SUBMITTED
                    claim.status = ExpenseClaimStatus.SUBMITTED
                    self.db.flush()

                    # Send notifications to eligible approvers
                    if notify_approvers and evaluation_result.eligible_approvers:
                        self._notify_approvers(claim, evaluation_result.eligible_approvers)

                    # Combine warnings
                    warning_msg = None
                    if receipt_warnings:
                        warning_msg = "; ".join(receipt_warnings)

                    self._set_action_status(
                        org_id,
                        claim_id,
                        ExpenseClaimActionType.SUBMIT,
                        ExpenseClaimActionStatus.COMPLETED,
                    )
                    return SubmitClaimResult(
                        claim=claim,
                        evaluation_result=evaluation_result,
                        requires_approval=True,
                        eligible_approvers=evaluation_result.eligible_approvers,
                        warning_message=warning_msg,
                    )

                elif evaluation_result.result == LimitResultType.WARNING:
                    # Allow submission but include warning
                    claim.status = ExpenseClaimStatus.SUBMITTED
                    self.db.flush()

                    # Combine limit warning with receipt warnings
                    all_warnings = [evaluation_result.message] + receipt_warnings
                    self._set_action_status(
                        org_id,
                        claim_id,
                        ExpenseClaimActionType.SUBMIT,
                        ExpenseClaimActionStatus.COMPLETED,
                    )
                    return SubmitClaimResult(
                        claim=claim,
                        evaluation_result=evaluation_result,
                        warning_message="; ".join(all_warnings),
                    )

            # Normal submission (passed or skipped)
            claim.status = ExpenseClaimStatus.SUBMITTED
            self.db.flush()

            # Include any receipt warnings
            warning_msg = "; ".join(receipt_warnings) if receipt_warnings else None

            self._set_action_status(
                org_id,
                claim_id,
                ExpenseClaimActionType.SUBMIT,
                ExpenseClaimActionStatus.COMPLETED,
            )
            return SubmitClaimResult(
                claim=claim,
                evaluation_result=evaluation_result,
                warning_message=warning_msg,
            )
        except Exception:
            self._set_action_status(
                org_id,
                claim_id,
                ExpenseClaimActionType.SUBMIT,
                ExpenseClaimActionStatus.FAILED,
            )
            raise

    def _notify_approvers(self, claim: ExpenseClaim, approvers: List["EligibleApprover"]) -> None:
        """Send approval request notifications to eligible approvers."""
        from app.services.expense.expense_notifications import ExpenseNotificationService
        from app.models.people.hr.employee import Employee

        notification_service = ExpenseNotificationService(self.db)

        for approver_info in approvers[:3]:  # Limit to top 3 approvers
            approver = self.db.get(Employee, approver_info.employee_id)
            if approver:
                notification_service.notify_approval_needed(claim, approver)

    def approve_claim(
        self,
        org_id: UUID,
        claim_id: UUID,
        *,
        approver_id: Optional[UUID] = None,
        approved_amounts: Optional[List[dict]] = None,
        notes: Optional[str] = None,
        auto_post_gl: bool = False,
        create_supplier_invoice: bool = False,
        send_notification: bool = True,
    ) -> ExpenseClaim:
        """
        Approve an expense claim.

        Args:
            org_id: Organization ID
            claim_id: Claim to approve
            approver_id: ID of the approver
            approved_amounts: Optional per-item approved amounts
            notes: Approval notes
            auto_post_gl: If True, post to GL immediately after approval
            create_supplier_invoice: If True, create AP invoice for payment
            send_notification: If True, send approval notification email

        Returns:
            The approved expense claim
        """
        claim = self.get_claim(org_id, claim_id)

        if claim.status in {ExpenseClaimStatus.APPROVED, ExpenseClaimStatus.PAID}:
            return claim

        # Allow approval only from SUBMITTED status
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

            # Post to GL if requested
            if auto_post_gl and approver_id:
                from app.services.expense.expense_posting_adapter import ExpensePostingAdapter

                posting_result = ExpensePostingAdapter.post_expense_claim(
                    self.db,
                    org_id,
                    claim_id,
                    date.today(),
                    approver_id,
                    auto_post=True,
                )

                if not posting_result.success:
                    # Log but don't fail the approval
                    import logging
                    logger = logging.getLogger(__name__)
                    logger.warning(
                        "GL posting failed for claim %s: %s",
                        claim_id,
                        posting_result.message,
                    )

            # Create supplier invoice if requested
            if create_supplier_invoice and approver_id:
                from app.services.expense.expense_posting_adapter import ExpensePostingAdapter

                invoice_result = ExpensePostingAdapter.create_supplier_invoice_from_expense(
                    self.db,
                    org_id,
                    claim_id,
                    approver_id,
                )

                if not invoice_result.success:
                    import logging
                    logger = logging.getLogger(__name__)
                    logger.warning(
                        "Supplier invoice creation failed for claim %s: %s",
                        claim_id,
                        invoice_result.message,
                    )

            # Send notification if requested
            if send_notification and claim.employee and claim.employee.work_email:
                from app.services.expense.expense_notifications import ExpenseNotificationService

                notification_service = ExpenseNotificationService(self.db)
                approver_name = None
                if approver_id:
                    from app.models.people.hr.employee import Employee
                    approver = self.db.get(Employee, approver_id)
                    if approver:
                        approver_name = f"{approver.first_name} {approver.last_name}"

                notification_service.notify_claim_approved(claim, approver_name=approver_name)

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
        approver_id: Optional[UUID] = None,
        reason: str,
        send_notification: bool = True,
    ) -> ExpenseClaim:
        """
        Reject an expense claim.

        Args:
            org_id: Organization ID
            claim_id: Claim to reject
            approver_id: ID of the approver rejecting
            reason: Reason for rejection
            send_notification: If True, send rejection notification email

        Returns:
            The rejected expense claim
        """
        claim = self.get_claim(org_id, claim_id)

        if claim.status == ExpenseClaimStatus.REJECTED:
            return claim

        # Allow rejection only from SUBMITTED status
        if claim.status != ExpenseClaimStatus.SUBMITTED:
            raise ExpenseClaimStatusError(
                claim.status.value, ExpenseClaimStatus.REJECTED.value
            )

        if not self._begin_action(org_id, claim_id, ExpenseClaimActionType.REJECT):
            return claim

        try:
            claim.status = ExpenseClaimStatus.REJECTED
            claim.approver_id = approver_id
            claim.approved_on = date.today()
            claim.rejection_reason = reason

            # Send notification if requested
            if send_notification and claim.employee and claim.employee.work_email:
                from app.services.expense.expense_notifications import ExpenseNotificationService

                notification_service = ExpenseNotificationService(self.db)
                approver_name = None
                if approver_id:
                    from app.models.people.hr.employee import Employee
                    approver = self.db.get(Employee, approver_id)
                    if approver:
                        approver_name = f"{approver.first_name} {approver.last_name}"

                notification_service.notify_claim_rejected(
                    claim,
                    reason,
                    approver_name=approver_name,
                )

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
        """Update an expense claim (only allowed in DRAFT status)."""
        claim = self.get_claim(org_id, claim_id)

        if claim.status != ExpenseClaimStatus.DRAFT:
            raise ExpenseClaimStatusError(claim.status.value, "update")

        for key, value in kwargs.items():
            if value is not None and hasattr(claim, key):
                setattr(claim, key, value)

        self.db.flush()
        return claim

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

        if claim.status != ExpenseClaimStatus.DRAFT:
            raise ExpenseClaimStatusError(claim.status.value, "remove item")

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
        self.db.flush()

    def mark_paid(
        self,
        org_id: UUID,
        claim_id: UUID,
        *,
        payment_reference: Optional[str] = None,
        payment_date: Optional[date] = None,
        send_notification: bool = True,
    ) -> ExpenseClaim:
        """
        Mark an expense claim as paid.

        Args:
            org_id: Organization ID
            claim_id: Claim to mark paid
            payment_reference: Payment reference number
            payment_date: Date of payment (defaults to today)
            send_notification: If True, send payment notification email

        Returns:
            The paid expense claim
        """
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

            # Send payment notification
            if send_notification and claim.employee and claim.employee.work_email:
                from app.services.expense.expense_notifications import ExpenseNotificationService

                notification_service = ExpenseNotificationService(self.db)
                notification_service.notify_claim_paid(
                    claim,
                    payment_reference=payment_reference,
                    payment_date=claim.paid_on,
                )

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

    def link_advance(
        self,
        org_id: UUID,
        claim_id: UUID,
        advance_id: UUID,
        amount_to_adjust: Decimal,
    ) -> ExpenseClaim:
        """Link a cash advance to an expense claim."""
        claim = self.get_claim(org_id, claim_id)
        advance = self.get_advance(org_id, advance_id)

        if claim.status not in {ExpenseClaimStatus.DRAFT, ExpenseClaimStatus.SUBMITTED}:
            raise ExpenseClaimStatusError(claim.status.value, "link advance")

        if not self._begin_action(org_id, claim_id, ExpenseClaimActionType.LINK_ADVANCE):
            return claim

        try:
            claim.cash_advance_id = advance_id
            claim.advance_adjusted = amount_to_adjust

            if claim.total_approved_amount:
                claim.net_payable_amount = claim.total_approved_amount - amount_to_adjust

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
        employee_id: Optional[UUID] = None,
        status: Optional[CashAdvanceStatus] = None,
        from_date: Optional[date] = None,
        to_date: Optional[date] = None,
        pagination: Optional[PaginationParams] = None,
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
        employee_id: Optional[UUID] = None,
        request_date: date,
        purpose: str,
        requested_amount: Decimal,
        currency_code: str = "NGN",
        expected_settlement_date: Optional[date] = None,
        cost_center_id: Optional[UUID] = None,
        advance_account_id: Optional[UUID] = None,
        notes: Optional[str] = None,
    ) -> CashAdvance:
        """Create a new cash advance request."""
        # Generate advance number
        count = self.db.scalar(
            select(func.count(CashAdvance.advance_id)).where(
                CashAdvance.organization_id == org_id
            )
        ) or 0
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
        approved_amount: Optional[Decimal] = None,
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
        approver_id: Optional[UUID] = None,
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
        disbursed_amount: Optional[Decimal] = None,
        disbursement_date: Optional[date] = None,
        payment_reference: Optional[str] = None,
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
        payment_reference: Optional[str] = None,
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
        settlement_date: Optional[date] = None,
        notes: Optional[str] = None,
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
        employee_id: Optional[UUID] = None,
        is_active: Optional[bool] = None,
        pagination: Optional[PaginationParams] = None,
    ) -> PaginatedResult[CorporateCard]:
        """List corporate cards."""
        query = select(CorporateCard).where(CorporateCard.organization_id == org_id)

        if employee_id:
            query = query.where(CorporateCard.employee_id == employee_id)

        if is_active is not None:
            query = query.where(CorporateCard.is_active == is_active)

        query = query.order_by(CorporateCard.assigned_date.desc().nullslast())

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
        employee_id: Optional[UUID] = None,
        assigned_date: Optional[date] = None,
        issuer: Optional[str] = None,
        expiry_date: Optional[date] = None,
        credit_limit: Optional[Decimal] = None,
        single_transaction_limit: Optional[Decimal] = None,
        monthly_limit: Optional[Decimal] = None,
        currency_code: str = "NGN",
        liability_account_id: Optional[UUID] = None,
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
        reason: Optional[str] = None,
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
        card_id: Optional[UUID] = None,
        status: Optional[CardTransactionStatus | str] = None,
        from_date: Optional[date] = None,
        to_date: Optional[date] = None,
        unmatched_only: bool = False,
        pagination: Optional[PaginationParams] = None,
    ) -> PaginatedResult[CardTransaction]:
        """List card transactions."""
        query = select(CardTransaction).where(CardTransaction.organization_id == org_id)

        if card_id:
            query = query.where(CardTransaction.card_id == card_id)

        if status:
            status_value: Optional[CardTransactionStatus] = None
            if isinstance(status, CardTransactionStatus):
                status_value = status
            elif isinstance(status, str):
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
        posting_date: Optional[date] = None,
        merchant_category: Optional[str] = None,
        currency_code: str = "NGN",
        original_currency: Optional[str] = None,
        original_amount: Optional[Decimal] = None,
        external_reference: Optional[str] = None,
        description: Optional[str] = None,
        notes: Optional[str] = None,
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
        pending_claims = self.db.scalar(
            select(func.count(ExpenseClaim.claim_id)).where(
                ExpenseClaim.organization_id == org_id,
                ExpenseClaim.status == ExpenseClaimStatus.SUBMITTED,
            )
        ) or 0

        # Total pending amount
        total_pending = self.db.scalar(
            select(func.sum(ExpenseClaim.total_claimed_amount)).where(
                ExpenseClaim.organization_id == org_id,
                ExpenseClaim.status == ExpenseClaimStatus.SUBMITTED,
            )
        ) or Decimal("0")

        # Claims this month
        claims_this_month = self.db.scalar(
            select(func.count(ExpenseClaim.claim_id)).where(
                ExpenseClaim.organization_id == org_id,
                ExpenseClaim.claim_date >= month_start,
            )
        ) or 0

        # Amount this month
        amount_this_month = self.db.scalar(
            select(func.sum(ExpenseClaim.total_claimed_amount)).where(
                ExpenseClaim.organization_id == org_id,
                ExpenseClaim.claim_date >= month_start,
            )
        ) or Decimal("0")

        # Outstanding advances
        outstanding_advances = self.db.scalar(
            select(func.count(CashAdvance.advance_id)).where(
                CashAdvance.organization_id == org_id,
                CashAdvance.status == CashAdvanceStatus.DISBURSED,
            )
        ) or 0

        # Outstanding advance amount
        advance_amount = self.db.scalar(
            select(
                func.sum(
                    CashAdvance.approved_amount - CashAdvance.amount_settled - CashAdvance.amount_refunded
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
        year: Optional[int] = None,
        month: Optional[int] = None,
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
        claims_in_period = self.db.scalar(
            select(func.count(ExpenseClaim.claim_id)).where(
                ExpenseClaim.organization_id == org_id,
                ExpenseClaim.employee_id == employee_id,
                ExpenseClaim.claim_date >= period_start,
                ExpenseClaim.claim_date < period_end,
            )
        ) or 0

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
        pending_claims = self.db.scalar(
            select(func.count(ExpenseClaim.claim_id)).where(
                ExpenseClaim.organization_id == org_id,
                ExpenseClaim.employee_id == employee_id,
                ExpenseClaim.status == ExpenseClaimStatus.SUBMITTED,
            )
        ) or 0

        # Outstanding advances
        outstanding_advances = self.db.scalar(
            select(
                func.sum(
                    CashAdvance.approved_amount - CashAdvance.amount_settled - CashAdvance.amount_refunded
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

    # =========================================================================
    # Report Methods
    # =========================================================================

    def get_expense_summary_report(
        self,
        org_id: UUID,
        *,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> dict:
        """
        Get expense summary report with totals by status.

        Returns totals for claims, approved amounts, and pending amounts.
        """
        today = date.today()
        if not start_date:
            start_date = today.replace(day=1)
        if not end_date:
            end_date = today

        # Base query filters
        base_filters = [
            ExpenseClaim.organization_id == org_id,
            ExpenseClaim.claim_date >= start_date,
            ExpenseClaim.claim_date <= end_date,
        ]

        # Total claims
        total_claims = self.db.scalar(
            select(func.count(ExpenseClaim.claim_id)).where(*base_filters)
        ) or 0

        # Total claimed amount
        total_claimed = self.db.scalar(
            select(func.sum(ExpenseClaim.total_claimed_amount)).where(*base_filters)
        ) or Decimal("0")

        # By status breakdown
        status_breakdown = []
        for status in ExpenseClaimStatus:
            count = self.db.scalar(
                select(func.count(ExpenseClaim.claim_id)).where(
                    *base_filters,
                    ExpenseClaim.status == status,
                )
            ) or 0
            amount = self.db.scalar(
                select(func.sum(ExpenseClaim.total_claimed_amount)).where(
                    *base_filters,
                    ExpenseClaim.status == status,
                )
            ) or Decimal("0")
            if count > 0:
                status_breakdown.append({
                    "status": status.value,
                    "count": count,
                    "amount": amount,
                })

        # Approved vs rejected
        approved_count = self.db.scalar(
            select(func.count(ExpenseClaim.claim_id)).where(
                *base_filters,
                ExpenseClaim.status.in_([ExpenseClaimStatus.APPROVED, ExpenseClaimStatus.PAID]),
            )
        ) or 0
        approved_amount = self.db.scalar(
            select(func.sum(ExpenseClaim.total_approved_amount)).where(
                *base_filters,
                ExpenseClaim.status.in_([ExpenseClaimStatus.APPROVED, ExpenseClaimStatus.PAID]),
            )
        ) or Decimal("0")

        rejected_count = self.db.scalar(
            select(func.count(ExpenseClaim.claim_id)).where(
                *base_filters,
                ExpenseClaim.status == ExpenseClaimStatus.REJECTED,
            )
        ) or 0

        return {
            "start_date": start_date,
            "end_date": end_date,
            "total_claims": total_claims,
            "total_claimed": total_claimed,
            "approved_count": approved_count,
            "approved_amount": approved_amount,
            "rejected_count": rejected_count,
            "status_breakdown": status_breakdown,
        }

    def get_expense_by_category_report(
        self,
        org_id: UUID,
        *,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> dict:
        """
        Get expense breakdown by category.

        Returns list of categories with claim counts and amounts.
        """
        today = date.today()
        if not start_date:
            start_date = today.replace(day=1)
        if not end_date:
            end_date = today

        # Query expenses by category
        results = (
            self.db.query(
                ExpenseCategory.category_code,
                ExpenseCategory.category_name,
                func.count(ExpenseClaimItem.item_id).label("item_count"),
                func.sum(ExpenseClaimItem.claimed_amount).label("claimed_amount"),
                func.sum(ExpenseClaimItem.approved_amount).label("approved_amount"),
            )
            .join(ExpenseClaimItem, ExpenseClaimItem.category_id == ExpenseCategory.category_id)
            .join(ExpenseClaim, ExpenseClaim.claim_id == ExpenseClaimItem.claim_id)
            .filter(
                ExpenseClaim.organization_id == org_id,
                ExpenseClaim.claim_date >= start_date,
                ExpenseClaim.claim_date <= end_date,
            )
            .group_by(ExpenseCategory.category_id, ExpenseCategory.category_code, ExpenseCategory.category_name)
            .order_by(func.sum(ExpenseClaimItem.claimed_amount).desc())
            .all()
        )

        categories = []
        total_claimed = Decimal("0")
        total_approved = Decimal("0")

        for row in results:
            claimed = row.claimed_amount or Decimal("0")
            approved = row.approved_amount or Decimal("0")
            categories.append({
                "category_code": row.category_code,
                "category_name": row.category_name,
                "item_count": row.item_count,
                "claimed_amount": claimed,
                "approved_amount": approved,
            })
            total_claimed += claimed
            total_approved += approved

        # Calculate percentages
        for cat in categories:
            if total_claimed > 0:
                cat["percentage"] = float(cat["claimed_amount"] / total_claimed * 100)
            else:
                cat["percentage"] = 0.0

        return {
            "start_date": start_date,
            "end_date": end_date,
            "categories": categories,
            "total_claimed": total_claimed,
            "total_approved": total_approved,
        }

    def get_expense_by_employee_report(
        self,
        org_id: UUID,
        *,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        department_id: Optional[UUID] = None,
    ) -> dict:
        """
        Get expense breakdown by employee.

        Returns list of employees with claim counts and amounts.
        """
        from app.models.people.hr.employee import Employee
        from app.models.people.hr.department import Department
        from app.models.person import Person

        today = date.today()
        if not start_date:
            start_date = today.replace(day=1)
        if not end_date:
            end_date = today

        # Base query
        query = (
            self.db.query(
                Employee.employee_id,
                Person.first_name,
                Person.last_name,
                Department.department_name.label("department_name"),
                func.count(ExpenseClaim.claim_id).label("claim_count"),
                func.sum(ExpenseClaim.total_claimed_amount).label("claimed_amount"),
                func.sum(ExpenseClaim.total_approved_amount).label("approved_amount"),
            )
            .join(ExpenseClaim, ExpenseClaim.employee_id == Employee.employee_id)
            .join(Person, Person.id == Employee.person_id)
            .outerjoin(Department, Employee.department_id == Department.department_id)
            .filter(
                ExpenseClaim.organization_id == org_id,
                ExpenseClaim.claim_date >= start_date,
                ExpenseClaim.claim_date <= end_date,
            )
        )

        if department_id:
            query = query.filter(Employee.department_id == department_id)

        results = (
            query
            .group_by(
                Employee.employee_id,
                Person.first_name,
                Person.last_name,
                Department.department_name,
            )
            .order_by(func.sum(ExpenseClaim.total_claimed_amount).desc())
            .all()
        )

        employees = []
        total_claimed = Decimal("0")
        total_approved = Decimal("0")

        for row in results:
            claimed = row.claimed_amount or Decimal("0")
            approved = row.approved_amount or Decimal("0")
            employees.append({
                "employee_id": str(row.employee_id),
                "employee_name": f"{row.first_name} {row.last_name}",
                "department_name": row.department_name or "No Department",
                "claim_count": row.claim_count,
                "claimed_amount": claimed,
                "approved_amount": approved,
            })
            total_claimed += claimed
            total_approved += approved

        return {
            "start_date": start_date,
            "end_date": end_date,
            "employees": employees,
            "total_claimed": total_claimed,
            "total_approved": total_approved,
        }

    def get_expense_trends_report(
        self,
        org_id: UUID,
        *,
        months: int = 12,
    ) -> dict:
        """
        Get expense trends over time (monthly).

        Returns list of months with claim counts and amounts.
        """
        from dateutil.relativedelta import relativedelta

        today = date.today()
        end_date = today.replace(day=1)
        start_date = end_date - relativedelta(months=months - 1)

        # Query monthly aggregates
        month_bucket = func.date_trunc("month", ExpenseClaim.claim_date)
        results = (
            self.db.query(
                month_bucket.label("month"),
                func.count(ExpenseClaim.claim_id).label("claim_count"),
                func.sum(ExpenseClaim.total_claimed_amount).label("claimed_amount"),
                func.sum(ExpenseClaim.total_approved_amount).label("approved_amount"),
            )
            .filter(
                ExpenseClaim.organization_id == org_id,
                ExpenseClaim.claim_date >= start_date,
                ExpenseClaim.claim_date <= today,
            )
            .group_by(month_bucket)
            .order_by(month_bucket)
            .all()
        )

        # Build results dict by month
        monthly_data = {}
        for row in results:
            month_key = row.month.strftime("%Y-%m")
            monthly_data[month_key] = {
                "month": row.month.strftime("%Y-%m"),
                "month_label": row.month.strftime("%b %Y"),
                "claim_count": row.claim_count,
                "claimed_amount": row.claimed_amount or Decimal("0"),
                "approved_amount": row.approved_amount or Decimal("0"),
            }

        # Fill in missing months with zeros
        months_list = []
        current = start_date
        while current <= today:
            month_key = current.strftime("%Y-%m")
            if month_key in monthly_data:
                months_list.append(monthly_data[month_key])
            else:
                months_list.append({
                    "month": month_key,
                    "month_label": current.strftime("%b %Y"),
                    "claim_count": 0,
                    "claimed_amount": Decimal("0"),
                    "approved_amount": Decimal("0"),
                })
            current = current + relativedelta(months=1)

        # Calculate totals
        total_claimed = sum(m["claimed_amount"] for m in months_list)
        total_approved = sum(m["approved_amount"] for m in months_list)
        num_months = len(months_list)
        average_monthly = total_claimed / num_months if num_months > 0 else Decimal("0")

        return {
            "months": months_list,
            "total_months": num_months,
            "total_claimed": total_claimed,
            "total_approved": total_approved,
            "average_monthly": average_monthly,
        }
