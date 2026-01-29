"""Add payroll GL account settings to organization and journal_entry_id to payroll_entry.

Revision ID: 20260128_payroll_gl
Revises:
Create Date: 2026-01-28
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260128_payroll_gl"
down_revision = "20260127_add_employee_extended_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add payroll GL account columns to organization
    op.add_column(
        "organization",
        sa.Column(
            "salaries_expense_account_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="Expense account for total gross salary (debit)",
        ),
        schema="core_org",
    )
    op.add_column(
        "organization",
        sa.Column(
            "salary_payable_account_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="Payable account for net salary owed to employees (credit)",
        ),
        schema="core_org",
    )

    # Add foreign key constraints
    op.create_foreign_key(
        "fk_org_salaries_expense_account",
        "organization",
        "account",
        ["salaries_expense_account_id"],
        ["account_id"],
        source_schema="core_org",
        referent_schema="gl",
    )
    op.create_foreign_key(
        "fk_org_salary_payable_account",
        "organization",
        "account",
        ["salary_payable_account_id"],
        ["account_id"],
        source_schema="core_org",
        referent_schema="gl",
    )

    # Add journal_entry_id to payroll_entry for consolidated GL posting
    op.add_column(
        "payroll_entry",
        sa.Column(
            "journal_entry_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="Consolidated GL entry for entire payroll run",
        ),
        schema="payroll",
    )
    op.create_foreign_key(
        "fk_payroll_entry_journal",
        "payroll_entry",
        "journal_entry",
        ["journal_entry_id"],
        ["journal_entry_id"],
        source_schema="payroll",
        referent_schema="gl",
    )


def downgrade() -> None:
    # Remove journal_entry_id from payroll_entry
    op.drop_constraint(
        "fk_payroll_entry_journal",
        "payroll_entry",
        schema="payroll",
        type_="foreignkey",
    )
    op.drop_column("payroll_entry", "journal_entry_id", schema="payroll")

    # Remove payroll GL account columns from organization
    op.drop_constraint(
        "fk_org_salary_payable_account",
        "organization",
        schema="core_org",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_org_salaries_expense_account",
        "organization",
        schema="core_org",
        type_="foreignkey",
    )
    op.drop_column("organization", "salary_payable_account_id", schema="core_org")
    op.drop_column("organization", "salaries_expense_account_id", schema="core_org")
