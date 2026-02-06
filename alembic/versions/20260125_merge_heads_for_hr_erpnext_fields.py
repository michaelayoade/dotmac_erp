"""Merge heads before adding ERPNext sync fields to HR assets.

Revision ID: 20260125_merge_heads_for_hr_erpnext_fields
Revises: 20260124_department_head, 20260124_setting_history, 20260124_expense_task_fk, 20260124_rbac_tables, 20260124_ticket_contact
Create Date: 2026-01-25
"""

# revision identifiers, used by Alembic.
revision = "20260125_merge_heads_for_hr_erpnext_fields"
down_revision = (
    "20260124_department_head",
    "20260124_setting_history",
    "20260124_expense_task_fk",
    "20260124_rbac_tables",
    "20260124_ticket_contact",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Merge multiple heads; no schema changes here.
    pass


def downgrade() -> None:
    # No-op downgrade for merge revision.
    pass
