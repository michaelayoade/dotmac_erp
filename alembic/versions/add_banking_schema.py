"""Add banking schema and tables.

Revision ID: add_banking_schema
Revises: make_person_org_required
Create Date: 2025-02-04
"""

from alembic import op
from app.alembic_utils import ensure_enum

# revision identifiers, used by Alembic.
revision = "add_banking_schema"
down_revision = "make_person_org_required"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    ensure_enum(
        bind,
        "bank_account_status",
        "active",
        "inactive",
        "closed",
        "suspended",
        schema="banking",
    )
    ensure_enum(
        bind,
        "bank_account_type",
        "checking",
        "savings",
        "money_market",
        "credit_line",
        "loan",
        "other",
        schema="banking",
    )
    ensure_enum(
        bind,
        "bank_statement_status",
        "imported",
        "processing",
        "reconciled",
        "closed",
        schema="banking",
    )
    ensure_enum(
        bind,
        "payee_type",
        "VENDOR",
        "CUSTOMER",
        "EMPLOYEE",
        "BANK",
        "TAX",
        "UTILITY",
        "OTHER",
        schema="banking",
    )
    ensure_enum(
        bind,
        "reconciliation_match_type",
        "auto_exact",
        "auto_fuzzy",
        "manual",
        "split",
        "adjustment",
        schema="banking",
    )
    ensure_enum(
        bind,
        "reconciliation_status",
        "draft",
        "pending_review",
        "approved",
        "rejected",
        schema="banking",
    )
    ensure_enum(
        bind,
        "rule_action",
        "CATEGORIZE",
        "FLAG_REVIEW",
        "SPLIT",
        "IGNORE",
        schema="banking",
    )
    ensure_enum(
        bind,
        "rule_type",
        "PAYEE_MATCH",
        "DESCRIPTION_CONTAINS",
        "DESCRIPTION_REGEX",
        "AMOUNT_RANGE",
        "REFERENCE_MATCH",
        "COMBINED",
        schema="banking",
    )
    ensure_enum(
        bind,
        "statement_line_type",
        "credit",
        "debit",
        schema="banking",
    )

    statements = [
        """CREATE SCHEMA IF NOT EXISTS banking;""",
        """CREATE TABLE IF NOT EXISTS banking.bank_accounts (
	bank_account_id UUID NOT NULL,
	organization_id UUID NOT NULL,
	bank_name VARCHAR(200) NOT NULL,
	bank_code VARCHAR(20),
	branch_code VARCHAR(20),
	branch_name VARCHAR(200),
	account_number VARCHAR(50) NOT NULL,
	account_name VARCHAR(200) NOT NULL,
	account_type banking.bank_account_type NOT NULL,
	iban VARCHAR(50),
	currency_code VARCHAR(3) NOT NULL,
	gl_account_id UUID NOT NULL,
	status banking.bank_account_status NOT NULL,
	last_statement_balance NUMERIC(19, 4),
	last_statement_date TIMESTAMP WITH TIME ZONE,
	last_reconciled_date TIMESTAMP WITH TIME ZONE,
	last_reconciled_balance NUMERIC(19, 4),
	contact_name VARCHAR(200),
	contact_phone VARCHAR(50),
	contact_email VARCHAR(200),
	notes TEXT,
	is_primary BOOLEAN NOT NULL,
	allow_overdraft BOOLEAN NOT NULL,
	overdraft_limit NUMERIC(19, 4),
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	created_by UUID,
	updated_by UUID,
	PRIMARY KEY (bank_account_id),
	CONSTRAINT uq_bank_account_number UNIQUE (organization_id, account_number, bank_code),
	FOREIGN KEY(gl_account_id) REFERENCES gl.account (account_id) ON DELETE RESTRICT
);""",
        """CREATE INDEX IF NOT EXISTS ix_banking_bank_accounts_organization_id ON banking.bank_accounts (organization_id);""",
        """CREATE TABLE IF NOT EXISTS banking.payee (
	payee_id UUID NOT NULL,
	organization_id UUID NOT NULL,
	payee_name VARCHAR(200) NOT NULL,
	payee_type banking.payee_type NOT NULL,
	name_patterns TEXT,
	default_account_id UUID,
	default_tax_code_id UUID,
	supplier_id UUID,
	customer_id UUID,
	match_count INTEGER NOT NULL,
	last_matched_at TIMESTAMP WITH TIME ZONE,
	is_active BOOLEAN NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	created_by UUID,
	updated_at TIMESTAMP WITH TIME ZONE,
	notes TEXT,
	PRIMARY KEY (payee_id),
	CONSTRAINT uq_payee_name UNIQUE (organization_id, payee_name),
	FOREIGN KEY(organization_id) REFERENCES core_org.organization (organization_id),
	FOREIGN KEY(default_account_id) REFERENCES gl.account (account_id),
	FOREIGN KEY(supplier_id) REFERENCES ap.supplier (supplier_id),
	FOREIGN KEY(customer_id) REFERENCES ar.customer (customer_id)
);""",
        """COMMENT ON COLUMN banking.payee.name_patterns IS 'Pipe-separated patterns for matching, e.g., ''AMAZON|AMZN|AWS''';""",
        """COMMENT ON COLUMN banking.payee.default_account_id IS 'Default GL account for transactions with this payee';""",
        """COMMENT ON COLUMN banking.payee.default_tax_code_id IS 'Default tax code for transactions with this payee';""",
        """CREATE TABLE IF NOT EXISTS banking.bank_reconciliations (
	reconciliation_id UUID NOT NULL,
	organization_id UUID NOT NULL,
	bank_account_id UUID NOT NULL,
	reconciliation_date DATE NOT NULL,
	period_start DATE NOT NULL,
	period_end DATE NOT NULL,
	statement_opening_balance NUMERIC(19, 4) NOT NULL,
	gl_opening_balance NUMERIC(19, 4) NOT NULL,
	statement_closing_balance NUMERIC(19, 4) NOT NULL,
	gl_closing_balance NUMERIC(19, 4) NOT NULL,
	total_matched NUMERIC(19, 4) NOT NULL,
	total_unmatched_statement NUMERIC(19, 4) NOT NULL,
	total_unmatched_gl NUMERIC(19, 4) NOT NULL,
	total_adjustments NUMERIC(19, 4) NOT NULL,
	reconciliation_difference NUMERIC(19, 4) NOT NULL,
	prior_outstanding_deposits NUMERIC(19, 4) NOT NULL,
	prior_outstanding_payments NUMERIC(19, 4) NOT NULL,
	outstanding_deposits NUMERIC(19, 4) NOT NULL,
	outstanding_payments NUMERIC(19, 4) NOT NULL,
	currency_code VARCHAR(3) NOT NULL,
	status banking.reconciliation_status NOT NULL,
	prepared_by UUID,
	prepared_at TIMESTAMP WITH TIME ZONE,
	reviewed_by UUID,
	reviewed_at TIMESTAMP WITH TIME ZONE,
	approved_by UUID,
	approved_at TIMESTAMP WITH TIME ZONE,
	notes TEXT,
	review_notes TEXT,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (reconciliation_id),
	CONSTRAINT uq_bank_reconciliation_date UNIQUE (bank_account_id, reconciliation_date),
	FOREIGN KEY(bank_account_id) REFERENCES banking.bank_accounts (bank_account_id) ON DELETE CASCADE
);""",
        """CREATE INDEX IF NOT EXISTS ix_bank_reconciliation_status ON banking.bank_reconciliations (bank_account_id, status);""",
        """CREATE INDEX IF NOT EXISTS ix_banking_bank_reconciliations_organization_id ON banking.bank_reconciliations (organization_id);""",
        """CREATE TABLE IF NOT EXISTS banking.bank_statements (
	statement_id UUID NOT NULL,
	organization_id UUID NOT NULL,
	bank_account_id UUID NOT NULL,
	statement_number VARCHAR(50) NOT NULL,
	statement_date DATE NOT NULL,
	period_start DATE NOT NULL,
	period_end DATE NOT NULL,
	opening_balance NUMERIC(19, 4) NOT NULL,
	closing_balance NUMERIC(19, 4) NOT NULL,
	total_credits NUMERIC(19, 4) NOT NULL,
	total_debits NUMERIC(19, 4) NOT NULL,
	currency_code VARCHAR(3) NOT NULL,
	status banking.bank_statement_status NOT NULL,
	import_source VARCHAR(50),
	import_filename VARCHAR(255),
	imported_at TIMESTAMP WITH TIME ZONE NOT NULL,
	imported_by UUID,
	total_lines INTEGER NOT NULL,
	matched_lines INTEGER NOT NULL,
	unmatched_lines INTEGER NOT NULL,
	notes TEXT,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (statement_id),
	CONSTRAINT uq_bank_statement_number UNIQUE (bank_account_id, statement_number),
	FOREIGN KEY(bank_account_id) REFERENCES banking.bank_accounts (bank_account_id) ON DELETE CASCADE
);""",
        """CREATE INDEX IF NOT EXISTS ix_bank_statement_period ON banking.bank_statements (bank_account_id, statement_date);""",
        """CREATE INDEX IF NOT EXISTS ix_banking_bank_statements_organization_id ON banking.bank_statements (organization_id);""",
        """CREATE TABLE IF NOT EXISTS banking.transaction_rule (
	rule_id UUID NOT NULL,
	organization_id UUID NOT NULL,
	rule_name VARCHAR(100) NOT NULL,
	description TEXT,
	rule_type banking.rule_type NOT NULL,
	conditions JSONB NOT NULL,
	bank_account_id UUID,
	applies_to_credits BOOLEAN NOT NULL,
	applies_to_debits BOOLEAN NOT NULL,
	action banking.rule_action NOT NULL,
	target_account_id UUID,
	tax_code_id UUID,
	split_config JSONB,
	payee_id UUID,
	priority INTEGER NOT NULL,
	auto_apply BOOLEAN NOT NULL,
	min_confidence INTEGER NOT NULL,
	match_count INTEGER NOT NULL,
	last_matched_at TIMESTAMP WITH TIME ZONE,
	success_count INTEGER NOT NULL,
	reject_count INTEGER NOT NULL,
	is_active BOOLEAN NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	created_by UUID,
	updated_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (rule_id),
	CONSTRAINT uq_rule_name UNIQUE (organization_id, rule_name),
	FOREIGN KEY(organization_id) REFERENCES core_org.organization (organization_id),
	FOREIGN KEY(bank_account_id) REFERENCES banking.bank_accounts (bank_account_id),
	FOREIGN KEY(target_account_id) REFERENCES gl.account (account_id),
	FOREIGN KEY(payee_id) REFERENCES banking.payee (payee_id)
);""",
        """COMMENT ON COLUMN banking.transaction_rule.conditions IS '
        Conditions based on rule_type:
        - PAYEE_MATCH: {"patterns": ["AMAZON", "AMZN"], "case_sensitive": false}
        - DESCRIPTION_CONTAINS: {"text": "DIRECT DEBIT", "case_sensitive": false}
        - DESCRIPTION_REGEX: {"pattern": "^DD\\s+\\d+"}
        - AMOUNT_RANGE: {"min": 0, "max": 100, "transaction_type": "debit"}
        - REFERENCE_MATCH: {"pattern": "INV-\\d+"}
        - COMBINED: {"operator": "AND", "rules": [...]}
        ';""",
        """COMMENT ON COLUMN banking.transaction_rule.bank_account_id IS 'If set, rule only applies to this bank account';""",
        """COMMENT ON COLUMN banking.transaction_rule.split_config IS '
        Split configuration for SPLIT action:
        {
            "lines": [
                {"account_id": "uuid", "percentage": 60},
                {"account_id": "uuid", "percentage": 40}
            ]
        }
        ';""",
        """COMMENT ON COLUMN banking.transaction_rule.auto_apply IS 'If true, automatically apply; if false, suggest for review';""",
        """COMMENT ON COLUMN banking.transaction_rule.min_confidence IS 'Minimum match confidence to apply/suggest this rule';""",
        """COMMENT ON COLUMN banking.transaction_rule.success_count IS 'Times user accepted the suggestion';""",
        """COMMENT ON COLUMN banking.transaction_rule.reject_count IS 'Times user rejected the suggestion';""",
        """CREATE TABLE IF NOT EXISTS banking.bank_statement_lines (
	line_id UUID NOT NULL,
	statement_id UUID NOT NULL,
	line_number INTEGER NOT NULL,
	transaction_id VARCHAR(100),
	transaction_date DATE NOT NULL,
	value_date DATE,
	transaction_type banking.statement_line_type NOT NULL,
	amount NUMERIC(19, 4) NOT NULL,
	running_balance NUMERIC(19, 4),
	description VARCHAR(500),
	reference VARCHAR(100),
	payee_payer VARCHAR(200),
	bank_reference VARCHAR(100),
	check_number VARCHAR(20),
	bank_category VARCHAR(100),
	bank_code VARCHAR(20),
	is_matched BOOLEAN NOT NULL,
	matched_at TIMESTAMP WITH TIME ZONE,
	matched_by UUID,
	matched_journal_line_id UUID,
	raw_data JSONB,
	notes TEXT,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (line_id),
	FOREIGN KEY(statement_id) REFERENCES banking.bank_statements (statement_id) ON DELETE CASCADE
);""",
        """CREATE INDEX IF NOT EXISTS ix_statement_line_date ON banking.bank_statement_lines (statement_id, transaction_date);""",
        """CREATE INDEX IF NOT EXISTS ix_statement_line_matched ON banking.bank_statement_lines (statement_id, is_matched);""",
        """CREATE TABLE IF NOT EXISTS banking.bank_reconciliation_lines (
	line_id UUID NOT NULL,
	reconciliation_id UUID NOT NULL,
	match_type banking.reconciliation_match_type NOT NULL,
	statement_line_id UUID,
	journal_line_id UUID,
	transaction_date DATE NOT NULL,
	description VARCHAR(500),
	reference VARCHAR(100),
	statement_amount NUMERIC(19, 4),
	gl_amount NUMERIC(19, 4),
	difference NUMERIC(19, 4),
	is_adjustment BOOLEAN NOT NULL,
	adjustment_type VARCHAR(50),
	adjustment_account_id UUID,
	is_outstanding BOOLEAN NOT NULL,
	outstanding_type VARCHAR(20),
	match_confidence NUMERIC(5, 2),
	match_details JSONB,
	is_cleared BOOLEAN NOT NULL,
	cleared_at TIMESTAMP WITH TIME ZONE,
	notes TEXT,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	created_by UUID,
	PRIMARY KEY (line_id),
	FOREIGN KEY(reconciliation_id) REFERENCES banking.bank_reconciliations (reconciliation_id) ON DELETE CASCADE,
	FOREIGN KEY(statement_line_id) REFERENCES banking.bank_statement_lines (line_id) ON DELETE SET NULL
);""",
        """CREATE INDEX IF NOT EXISTS ix_recon_line_type ON banking.bank_reconciliation_lines (reconciliation_id, match_type);""",
    ]
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    statements = [
        """DROP TABLE IF EXISTS banking.bank_reconciliation_lines CASCADE;""",
        """DROP TABLE IF EXISTS banking.bank_statement_lines CASCADE;""",
        """DROP TABLE IF EXISTS banking.transaction_rule CASCADE;""",
        """DROP TABLE IF EXISTS banking.bank_statements CASCADE;""",
        """DROP TABLE IF EXISTS banking.bank_reconciliations CASCADE;""",
        """DROP TABLE IF EXISTS banking.payee CASCADE;""",
        """DROP TABLE IF EXISTS banking.bank_accounts CASCADE;""",
        """DROP TYPE IF EXISTS banking.statement_line_type CASCADE;""",
        """DROP TYPE IF EXISTS banking.rule_type CASCADE;""",
        """DROP TYPE IF EXISTS banking.rule_action CASCADE;""",
        """DROP TYPE IF EXISTS banking.reconciliation_status CASCADE;""",
        """DROP TYPE IF EXISTS banking.reconciliation_match_type CASCADE;""",
        """DROP TYPE IF EXISTS banking.payee_type CASCADE;""",
        """DROP TYPE IF EXISTS banking.bank_statement_status CASCADE;""",
        """DROP TYPE IF EXISTS banking.bank_account_type CASCADE;""",
        """DROP TYPE IF EXISTS banking.bank_account_status CASCADE;""",
        """DROP SCHEMA IF EXISTS banking CASCADE;""",
    ]
    for statement in statements:
        op.execute(statement)
