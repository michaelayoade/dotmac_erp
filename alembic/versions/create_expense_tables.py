"""Create expense management tables.

Revision ID: create_expense_tables
Revises: create_performance_tables
Create Date: 2025-01-20

Phase 6: Expense Management tables with AP integration for People module.
"""
from typing import Sequence, Union

from alembic import op
from app.alembic_utils import ensure_enum
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'create_expense_tables'
down_revision: Union[str, None] = 'create_performance_tables'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create expense schema
    op.execute("CREATE SCHEMA IF NOT EXISTS expense")

    # Create enum types
    bind = op.get_bind()
    ensure_enum(
        bind,
        "expense_claim_status",
        "DRAFT",
        "SUBMITTED",
        "PENDING_APPROVAL",
        "APPROVED",
        "REJECTED",
        "PAID",
        "CANCELLED",
        schema="expense",
    )
    ensure_enum(
        bind,
        "cash_advance_status",
        "DRAFT",
        "SUBMITTED",
        "PENDING_APPROVAL",
        "APPROVED",
        "REJECTED",
        "DISBURSED",
        "PARTIALLY_SETTLED",
        "FULLY_SETTLED",
        "REFUNDED",
        "CANCELLED",
        schema="expense",
    )
    ensure_enum(
        bind,
        "card_transaction_status",
        "PENDING",
        "MATCHED",
        "APPROVED",
        "DISPUTED",
        "PERSONAL",
        "CANCELLED",
        schema="expense",
    )

    # ========== EXPENSE CATEGORY ==========
    op.create_table(
        'expense_category',
        sa.Column('category_id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('category_code', sa.String(30), nullable=False),
        sa.Column('category_name', sa.String(200), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('expense_account_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('max_amount_per_claim', sa.Numeric(12, 2), nullable=True),
        sa.Column('requires_receipt', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('erpnext_id', sa.String(255), nullable=True),
        sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('updated_by_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint('category_id'),
        sa.ForeignKeyConstraint(['organization_id'], ['core_org.organization.organization_id']),
        sa.ForeignKeyConstraint(['expense_account_id'], ['gl.account.account_id']),
        sa.ForeignKeyConstraint(['created_by_id'], ['people.id']),
        sa.ForeignKeyConstraint(['updated_by_id'], ['people.id']),
        sa.UniqueConstraint('organization_id', 'category_code', name='uq_expense_category_code'),
        schema='expense'
    )
    op.create_index('idx_expense_category_org', 'expense_category', ['organization_id'], schema='expense')
    op.create_index('idx_expense_category_erpnext', 'expense_category', ['erpnext_id'], schema='expense')

    # ========== CASH ADVANCE (must be before expense_claim due to FK) ==========
    op.create_table(
        'cash_advance',
        sa.Column('advance_id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('advance_number', sa.String(30), nullable=False, unique=True),
        sa.Column('employee_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('request_date', sa.Date(), nullable=False),
        sa.Column('purpose', sa.String(500), nullable=False),
        sa.Column('requested_amount', sa.Numeric(12, 2), nullable=False),
        sa.Column('approved_amount', sa.Numeric(12, 2), nullable=True),
        sa.Column('currency_code', sa.String(3), nullable=False, server_default='NGN'),
        sa.Column('amount_settled', sa.Numeric(12, 2), nullable=False, server_default='0.00'),
        sa.Column('amount_refunded', sa.Numeric(12, 2), nullable=False, server_default='0.00'),
        sa.Column('expected_settlement_date', sa.Date(), nullable=True),
        sa.Column('disbursed_on', sa.Date(), nullable=True),
        sa.Column('settled_on', sa.Date(), nullable=True),
        sa.Column('cost_center_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('advance_account_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('journal_entry_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('status', postgresql.ENUM('DRAFT', 'SUBMITTED', 'PENDING_APPROVAL', 'APPROVED', 'REJECTED', 'DISBURSED', 'PARTIALLY_SETTLED', 'FULLY_SETTLED', 'REFUNDED', 'CANCELLED', name='cash_advance_status', schema='expense', create_type=False), nullable=False, server_default='DRAFT'),
        sa.Column('approver_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('approved_on', sa.Date(), nullable=True),
        sa.Column('rejection_reason', sa.Text(), nullable=True),
        sa.Column('payment_mode', sa.String(30), nullable=True),
        sa.Column('payment_reference', sa.String(100), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('erpnext_id', sa.String(255), nullable=True),
        sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('status_changed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('status_changed_by_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('updated_by_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint('advance_id'),
        sa.ForeignKeyConstraint(['organization_id'], ['core_org.organization.organization_id']),
        sa.ForeignKeyConstraint(['employee_id'], ['hr.employee.employee_id']),
        sa.ForeignKeyConstraint(['approver_id'], ['hr.employee.employee_id']),
        sa.ForeignKeyConstraint(['cost_center_id'], ['core_org.cost_center.cost_center_id']),
        sa.ForeignKeyConstraint(['advance_account_id'], ['gl.account.account_id']),
        sa.ForeignKeyConstraint(['journal_entry_id'], ['gl.journal_entry.journal_entry_id']),
        sa.ForeignKeyConstraint(['status_changed_by_id'], ['people.id']),
        sa.ForeignKeyConstraint(['created_by_id'], ['people.id']),
        sa.ForeignKeyConstraint(['updated_by_id'], ['people.id']),
        schema='expense'
    )
    op.create_index('idx_cash_advance_org', 'cash_advance', ['organization_id'], schema='expense')
    op.create_index('idx_cash_advance_employee', 'cash_advance', ['employee_id'], schema='expense')
    op.create_index('idx_cash_advance_status', 'cash_advance', ['organization_id', 'status'], schema='expense')
    op.create_index('idx_cash_advance_erpnext', 'cash_advance', ['erpnext_id'], schema='expense')

    # ========== EXPENSE CLAIM ==========
    op.create_table(
        'expense_claim',
        sa.Column('claim_id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('claim_number', sa.String(30), nullable=False, unique=True),
        sa.Column('employee_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('claim_date', sa.Date(), nullable=False),
        sa.Column('expense_period_start', sa.Date(), nullable=True),
        sa.Column('expense_period_end', sa.Date(), nullable=True),
        sa.Column('purpose', sa.String(500), nullable=False),
        sa.Column('project_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('total_claimed_amount', sa.Numeric(12, 2), nullable=False, server_default='0.00'),
        sa.Column('total_approved_amount', sa.Numeric(12, 2), nullable=True),
        sa.Column('currency_code', sa.String(3), nullable=False, server_default='NGN'),
        sa.Column('advance_adjusted', sa.Numeric(12, 2), nullable=False, server_default='0.00'),
        sa.Column('cash_advance_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('net_payable_amount', sa.Numeric(12, 2), nullable=True),
        sa.Column('cost_center_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('status', postgresql.ENUM('DRAFT', 'SUBMITTED', 'PENDING_APPROVAL', 'APPROVED', 'REJECTED', 'PAID', 'CANCELLED', name='expense_claim_status', schema='expense', create_type=False), nullable=False, server_default='DRAFT'),
        sa.Column('approver_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('approved_on', sa.Date(), nullable=True),
        sa.Column('rejection_reason', sa.Text(), nullable=True),
        sa.Column('supplier_invoice_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('payment_reference', sa.String(100), nullable=True),
        sa.Column('paid_on', sa.Date(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('erpnext_id', sa.String(255), nullable=True),
        sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('status_changed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('status_changed_by_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('updated_by_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint('claim_id'),
        sa.ForeignKeyConstraint(['organization_id'], ['core_org.organization.organization_id']),
        sa.ForeignKeyConstraint(['employee_id'], ['hr.employee.employee_id']),
        sa.ForeignKeyConstraint(['approver_id'], ['hr.employee.employee_id']),
        sa.ForeignKeyConstraint(['cash_advance_id'], ['expense.cash_advance.advance_id']),
        sa.ForeignKeyConstraint(['cost_center_id'], ['core_org.cost_center.cost_center_id']),
        sa.ForeignKeyConstraint(['supplier_invoice_id'], ['ap.supplier_invoice.invoice_id']),
        sa.ForeignKeyConstraint(['status_changed_by_id'], ['people.id']),
        sa.ForeignKeyConstraint(['created_by_id'], ['people.id']),
        sa.ForeignKeyConstraint(['updated_by_id'], ['people.id']),
        schema='expense'
    )
    op.create_index('idx_expense_claim_org', 'expense_claim', ['organization_id'], schema='expense')
    op.create_index('idx_expense_claim_employee', 'expense_claim', ['employee_id'], schema='expense')
    op.create_index('idx_expense_claim_status', 'expense_claim', ['organization_id', 'status'], schema='expense')
    op.create_index('idx_expense_claim_date', 'expense_claim', ['organization_id', 'claim_date'], schema='expense')
    op.create_index('idx_expense_claim_erpnext', 'expense_claim', ['erpnext_id'], schema='expense')

    # ========== EXPENSE CLAIM ITEM ==========
    op.create_table(
        'expense_claim_item',
        sa.Column('item_id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('claim_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('expense_date', sa.Date(), nullable=False),
        sa.Column('category_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('description', sa.String(500), nullable=False),
        sa.Column('claimed_amount', sa.Numeric(12, 2), nullable=False),
        sa.Column('approved_amount', sa.Numeric(12, 2), nullable=True),
        sa.Column('expense_account_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('cost_center_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('receipt_url', sa.String(500), nullable=True),
        sa.Column('receipt_number', sa.String(50), nullable=True),
        sa.Column('vendor_name', sa.String(200), nullable=True),
        sa.Column('is_travel_expense', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('travel_from', sa.String(200), nullable=True),
        sa.Column('travel_to', sa.String(200), nullable=True),
        sa.Column('distance_km', sa.Numeric(10, 2), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('sequence', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('item_id'),
        sa.ForeignKeyConstraint(['organization_id'], ['core_org.organization.organization_id']),
        sa.ForeignKeyConstraint(['claim_id'], ['expense.expense_claim.claim_id']),
        sa.ForeignKeyConstraint(['category_id'], ['expense.expense_category.category_id']),
        sa.ForeignKeyConstraint(['expense_account_id'], ['gl.account.account_id']),
        sa.ForeignKeyConstraint(['cost_center_id'], ['core_org.cost_center.cost_center_id']),
        schema='expense'
    )
    op.create_index('idx_expense_claim_item_org', 'expense_claim_item', ['organization_id'], schema='expense')
    op.create_index('idx_expense_claim_item_claim', 'expense_claim_item', ['claim_id'], schema='expense')
    op.create_index('idx_expense_claim_item_category', 'expense_claim_item', ['category_id'], schema='expense')

    # ========== CORPORATE CARD ==========
    op.create_table(
        'corporate_card',
        sa.Column('card_id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('card_number_last4', sa.String(4), nullable=False),
        sa.Column('card_name', sa.String(100), nullable=False),
        sa.Column('card_type', sa.String(20), nullable=False),
        sa.Column('issuer', sa.String(100), nullable=True),
        sa.Column('employee_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('assigned_date', sa.Date(), nullable=False),
        sa.Column('expiry_date', sa.Date(), nullable=True),
        sa.Column('credit_limit', sa.Numeric(12, 2), nullable=True),
        sa.Column('single_transaction_limit', sa.Numeric(12, 2), nullable=True),
        sa.Column('monthly_limit', sa.Numeric(12, 2), nullable=True),
        sa.Column('currency_code', sa.String(3), nullable=False, server_default='NGN'),
        sa.Column('liability_account_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('deactivated_on', sa.Date(), nullable=True),
        sa.Column('deactivation_reason', sa.String(200), nullable=True),
        sa.Column('erpnext_id', sa.String(255), nullable=True),
        sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('updated_by_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint('card_id'),
        sa.ForeignKeyConstraint(['organization_id'], ['core_org.organization.organization_id']),
        sa.ForeignKeyConstraint(['employee_id'], ['hr.employee.employee_id']),
        sa.ForeignKeyConstraint(['liability_account_id'], ['gl.account.account_id']),
        sa.ForeignKeyConstraint(['created_by_id'], ['people.id']),
        sa.ForeignKeyConstraint(['updated_by_id'], ['people.id']),
        schema='expense'
    )
    op.create_index('idx_corporate_card_org', 'corporate_card', ['organization_id'], schema='expense')
    op.create_index('idx_corporate_card_employee', 'corporate_card', ['employee_id'], schema='expense')
    op.create_index('idx_corporate_card_erpnext', 'corporate_card', ['erpnext_id'], schema='expense')

    # ========== CARD TRANSACTION ==========
    op.create_table(
        'card_transaction',
        sa.Column('transaction_id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('card_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('transaction_date', sa.Date(), nullable=False),
        sa.Column('posting_date', sa.Date(), nullable=True),
        sa.Column('merchant_name', sa.String(200), nullable=False),
        sa.Column('merchant_category', sa.String(100), nullable=True),
        sa.Column('amount', sa.Numeric(12, 2), nullable=False),
        sa.Column('currency_code', sa.String(3), nullable=False, server_default='NGN'),
        sa.Column('original_currency', sa.String(3), nullable=True),
        sa.Column('original_amount', sa.Numeric(12, 2), nullable=True),
        sa.Column('external_reference', sa.String(100), nullable=True),
        sa.Column('status', postgresql.ENUM('PENDING', 'MATCHED', 'APPROVED', 'DISPUTED', 'PERSONAL', 'CANCELLED', name='card_transaction_status', schema='expense', create_type=False), nullable=False, server_default='PENDING'),
        sa.Column('expense_claim_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('matched_on', sa.Date(), nullable=True),
        sa.Column('is_personal_expense', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('personal_deduction_from_salary', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('updated_by_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint('transaction_id'),
        sa.ForeignKeyConstraint(['organization_id'], ['core_org.organization.organization_id']),
        sa.ForeignKeyConstraint(['card_id'], ['expense.corporate_card.card_id']),
        sa.ForeignKeyConstraint(['expense_claim_id'], ['expense.expense_claim.claim_id']),
        sa.ForeignKeyConstraint(['created_by_id'], ['people.id']),
        sa.ForeignKeyConstraint(['updated_by_id'], ['people.id']),
        schema='expense'
    )
    op.create_index('idx_card_transaction_org', 'card_transaction', ['organization_id'], schema='expense')
    op.create_index('idx_card_transaction_card', 'card_transaction', ['card_id'], schema='expense')
    op.create_index('idx_card_transaction_date', 'card_transaction', ['organization_id', 'transaction_date'], schema='expense')
    op.create_index('idx_card_transaction_status', 'card_transaction', ['organization_id', 'status'], schema='expense')

    # ========== RLS POLICIES ==========
    for table in ['expense_category', 'cash_advance', 'expense_claim', 'expense_claim_item',
                  'corporate_card', 'card_transaction']:
        op.execute(f"ALTER TABLE expense.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY {table}_tenant_isolation ON expense.{table}
            USING (organization_id = current_setting('app.current_organization_id')::uuid)
        """)


def downgrade() -> None:
    # Drop RLS policies
    for table in ['expense_category', 'cash_advance', 'expense_claim', 'expense_claim_item',
                  'corporate_card', 'card_transaction']:
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON expense.{table}")
        op.execute(f"ALTER TABLE expense.{table} DISABLE ROW LEVEL SECURITY")

    # Drop tables in reverse order
    op.drop_table('card_transaction', schema='expense')
    op.drop_table('corporate_card', schema='expense')
    op.drop_table('expense_claim_item', schema='expense')
    op.drop_table('expense_claim', schema='expense')
    op.drop_table('cash_advance', schema='expense')
    op.drop_table('expense_category', schema='expense')

    # Drop enum types
    op.execute("DROP TYPE IF EXISTS expense.card_transaction_status")
    op.execute("DROP TYPE IF EXISTS expense.cash_advance_status")
    op.execute("DROP TYPE IF EXISTS expense.expense_claim_status")

    # Drop schema
    op.execute("DROP SCHEMA IF EXISTS expense CASCADE")
