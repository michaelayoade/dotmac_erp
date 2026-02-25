"""
Expense Limit Enforcement Models - Expense Schema.

Multi-dimensional limit enforcement system for expense claims:
- ExpenseLimitRule: Spending limit rules by scope and period
- ExpenseApproverLimit: Approval authority configuration
- ExpenseLimitEvaluation: Audit trail for limit evaluations
- ExpensePeriodUsage: Usage cache for period-based limits
"""

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.mixins import AuditMixin

if TYPE_CHECKING:
    from app.models.expense.expense_claim import ExpenseClaim
    from app.models.people.hr.employee import Employee


# =============================================================================
# Enums
# =============================================================================


class LimitScopeType(str, enum.Enum):
    """Scope types for expense limits."""

    EMPLOYEE = "EMPLOYEE"
    GRADE = "GRADE"
    DESIGNATION = "DESIGNATION"
    DEPARTMENT = "DEPARTMENT"
    EMPLOYMENT_TYPE = "EMPLOYMENT_TYPE"
    ORGANIZATION = "ORGANIZATION"


class LimitPeriodType(str, enum.Enum):
    """Period types for expense limits."""

    TRANSACTION = "TRANSACTION"  # Per single transaction
    DAY = "DAY"
    WEEK = "WEEK"
    MONTH = "MONTH"
    QUARTER = "QUARTER"
    YEAR = "YEAR"
    CUSTOM = "CUSTOM"  # Custom number of days


class LimitActionType(str, enum.Enum):
    """Actions when expense limit is exceeded."""

    BLOCK = "BLOCK"  # Block submission
    WARN = "WARN"  # Allow with warning
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"  # Require approval
    REQUIRE_MULTI_APPROVAL = "REQUIRE_MULTI_APPROVAL"  # Require multiple approvers
    AUTO_ESCALATE = "AUTO_ESCALATE"  # Auto-route to higher authority


class LimitResultType(str, enum.Enum):
    """Result of limit evaluation."""

    PASSED = "PASSED"  # Within limits
    BLOCKED = "BLOCKED"  # Submission blocked
    WARNING = "WARNING"  # Allowed with warning
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"  # Needs approval
    MULTI_APPROVAL_REQUIRED = "MULTI_APPROVAL_REQUIRED"  # Needs multiple approvals
    ESCALATED = "ESCALATED"  # Escalated to higher authority


# =============================================================================
# Models
# =============================================================================


class ExpenseLimitRule(Base, AuditMixin):
    """
    Expense Limit Rule - spending cap configuration.

    Defines spending limits by scope (employee, grade, department, etc.)
    and period (transaction, day, month, year, etc.).
    """

    __tablename__ = "expense_limit_rule"
    __table_args__ = (
        Index(
            "idx_expense_limit_rule_scope", "organization_id", "scope_type", "scope_id"
        ),
        Index(
            "idx_expense_limit_rule_active",
            "organization_id",
            "is_active",
            "effective_from",
        ),
        Index("idx_expense_limit_rule_priority", "organization_id", "priority"),
        UniqueConstraint(
            "organization_id", "rule_code", name="uq_expense_limit_rule_code"
        ),
        {"schema": "expense"},
    )

    rule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
        index=True,
    )

    # Identification
    rule_code: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="Unique identifier code, e.g., 'GRADE-A-MONTHLY'",
    )
    rule_name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        comment="Human-readable name for the rule",
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Scope - who the rule applies to
    scope_type: Mapped[LimitScopeType] = mapped_column(
        Enum(LimitScopeType, name="limit_scope_type", schema="expense"),
        nullable=False,
    )
    scope_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="ID of the scoped entity (null for ORGANIZATION scope)",
    )

    # Period - time window for cumulative limits
    period_type: Mapped[LimitPeriodType] = mapped_column(
        Enum(LimitPeriodType, name="limit_period_type", schema="expense"),
        nullable=False,
    )
    custom_period_days: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Number of days for CUSTOM period type",
    )

    # Limit amount
    limit_amount: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        nullable=False,
    )
    currency_code: Mapped[str] = mapped_column(
        String(3),
        default="NGN",
    )

    # Action when limit is exceeded
    action_type: Mapped[LimitActionType] = mapped_column(
        Enum(LimitActionType, name="limit_action_type", schema="expense"),
        nullable=False,
    )

    # JSONB for flexible filtering and configuration
    dimension_filters: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        default=dict,
        server_default="{}",
        comment="Filters: {category_ids: [], cost_center_ids: [], project_ids: [], is_cumulative: true}",
    )
    action_config: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        default=dict,
        server_default="{}",
        comment="Action config: {approver_id, escalation_levels, min_approvers, warning_message}",
    )

    # Priority and validity
    priority: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=100,
        comment="Lower = higher priority (evaluated first)",
    )
    effective_from: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )
    effective_to: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
    )

    # Statistics
    evaluation_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        comment="Number of times this rule has been evaluated",
    )
    trigger_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        comment="Number of times limit exceeded",
    )
    block_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        comment="Number of times blocked",
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )

    # Relationships
    evaluations: Mapped[list["ExpenseLimitEvaluation"]] = relationship(
        "ExpenseLimitEvaluation",
        back_populates="rule",
        foreign_keys="ExpenseLimitEvaluation.rule_id",
    )

    @property
    def is_currently_active(self) -> bool:
        """Check if rule is currently active."""
        if not self.is_active:
            return False
        today = date.today()
        if self.effective_from > today:
            return False
        return not (self.effective_to and self.effective_to < today)

    @property
    def is_cumulative(self) -> bool:
        """Check if this is a cumulative limit (not per-transaction)."""
        return self.period_type != LimitPeriodType.TRANSACTION

    @property
    def category_ids(self) -> list[uuid.UUID]:
        """Get category IDs from dimension filters."""
        if not self.dimension_filters:
            return []
        raw = self.dimension_filters.get("category_ids", [])
        return [uuid.UUID(c) if isinstance(c, str) else c for c in raw]

    @property
    def cost_center_ids(self) -> list[uuid.UUID]:
        """Get cost center IDs from dimension filters."""
        if not self.dimension_filters:
            return []
        raw = self.dimension_filters.get("cost_center_ids", [])
        return [uuid.UUID(c) if isinstance(c, str) else c for c in raw]

    def increment_evaluation_count(self) -> None:
        """Increment evaluation count."""
        self.evaluation_count += 1

    def increment_trigger_count(self) -> None:
        """Increment trigger count (limit exceeded)."""
        self.trigger_count += 1

    def increment_block_count(self) -> None:
        """Increment block count."""
        self.block_count += 1

    def __repr__(self) -> str:
        return f"<ExpenseLimitRule {self.rule_code}: {self.limit_amount} {self.currency_code}>"


