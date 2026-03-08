"""Add composite indexes on posted_ledger_line for dashboard queries.

Revision ID: 20260307_pll_idx
Revises: 20260304_add_vat_exempt_supplier_tax
Create Date: 2026-03-07
"""

from alembic import op

revision = "20260307_pll_idx"
down_revision = "20260226_add_payroll_entry_employment_type_filter"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_pll_org_posting_date "
        "ON gl.posted_ledger_line (organization_id, posting_date)"
    )
    op.execute(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_pll_org_account_date "
        "ON gl.posted_ledger_line (organization_id, account_id, posting_date)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS gl.idx_pll_org_posting_date")
    op.execute("DROP INDEX IF EXISTS gl.idx_pll_org_account_date")
