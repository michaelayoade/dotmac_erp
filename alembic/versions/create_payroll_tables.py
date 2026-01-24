"""Create Payroll tables for DotMac People Ops.

Revision ID: create_payroll_tables
Revises: add_hr_rls_policies
Create Date: 2025-01-20

This migration creates the core payroll tables:
- salary_component: Earnings and deductions with GL account mappings
- salary_structure: Pay structure templates
- salary_structure_earning: Earning lines in structures
- salary_structure_deduction: Deduction lines in structures
- salary_structure_assignment: Employee-structure assignments
- payroll_entry: Bulk payroll runs
- salary_slip: Employee payslips
- salary_slip_earning: Earning lines on slips
- salary_slip_deduction: Deduction lines on slips
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "create_payroll_tables"
down_revision = "add_hr_rls_policies"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ========================================
    # salary_component table
    # ========================================
    op.create_table(
        "salary_component",
        sa.Column("component_id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("component_code", sa.String(30), nullable=False),
        sa.Column("component_name", sa.String(100), nullable=False),
        sa.Column("abbr", sa.String(20), nullable=True),
        sa.Column("component_type", sa.Enum("EARNING", "DEDUCTION", name="salary_component_type"), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("expense_account_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("liability_account_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_tax_applicable", sa.Boolean(), default=False),
        sa.Column("is_statutory", sa.Boolean(), default=False),
        sa.Column("exempted_from_income_tax", sa.Boolean(), default=False),
        sa.Column("depends_on_payment_days", sa.Boolean(), default=True),
        sa.Column("statistical_component", sa.Boolean(), default=False),
        sa.Column("do_not_include_in_total", sa.Boolean(), default=False),
        sa.Column("is_active", sa.Boolean(), default=True),
        sa.Column("display_order", sa.Integer(), default=0),
        sa.Column("erpnext_id", sa.String(255), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("component_id"),
        sa.ForeignKeyConstraint(["organization_id"], ["core_org.organization.organization_id"]),
        sa.ForeignKeyConstraint(["expense_account_id"], ["gl.account.account_id"]),
        sa.ForeignKeyConstraint(["liability_account_id"], ["gl.account.account_id"]),
        sa.ForeignKeyConstraint(["created_by_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["updated_by_id"], ["people.id"]),
        sa.UniqueConstraint("organization_id", "component_code", name="uq_salary_component_org_code"),
        schema="payroll",
    )
    op.create_index("idx_salary_component_org", "salary_component", ["organization_id"], schema="payroll")
    op.create_index("idx_salary_component_type", "salary_component", ["organization_id", "component_type"], schema="payroll")
    op.create_index("idx_salary_component_erpnext", "salary_component", ["erpnext_id"], schema="payroll")

    # ========================================
    # salary_structure table
    # ========================================
    op.create_table(
        "salary_structure",
        sa.Column("structure_id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("structure_code", sa.String(30), nullable=False),
        sa.Column("structure_name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("payroll_frequency", sa.Enum("WEEKLY", "BIWEEKLY", "SEMIMONTHLY", "MONTHLY", name="payroll_frequency"), default="MONTHLY"),
        sa.Column("currency_code", sa.String(3), default="NGN"),
        sa.Column("is_active", sa.Boolean(), default=True),
        sa.Column("erpnext_id", sa.String(255), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("structure_id"),
        sa.ForeignKeyConstraint(["organization_id"], ["core_org.organization.organization_id"]),
        sa.ForeignKeyConstraint(["created_by_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["updated_by_id"], ["people.id"]),
        sa.UniqueConstraint("organization_id", "structure_code", name="uq_salary_structure_org_code"),
        schema="payroll",
    )
    op.create_index("idx_salary_structure_org", "salary_structure", ["organization_id"], schema="payroll")

    # ========================================
    # salary_structure_earning table
    # ========================================
    op.create_table(
        "salary_structure_earning",
        sa.Column("earning_id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("structure_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("component_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), default=0),
        sa.Column("amount_based_on_formula", sa.Boolean(), default=False),
        sa.Column("formula", sa.Text(), nullable=True),
        sa.Column("condition", sa.Text(), nullable=True),
        sa.Column("display_order", sa.Integer(), default=0),
        sa.PrimaryKeyConstraint("earning_id"),
        sa.ForeignKeyConstraint(["structure_id"], ["payroll.salary_structure.structure_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["component_id"], ["payroll.salary_component.component_id"]),
        schema="payroll",
    )
    op.create_index("idx_struct_earning_struct", "salary_structure_earning", ["structure_id"], schema="payroll")

    # ========================================
    # salary_structure_deduction table
    # ========================================
    op.create_table(
        "salary_structure_deduction",
        sa.Column("deduction_id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("structure_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("component_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), default=0),
        sa.Column("amount_based_on_formula", sa.Boolean(), default=False),
        sa.Column("formula", sa.Text(), nullable=True),
        sa.Column("condition", sa.Text(), nullable=True),
        sa.Column("display_order", sa.Integer(), default=0),
        sa.PrimaryKeyConstraint("deduction_id"),
        sa.ForeignKeyConstraint(["structure_id"], ["payroll.salary_structure.structure_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["component_id"], ["payroll.salary_component.component_id"]),
        schema="payroll",
    )
    op.create_index("idx_struct_deduction_struct", "salary_structure_deduction", ["structure_id"], schema="payroll")

    # ========================================
    # salary_structure_assignment table
    # ========================================
    op.create_table(
        "salary_structure_assignment",
        sa.Column("assignment_id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("employee_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("structure_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_date", sa.Date(), nullable=False),
        sa.Column("to_date", sa.Date(), nullable=True),
        sa.Column("base", sa.Numeric(18, 2), default=0),
        sa.Column("variable", sa.Numeric(18, 2), default=0),
        sa.Column("income_tax_slab", sa.String(100), nullable=True),
        sa.Column("erpnext_id", sa.String(255), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("assignment_id"),
        sa.ForeignKeyConstraint(["organization_id"], ["core_org.organization.organization_id"]),
        sa.ForeignKeyConstraint(["employee_id"], ["hr.employee.employee_id"]),
        sa.ForeignKeyConstraint(["structure_id"], ["payroll.salary_structure.structure_id"]),
        sa.ForeignKeyConstraint(["created_by_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["updated_by_id"], ["people.id"]),
        schema="payroll",
    )
    op.create_index("idx_ssa_emp_date", "salary_structure_assignment", ["employee_id", "from_date"], schema="payroll")
    op.create_index("idx_ssa_org_emp", "salary_structure_assignment", ["organization_id", "employee_id"], schema="payroll")

    # ========================================
    # payroll_entry table
    # ========================================
    op.create_table(
        "payroll_entry",
        sa.Column("entry_id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entry_number", sa.String(30), nullable=False),
        sa.Column("posting_date", sa.Date(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("payroll_frequency", sa.Enum("WEEKLY", "BIWEEKLY", "SEMIMONTHLY", "MONTHLY", name="payroll_frequency", create_type=False)),
        sa.Column("currency_code", sa.String(3), default="NGN"),
        sa.Column("exchange_rate", sa.Numeric(18, 6), default=1),
        sa.Column("department_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("designation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("total_gross_pay", sa.Numeric(18, 2), default=0),
        sa.Column("total_deductions", sa.Numeric(18, 2), default=0),
        sa.Column("total_net_pay", sa.Numeric(18, 2), default=0),
        sa.Column("employee_count", sa.Integer(), default=0),
        sa.Column("status", sa.Enum("DRAFT", "SLIPS_CREATED", "SUBMITTED", "APPROVED", "POSTED", "CANCELLED", name="payroll_entry_status")),
        sa.Column("salary_slips_created", sa.Boolean(), default=False),
        sa.Column("salary_slips_submitted", sa.Boolean(), default=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("erpnext_id", sa.String(255), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status_changed_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("entry_id"),
        sa.ForeignKeyConstraint(["organization_id"], ["core_org.organization.organization_id"]),
        sa.ForeignKeyConstraint(["department_id"], ["hr.department.department_id"]),
        sa.ForeignKeyConstraint(["designation_id"], ["hr.designation.designation_id"]),
        sa.ForeignKeyConstraint(["created_by_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["updated_by_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["status_changed_by_id"], ["people.id"]),
        schema="payroll",
    )
    op.create_index("idx_payroll_entry_org", "payroll_entry", ["organization_id"], schema="payroll")
    op.create_index("idx_payroll_entry_period", "payroll_entry", ["organization_id", "start_date", "end_date"], schema="payroll")

    # ========================================
    # salary_slip table
    # ========================================
    op.create_table(
        "salary_slip",
        sa.Column("slip_id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("slip_number", sa.String(30), nullable=False),
        sa.Column("employee_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("employee_name", sa.String(200), nullable=True),
        sa.Column("structure_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("posting_date", sa.Date(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("currency_code", sa.String(3), default="NGN"),
        sa.Column("exchange_rate", sa.Numeric(18, 6), default=1),
        sa.Column("total_working_days", sa.Numeric(8, 2), default=0),
        sa.Column("absent_days", sa.Numeric(8, 2), default=0),
        sa.Column("payment_days", sa.Numeric(8, 2), default=0),
        sa.Column("leave_without_pay", sa.Numeric(8, 2), default=0),
        sa.Column("gross_pay", sa.Numeric(18, 2), default=0),
        sa.Column("total_deduction", sa.Numeric(18, 2), default=0),
        sa.Column("net_pay", sa.Numeric(18, 2), default=0),
        sa.Column("gross_pay_functional", sa.Numeric(18, 2), default=0),
        sa.Column("total_deduction_functional", sa.Numeric(18, 2), default=0),
        sa.Column("net_pay_functional", sa.Numeric(18, 2), default=0),
        sa.Column("cost_center_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.Enum("DRAFT", "SUBMITTED", "APPROVED", "POSTED", "PAID", "CANCELLED", name="salary_slip_status")),
        sa.Column("journal_entry_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("posted_at", sa.DateTime(), nullable=True),
        sa.Column("posted_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(), nullable=True),
        sa.Column("paid_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("payment_reference", sa.String(100), nullable=True),
        sa.Column("bank_name", sa.String(100), nullable=True),
        sa.Column("bank_account_number", sa.String(30), nullable=True),
        sa.Column("bank_account_name", sa.String(100), nullable=True),
        sa.Column("payroll_entry_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("erpnext_id", sa.String(255), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status_changed_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("slip_id"),
        sa.ForeignKeyConstraint(["organization_id"], ["core_org.organization.organization_id"]),
        sa.ForeignKeyConstraint(["employee_id"], ["hr.employee.employee_id"]),
        sa.ForeignKeyConstraint(["structure_id"], ["payroll.salary_structure.structure_id"]),
        sa.ForeignKeyConstraint(["cost_center_id"], ["core_org.cost_center.cost_center_id"]),
        sa.ForeignKeyConstraint(["journal_entry_id"], ["gl.journal_entry.journal_entry_id"]),
        sa.ForeignKeyConstraint(["payroll_entry_id"], ["payroll.payroll_entry.entry_id"]),
        sa.ForeignKeyConstraint(["created_by_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["updated_by_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["posted_by_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["paid_by_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["status_changed_by_id"], ["people.id"]),
        sa.UniqueConstraint("organization_id", "employee_id", "start_date", "end_date", name="uq_salary_slip_emp_period"),
        schema="payroll",
    )
    op.create_index("idx_salary_slip_org", "salary_slip", ["organization_id"], schema="payroll")
    op.create_index("idx_salary_slip_emp", "salary_slip", ["employee_id"], schema="payroll")
    op.create_index("idx_salary_slip_period", "salary_slip", ["organization_id", "start_date", "end_date"], schema="payroll")
    op.create_index("idx_salary_slip_status", "salary_slip", ["organization_id", "status"], schema="payroll")

    # ========================================
    # salary_slip_earning table
    # ========================================
    op.create_table(
        "salary_slip_earning",
        sa.Column("line_id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("slip_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("component_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("component_name", sa.String(100), nullable=False),
        sa.Column("abbr", sa.String(20), nullable=True),
        sa.Column("amount", sa.Numeric(18, 2), default=0),
        sa.Column("default_amount", sa.Numeric(18, 2), default=0),
        sa.Column("additional_amount", sa.Numeric(18, 2), default=0),
        sa.Column("year_to_date", sa.Numeric(18, 2), default=0),
        sa.Column("statistical_component", sa.Boolean(), default=False),
        sa.Column("do_not_include_in_total", sa.Boolean(), default=False),
        sa.Column("display_order", sa.Integer(), default=0),
        sa.PrimaryKeyConstraint("line_id"),
        sa.ForeignKeyConstraint(["slip_id"], ["payroll.salary_slip.slip_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["component_id"], ["payroll.salary_component.component_id"]),
        schema="payroll",
    )
    op.create_index("idx_slip_earning_slip", "salary_slip_earning", ["slip_id"], schema="payroll")

    # ========================================
    # salary_slip_deduction table
    # ========================================
    op.create_table(
        "salary_slip_deduction",
        sa.Column("line_id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("slip_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("component_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("component_name", sa.String(100), nullable=False),
        sa.Column("abbr", sa.String(20), nullable=True),
        sa.Column("amount", sa.Numeric(18, 2), default=0),
        sa.Column("default_amount", sa.Numeric(18, 2), default=0),
        sa.Column("additional_amount", sa.Numeric(18, 2), default=0),
        sa.Column("year_to_date", sa.Numeric(18, 2), default=0),
        sa.Column("statistical_component", sa.Boolean(), default=False),
        sa.Column("do_not_include_in_total", sa.Boolean(), default=False),
        sa.Column("display_order", sa.Integer(), default=0),
        sa.PrimaryKeyConstraint("line_id"),
        sa.ForeignKeyConstraint(["slip_id"], ["payroll.salary_slip.slip_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["component_id"], ["payroll.salary_component.component_id"]),
        schema="payroll",
    )
    op.create_index("idx_slip_deduction_slip", "salary_slip_deduction", ["slip_id"], schema="payroll")

    # ========================================
    # RLS Policies for Payroll tables
    # ========================================
    # Enable RLS on all payroll tables
    payroll_tables = [
        "salary_component",
        "salary_structure",
        "salary_structure_assignment",
        "payroll_entry",
        "salary_slip",
    ]

    for table in payroll_tables:
        op.execute(f"ALTER TABLE payroll.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY {table}_tenant_isolation ON payroll.{table}
            USING (organization_id::text = current_setting('app.current_organization_id', true))
        """)


def downgrade() -> None:
    # Drop RLS policies
    payroll_tables = [
        "salary_slip",
        "payroll_entry",
        "salary_structure_assignment",
        "salary_structure",
        "salary_component",
    ]

    for table in payroll_tables:
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON payroll.{table}")
        op.execute(f"ALTER TABLE payroll.{table} DISABLE ROW LEVEL SECURITY")

    # Drop tables in reverse dependency order
    op.drop_table("salary_slip_deduction", schema="payroll")
    op.drop_table("salary_slip_earning", schema="payroll")
    op.drop_table("salary_slip", schema="payroll")
    op.drop_table("payroll_entry", schema="payroll")
    op.drop_table("salary_structure_assignment", schema="payroll")
    op.drop_table("salary_structure_deduction", schema="payroll")
    op.drop_table("salary_structure_earning", schema="payroll")
    op.drop_table("salary_structure", schema="payroll")
    op.drop_table("salary_component", schema="payroll")

    # Drop enums
    op.execute("DROP TYPE IF EXISTS salary_slip_status")
    op.execute("DROP TYPE IF EXISTS payroll_entry_status")
    op.execute("DROP TYPE IF EXISTS payroll_frequency")
    op.execute("DROP TYPE IF EXISTS salary_component_type")