class ExpenseApproverLimit(Base, AuditMixin):
    """
    Expense Approver Limit - approval authority configuration.

    Defines how much an approver (by employee, grade, or role) can approve.
    """

    __tablename__ = "expense_approver_limit"
    __table_args__ = (
        Index(
            "idx_expense_approver_limit_scope",
            "organization_id",
            "scope_type",
            "scope_id",
        ),
        {"schema": "expense"},
    )

    approver_limit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
        index=True,
    )

    # Scope - who has this approval authority
    scope_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        comment="EMPLOYEE, GRADE, DESIGNATION, or ROLE",
    )
    scope_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Approval limits
    max_approval_amount: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        nullable=False,
    )
    monthly_approval_budget: Mapped[Decimal | None] = mapped_column(
        Numeric(15, 2),
        nullable=True,
        comment="Monthly budget cap for total approvals. NULL = unlimited.",
    )
    weekly_approval_budget: Mapped[Decimal | None] = mapped_column(
        Numeric(15, 2),
        nullable=True,
        comment="Weekly budget cap for total approvals. NULL = unlimited.",
    )
    currency_code: Mapped[str] = mapped_column(
        String(3),
        default="NGN",
    )

    # Dimension restrictions
    dimension_filters: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        default=dict,
        server_default="{}",
        comment="Category/cost center restrictions for this approver",
    )

    # Escalation configuration
    escalate_to_employee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=True,
    )
    escalate_to_grade_min_rank: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Auto-escalate to grade with this minimum rank",
    )
    can_approve_own_expenses: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )

    # Relationships
    escalate_to: Mapped[Optional["Employee"]] = relationship(
        "Employee",
        foreign_keys=[escalate_to_employee_id],
    )

    @property
    def category_ids(self) -> list[uuid.UUID]:
        """Get category IDs from dimension filters."""
        if not self.dimension_filters:
            return []
        raw = self.dimension_filters.get("category_ids", [])
        return [uuid.UUID(c) if isinstance(c, str) else c for c in raw]

    def __repr__(self) -> str:
        return f"<ExpenseApproverLimit {self.scope_type}:{self.scope_id} max={self.max_approval_amount}>"


