"""Add expense limit enforcement tables.

Revision ID: 20260123_add_expense_limit_tables
Revises: 20260123_fix_annual_rent_nullable
Create Date: 2026-01-23

This migration creates tables for multi-dimensional expense limit enforcement:
- expense_limit_rule: Spending limit rules (by employee, grade, department, etc.)
- expense_approver_limit: Approval authority configuration
- expense_limit_evaluation: Audit trail for limit evaluations
- expense_period_usage: Usage cache for period-based limits
"""

from typing import Sequence, Union

from alembic import op
from app.alembic_utils import ensure_enum
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260123_add_expense_limit_tables"
down_revision: Union[str, None] = "20260123_fix_annual_rent_nullable"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    # ========================================
    # Create enum types
    # ========================================
    ensure_enum(
        bind,
        "limit_scope_type",
        "EMPLOYEE",
        "GRADE",
        "DESIGNATION",
        "DEPARTMENT",
        "EMPLOYMENT_TYPE",
        "ORGANIZATION",
        schema="expense",
    )

    ensure_enum(
        bind,
        "limit_period_type",
        "TRANSACTION",
        "DAY",
        "WEEK",
        "MONTH",
        "QUARTER",
        "YEAR",
        "CUSTOM",
        schema="expense",
    )

    ensure_enum(
        bind,
        "limit_action_type",
        "BLOCK",
        "WARN",
        "REQUIRE_APPROVAL",
        "REQUIRE_MULTI_APPROVAL",
        "AUTO_ESCALATE",
        schema="expense",
    )

    ensure_enum(
        bind,
        "limit_result_type",
        "PASSED",
        "BLOCKED",
        "WARNING",
        "APPROVAL_REQUIRED",
        "MULTI_APPROVAL_REQUIRED",
        "ESCALATED",
        schema="expense",
    )

    # ========================================
    # expense_limit_rule table
    # ========================================
    op.create_table(
        "expense_limit_rule",
        sa.Column(
            "rule_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "rule_code",
            sa.String(50),
            nullable=False,
            comment="Unique identifier code, e.g., 'GRADE-A-MONTHLY'",
        ),
        sa.Column(
            "rule_name",
            sa.String(200),
            nullable=False,
            comment="Human-readable name for the rule",
        ),
        sa.Column(
            "description",
            sa.Text(),
            nullable=True,
            comment="Detailed description of the rule",
        ),
        # Scope - who the rule applies to
        sa.Column(
            "scope_type",
            postgresql.ENUM(
                "EMPLOYEE",
                "GRADE",
                "DESIGNATION",
                "DEPARTMENT",
                "EMPLOYMENT_TYPE",
                "ORGANIZATION",
                name="limit_scope_type",
                schema="expense",
                create_type=False,
            ),
            nullable=False,
            comment="Type of entity this limit applies to",
        ),
        sa.Column(
            "scope_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="ID of the scoped entity (null for ORGANIZATION scope)",
        ),
        # Period - time window for cumulative limits
        sa.Column(
            "period_type",
            postgresql.ENUM(
                "TRANSACTION",
                "DAY",
                "WEEK",
                "MONTH",
                "QUARTER",
                "YEAR",
                "CUSTOM",
                name="limit_period_type",
                schema="expense",
                create_type=False,
            ),
            nullable=False,
            comment="Time period for limit calculation",
        ),
        sa.Column(
            "custom_period_days",
            sa.Integer(),
            nullable=True,
            comment="Number of days for CUSTOM period type",
        ),
        # Limit amount
        sa.Column(
            "limit_amount",
            sa.Numeric(15, 2),
            nullable=False,
            comment="Maximum amount allowed",
        ),
        sa.Column(
            "currency_code",
            sa.String(3),
            nullable=False,
            server_default="NGN",
        ),
        # Action when limit is exceeded
        sa.Column(
            "action_type",
            postgresql.ENUM(
                "BLOCK",
                "WARN",
                "REQUIRE_APPROVAL",
                "REQUIRE_MULTI_APPROVAL",
                "AUTO_ESCALATE",
                name="limit_action_type",
                schema="expense",
                create_type=False,
            ),
            nullable=False,
            comment="Action to take when limit is exceeded",
        ),
        # JSONB for flexible filtering and configuration
        sa.Column(
            "dimension_filters",
            postgresql.JSONB(),
            nullable=True,
            server_default="{}",
            comment="Filters: {category_ids: [], cost_center_ids: [], project_ids: [], is_cumulative: true}",
        ),
        sa.Column(
            "action_config",
            postgresql.JSONB(),
            nullable=True,
            server_default="{}",
            comment="Action config: {approver_id, escalation_levels, min_approvers, warning_message}",
        ),
        # Priority and validity
        sa.Column(
            "priority",
            sa.Integer(),
            nullable=False,
            server_default="100",
            comment="Lower = higher priority (evaluated first)",
        ),
        sa.Column(
            "effective_from",
            sa.Date(),
            nullable=False,
            comment="Rule validity start date",
        ),
        sa.Column(
            "effective_to",
            sa.Date(),
            nullable=True,
            comment="Rule validity end date (null = no end)",
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
        # Statistics
        sa.Column(
            "evaluation_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="Number of times this rule has been evaluated",
        ),
        sa.Column(
            "trigger_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="Number of times this rule has been triggered (limit exceeded)",
        ),
        sa.Column(
            "block_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="Number of times this rule has blocked a claim",
        ),
        # Audit fields
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("rule_id"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["core_org.organization.organization_id"]
        ),
        sa.ForeignKeyConstraint(["created_by_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["updated_by_id"], ["people.id"]),
        sa.UniqueConstraint(
            "organization_id", "rule_code", name="uq_expense_limit_rule_code"
        ),
        schema="expense",
    )

    # Indexes for expense_limit_rule
    op.create_index(
        "idx_expense_limit_rule_org",
        "expense_limit_rule",
        ["organization_id"],
        schema="expense",
    )
    op.create_index(
        "idx_expense_limit_rule_scope",
        "expense_limit_rule",
        ["organization_id", "scope_type", "scope_id"],
        schema="expense",
    )
    op.create_index(
        "idx_expense_limit_rule_active",
        "expense_limit_rule",
        ["organization_id", "is_active", "effective_from"],
        schema="expense",
    )
    op.create_index(
        "idx_expense_limit_rule_priority",
        "expense_limit_rule",
        ["organization_id", "priority"],
        schema="expense",
    )

    # ========================================
    # expense_approver_limit table
    # ========================================
    op.create_table(
        "expense_approver_limit",
        sa.Column(
            "approver_limit_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        # Scope - who has this approval authority
        sa.Column(
            "scope_type",
            sa.String(30),
            nullable=False,
            comment="EMPLOYEE, GRADE, DESIGNATION, or ROLE",
        ),
        sa.Column(
            "scope_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="ID of the entity with approval authority",
        ),
        # Approval limits
        sa.Column(
            "max_approval_amount",
            sa.Numeric(15, 2),
            nullable=False,
            comment="Maximum amount this approver can approve",
        ),
        sa.Column(
            "currency_code",
            sa.String(3),
            nullable=False,
            server_default="NGN",
        ),
        # Dimension restrictions
        sa.Column(
            "dimension_filters",
            postgresql.JSONB(),
            nullable=True,
            server_default="{}",
            comment="Category/cost center restrictions for this approver",
        ),
        # Escalation configuration
        sa.Column(
            "escalate_to_employee_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="Explicit escalation target",
        ),
        sa.Column(
            "escalate_to_grade_min_rank",
            sa.Integer(),
            nullable=True,
            comment="Auto-escalate to grade with this minimum rank",
        ),
        sa.Column(
            "can_approve_own_expenses",
            sa.Boolean(),
            nullable=False,
            server_default="false",
            comment="Whether approver can approve their own expenses",
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
        # Audit fields
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("approver_limit_id"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["core_org.organization.organization_id"]
        ),
        sa.ForeignKeyConstraint(
            ["escalate_to_employee_id"], ["hr.employee.employee_id"]
        ),
        sa.ForeignKeyConstraint(["created_by_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["updated_by_id"], ["people.id"]),
        schema="expense",
    )

    # Indexes for expense_approver_limit
    op.create_index(
        "idx_expense_approver_limit_org",
        "expense_approver_limit",
        ["organization_id"],
        schema="expense",
    )
    op.create_index(
        "idx_expense_approver_limit_scope",
        "expense_approver_limit",
        ["organization_id", "scope_type", "scope_id"],
        schema="expense",
    )

    # ========================================
    # expense_limit_evaluation table
    # ========================================
    op.create_table(
        "expense_limit_evaluation",
        sa.Column(
            "evaluation_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        # Claim being evaluated
        sa.Column(
            "claim_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="FK to expense_claim being evaluated",
        ),
        sa.Column(
            "claim_amount",
            sa.Numeric(15, 2),
            nullable=False,
            comment="Amount being evaluated",
        ),
        # Period context
        sa.Column(
            "period_spent_amount",
            sa.Numeric(15, 2),
            nullable=True,
            comment="Amount already spent in the period",
        ),
        sa.Column(
            "period_start",
            sa.Date(),
            nullable=True,
            comment="Start of evaluation period",
        ),
        sa.Column(
            "period_end",
            sa.Date(),
            nullable=True,
            comment="End of evaluation period",
        ),
        # Rule that triggered (if any)
        sa.Column(
            "rule_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="Rule that triggered this evaluation result",
        ),
        sa.Column(
            "rule_code",
            sa.String(50),
            nullable=True,
            comment="Denormalized rule code for reference",
        ),
        # Result
        sa.Column(
            "result",
            postgresql.ENUM(
                "PASSED",
                "BLOCKED",
                "WARNING",
                "APPROVAL_REQUIRED",
                "MULTI_APPROVAL_REQUIRED",
                "ESCALATED",
                name="limit_result_type",
                schema="expense",
                create_type=False,
            ),
            nullable=False,
            comment="Evaluation result",
        ),
        sa.Column(
            "result_message",
            sa.Text(),
            nullable=True,
            comment="Human-readable result message",
        ),
        # Full context as JSONB
        sa.Column(
            "context_data",
            postgresql.JSONB(),
            nullable=True,
            server_default="{}",
            comment="Full evaluation context: {employee_id, grade_id, rules_evaluated, etc.}",
        ),
        # Timestamps
        sa.Column(
            "evaluated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("evaluated_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("evaluation_id"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["core_org.organization.organization_id"]
        ),
        sa.ForeignKeyConstraint(["claim_id"], ["expense.expense_claim.claim_id"]),
        sa.ForeignKeyConstraint(["rule_id"], ["expense.expense_limit_rule.rule_id"]),
        sa.ForeignKeyConstraint(["evaluated_by_id"], ["people.id"]),
        schema="expense",
    )

    # Indexes for expense_limit_evaluation
    op.create_index(
        "idx_expense_limit_evaluation_org",
        "expense_limit_evaluation",
        ["organization_id"],
        schema="expense",
    )
    op.create_index(
        "idx_expense_limit_evaluation_claim",
        "expense_limit_evaluation",
        ["claim_id"],
        schema="expense",
    )
    op.create_index(
        "idx_expense_limit_evaluation_rule",
        "expense_limit_evaluation",
        ["rule_id"],
        schema="expense",
    )
    op.create_index(
        "idx_expense_limit_evaluation_date",
        "expense_limit_evaluation",
        ["organization_id", "evaluated_at"],
        schema="expense",
    )

    # ========================================
    # expense_period_usage table
    # ========================================
    op.create_table(
        "expense_period_usage",
        sa.Column(
            "usage_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        # Employee this usage belongs to
        sa.Column(
            "employee_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="FK to employee",
        ),
        # Period definition
        sa.Column(
            "period_type",
            sa.String(20),
            nullable=False,
            comment="DAY, WEEK, MONTH, QUARTER, YEAR",
        ),
        sa.Column(
            "period_start",
            sa.Date(),
            nullable=False,
            comment="Start of period",
        ),
        sa.Column(
            "period_end",
            sa.Date(),
            nullable=False,
            comment="End of period",
        ),
        # Dimension (optional filter)
        sa.Column(
            "dimension_type",
            sa.String(30),
            nullable=True,
            comment="CATEGORY, COST_CENTER, PROJECT, or NULL for ALL",
        ),
        sa.Column(
            "dimension_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="ID of the dimension filter",
        ),
        # Usage amounts
        sa.Column(
            "total_claimed",
            sa.Numeric(15, 2),
            nullable=False,
            server_default="0",
            comment="Cumulative claimed amount in period",
        ),
        sa.Column(
            "total_approved",
            sa.Numeric(15, 2),
            nullable=False,
            server_default="0",
            comment="Cumulative approved amount in period",
        ),
        sa.Column(
            "claim_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="Number of claims in period",
        ),
        sa.Column(
            "currency_code",
            sa.String(3),
            nullable=False,
            server_default="NGN",
        ),
        # Cache metadata
        sa.Column(
            "last_calculated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
            comment="When this usage was last calculated",
        ),
        sa.Column(
            "is_stale",
            sa.Boolean(),
            nullable=False,
            server_default="false",
            comment="Flag to indicate cache needs refresh",
        ),
        sa.PrimaryKeyConstraint("usage_id"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["core_org.organization.organization_id"]
        ),
        sa.ForeignKeyConstraint(["employee_id"], ["hr.employee.employee_id"]),
        sa.UniqueConstraint(
            "organization_id",
            "employee_id",
            "period_type",
            "period_start",
            "dimension_type",
            "dimension_id",
            name="uq_expense_period_usage_key",
        ),
        schema="expense",
    )

    # Indexes for expense_period_usage
    op.create_index(
        "idx_expense_period_usage_org",
        "expense_period_usage",
        ["organization_id"],
        schema="expense",
    )
    op.create_index(
        "idx_expense_period_usage_employee",
        "expense_period_usage",
        ["employee_id"],
        schema="expense",
    )
    op.create_index(
        "idx_expense_period_usage_period",
        "expense_period_usage",
        ["organization_id", "period_type", "period_start"],
        schema="expense",
    )
    op.create_index(
        "idx_expense_period_usage_lookup",
        "expense_period_usage",
        ["organization_id", "employee_id", "period_type", "period_start", "period_end"],
        schema="expense",
    )

    # ========================================
    # RLS Policies
    # ========================================
    limit_tables = [
        "expense_limit_rule",
        "expense_approver_limit",
        "expense_limit_evaluation",
        "expense_period_usage",
    ]

    for table in limit_tables:
        op.execute(f"ALTER TABLE expense.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_tenant_isolation ON expense.{table}
            USING (organization_id::text = current_setting('app.current_organization_id', true))
        """
        )


def downgrade() -> None:
    # Drop RLS policies
    limit_tables = [
        "expense_period_usage",
        "expense_limit_evaluation",
        "expense_approver_limit",
        "expense_limit_rule",
    ]

    for table in limit_tables:
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON expense.{table}")
        op.execute(f"ALTER TABLE expense.{table} DISABLE ROW LEVEL SECURITY")

    # Drop tables in reverse order (child tables first)
    op.drop_table("expense_period_usage", schema="expense")
    op.drop_table("expense_limit_evaluation", schema="expense")
    op.drop_table("expense_approver_limit", schema="expense")
    op.drop_table("expense_limit_rule", schema="expense")

    # Drop enum types
    op.execute("DROP TYPE IF EXISTS expense.limit_result_type")
    op.execute("DROP TYPE IF EXISTS expense.limit_action_type")
    op.execute("DROP TYPE IF EXISTS expense.limit_period_type")
    op.execute("DROP TYPE IF EXISTS expense.limit_scope_type")
