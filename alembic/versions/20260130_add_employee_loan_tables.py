"""Add employee loan tables for payroll deductions.

Creates tables:
- payroll.loan_type - Loan type configuration
- payroll.employee_loan - Active employee loans
- payroll.loan_repayment - Repayment transactions
- payroll.salary_slip_loan_deduction - Link table

Revision ID: 20260130_add_employee_loans
Revises: 20260130_add_settings_org_scope
Create Date: 2026-01-30
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260130_add_employee_loans"
down_revision = "20260130_add_settings_org_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create enum types (idempotent)
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE loan_category AS ENUM (
                'SALARY_ADVANCE', 'PERSONAL_LOAN', 'EQUIPMENT_LOAN',
                'EMERGENCY_LOAN', 'HOUSING_LOAN', 'EDUCATION_LOAN'
            );
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE interest_method AS ENUM (
                'NONE', 'FLAT', 'REDUCING_BALANCE'
            );
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE loan_status AS ENUM (
                'DRAFT', 'PENDING', 'APPROVED', 'DISBURSED',
                'COMPLETED', 'WRITTEN_OFF', 'CANCELLED', 'REJECTED'
            );
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE repayment_type AS ENUM (
                'PAYROLL_DEDUCTION', 'MANUAL_PAYMENT', 'PREPAYMENT', 'WRITE_OFF'
            );
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
        """
    )

    # Create loan_type table
    op.create_table(
        "loan_type",
        sa.Column("loan_type_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("core_org.organization.organization_id"),
            nullable=False,
        ),
        sa.Column("type_code", sa.String(20), nullable=False),
        sa.Column("type_name", sa.String(100), nullable=False),
        sa.Column(
            "category",
            postgresql.ENUM(
                "SALARY_ADVANCE",
                "PERSONAL_LOAN",
                "EQUIPMENT_LOAN",
                "EMERGENCY_LOAN",
                "HOUSING_LOAN",
                "EDUCATION_LOAN",
                name="loan_category",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("max_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("min_amount", sa.Numeric(18, 2), default=0),
        sa.Column("max_tenure_months", sa.Integer(), default=12),
        sa.Column("min_tenure_months", sa.Integer(), default=1),
        sa.Column(
            "interest_method",
            postgresql.ENUM(
                "NONE",
                "FLAT",
                "REDUCING_BALANCE",
                name="interest_method",
                create_type=False,
            ),
            default="NONE",
        ),
        sa.Column("default_interest_rate", sa.Numeric(5, 2), default=0),
        sa.Column("min_service_months", sa.Integer(), default=0),
        sa.Column("requires_approval", sa.Boolean(), default=True),
        sa.Column(
            "loan_receivable_account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("gl.account.account_id"),
            nullable=True,
        ),
        sa.Column(
            "loan_disbursement_account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("gl.account.account_id"),
            nullable=True,
        ),
        sa.Column(
            "interest_income_account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("gl.account.account_id"),
            nullable=True,
        ),
        sa.Column("is_active", sa.Boolean(), default=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("people.id"),
            nullable=True,
        ),
        sa.UniqueConstraint(
            "organization_id", "type_code", name="uq_loan_type_org_code"
        ),
        schema="payroll",
    )
    op.create_index(
        "idx_loan_type_org", "loan_type", ["organization_id"], schema="payroll"
    )

    # Create employee_loan table
    op.create_table(
        "employee_loan",
        sa.Column("loan_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("core_org.organization.organization_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("loan_number", sa.String(30), nullable=False),
        sa.Column(
            "employee_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("hr.employee.employee_id"),
            nullable=False,
        ),
        sa.Column(
            "loan_type_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("payroll.loan_type.loan_type_id"),
            nullable=False,
        ),
        sa.Column("principal_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("interest_rate", sa.Numeric(5, 2), default=0),
        sa.Column("total_interest", sa.Numeric(18, 2), default=0),
        sa.Column("total_repayable", sa.Numeric(18, 2), nullable=False),
        sa.Column("tenure_months", sa.Integer(), nullable=False),
        sa.Column("monthly_installment", sa.Numeric(18, 2), nullable=False),
        sa.Column("installments_paid", sa.Integer(), default=0),
        sa.Column("principal_paid", sa.Numeric(18, 2), default=0),
        sa.Column("interest_paid", sa.Numeric(18, 2), default=0),
        sa.Column("outstanding_balance", sa.Numeric(18, 2), nullable=False),
        sa.Column("application_date", sa.Date(), nullable=False),
        sa.Column("approval_date", sa.Date(), nullable=True),
        sa.Column("disbursement_date", sa.Date(), nullable=True),
        sa.Column("first_repayment_date", sa.Date(), nullable=True),
        sa.Column("completion_date", sa.Date(), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(
                "DRAFT",
                "PENDING",
                "APPROVED",
                "DISBURSED",
                "COMPLETED",
                "WRITTEN_OFF",
                "CANCELLED",
                "REJECTED",
                name="loan_status",
                create_type=False,
            ),
            default="DRAFT",
        ),
        sa.Column(
            "approved_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("people.id"),
            nullable=True,
        ),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("disbursement_reference", sa.String(100), nullable=True),
        sa.Column(
            "disbursed_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("people.id"),
            nullable=True,
        ),
        sa.Column("purpose", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("people.id"),
            nullable=True,
        ),
        sa.UniqueConstraint(
            "organization_id", "loan_number", name="uq_employee_loan_number"
        ),
        schema="payroll",
    )
    op.create_index(
        "idx_employee_loan_employee", "employee_loan", ["employee_id"], schema="payroll"
    )
    op.create_index(
        "idx_employee_loan_status",
        "employee_loan",
        ["organization_id", "status"],
        schema="payroll",
    )
    op.create_index(
        "idx_employee_loan_active",
        "employee_loan",
        ["employee_id", "status"],
        schema="payroll",
    )

    # Create loan_repayment table
    op.create_table(
        "loan_repayment",
        sa.Column("repayment_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "loan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("payroll.employee_loan.loan_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "repayment_type",
            postgresql.ENUM(
                "PAYROLL_DEDUCTION",
                "MANUAL_PAYMENT",
                "PREPAYMENT",
                "WRITE_OFF",
                name="repayment_type",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("repayment_date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("principal_portion", sa.Numeric(18, 2), nullable=False),
        sa.Column("interest_portion", sa.Numeric(18, 2), default=0),
        sa.Column("balance_after", sa.Numeric(18, 2), nullable=False),
        sa.Column(
            "salary_slip_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("payroll.salary_slip.slip_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("payment_reference", sa.String(100), nullable=True),
        sa.Column("payment_method", sa.String(50), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column(
            "created_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("people.id"),
            nullable=True,
        ),
        schema="payroll",
    )
    op.create_index(
        "idx_loan_repayment_loan", "loan_repayment", ["loan_id"], schema="payroll"
    )
    op.create_index(
        "idx_loan_repayment_slip",
        "loan_repayment",
        ["salary_slip_id"],
        schema="payroll",
    )
    op.create_index(
        "idx_loan_repayment_date",
        "loan_repayment",
        ["repayment_date"],
        schema="payroll",
    )

    # Create salary_slip_loan_deduction link table
    op.create_table(
        "salary_slip_loan_deduction",
        sa.Column("deduction_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "slip_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("payroll.salary_slip.slip_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "loan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("payroll.employee_loan.loan_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("principal_portion", sa.Numeric(18, 2), nullable=False),
        sa.Column("interest_portion", sa.Numeric(18, 2), default=0),
        sa.Column(
            "repayment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("payroll.loan_repayment.repayment_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        schema="payroll",
    )
    op.create_index(
        "idx_slip_loan_slip",
        "salary_slip_loan_deduction",
        ["slip_id"],
        schema="payroll",
    )
    op.create_index(
        "idx_slip_loan_loan",
        "salary_slip_loan_deduction",
        ["loan_id"],
        schema="payroll",
    )


def downgrade() -> None:
    # Drop tables in reverse order
    op.drop_table("salary_slip_loan_deduction", schema="payroll")
    op.drop_table("loan_repayment", schema="payroll")
    op.drop_table("employee_loan", schema="payroll")
    op.drop_table("loan_type", schema="payroll")

    # Drop enum types
    op.execute("DROP TYPE IF EXISTS repayment_type")
    op.execute("DROP TYPE IF EXISTS loan_status")
    op.execute("DROP TYPE IF EXISTS interest_method")
    op.execute("DROP TYPE IF EXISTS loan_category")