class ExpenseApproverBudgetAdjustment(Base):
    """
    One-time additive budget adjustment for a specific month.

    Allows increasing (or decreasing) an approver's monthly approval
    budget for a single month without changing the base
    ``monthly_approval_budget`` on the parent limit.

    Effective budget for the month = base budget + additional_amount.
    """

    __tablename__ = "expense_approver_budget_adjustment"
    __table_args__ = (
        UniqueConstraint(
            "approver_limit_id",
            "adjustment_month",
            name="uq_approver_budget_adj_limit_month",
        ),
        Index(
            "idx_approver_budget_adj_org",
            "organization_id",
        ),
        {"schema": "expense"},
    )

    adjustment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    approver_limit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("expense.expense_approver_limit.approver_limit_id"),
        nullable=False,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
    )
    adjustment_month: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        comment="First day of the target month, e.g. 2026-02-01",
    )
    additional_amount: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        nullable=False,
        comment="Additive adjustment to monthly budget (positive = increase)",
    )
    reason: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Audit trail — why this adjustment was made",
    )
    adjusted_by_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("people.id"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Relationships
    approver_limit: Mapped["ExpenseApproverLimit"] = relationship(
        "ExpenseApproverLimit",
        foreign_keys=[approver_limit_id],
    )

    def __repr__(self) -> str:
        return (
            f"<ExpenseApproverBudgetAdjustment"
            f" limit={self.approver_limit_id}"
            f" month={self.adjustment_month}"
            f" amount={self.additional_amount}>"
        )


class ExpenseApproverLimitReset(Base):
    """Manual reset event for weekly approver budget usage."""

    __tablename__ = "expense_approver_limit_reset"
    __table_args__ = (
        Index(
            "idx_approver_limit_reset_lookup",
            "organization_id",
            "approver_id",
            "approver_limit_id",
            "reset_at",
        ),
        {"schema": "expense"},
    )

    reset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
        index=True,
    )
    approver_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=False,
    )
    approver_limit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("expense.expense_approver_limit.approver_limit_id"),
        nullable=False,
    )
    reset_reason: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Reviewer reason for resetting consumed weekly budget.",
    )
    reviewed_by_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("people.id"),
        nullable=False,
    )
    reviewed_from: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )
    reviewed_to: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )
    reviewed_claim_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    reset_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class ExpenseLimitEvaluation(Base):
    """
    Expense Limit Evaluation - audit trail.

    Records each limit evaluation for compliance and troubleshooting.
    """

    __tablename__ = "expense_limit_evaluation"
    __table_args__ = (
        Index("idx_expense_limit_evaluation_claim", "claim_id"),
        Index("idx_expense_limit_evaluation_rule", "rule_id"),
        Index("idx_expense_limit_evaluation_date", "organization_id", "evaluated_at"),
        {"schema": "expense"},
    )

    evaluation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
        index=True,
    )

    # Claim being evaluated
    claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("expense.expense_claim.claim_id"),
        nullable=False,
    )
    claim_amount: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        nullable=False,
    )

    # Period context
    period_spent_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(15, 2),
        nullable=True,
    )
    period_start: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )
    period_end: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    # Rule that triggered (if any)
    rule_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("expense.expense_limit_rule.rule_id"),
        nullable=True,
    )
    rule_code: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    # Result
    result: Mapped[LimitResultType] = mapped_column(
        Enum(LimitResultType, name="limit_result_type", schema="expense"),
        nullable=False,
    )
    result_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Full context as JSONB
    context_data: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        default=dict,
        server_default="{}",
    )

    # Timestamps
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    evaluated_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("people.id"),
        nullable=True,
    )

    # Relationships
    claim: Mapped["ExpenseClaim"] = relationship(
        "ExpenseClaim",
        foreign_keys=[claim_id],
    )
    rule: Mapped[Optional["ExpenseLimitRule"]] = relationship(
        "ExpenseLimitRule",
        back_populates="evaluations",
        foreign_keys=[rule_id],
    )

    def __repr__(self) -> str:
        return (
            f"<ExpenseLimitEvaluation claim={self.claim_id} result={self.result.value}>"
        )


class ExpensePeriodUsage(Base):
    """
    Expense Period Usage - usage cache.

    Caches cumulative expense usage per employee/period for efficient limit checking.
    """

    __tablename__ = "expense_period_usage"
    __table_args__ = (
        Index("idx_expense_period_usage_employee", "employee_id"),
        Index(
            "idx_expense_period_usage_period",
            "organization_id",
            "period_type",
            "period_start",
        ),
        Index(
            "idx_expense_period_usage_lookup",
            "organization_id",
            "employee_id",
            "period_type",
            "period_start",
            "period_end",
        ),
        UniqueConstraint(
            "organization_id",
            "employee_id",
            "period_type",
            "period_start",
            "dimension_type",
            "dimension_id",
            name="uq_expense_period_usage_key",
        ),
        {"schema": "expense"},
    )

    usage_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
        index=True,
    )

    # Employee this usage belongs to
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=False,
    )

    # Period definition
    period_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )
    period_start: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )
    period_end: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    # Dimension (optional filter)
    dimension_type: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
        comment="CATEGORY, COST_CENTER, PROJECT, or NULL for ALL",
    )
    dimension_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Usage amounts
    total_claimed: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        nullable=False,
        default=Decimal("0"),
    )
    total_approved: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        nullable=False,
        default=Decimal("0"),
    )
    claim_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    currency_code: Mapped[str] = mapped_column(
        String(3),
        default="NGN",
    )

    # Cache metadata
    last_calculated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    is_stale: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )

    # Relationships
    employee: Mapped["Employee"] = relationship(
        "Employee",
        foreign_keys=[employee_id],
    )

    def mark_stale(self) -> None:
        """Mark this usage cache as stale."""
        self.is_stale = True

    def refresh(self, claimed: Decimal, approved: Decimal, count: int) -> None:
        """Refresh usage with new values."""
        self.total_claimed = claimed
        self.total_approved = approved
        self.claim_count = count
        self.last_calculated_at = datetime.utcnow()
        self.is_stale = False

    def __repr__(self) -> str:
        return f"<ExpensePeriodUsage emp={self.employee_id} period={self.period_type} claimed={self.total_claimed}>"
