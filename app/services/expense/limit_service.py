"""Expense limit enforcement service.

Handles multi-dimensional expense limit enforcement including:
- Spending limit rules by scope (employee, grade, department, etc.)
- Period-based limit tracking (day, week, month, quarter, year)
- Approver authority configuration
- Limit evaluation with audit trail
- Approver selection and escalation
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, List, Optional, Tuple
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.models.expense import (
    ExpenseApproverLimit,
    ExpenseClaim,
    ExpenseClaimStatus,
    ExpenseLimitEvaluation,
    ExpenseLimitRule,
    ExpensePeriodUsage,
    LimitActionType,
    LimitPeriodType,
    LimitResultType,
    LimitScopeType,
)
from app.services.common import PaginatedResult, PaginationParams

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.models.people.hr.employee import Employee
    from app.web.deps import WebAuthContext

__all__ = ["ExpenseLimitService"]


# =============================================================================
# Exceptions
# =============================================================================


class ExpenseLimitServiceError(Exception):
    """Base error for expense limit service."""

    pass


class ExpenseLimitRuleNotFoundError(ExpenseLimitServiceError):
    """Expense limit rule not found."""

    def __init__(self, rule_id: UUID):
        self.rule_id = rule_id
        super().__init__(f"Expense limit rule {rule_id} not found")


class ExpenseApproverLimitNotFoundError(ExpenseLimitServiceError):
    """Expense approver limit not found."""

    def __init__(self, approver_limit_id: UUID):
        self.approver_limit_id = approver_limit_id
        super().__init__(f"Expense approver limit {approver_limit_id} not found")


class ExpenseLimitExceededError(ExpenseLimitServiceError):
    """Expense limit exceeded."""

    def __init__(self, message: str, rule: ExpenseLimitRule):
        self.rule = rule
        super().__init__(message)


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class EvaluationResult:
    """Result of limit evaluation."""

    result: LimitResultType
    message: str
    triggered_rule: Optional[ExpenseLimitRule] = None
    period_spent: Decimal = Decimal("0")
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    eligible_approvers: List["EligibleApprover"] = field(default_factory=list)


@dataclass
class EligibleApprover:
    """Eligible approver for an expense claim."""

    employee_id: UUID
    employee_name: str
    max_approval_amount: Decimal
    is_direct_manager: bool = False
    grade_rank: Optional[int] = None


# =============================================================================
# Service
# =============================================================================


class ExpenseLimitService:
    """Service for expense limit enforcement.

    Handles:
    - Limit rule configuration and management
    - Approver authority configuration
    - Limit evaluation on claim submission
    - Period usage tracking and caching
    - Approver selection and escalation
    """

    def __init__(
        self,
        db: Session,
        ctx: Optional["WebAuthContext"] = None,
    ) -> None:
        self.db = db
        self.ctx = ctx

    # =========================================================================
    # Limit Rules CRUD
    # =========================================================================

    def list_rules(
        self,
        org_id: UUID,
        *,
        scope_type: Optional[LimitScopeType] = None,
        is_active: Optional[bool] = None,
        search: Optional[str] = None,
        pagination: Optional[PaginationParams] = None,
    ) -> PaginatedResult[ExpenseLimitRule]:
        """List expense limit rules."""
        query = select(ExpenseLimitRule).where(
            ExpenseLimitRule.organization_id == org_id
        )

        if scope_type:
            query = query.where(ExpenseLimitRule.scope_type == scope_type)

        if is_active is not None:
            query = query.where(ExpenseLimitRule.is_active == is_active)

        if search:
            search_term = f"%{search}%"
            query = query.where(
                or_(
                    ExpenseLimitRule.rule_code.ilike(search_term),
                    ExpenseLimitRule.rule_name.ilike(search_term),
                )
            )

        query = query.order_by(ExpenseLimitRule.priority, ExpenseLimitRule.rule_code)

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

    def get_rule(self, org_id: UUID, rule_id: UUID) -> ExpenseLimitRule:
        """Get an expense limit rule by ID."""
        rule = self.db.scalar(
            select(ExpenseLimitRule).where(
                ExpenseLimitRule.rule_id == rule_id,
                ExpenseLimitRule.organization_id == org_id,
            )
        )
        if not rule:
            raise ExpenseLimitRuleNotFoundError(rule_id)
        return rule

    def create_rule(
        self,
        org_id: UUID,
        *,
        rule_code: str,
        rule_name: str,
        scope_type: LimitScopeType,
        period_type: LimitPeriodType,
        limit_amount: Decimal,
        action_type: LimitActionType,
        effective_from: date,
        scope_id: Optional[UUID] = None,
        custom_period_days: Optional[int] = None,
        currency_code: str = "NGN",
        dimension_filters: Optional[dict] = None,
        action_config: Optional[dict] = None,
        priority: int = 100,
        effective_to: Optional[date] = None,
        is_active: bool = True,
        description: Optional[str] = None,
    ) -> ExpenseLimitRule:
        """Create a new expense limit rule."""
        rule = ExpenseLimitRule(
            organization_id=org_id,
            rule_code=rule_code,
            rule_name=rule_name,
            description=description,
            scope_type=scope_type,
            scope_id=scope_id,
            period_type=period_type,
            custom_period_days=custom_period_days,
            limit_amount=limit_amount,
            currency_code=currency_code,
            action_type=action_type,
            dimension_filters=dimension_filters or {},
            action_config=action_config or {},
            priority=priority,
            effective_from=effective_from,
            effective_to=effective_to,
            is_active=is_active,
        )

        self.db.add(rule)
        self.db.flush()
        return rule

    def update_rule(
        self,
        org_id: UUID,
        rule_id: UUID,
        **kwargs,
    ) -> ExpenseLimitRule:
        """Update an expense limit rule."""
        rule = self.get_rule(org_id, rule_id)

        for key, value in kwargs.items():
            if value is not None and hasattr(rule, key):
                setattr(rule, key, value)

        self.db.flush()
        return rule

    def delete_rule(self, org_id: UUID, rule_id: UUID) -> None:
        """Delete an expense limit rule."""
        rule = self.get_rule(org_id, rule_id)
        self.db.delete(rule)
        self.db.flush()

    # =========================================================================
    # Approver Limits CRUD
    # =========================================================================

    def list_approver_limits(
        self,
        org_id: UUID,
        *,
        scope_type: Optional[str] = None,
        is_active: Optional[bool] = None,
        pagination: Optional[PaginationParams] = None,
    ) -> PaginatedResult[ExpenseApproverLimit]:
        """List expense approver limits."""
        query = select(ExpenseApproverLimit).where(
            ExpenseApproverLimit.organization_id == org_id
        )

        if scope_type:
            query = query.where(ExpenseApproverLimit.scope_type == scope_type)

        if is_active is not None:
            query = query.where(ExpenseApproverLimit.is_active == is_active)

        query = query.order_by(ExpenseApproverLimit.max_approval_amount.desc())

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

    def get_approver_limit(
        self, org_id: UUID, approver_limit_id: UUID
    ) -> ExpenseApproverLimit:
        """Get an expense approver limit by ID."""
        limit = self.db.scalar(
            select(ExpenseApproverLimit).where(
                ExpenseApproverLimit.approver_limit_id == approver_limit_id,
                ExpenseApproverLimit.organization_id == org_id,
            )
        )
        if not limit:
            raise ExpenseApproverLimitNotFoundError(approver_limit_id)
        return limit

    def create_approver_limit(
        self,
        org_id: UUID,
        *,
        scope_type: str,
        max_approval_amount: Decimal,
        scope_id: Optional[UUID] = None,
        currency_code: str = "NGN",
        dimension_filters: Optional[dict] = None,
        escalate_to_employee_id: Optional[UUID] = None,
        escalate_to_grade_min_rank: Optional[int] = None,
        can_approve_own_expenses: bool = False,
        is_active: bool = True,
    ) -> ExpenseApproverLimit:
        """Create a new expense approver limit."""
        limit = ExpenseApproverLimit(
            organization_id=org_id,
            scope_type=scope_type,
            scope_id=scope_id,
            max_approval_amount=max_approval_amount,
            currency_code=currency_code,
            dimension_filters=dimension_filters or {},
            escalate_to_employee_id=escalate_to_employee_id,
            escalate_to_grade_min_rank=escalate_to_grade_min_rank,
            can_approve_own_expenses=can_approve_own_expenses,
            is_active=is_active,
        )

        self.db.add(limit)
        self.db.flush()
        return limit

    def update_approver_limit(
        self,
        org_id: UUID,
        approver_limit_id: UUID,
        **kwargs,
    ) -> ExpenseApproverLimit:
        """Update an expense approver limit."""
        limit = self.get_approver_limit(org_id, approver_limit_id)

        for key, value in kwargs.items():
            if value is not None and hasattr(limit, key):
                setattr(limit, key, value)

        self.db.flush()
        return limit

    def delete_approver_limit(self, org_id: UUID, approver_limit_id: UUID) -> None:
        """Delete an expense approver limit."""
        limit = self.get_approver_limit(org_id, approver_limit_id)
        self.db.delete(limit)
        self.db.flush()

    # =========================================================================
    # Period Usage
    # =========================================================================

    def get_period_bounds(
        self, period_type: LimitPeriodType, reference_date: Optional[date] = None
    ) -> Tuple[date, date]:
        """Calculate period start and end dates."""
        ref = reference_date or date.today()

        if period_type == LimitPeriodType.DAY:
            return ref, ref

        elif period_type == LimitPeriodType.WEEK:
            # ISO week: Monday to Sunday
            start = ref - timedelta(days=ref.weekday())
            end = start + timedelta(days=6)
            return start, end

        elif period_type == LimitPeriodType.MONTH:
            start = ref.replace(day=1)
            # Last day of month
            if ref.month == 12:
                end = date(ref.year + 1, 1, 1) - timedelta(days=1)
            else:
                end = date(ref.year, ref.month + 1, 1) - timedelta(days=1)
            return start, end

        elif period_type == LimitPeriodType.QUARTER:
            quarter = (ref.month - 1) // 3
            start = date(ref.year, quarter * 3 + 1, 1)
            if quarter == 3:
                end = date(ref.year + 1, 1, 1) - timedelta(days=1)
            else:
                end = date(ref.year, (quarter + 1) * 3 + 1, 1) - timedelta(days=1)
            return start, end

        elif period_type == LimitPeriodType.YEAR:
            start = date(ref.year, 1, 1)
            end = date(ref.year, 12, 31)
            return start, end

        else:
            # TRANSACTION or CUSTOM - no period bounds
            return ref, ref

    def calculate_period_usage(
        self,
        org_id: UUID,
        employee_id: UUID,
        period_type: LimitPeriodType,
        period_start: date,
        period_end: date,
        *,
        category_ids: Optional[List[UUID]] = None,
        cost_center_ids: Optional[List[UUID]] = None,
    ) -> Tuple[Decimal, int]:
        """
        Calculate cumulative expense usage for an employee in a period.

        Returns (total_claimed_amount, claim_count).
        Counts claims with status in: SUBMITTED, PENDING_APPROVAL, APPROVED, PAID.
        """
        # Statuses that count towards usage
        counting_statuses = [
            ExpenseClaimStatus.SUBMITTED,
            ExpenseClaimStatus.PENDING_APPROVAL,
            ExpenseClaimStatus.APPROVED,
            ExpenseClaimStatus.PAID,
        ]

        # Base query
        query = select(
            func.coalesce(func.sum(ExpenseClaim.total_claimed_amount), Decimal("0")),
            func.count(ExpenseClaim.claim_id),
        ).where(
            ExpenseClaim.organization_id == org_id,
            ExpenseClaim.employee_id == employee_id,
            ExpenseClaim.status.in_(counting_statuses),
            ExpenseClaim.claim_date >= period_start,
            ExpenseClaim.claim_date <= period_end,
        )

        # TODO: Add category/cost_center filtering via claim items if needed
        # This would require a join with expense_claim_item

        result = self.db.execute(query).one()
        return result[0], result[1]

    def get_or_create_usage_cache(
        self,
        org_id: UUID,
        employee_id: UUID,
        period_type: str,
        period_start: date,
        period_end: date,
        dimension_type: Optional[str] = None,
        dimension_id: Optional[UUID] = None,
    ) -> ExpensePeriodUsage:
        """Get or create a period usage cache entry."""
        usage = self.db.scalar(
            select(ExpensePeriodUsage).where(
                ExpensePeriodUsage.organization_id == org_id,
                ExpensePeriodUsage.employee_id == employee_id,
                ExpensePeriodUsage.period_type == period_type,
                ExpensePeriodUsage.period_start == period_start,
                ExpensePeriodUsage.dimension_type == dimension_type
                if dimension_type
                else ExpensePeriodUsage.dimension_type.is_(None),
                ExpensePeriodUsage.dimension_id == dimension_id
                if dimension_id
                else ExpensePeriodUsage.dimension_id.is_(None),
            )
        )

        if not usage:
            usage = ExpensePeriodUsage(
                organization_id=org_id,
                employee_id=employee_id,
                period_type=period_type,
                period_start=period_start,
                period_end=period_end,
                dimension_type=dimension_type,
                dimension_id=dimension_id,
            )
            self.db.add(usage)
            self.db.flush()

        return usage

    def refresh_usage_cache(
        self,
        org_id: UUID,
        employee_id: UUID,
        period_type: LimitPeriodType,
    ) -> ExpensePeriodUsage:
        """Refresh usage cache for an employee/period."""
        period_start, period_end = self.get_period_bounds(period_type)

        total_claimed, claim_count = self.calculate_period_usage(
            org_id, employee_id, period_type, period_start, period_end
        )

        usage = self.get_or_create_usage_cache(
            org_id, employee_id, period_type.value, period_start, period_end
        )
        usage.refresh(total_claimed, Decimal("0"), claim_count)

        self.db.flush()
        return usage

    # =========================================================================
    # Rule Matching
    # =========================================================================

    def get_applicable_rules(
        self,
        org_id: UUID,
        employee: "Employee",
        claim_date: Optional[date] = None,
    ) -> List[ExpenseLimitRule]:
        """
        Get all applicable limit rules for an employee.

        Rules match by scope:
        - ORGANIZATION: Applies to all employees
        - DEPARTMENT: Matches employee.department_id
        - GRADE: Matches employee.grade_id
        - DESIGNATION: Matches employee.designation_id
        - EMPLOYMENT_TYPE: Matches employee.employment_type
        - EMPLOYEE: Matches employee.employee_id
        """
        today = claim_date or date.today()

        # Build conditions for scope matching
        scope_conditions = [
            # Organization-wide rules (null scope_id)
            and_(
                ExpenseLimitRule.scope_type == LimitScopeType.ORGANIZATION,
                ExpenseLimitRule.scope_id.is_(None),
            ),
        ]

        # Employee-specific rules
        scope_conditions.append(
            and_(
                ExpenseLimitRule.scope_type == LimitScopeType.EMPLOYEE,
                ExpenseLimitRule.scope_id == employee.employee_id,
            )
        )

        # Department rules
        if employee.department_id:
            scope_conditions.append(
                and_(
                    ExpenseLimitRule.scope_type == LimitScopeType.DEPARTMENT,
                    ExpenseLimitRule.scope_id == employee.department_id,
                )
            )

        # Grade rules
        if employee.grade_id:
            scope_conditions.append(
                and_(
                    ExpenseLimitRule.scope_type == LimitScopeType.GRADE,
                    ExpenseLimitRule.scope_id == employee.grade_id,
                )
            )

        # Designation rules
        if employee.designation_id:
            scope_conditions.append(
                and_(
                    ExpenseLimitRule.scope_type == LimitScopeType.DESIGNATION,
                    ExpenseLimitRule.scope_id == employee.designation_id,
                )
            )

        # Employment type rules
        if hasattr(employee, "employment_type") and employee.employment_type:
            # Employment type is stored as scope_id = hash or direct match
            # For simplicity, we'll skip this for now
            pass

        # Query
        query = (
            select(ExpenseLimitRule)
            .where(
                ExpenseLimitRule.organization_id == org_id,
                ExpenseLimitRule.is_active == True,
                ExpenseLimitRule.effective_from <= today,
                or_(
                    ExpenseLimitRule.effective_to.is_(None),
                    ExpenseLimitRule.effective_to >= today,
                ),
                or_(*scope_conditions),
            )
            .order_by(ExpenseLimitRule.priority, ExpenseLimitRule.rule_code)
        )

        return list(self.db.scalars(query).all())

    # =========================================================================
    # Limit Evaluation
    # =========================================================================

    def evaluate_claim(
        self,
        claim: ExpenseClaim,
        *,
        preview_only: bool = False,
    ) -> EvaluationResult:
        """
        Evaluate expense limits for a claim.

        Steps:
        1. Get employee from claim
        2. Get all applicable rules for employee
        3. For each rule (sorted by priority):
           a. Calculate period usage
           b. Check if current_usage + claim_amount > limit
           c. If exceeded, determine action
        4. Return most restrictive result

        Args:
            claim: The expense claim to evaluate
            preview_only: If True, don't record evaluation or update statistics

        Returns:
            EvaluationResult with result type, message, and eligible approvers
        """
        from app.models.people.hr.employee import Employee

        org_id = claim.organization_id
        claim_amount = claim.total_claimed_amount

        # Get employee
        employee = self.db.get(Employee, claim.employee_id)
        if not employee:
            # No employee - allow (might be org-level claim)
            return EvaluationResult(
                result=LimitResultType.PASSED,
                message="No employee linked to claim",
            )

        # Get applicable rules
        rules = self.get_applicable_rules(org_id, employee, claim.claim_date)

        if not rules:
            # No rules configured - allow
            return EvaluationResult(
                result=LimitResultType.PASSED,
                message="No limit rules configured",
            )

        # Track most restrictive result
        most_restrictive: Optional[EvaluationResult] = None
        result_priority = {
            LimitResultType.PASSED: 0,
            LimitResultType.WARNING: 1,
            LimitResultType.APPROVAL_REQUIRED: 2,
            LimitResultType.MULTI_APPROVAL_REQUIRED: 3,
            LimitResultType.ESCALATED: 4,
            LimitResultType.BLOCKED: 5,
        }

        # Evaluate each rule
        for rule in rules:
            rule_result = self._evaluate_rule(claim, rule, employee)

            # Update statistics
            if not preview_only:
                rule.increment_evaluation_count()
                if rule_result.result != LimitResultType.PASSED:
                    rule.increment_trigger_count()
                    if rule_result.result == LimitResultType.BLOCKED:
                        rule.increment_block_count()

            # Track most restrictive
            if most_restrictive is None:
                most_restrictive = rule_result
            elif result_priority.get(rule_result.result, 0) > result_priority.get(
                most_restrictive.result, 0
            ):
                most_restrictive = rule_result

        final_result = most_restrictive or EvaluationResult(
            result=LimitResultType.PASSED,
            message="All limits passed",
        )

        # Record evaluation
        if not preview_only and final_result.result != LimitResultType.PASSED:
            evaluation = ExpenseLimitEvaluation(
                organization_id=org_id,
                claim_id=claim.claim_id,
                claim_amount=claim_amount,
                period_spent_amount=final_result.period_spent,
                period_start=final_result.period_start,
                period_end=final_result.period_end,
                rule_id=final_result.triggered_rule.rule_id
                if final_result.triggered_rule
                else None,
                rule_code=final_result.triggered_rule.rule_code
                if final_result.triggered_rule
                else None,
                result=final_result.result,
                result_message=final_result.message,
                context_data={
                    "employee_id": str(employee.employee_id),
                    "employee_name": f"{employee.first_name} {employee.last_name}",
                    "rules_evaluated": len(rules),
                },
            )
            self.db.add(evaluation)
            self.db.flush()

        # If approval required, find eligible approvers
        if final_result.result in [
            LimitResultType.APPROVAL_REQUIRED,
            LimitResultType.MULTI_APPROVAL_REQUIRED,
            LimitResultType.ESCALATED,
        ]:
            final_result.eligible_approvers = self.get_eligible_approvers(
                org_id, employee, claim_amount
            )

        return final_result

    def _evaluate_rule(
        self,
        claim: ExpenseClaim,
        rule: ExpenseLimitRule,
        employee: "Employee",
    ) -> EvaluationResult:
        """Evaluate a single rule against a claim."""
        claim_amount = claim.total_claimed_amount
        org_id = claim.organization_id

        # For per-transaction limits, just check the amount
        if rule.period_type == LimitPeriodType.TRANSACTION:
            if claim_amount > rule.limit_amount:
                return self._create_exceeded_result(rule, claim_amount, Decimal("0"))
            return EvaluationResult(
                result=LimitResultType.PASSED,
                message=f"Within {rule.rule_code} limit",
                triggered_rule=rule,
            )

        # For cumulative limits, calculate period usage
        period_start, period_end = self.get_period_bounds(
            rule.period_type, claim.claim_date
        )

        period_spent, _ = self.calculate_period_usage(
            org_id,
            employee.employee_id,
            rule.period_type,
            period_start,
            period_end,
            category_ids=rule.category_ids if rule.category_ids else None,
            cost_center_ids=rule.cost_center_ids if rule.cost_center_ids else None,
        )

        # Check if limit would be exceeded
        total_after = period_spent + claim_amount
        if total_after > rule.limit_amount:
            return self._create_exceeded_result(
                rule, claim_amount, period_spent, period_start, period_end
            )

        return EvaluationResult(
            result=LimitResultType.PASSED,
            message=f"Within {rule.rule_code} limit ({period_spent + claim_amount}/{rule.limit_amount})",
            triggered_rule=rule,
            period_spent=period_spent,
            period_start=period_start,
            period_end=period_end,
        )

    def _create_exceeded_result(
        self,
        rule: ExpenseLimitRule,
        claim_amount: Decimal,
        period_spent: Decimal,
        period_start: Optional[date] = None,
        period_end: Optional[date] = None,
    ) -> EvaluationResult:
        """Create result for exceeded limit based on action type."""
        remaining = rule.limit_amount - period_spent
        excess = claim_amount - remaining

        base_message = (
            f"Exceeds {rule.rule_name} limit. "
            f"Limit: {rule.limit_amount} {rule.currency_code}, "
            f"Already spent: {period_spent}, "
            f"This claim: {claim_amount}, "
            f"Over by: {excess}"
        )

        # Map action type to result type
        if rule.action_type == LimitActionType.BLOCK:
            result_type = LimitResultType.BLOCKED
            message = f"BLOCKED: {base_message}"
        elif rule.action_type == LimitActionType.WARN:
            result_type = LimitResultType.WARNING
            message = f"WARNING: {base_message}"
        elif rule.action_type == LimitActionType.REQUIRE_APPROVAL:
            result_type = LimitResultType.APPROVAL_REQUIRED
            message = f"APPROVAL REQUIRED: {base_message}"
        elif rule.action_type == LimitActionType.REQUIRE_MULTI_APPROVAL:
            result_type = LimitResultType.MULTI_APPROVAL_REQUIRED
            message = f"MULTI-APPROVAL REQUIRED: {base_message}"
        elif rule.action_type == LimitActionType.AUTO_ESCALATE:
            result_type = LimitResultType.ESCALATED
            message = f"ESCALATED: {base_message}"
        else:
            result_type = LimitResultType.BLOCKED
            message = f"BLOCKED (default): {base_message}"

        # Add custom warning message if configured
        if rule.action_config and rule.action_config.get("warning_message"):
            message = f"{rule.action_config['warning_message']} - {message}"

        return EvaluationResult(
            result=result_type,
            message=message,
            triggered_rule=rule,
            period_spent=period_spent,
            period_start=period_start,
            period_end=period_end,
        )

    # =========================================================================
    # Approver Selection
    # =========================================================================

    def get_eligible_approvers(
        self,
        org_id: UUID,
        employee: "Employee",
        amount: Decimal,
    ) -> List[EligibleApprover]:
        """
        Find employees who can approve this amount.

        Selection criteria:
        1. Must have approval authority >= amount
        2. Must not be the same employee (unless can_approve_own_expenses)
        3. Prefer direct manager first
        4. Then by approval limit scope (employee > grade > designation > role)
        """
        from app.models.people.hr.employee import Employee as EmployeeModel

        eligible = []
        seen_ids = set()

        # 1. Check direct manager
        if employee.reports_to_id:
            manager = self.db.get(EmployeeModel, employee.reports_to_id)
            if manager:
                manager_limit = self._get_employee_approval_limit(org_id, manager)
                if manager_limit and manager_limit >= amount:
                    eligible.append(
                        EligibleApprover(
                            employee_id=manager.employee_id,
                            employee_name=f"{manager.first_name} {manager.last_name}",
                            max_approval_amount=manager_limit,
                            is_direct_manager=True,
                            grade_rank=manager.grade.rank if manager.grade else None,
                        )
                    )
                    seen_ids.add(manager.employee_id)

        # 2. Get all approver limits that can approve this amount
        limits = self.db.scalars(
            select(ExpenseApproverLimit).where(
                ExpenseApproverLimit.organization_id == org_id,
                ExpenseApproverLimit.is_active == True,
                ExpenseApproverLimit.max_approval_amount >= amount,
            )
        ).all()

        for limit in limits:
            # Find employees matching this limit scope
            matching_employees = self._find_employees_for_approver_limit(
                org_id, limit, employee
            )
            for emp in matching_employees:
                if emp.employee_id in seen_ids:
                    continue
                # Skip if this is the same employee and can't approve own
                if (
                    emp.employee_id == employee.employee_id
                    and not limit.can_approve_own_expenses
                ):
                    continue

                eligible.append(
                    EligibleApprover(
                        employee_id=emp.employee_id,
                        employee_name=f"{emp.first_name} {emp.last_name}",
                        max_approval_amount=limit.max_approval_amount,
                        is_direct_manager=False,
                        grade_rank=emp.grade.rank if emp.grade else None,
                    )
                )
                seen_ids.add(emp.employee_id)

        # Sort by: direct manager first, then by grade rank (higher first)
        eligible.sort(
            key=lambda x: (
                0 if x.is_direct_manager else 1,
                -(x.grade_rank or 0),
            )
        )

        return eligible

    def _get_employee_approval_limit(
        self, org_id: UUID, employee: "Employee"
    ) -> Optional[Decimal]:
        """Get the maximum approval amount for an employee."""
        # Check employee-specific limit
        emp_limit = self.db.scalar(
            select(ExpenseApproverLimit.max_approval_amount).where(
                ExpenseApproverLimit.organization_id == org_id,
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
                    ExpenseApproverLimit.organization_id == org_id,
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
                    ExpenseApproverLimit.organization_id == org_id,
                    ExpenseApproverLimit.is_active == True,
                    ExpenseApproverLimit.scope_type == "DESIGNATION",
                    ExpenseApproverLimit.scope_id == employee.designation_id,
                )
            )
            if desig_limit:
                return desig_limit

        return None

    def _find_employees_for_approver_limit(
        self,
        org_id: UUID,
        limit: ExpenseApproverLimit,
        requester: "Employee",
    ) -> List["Employee"]:
        """Find employees who have this approver limit."""
        from app.models.people.hr.employee import Employee as EmployeeModel
        from app.models.people.hr.employee import EmployeeStatus

        if limit.scope_type == "EMPLOYEE":
            if limit.scope_id:
                emp = self.db.get(EmployeeModel, limit.scope_id)
                return [emp] if emp else []
            return []

        elif limit.scope_type == "GRADE":
            if limit.scope_id:
                return list(
                    self.db.scalars(
                        select(EmployeeModel)
                        .where(
                            EmployeeModel.organization_id == org_id,
                            EmployeeModel.grade_id == limit.scope_id,
                            EmployeeModel.status == EmployeeStatus.ACTIVE,
                        )
                        .limit(20)
                    ).all()
                )
            return []

        elif limit.scope_type == "DESIGNATION":
            if limit.scope_id:
                return list(
                    self.db.scalars(
                        select(EmployeeModel)
                        .where(
                            EmployeeModel.organization_id == org_id,
                            EmployeeModel.designation_id == limit.scope_id,
                            EmployeeModel.status == EmployeeStatus.ACTIVE,
                        )
                        .limit(20)
                    ).all()
                )
            return []

        return []

    # =========================================================================
    # Evaluations (Audit Trail)
    # =========================================================================

    def list_evaluations(
        self,
        org_id: UUID,
        *,
        claim_id: Optional[UUID] = None,
        rule_id: Optional[UUID] = None,
        result: Optional[LimitResultType] = None,
        from_date: Optional[date] = None,
        to_date: Optional[date] = None,
        pagination: Optional[PaginationParams] = None,
    ) -> PaginatedResult[ExpenseLimitEvaluation]:
        """List expense limit evaluations."""
        query = (
            select(ExpenseLimitEvaluation)
            .where(ExpenseLimitEvaluation.organization_id == org_id)
            .options(joinedload(ExpenseLimitEvaluation.rule))
        )

        if claim_id:
            query = query.where(ExpenseLimitEvaluation.claim_id == claim_id)

        if rule_id:
            query = query.where(ExpenseLimitEvaluation.rule_id == rule_id)

        if result:
            query = query.where(ExpenseLimitEvaluation.result == result)

        if from_date:
            query = query.where(
                func.date(ExpenseLimitEvaluation.evaluated_at) >= from_date
            )

        if to_date:
            query = query.where(
                func.date(ExpenseLimitEvaluation.evaluated_at) <= to_date
            )

        query = query.order_by(ExpenseLimitEvaluation.evaluated_at.desc())

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

    # =========================================================================
    # Usage Summary
    # =========================================================================

    def get_employee_usage_summary(
        self,
        org_id: UUID,
        employee_id: UUID,
    ) -> dict:
        """Get comprehensive usage summary for an employee."""
        from app.models.people.hr.employee import Employee as EmployeeModel

        employee = self.db.get(EmployeeModel, employee_id)
        if not employee:
            return {}

        today = date.today()

        # Current month
        month_start, month_end = self.get_period_bounds(LimitPeriodType.MONTH, today)
        month_claimed, month_count = self.calculate_period_usage(
            org_id, employee_id, LimitPeriodType.MONTH, month_start, month_end
        )

        # Current quarter
        quarter_start, quarter_end = self.get_period_bounds(
            LimitPeriodType.QUARTER, today
        )
        quarter_claimed, _ = self.calculate_period_usage(
            org_id, employee_id, LimitPeriodType.QUARTER, quarter_start, quarter_end
        )

        # Current year
        year_start, year_end = self.get_period_bounds(LimitPeriodType.YEAR, today)
        year_claimed, _ = self.calculate_period_usage(
            org_id, employee_id, LimitPeriodType.YEAR, year_start, year_end
        )

        # Pending claims
        pending = self.db.execute(
            select(
                func.count(ExpenseClaim.claim_id),
                func.coalesce(
                    func.sum(ExpenseClaim.total_claimed_amount), Decimal("0")
                ),
            ).where(
                ExpenseClaim.organization_id == org_id,
                ExpenseClaim.employee_id == employee_id,
                ExpenseClaim.status.in_(
                    [
                        ExpenseClaimStatus.SUBMITTED,
                        ExpenseClaimStatus.PENDING_APPROVAL,
                    ]
                ),
            )
        ).one()

        # Applicable limits
        rules = self.get_applicable_rules(org_id, employee, today)
        rule_briefs = [
            {
                "rule_id": str(r.rule_id),
                "rule_code": r.rule_code,
                "rule_name": r.rule_name,
                "limit_amount": float(r.limit_amount),
                "action_type": r.action_type.value,
            }
            for r in rules
        ]

        return {
            "employee_id": str(employee_id),
            "employee_name": f"{employee.first_name} {employee.last_name}",
            "current_month_claimed": float(month_claimed),
            "current_month_claim_count": month_count,
            "current_quarter_claimed": float(quarter_claimed),
            "current_year_claimed": float(year_claimed),
            "pending_claims_count": pending[0],
            "pending_claims_amount": float(pending[1]),
            "applicable_limits": rule_briefs,
        }
