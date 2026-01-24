"""Add Paystack payment integration tables.

Revision ID: add_paystack_payment_tables
Revises: add_expense_project_ticket_fk
Create Date: 2025-02-15
"""

from alembic import op
from app.alembic_utils import ensure_enum

# revision identifiers, used by Alembic.
revision = "add_paystack_payment_tables"
down_revision = "add_expense_project_ticket_fk"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    # Create the payments schema first
    op.execute("CREATE SCHEMA IF NOT EXISTS payments;")

    # Create enum types in the payments schema
    ensure_enum(
        bind,
        "payment_intent_status",
        "PENDING",
        "PROCESSING",
        "COMPLETED",
        "FAILED",
        "ABANDONED",
        "EXPIRED",
        schema="payments",
    )
    ensure_enum(
        bind,
        "payment_direction",
        "INBOUND",
        "OUTBOUND",
        schema="payments",
    )
    ensure_enum(
        bind,
        "webhook_status",
        "RECEIVED",
        "PROCESSING",
        "PROCESSED",
        "FAILED",
        "DUPLICATE",
        schema="payments",
    )

    statements = [
        # Payment Intent table - tracks payment initialization and completion
        """CREATE TABLE IF NOT EXISTS payments.payment_intent (
    intent_id UUID NOT NULL,
    organization_id UUID NOT NULL,
    paystack_reference VARCHAR(100) NOT NULL,
    paystack_access_code VARCHAR(100),
    authorization_url VARCHAR(500),
    amount NUMERIC(19, 4) NOT NULL,
    currency_code VARCHAR(3) NOT NULL DEFAULT 'NGN',
    email VARCHAR(255) NOT NULL,
    -- Payment direction: INBOUND (collection) or OUTBOUND (transfer/payout)
    direction payments.payment_direction NOT NULL DEFAULT 'INBOUND',
    -- Bank account linkage for reconciliation
    bank_account_id UUID,
    source_type VARCHAR(30) NOT NULL,
    source_id UUID,
    -- Transfer-specific fields (for OUTBOUND payments)
    transfer_recipient_code VARCHAR(100),
    transfer_code VARCHAR(100),
    recipient_bank_code VARCHAR(20),
    recipient_account_number VARCHAR(20),
    recipient_account_name VARCHAR(200),
    status payments.payment_intent_status NOT NULL DEFAULT 'PENDING',
    customer_payment_id UUID,
    paystack_transaction_id VARCHAR(100),
    paid_at TIMESTAMP WITH TIME ZONE,
    gateway_response JSONB,
    intent_metadata JSONB,
    expires_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    updated_at TIMESTAMP WITH TIME ZONE,
    PRIMARY KEY (intent_id),
    CONSTRAINT uq_payment_intent_reference UNIQUE (paystack_reference)
);""",
        """COMMENT ON TABLE payments.payment_intent IS 'Tracks Paystack payment initialization and completion';""",
        """COMMENT ON COLUMN payments.payment_intent.paystack_reference IS 'Our unique reference sent to Paystack';""",
        """COMMENT ON COLUMN payments.payment_intent.paystack_access_code IS 'Paystack access code for the transaction';""",
        """COMMENT ON COLUMN payments.payment_intent.source_type IS 'Type of document being paid: INVOICE, EXPENSE_CLAIM, GENERAL';""",
        """COMMENT ON COLUMN payments.payment_intent.source_id IS 'ID of the document being paid (e.g., invoice_id)';""",
        """COMMENT ON COLUMN payments.payment_intent.customer_payment_id IS 'Links to AR customer_payment after successful payment';""",
        # Payment Webhook table - tracks incoming webhooks for idempotency and audit
        """CREATE TABLE IF NOT EXISTS payments.payment_webhook (
    webhook_id UUID NOT NULL,
    organization_id UUID,
    event_type VARCHAR(50) NOT NULL,
    paystack_event_id VARCHAR(100) NOT NULL,
    paystack_reference VARCHAR(100),
    payload JSONB,
    signature VARCHAR(200),
    status payments.webhook_status NOT NULL DEFAULT 'RECEIVED',
    processed_at TIMESTAMP WITH TIME ZONE,
    error_message TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    PRIMARY KEY (webhook_id),
    CONSTRAINT uq_payment_webhook_event_id UNIQUE (paystack_event_id)
);""",
        """COMMENT ON TABLE payments.payment_webhook IS 'Audit log for incoming Paystack webhooks with idempotency';""",
        """COMMENT ON COLUMN payments.payment_webhook.paystack_event_id IS 'Unique event identifier for idempotency';""",
        """COMMENT ON COLUMN payments.payment_webhook.signature IS 'X-Paystack-Signature header for audit';""",
        # Indexes for efficient queries
        """CREATE INDEX IF NOT EXISTS idx_payment_intent_org_status
    ON payments.payment_intent (organization_id, status);""",
        """CREATE INDEX IF NOT EXISTS idx_payment_intent_source
    ON payments.payment_intent (source_type, source_id)
    WHERE source_id IS NOT NULL;""",
        """CREATE INDEX IF NOT EXISTS idx_payment_intent_expires
    ON payments.payment_intent (expires_at)
    WHERE status = 'PENDING';""",
        """CREATE INDEX IF NOT EXISTS idx_payment_webhook_reference
    ON payments.payment_webhook (paystack_reference)
    WHERE paystack_reference IS NOT NULL;""",
        """CREATE INDEX IF NOT EXISTS idx_payment_webhook_status
    ON payments.payment_webhook (status, created_at);""",
        # Bank account linkage index for reconciliation
        """CREATE INDEX IF NOT EXISTS idx_payment_intent_bank_account
    ON payments.payment_intent (bank_account_id, direction, status)
    WHERE bank_account_id IS NOT NULL;""",
        # Transfer index for outbound payments
        """CREATE INDEX IF NOT EXISTS idx_payment_intent_transfer
    ON payments.payment_intent (transfer_code)
    WHERE direction = 'OUTBOUND' AND transfer_code IS NOT NULL;""",
    ]
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    statements = [
        """DROP TABLE IF EXISTS payments.payment_webhook CASCADE;""",
        """DROP TABLE IF EXISTS payments.payment_intent CASCADE;""",
        """DROP TYPE IF EXISTS payments.webhook_status CASCADE;""",
        """DROP TYPE IF EXISTS payments.payment_direction CASCADE;""",
        """DROP TYPE IF EXISTS payments.payment_intent_status CASCADE;""",
        """DROP SCHEMA IF EXISTS payments CASCADE;""",
    ]
    for statement in statements:
        op.execute(statement)
