"""add_phase2_fk_indexes

Add critical foreign key indexes to prevent N+1 query patterns.

Phase 2 indexes:
- gl.journal_entry_line: journal_entry_id (FK to journal_entry)
- ar.invoice_line: invoice_id (FK to invoice)
- ap.supplier_invoice_line: invoice_id (FK to supplier_invoice)
- payroll.salary_slip_earning: component_id (FK to salary_component)
- payroll.salary_slip_deduction: component_id (FK to salary_component)
- hr.employee: reports_to_id (FK for manager hierarchy)

Revision ID: 20260202_add_phase2_fk_indexes
Revises: 20260202_add_phase1_performance_indexes
Create Date: 2026-02-02 13:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "20260202_add_phase2_fk_indexes"
down_revision = "20260202_add_phase1_performance_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # --- GL Schema: Journal Entry Line ---
    # Critical for loading journal lines - prevents N+1 when loading journal with lines
    if inspector.has_table("journal_entry_line", schema="gl"):
        indexes = {
            idx["name"]
            for idx in inspector.get_indexes("journal_entry_line", schema="gl")
        }
        if "idx_jel_journal_entry" not in indexes:
            op.create_index(
                "idx_jel_journal_entry",
                "journal_entry_line",
                ["journal_entry_id"],
                schema="gl",
            )

    # --- AR Schema: Invoice Line ---
    # Critical for loading invoice details - prevents N+1 when rendering invoice
    if inspector.has_table("invoice_line", schema="ar"):
        indexes = {
            idx["name"] for idx in inspector.get_indexes("invoice_line", schema="ar")
        }
        if "idx_invoice_line_invoice" not in indexes:
            op.create_index(
                "idx_invoice_line_invoice",
                "invoice_line",
                ["invoice_id"],
                schema="ar",
            )

    # --- AP Schema: Supplier Invoice Line ---
    # Critical for three-way matching and invoice detail loading
    if inspector.has_table("supplier_invoice_line", schema="ap"):
        indexes = {
            idx["name"]
            for idx in inspector.get_indexes("supplier_invoice_line", schema="ap")
        }
        if "idx_supplier_invoice_line_invoice" not in indexes:
            op.create_index(
                "idx_supplier_invoice_line_invoice",
                "supplier_invoice_line",
                ["invoice_id"],
                schema="ap",
            )

    # --- Payroll Schema: Salary Slip Earning ---
    # Speeds up component lookups when rendering payslips
    if inspector.has_table("salary_slip_earning", schema="payroll"):
        indexes = {
            idx["name"]
            for idx in inspector.get_indexes("salary_slip_earning", schema="payroll")
        }
        if "idx_slip_earning_component" not in indexes:
            op.create_index(
                "idx_slip_earning_component",
                "salary_slip_earning",
                ["component_id"],
                schema="payroll",
            )

    # --- Payroll Schema: Salary Slip Deduction ---
    # Speeds up component lookups when rendering payslips
    if inspector.has_table("salary_slip_deduction", schema="payroll"):
        indexes = {
            idx["name"]
            for idx in inspector.get_indexes("salary_slip_deduction", schema="payroll")
        }
        if "idx_slip_deduction_component" not in indexes:
            op.create_index(
                "idx_slip_deduction_component",
                "salary_slip_deduction",
                ["component_id"],
                schema="payroll",
            )

    # --- HR Schema: Employee Manager Hierarchy ---
    # Speeds up org chart queries and direct reports lookups
    # Partial index since most employees have a manager (excludes CEO/top level)
    if inspector.has_table("employee", schema="hr"):
        indexes = {
            idx["name"] for idx in inspector.get_indexes("employee", schema="hr")
        }
        if "idx_employee_reports_to" not in indexes:
            op.create_index(
                "idx_employee_reports_to",
                "employee",
                ["organization_id", "reports_to_id"],
                schema="hr",
                postgresql_where=sa.text("reports_to_id IS NOT NULL"),
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # --- Drop HR Schema Index ---
    if inspector.has_table("employee", schema="hr"):
        indexes = {
            idx["name"] for idx in inspector.get_indexes("employee", schema="hr")
        }
        if "idx_employee_reports_to" in indexes:
            op.drop_index("idx_employee_reports_to", table_name="employee", schema="hr")

    # --- Drop Payroll Schema Indexes ---
    if inspector.has_table("salary_slip_deduction", schema="payroll"):
        indexes = {
            idx["name"]
            for idx in inspector.get_indexes("salary_slip_deduction", schema="payroll")
        }
        if "idx_slip_deduction_component" in indexes:
            op.drop_index(
                "idx_slip_deduction_component",
                table_name="salary_slip_deduction",
                schema="payroll",
            )

    if inspector.has_table("salary_slip_earning", schema="payroll"):
        indexes = {
            idx["name"]
            for idx in inspector.get_indexes("salary_slip_earning", schema="payroll")
        }
        if "idx_slip_earning_component" in indexes:
            op.drop_index(
                "idx_slip_earning_component",
                table_name="salary_slip_earning",
                schema="payroll",
            )

    # --- Drop AP Schema Index ---
    if inspector.has_table("supplier_invoice_line", schema="ap"):
        indexes = {
            idx["name"]
            for idx in inspector.get_indexes("supplier_invoice_line", schema="ap")
        }
        if "idx_supplier_invoice_line_invoice" in indexes:
            op.drop_index(
                "idx_supplier_invoice_line_invoice",
                table_name="supplier_invoice_line",
                schema="ap",
            )

    # --- Drop AR Schema Index ---
    if inspector.has_table("invoice_line", schema="ar"):
        indexes = {
            idx["name"] for idx in inspector.get_indexes("invoice_line", schema="ar")
        }
        if "idx_invoice_line_invoice" in indexes:
            op.drop_index(
                "idx_invoice_line_invoice", table_name="invoice_line", schema="ar"
            )

    # --- Drop GL Schema Index ---
    if inspector.has_table("journal_entry_line", schema="gl"):
        indexes = {
            idx["name"]
            for idx in inspector.get_indexes("journal_entry_line", schema="gl")
        }
        if "idx_jel_journal_entry" in indexes:
            op.drop_index(
                "idx_jel_journal_entry", table_name="journal_entry_line", schema="gl"
            )
