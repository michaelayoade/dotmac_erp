"""
Expense Approval Service - Multi-approval workflow management.

Handles:
- Approval chain determination based on limit rules
- Multi-step approval tracking
- Escalation logic
- Receipt validation
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, List, Optional
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.models.expense import (
    ExpenseApproverLimit,
    ExpenseClaim,
    ExpenseClaimItem,
    ExpenseClaimStatus,
    ExpenseCategory,
    ExpenseLimitRule,
    LimitActionType,
    LimitScopeType,
)

if TYPE_CHECKING:
    from app.models.people.hr.employee import Employee
    from app.web.deps import WebAuthContext

__all__ = ["ExpenseApprovalService", "ApprovalStep", "ApprovalChain", "ReceiptValidationResult"]


@dataclass
class ApprovalStep:
    """Represents a single step in an approval chain."""

    step_number: int
    approver_id: UUID
    approver_name: str
    max_amount: Decimal
    is_escalation: bool = False
    is_completed: bool = False
    decision: Optional[str] = None  # "APPROVED", "REJECTED", None
    decided_at: Optional[datetime] = None
    notes: Optional[str] = None


@dataclass
class ApprovalChain:
    """Complete approval chain for an expense claim."""

    claim_id: UUID
    total_steps: int
    completed_steps: int
    current_step: int
    steps: List[ApprovalStep] = field(default_factory=list)
    requires_all_approvals: bool = False
    is_complete: bool = False
    final_decision: Optional[str] = None

    @property
    def pending_steps(self) -> List[ApprovalStep]:
        """Get steps that are pending approval."""
        return [s for s in self.steps if not s.is_completed]

    @property
    def current_approvers(self) -> List[UUID]:
        """Get IDs of approvers who can currently approve."""
        if self.is_complete:
            return []
        pending = self.pending_steps
        if not pending:
            return []
        # Return first pending step's approver (sequential approval)
        # For parallel approval, return all pending approvers
        if self.requires_all_approvals:
            return [s.approver_id for s in pending]
        return [pending[0].approver_id]


@dataclass
class ReceiptValidationResult:
    """Result of receipt validation for an expense claim."""

    is_valid: bool
    missing_receipts: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def has_issues(self) -> bool:
        return not self.is_valid or len(self.warnings) > 0


class ExpenseApprovalService:
    """
    Service for expense approval workflow management.

    Handles:
    - Building approval chains based on amount and rules
    - Processing individual approval decisions
    - Escalation when approver authority is insufficient
    - Receipt validation against category requirements
    """

    def __init__(
        self,
        db: Session,
        ctx: Optional["WebAuthContext"] = None,
    ) -> None:
        self.db = db
        self.ctx = ctx

    # =========================================================================
    # Approval Chain Management
    # =========================================================================

    def get_approval_chain(
        self,
        claim: ExpenseClaim,
        *,
        force_rebuild: bool = False,
    ) -> ApprovalChain:
        """
        Get or build the approval chain for an expense claim.

        The approval chain is determined by:
        1. Claim amount vs approver authority limits
        2. Triggered limit rules and their action configs
        3. Organizational hierarchy (manager chain)

        Args:
            claim: The expense claim
            force_rebuild: If True, rebuild chain even if cached

        Returns:
            ApprovalChain with all required approval steps
        """
        from app.models.people.hr.employee import Employee, EmployeeStatus

        org_id = claim.organization_id
        claim_amount = claim.total_approved_amount or claim.total_claimed_amount

        # Get employee
        employee = self.db.get(Employee, claim.employee_id) if claim.employee_id else None
        if not employee:
            # No employee - simple single approval
            return ApprovalChain(
                claim_id=claim.claim_id,
                total_steps=1,
                completed_steps=0,
                current_step=1,
                steps=[],
                requires_all_approvals=False,
                is_complete=False,
            )

        steps: List[ApprovalStep] = []
        seen_approvers = set()

        # Step 1: Check for triggered rules requiring multi-approval
        rules = self._get_triggered_rules(claim, employee)
        multi_approval_required = any(
            r.action_type == LimitActionType.REQUIRE_MULTI_APPROVAL for r in rules
        )
        escalation_required = any(
            r.action_type == LimitActionType.AUTO_ESCALATE for r in rules
        )

        # Step 2: Start with direct manager if available
        if employee.reports_to_id:
            manager = self.db.get(Employee, employee.reports_to_id)
            if manager:
                manager_limit = self._get_approver_max_amount(org_id, manager)
                if manager_limit and manager_limit >= claim_amount:
                    steps.append(ApprovalStep(
                        step_number=1,
                        approver_id=manager.employee_id,
                        approver_name=f"{manager.first_name} {manager.last_name}",
                        max_amount=manager_limit,
                        is_escalation=False,
                    ))
                    seen_approvers.add(manager.employee_id)

        # Step 3: Check if escalation is needed (manager can't approve amount)
        if not steps or escalation_required:
            escalation_approvers = self._get_escalation_approvers(
                org_id, employee, claim_amount, seen_approvers
            )
            for idx, (approver, max_amt, is_esc) in enumerate(escalation_approvers, start=len(steps) + 1):
                if approver.employee_id not in seen_approvers:
                    steps.append(ApprovalStep(
                        step_number=idx,
                        approver_id=approver.employee_id,
                        approver_name=f"{approver.first_name} {approver.last_name}",
                        max_amount=max_amt,
                        is_escalation=is_esc,
                    ))
                    seen_approvers.add(approver.employee_id)

        # Step 4: Handle multi-approval requirements
        if multi_approval_required and len(steps) < 2:
            # Need at least 2 approvers for multi-approval
            additional = self._get_additional_approvers(
                org_id, employee, claim_amount, seen_approvers, needed=2 - len(steps)
            )
            for idx, (approver, max_amt) in enumerate(additional, start=len(steps) + 1):
                steps.append(ApprovalStep(
                    step_number=idx,
                    approver_id=approver.employee_id,
                    approver_name=f"{approver.first_name} {approver.last_name}",
                    max_amount=max_amt,
                    is_escalation=False,
                ))

        # Step 5: Apply rule-specific approvers
        for rule in rules:
            if rule.action_config:
                specific_approver_id = rule.action_config.get("approver_id")
                if specific_approver_id:
                    approver = self.db.get(Employee, specific_approver_id)
                    if approver and approver.employee_id not in seen_approvers:
                        steps.append(ApprovalStep(
                            step_number=len(steps) + 1,
                            approver_id=approver.employee_id,
                            approver_name=f"{approver.first_name} {approver.last_name}",
                            max_amount=Decimal("999999999"),  # Rule-specified approvers have no limit
                            is_escalation=False,
                        ))
                        seen_approvers.add(approver.employee_id)

        # Ensure we have at least one approver
        if not steps:
            # Fall back to organization-wide approvers
            fallback = self._get_fallback_approvers(org_id, claim_amount)
            for idx, (approver, max_amt) in enumerate(fallback, start=1):
                steps.append(ApprovalStep(
                    step_number=idx,
                    approver_id=approver.employee_id,
                    approver_name=f"{approver.first_name} {approver.last_name}",
                    max_amount=max_amt,
                    is_escalation=False,
                ))

        return ApprovalChain(
            claim_id=claim.claim_id,
            total_steps=len(steps),
            completed_steps=0,
            current_step=1,
            steps=steps,
            requires_all_approvals=multi_approval_required,
            is_complete=False,
        )

    def check_escalation_needed(
        self,
        claim: ExpenseClaim,
        approver_id: UUID,
    ) -> bool:
        """
        Check if escalation is needed because approver lacks authority.

        Returns True if the claim amount exceeds the approver's authority.
        """
        from app.models.people.hr.employee import Employee, EmployeeStatus

        approver = self.db.get(Employee, approver_id)
        if not approver:
            return True

        claim_amount = claim.total_approved_amount or claim.total_claimed_amount
        max_amount = self._get_approver_max_amount(claim.organization_id, approver)

        if not max_amount:
            return True

        return claim_amount > max_amount

    def escalate_approval(
        self,
        claim: ExpenseClaim,
        current_approver_id: UUID,
        *,
        reason: Optional[str] = None,
    ) -> Optional[UUID]:
        """
        Escalate approval to a higher authority.

        Args:
            claim: The expense claim
            current_approver_id: Current approver who is escalating
            reason: Reason for escalation

        Returns:
            New approver ID if escalation successful, None otherwise
        """
        from app.models.people.hr.employee import Employee, EmployeeStatus

        org_id = claim.organization_id

        # Get current approver's limit config
        approver_limit = self.db.scalar(
            select(ExpenseApproverLimit).where(
                ExpenseApproverLimit.organization_id == org_id,
                ExpenseApproverLimit.scope_type == "EMPLOYEE",
                ExpenseApproverLimit.scope_id == current_approver_id,
                ExpenseApproverLimit.is_active == True,
            )
        )

        # Check for explicit escalation target
        if approver_limit and approver_limit.escalate_to_employee_id:
            return approver_limit.escalate_to_employee_id

        # Fall back to manager's manager
        current_approver = self.db.get(Employee, current_approver_id)
        if current_approver and current_approver.reports_to_id:
            manager = self.db.get(Employee, current_approver.reports_to_id)
            if manager and manager.reports_to_id:
                return manager.reports_to_id

        # Fall back to grade-based escalation
        if approver_limit and approver_limit.escalate_to_grade_min_rank:
            from app.models.people.hr.grade import Grade
            min_rank = approver_limit.escalate_to_grade_min_rank

            higher_grade_employee = self.db.scalar(
                select(Employee)
                .join(Grade, Employee.grade_id == Grade.grade_id)
                .where(
                    Employee.organization_id == org_id,
                    Grade.rank >= min_rank,
                    Employee.status == EmployeeStatus.ACTIVE,
                    Employee.employee_id != current_approver_id,
                )
                .order_by(Grade.rank)
                .limit(1)
            )
            if higher_grade_employee:
                return higher_grade_employee.employee_id

        return None

    def process_approval_decision(
        self,
        claim: ExpenseClaim,
        approver_id: UUID,
        decision: str,  # "APPROVED" or "REJECTED"
        *,
        notes: Optional[str] = None,
        approved_amounts: Optional[List[dict]] = None,
    ) -> ApprovalChain:
        """
        Process an approval decision for a claim.

        Args:
            claim: The expense claim
            approver_id: ID of the approver making the decision
            decision: "APPROVED" or "REJECTED"
            notes: Optional notes/comments
            approved_amounts: Optional per-item approved amounts

        Returns:
            Updated ApprovalChain
        """
        chain = self.get_approval_chain(claim)

        # Find the step for this approver
        step = next((s for s in chain.steps if s.approver_id == approver_id), None)
        if not step:
            # Approver not in chain - check if they have authority
            if self.check_escalation_needed(claim, approver_id):
                raise ValueError("Approver does not have authority for this claim")
            # Add them as an ad-hoc approver
            step = ApprovalStep(
                step_number=len(chain.steps) + 1,
                approver_id=approver_id,
                approver_name="Ad-hoc Approver",
                max_amount=Decimal("0"),
                is_escalation=False,
            )
            chain.steps.append(step)

        # Update step
        step.is_completed = True
        step.decision = decision
        step.decided_at = datetime.utcnow()
        step.notes = notes

        # Update chain status
        chain.completed_steps = sum(1 for s in chain.steps if s.is_completed)

        if decision == "REJECTED":
            chain.is_complete = True
            chain.final_decision = "REJECTED"
        elif chain.requires_all_approvals:
            if chain.completed_steps >= chain.total_steps:
                all_approved = all(s.decision == "APPROVED" for s in chain.steps if s.is_completed)
                chain.is_complete = True
                chain.final_decision = "APPROVED" if all_approved else "REJECTED"
        else:
            # Single approval sufficient
            chain.is_complete = True
            chain.final_decision = "APPROVED"

        chain.current_step = chain.completed_steps + 1

        return chain

    # =========================================================================
    # Receipt Validation
    # =========================================================================

    def validate_receipt_requirements(
        self,
        claim: ExpenseClaim,
    ) -> ReceiptValidationResult:
        """
        Validate that all receipt requirements are met for a claim.

        Checks each item against its category's requires_receipt setting.

        Args:
            claim: The expense claim to validate

        Returns:
            ReceiptValidationResult with validation status and issues
        """
        missing_receipts = []
        warnings = []

        for item in claim.items:
            # Load category
            category = self.db.get(ExpenseCategory, item.category_id) if item.category_id else None

            # Check if receipt is required
            if category and category.requires_receipt:
                if not item.receipt_url and not item.receipt_number:
                    missing_receipts.append(
                        f"Item '{item.description[:50]}' ({category.category_name}): Receipt required"
                    )

            # Check amount limits
            if category and category.max_amount_per_claim:
                if item.claimed_amount > category.max_amount_per_claim:
                    warnings.append(
                        f"Item '{item.description[:50]}' exceeds category limit "
                        f"({item.claimed_amount} > {category.max_amount_per_claim})"
                    )

        return ReceiptValidationResult(
            is_valid=len(missing_receipts) == 0,
            missing_receipts=missing_receipts,
            warnings=warnings,
        )

    # =========================================================================
    # Helper Methods
    # =========================================================================

    def _get_triggered_rules(
        self,
        claim: ExpenseClaim,
        employee: "Employee",
    ) -> List[ExpenseLimitRule]:
        """Get limit rules that were triggered for this claim."""
        from app.services.expense.limit_service import ExpenseLimitService

        service = ExpenseLimitService(self.db, self.ctx)
        rules = service.get_applicable_rules(claim.organization_id, employee, claim.claim_date)

        claim_amount = claim.total_approved_amount or claim.total_claimed_amount

        # Filter to rules that are actually triggered
        triggered = []
        for rule in rules:
            if claim_amount > rule.limit_amount:
                triggered.append(rule)

        return triggered

    def _get_approver_max_amount(
        self,
        organization_id: UUID,
        employee: "Employee",
    ) -> Optional[Decimal]:
        """Get the maximum amount an employee can approve."""
        # Check employee-specific limit
        emp_limit = self.db.scalar(
            select(ExpenseApproverLimit.max_approval_amount).where(
                ExpenseApproverLimit.organization_id == organization_id,
                ExpenseApproverLimit.is_active == True,
                ExpenseApproverLimit.scope_type == "EMPLOYEE",
                ExpenseApproverLimit.scope_id == employee.employee_id,
            )
        )
        if emp_limit:
            return emp_limit

        # Check grade-based limit
        if employee.grade_id:
            grade_limit = self.db.scalar(
                select(ExpenseApproverLimit.max_approval_amount).where(
                    ExpenseApproverLimit.organization_id == organization_id,
                    ExpenseApproverLimit.is_active == True,
                    ExpenseApproverLimit.scope_type == "GRADE",
                    ExpenseApproverLimit.scope_id == employee.grade_id,
                )
            )
            if grade_limit:
                return grade_limit

        # Check designation-based limit
        if employee.designation_id:
            desig_limit = self.db.scalar(
                select(ExpenseApproverLimit.max_approval_amount).where(
                    ExpenseApproverLimit.organization_id == organization_id,
                    ExpenseApproverLimit.is_active == True,
                    ExpenseApproverLimit.scope_type == "DESIGNATION",
                    ExpenseApproverLimit.scope_id == employee.designation_id,
                )
            )
            if desig_limit:
                return desig_limit

        return None

    def _get_escalation_approvers(
        self,
        organization_id: UUID,
        employee: "Employee",
        amount: Decimal,
        exclude_ids: set,
    ) -> List[tuple]:
        """Get approvers through escalation chain."""
        from app.models.people.hr.employee import Employee as EmployeeModel, EmployeeStatus

        result = []

        # Walk up the management chain
        current = employee
        for _ in range(5):  # Max 5 levels of escalation
            if not current.reports_to_id:
                break
            if current.reports_to_id in exclude_ids:
                next_employee = self.db.get(EmployeeModel, current.reports_to_id)
                if not next_employee:
                    break
                current = next_employee
                continue

            manager = self.db.get(EmployeeModel, current.reports_to_id)
            if not manager:
                break

            max_amt = self._get_approver_max_amount(organization_id, manager)
            if max_amt and max_amt >= amount:
                result.append((manager, max_amt, True))
                break

            # Manager can't approve, add and continue
            if max_amt:
                result.append((manager, max_amt, True))
                exclude_ids.add(manager.employee_id)

            current = manager

        return result

    def _get_additional_approvers(
        self,
        organization_id: UUID,
        employee: "Employee",
        amount: Decimal,
        exclude_ids: set,
        needed: int,
    ) -> List[tuple]:
        """Get additional approvers for multi-approval requirements."""
        from app.models.people.hr.employee import Employee as EmployeeModel, EmployeeStatus

        result = []

        # Find approvers with sufficient authority
        approvers = self.db.execute(
            select(ExpenseApproverLimit, EmployeeModel)
            .join(EmployeeModel, ExpenseApproverLimit.scope_id == EmployeeModel.employee_id)
            .where(
                ExpenseApproverLimit.organization_id == organization_id,
                ExpenseApproverLimit.is_active == True,
                ExpenseApproverLimit.scope_type == "EMPLOYEE",
                ExpenseApproverLimit.max_approval_amount >= amount,
                EmployeeModel.status == EmployeeStatus.ACTIVE,
                ~EmployeeModel.employee_id.in_(exclude_ids),
                EmployeeModel.employee_id != employee.employee_id,
            )
            .order_by(ExpenseApproverLimit.max_approval_amount)
            .limit(needed)
        ).all()

        for limit, approver in approvers:
            result.append((approver, limit.max_approval_amount))

        return result

    def _get_fallback_approvers(
        self,
        organization_id: UUID,
        amount: Decimal,
    ) -> List[tuple]:
        """Get fallback approvers when no specific chain is available."""
        from app.models.people.hr.employee import Employee as EmployeeModel, EmployeeStatus

        result = []

        # Find any approvers with sufficient authority
        approvers = self.db.execute(
            select(ExpenseApproverLimit, EmployeeModel)
            .join(EmployeeModel, ExpenseApproverLimit.scope_id == EmployeeModel.employee_id)
            .where(
                ExpenseApproverLimit.organization_id == organization_id,
                ExpenseApproverLimit.is_active == True,
                ExpenseApproverLimit.scope_type == "EMPLOYEE",
                ExpenseApproverLimit.max_approval_amount >= amount,
                EmployeeModel.status == EmployeeStatus.ACTIVE,
            )
            .order_by(ExpenseApproverLimit.max_approval_amount)
            .limit(3)
        ).all()

        for limit, approver in approvers:
            result.append((approver, limit.max_approval_amount))

        return result

    # =========================================================================
    # Employment Type Filtering
    # =========================================================================

    def filter_limits_by_employment_type(
        self,
        organization_id: UUID,
        employee: "Employee",
    ) -> List[ExpenseLimitRule]:
        """
        Get expense limit rules filtered by employee's employment type.

        Rules can specify employment_type in dimension_filters to only
        apply to certain types (e.g., PERMANENT, CONTRACT, INTERN).
        """
        today = date.today()

        # Get all active rules for organization
        all_rules = list(self.db.scalars(
            select(ExpenseLimitRule).where(
                ExpenseLimitRule.organization_id == organization_id,
                ExpenseLimitRule.is_active == True,
                ExpenseLimitRule.effective_from <= today,
                or_(
                    ExpenseLimitRule.effective_to.is_(None),
                    ExpenseLimitRule.effective_to >= today,
                ),
            )
        ).all())

        # Filter by employment type
        employee_type = getattr(employee, 'employment_type', None)
        filtered = []

        for rule in all_rules:
            if not rule.dimension_filters:
                filtered.append(rule)
                continue

            # Check employment_type filter
            type_filter = rule.dimension_filters.get("employment_types", [])
            if not type_filter:
                filtered.append(rule)
                continue

            if employee_type and employee_type in type_filter:
                filtered.append(rule)
            elif not employee_type:
                # No employment type set - include rules without type filter
                filtered.append(rule)

        return filtered


# Module-level instance
expense_approval_service = ExpenseApprovalService
