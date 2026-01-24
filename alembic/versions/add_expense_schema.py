"""Add expense schema and tables.

Revision ID: add_expense_schema
Revises: add_tax_schema_tables
Create Date: 2025-02-04
"""

from alembic import op
from app.alembic_utils import ensure_enum

# revision identifiers, used by Alembic.
revision = "add_expense_schema"
down_revision = "add_tax_schema_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    ensure_enum(
        bind,
        "expense_payment_method",
        "CASH",
        "PETTY_CASH",
        "CORPORATE_CARD",
        "PERSONAL_CARD",
        "BANK_TRANSFER",
        "OTHER",
    )
    ensure_enum(
        bind,
        "expense_status",
        "DRAFT",
        "SUBMITTED",
        "APPROVED",
        "POSTED",
        "REJECTED",
        "VOID",
    )

    statements = [
        """CREATE SCHEMA IF NOT EXISTS exp;""",
        """CREATE TABLE exp.expense_entry (
	expense_id UUID DEFAULT gen_random_uuid() NOT NULL, 
	organization_id UUID NOT NULL, 
	expense_number VARCHAR(30) NOT NULL, 
	description VARCHAR(500) NOT NULL, 
	notes TEXT, 
	expense_date DATE NOT NULL, 
	expense_account_id UUID NOT NULL, 
	payment_account_id UUID, 
	amount NUMERIC(19, 4) NOT NULL, 
	currency_code VARCHAR(3) NOT NULL, 
	tax_code_id UUID, 
	tax_amount NUMERIC(19, 4) NOT NULL, 
	project_id UUID, 
	cost_center_id UUID, 
	business_unit_id UUID, 
	payment_method expense_payment_method NOT NULL, 
	payee VARCHAR(200), 
	receipt_reference VARCHAR(100), 
	status expense_status NOT NULL, 
	journal_entry_id UUID, 
	submitted_by UUID, 
	submitted_at TIMESTAMP WITH TIME ZONE, 
	approved_by UUID, 
	approved_at TIMESTAMP WITH TIME ZONE, 
	posted_by UUID, 
	posted_at TIMESTAMP WITH TIME ZONE, 
	created_by UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_by UUID, 
	updated_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (expense_id), 
	CONSTRAINT uq_expense_entry_number UNIQUE (organization_id, expense_number), 
	FOREIGN KEY(expense_account_id) REFERENCES gl.account (account_id), 
	FOREIGN KEY(payment_account_id) REFERENCES gl.account (account_id), 
	FOREIGN KEY(tax_code_id) REFERENCES tax.tax_code (tax_code_id), 
	FOREIGN KEY(project_id) REFERENCES core_org.project (project_id), 
	FOREIGN KEY(cost_center_id) REFERENCES core_org.cost_center (cost_center_id), 
	FOREIGN KEY(business_unit_id) REFERENCES core_org.business_unit (business_unit_id), 
	FOREIGN KEY(journal_entry_id) REFERENCES gl.journal_entry (journal_entry_id)
);""",
        """COMMENT ON COLUMN exp.expense_entry.expense_account_id IS 'Expense account (debit)';""",
        """COMMENT ON COLUMN exp.expense_entry.payment_account_id IS 'Payment source account (credit) - cash, bank, etc.';""",
        """COMMENT ON COLUMN exp.expense_entry.project_id IS 'Project for cost allocation';""",
        """COMMENT ON COLUMN exp.expense_entry.cost_center_id IS 'Cost center for departmental allocation';""",
        """COMMENT ON COLUMN exp.expense_entry.business_unit_id IS 'Business unit for segment reporting';""",
        """CREATE INDEX idx_expense_entry_account ON exp.expense_entry (expense_account_id);""",
        """CREATE INDEX idx_expense_entry_cost_center ON exp.expense_entry (cost_center_id);""",
        """CREATE INDEX idx_expense_entry_org_date ON exp.expense_entry (organization_id, expense_date);""",
        """CREATE INDEX idx_expense_entry_project ON exp.expense_entry (project_id);""",
        """CREATE INDEX idx_expense_entry_status ON exp.expense_entry (organization_id, status);""",
    ]
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    statements = [
        """DROP TABLE IF EXISTS exp.expense_entry CASCADE;""",
        """DROP TYPE IF EXISTS expense_status CASCADE;""",
        """DROP TYPE IF EXISTS expense_payment_method CASCADE;""",
        """DROP SCHEMA IF EXISTS exp CASCADE;""",
    ]
    for statement in statements:
        op.execute(statement)
