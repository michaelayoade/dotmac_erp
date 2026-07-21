"""Extend employee info change requests for extended-profile self-service.

Revision ID: 20260721_extended_info_changes
Revises: current migration heads
Create Date: 2026-07-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.alembic_utils import ensure_enum

revision: str = "20260721_extended_info_changes"
down_revision: str | tuple[str, ...] = (
    "20260123_add_employee_location_shift_fields",
    "20260124_add_customer_relationships",
    "20260124_department_head",
    "20260124_expense_gl",
    "20260124_material_request",
    "20260124_rbac_tables",
    "20260124_setting_history",
    "20260124_support",
    "20260124_ticket_contact",
    "20260124_transfer_batch",
    "20260125_add_employee_extended_tables",
    "20260125_add_hr_checklist_jobdesc_erpnext_fields",
    "20260127_add_salary_slip_bank_branch_code",
    "20260128_add_organization_slug",
    "20260128_enhance_onboarding",
    "20260128_extend_doc_templates",
    "20260128_scheduling_emp_idx",
    "20260130_add_payroll_number_sequence",
    "20260130_add_payslip_branding_options",
    "20260130_add_scheduling_audit_ids",
    "20260130_payroll_payslip_email_tracking",
    "20260131_add_audit_events_org_id",
    "20260131_demotion",
    "20260131_mandatory_training",
    "20260131_update_email_module_enum",
    "20260203_split_operations_modules",
    "20260204_add_material_request_ticket_id",
    "20260206_add_inv_count_indexes",
    "20260206_add_material_request_cancel_reason",
    "20260207_add_categorization_fields",
    "20260207_add_customer_default_tax_code_id",
    "20260208_add_employee_expense_approver",
    "20260211_optional_statement_number_date",
    "20260213_add_external_ids_to_ar_docs",
    "20260224_add_settingdomain_banking",
    "20260224_add_weekly_approver_budget_and_resets",
    "20260225_add_balance_staleness_and_refresh_queue",
    "20260225_add_server_defaults_for_pks_and_booleans",
    "20260312_add_missing_project_sync_columns",
    "20260323_add_invoice_purpose_columns",
    "20260328_pms_create_new_tables",
    "20260402_add_unique_material_request_crm_id",
    "20260402_pms_absence_evidence",
    "20260403_add_appraisal_template_pms_config",
    "20260410_add_inventory_return_updated_by",
    "20260410_merge_inventory_ar_expense_heads",
    "20260410_remove_inventory_lot_legacy_snapshot",
    "20260410_repair_expense_claim_action_constraints",
    "20260415_add_statement_line_number_uniqueness",
    "20260418_add_posting_batch_journal_entry_id",
    "20260418_harden_gl_posting",
    "20260418_merge_gl_posting_heads",
    "20260424_merge_gl_and_fa_status_heads",
    "20260427_add_contract_sequence_type",
    "20260525_ap_invoice_auto_receipt",
    "20260526_add_fa_gl_recon",
    "20260610_mri_serials",
    "20260614_tax_code_is_fixed_amount",
    "20260616_dotmac_sub_integration",
    "20260707_dept_template_perspective",
    "20260712_encrypt_secret_settings",
    "20260715_dept_discipline_workflow",
    "20260720_federated_identity",
    "2c732d9afaa6",
    "add_attendance_requests_shift_assignments",
    "add_cancel_resubmit_actions",
    "add_flexible_tax_support",
    "add_item_sequence_type",
    "add_paystack_payment_tables",
    "add_scheduler_crontab",
    "add_settingdomain_values",
    "add_sync_tables",
    "ae7bbaefd73d",
    "create_staging_tables",
    "f8c4a2c1e9bf",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    ensure_enum(
        bind,
        "info_change_operation",
        "CREATE",
        "UPDATE",
        schema="hr",
    )
    op.execute("ALTER TYPE hr.info_change_type ADD VALUE IF NOT EXISTS 'QUALIFICATION'")
    op.execute("ALTER TYPE hr.info_change_type ADD VALUE IF NOT EXISTS 'CERTIFICATION'")
    op.execute("ALTER TYPE hr.info_change_type ADD VALUE IF NOT EXISTS 'SKILL'")
    op.execute("ALTER TYPE hr.info_change_type ADD VALUE IF NOT EXISTS 'DEPENDENT'")
    op.execute("ALTER TYPE hr.info_change_type ADD VALUE IF NOT EXISTS 'DOCUMENT'")

    op.add_column(
        "employee_info_change_request",
        sa.Column(
            "operation",
            sa.Enum(
                "CREATE",
                "UPDATE",
                name="info_change_operation",
                schema="hr",
            ),
            nullable=False,
            server_default="UPDATE",
        ),
        schema="hr",
    )
    op.add_column(
        "employee_info_change_request",
        sa.Column("target_record_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema="hr",
    )
    op.add_column(
        "employee_info_change_request",
        sa.Column("pending_document_path", sa.Text(), nullable=True),
        schema="hr",
    )
    op.add_column(
        "employee_info_change_request",
        sa.Column("pending_document_name", sa.Text(), nullable=True),
        schema="hr",
    )
    op.add_column(
        "employee_info_change_request",
        sa.Column("pending_document_size", sa.Integer(), nullable=True),
        schema="hr",
    )
    op.add_column(
        "employee_info_change_request",
        sa.Column("pending_document_mime_type", sa.Text(), nullable=True),
        schema="hr",
    )
    op.add_column(
        "employee_info_change_request",
        sa.Column("pending_document_checksum", sa.Text(), nullable=True),
        schema="hr",
    )
    op.create_index(
        "idx_info_change_request_section_pending",
        "employee_info_change_request",
        ["organization_id", "employee_id", "change_type", "status"],
        unique=False,
        schema="hr",
    )
    op.alter_column(
        "employee_info_change_request",
        "operation",
        server_default=None,
        schema="hr",
    )


def downgrade() -> None:
    op.drop_index(
        "idx_info_change_request_section_pending",
        table_name="employee_info_change_request",
        schema="hr",
    )
    op.drop_column(
        "employee_info_change_request",
        "pending_document_checksum",
        schema="hr",
    )
    op.drop_column(
        "employee_info_change_request",
        "pending_document_mime_type",
        schema="hr",
    )
    op.drop_column(
        "employee_info_change_request",
        "pending_document_size",
        schema="hr",
    )
    op.drop_column(
        "employee_info_change_request",
        "pending_document_name",
        schema="hr",
    )
    op.drop_column(
        "employee_info_change_request",
        "pending_document_path",
        schema="hr",
    )
    op.drop_column(
        "employee_info_change_request",
        "target_record_id",
        schema="hr",
    )
    op.drop_column(
        "employee_info_change_request",
        "operation",
        schema="hr",
    )
