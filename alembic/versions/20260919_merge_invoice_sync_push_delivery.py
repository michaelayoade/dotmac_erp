"""Join the invoice-accounting-sync evidence split and push-delivery outcomes
migration directions.

Revision ID: 20260919_merge_invoice_sync_push_delivery
Revises: 20260918_invoice_sync_canonical_evidence, 20260918_push_delivery_outcomes

Both independently authored migrations must run. This merge does not rewrite
an already-published migration or drop either feature's database state.
"""

revision = "20260919_merge_invoice_sync_push_delivery"
down_revision = (
    "20260918_invoice_sync_canonical_evidence",
    "20260918_push_delivery_outcomes",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
