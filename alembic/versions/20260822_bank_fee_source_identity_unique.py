"""Make a bank fee's statement line its typed, unique source identity.

Revision ID: 20260822_bank_fee_source_identity
Revises: 20260821_add_notification_email_retry_state

Bank-fee journals knew their statement line and recorded it only as the display
string ``correlation_id = "bank-fee-<line_id>"``. Nothing keyed on it, so the
same line could be journalled again and again: production holds **12,117
APPROVED bank-fee journals for 149 statement lines**, one line carrying 85.

`gl.posting_batch.idempotency_key` is globally unique, so *posting* was already
at-most-once. **Creation was not** — and creation ran first. An application-level
check cannot close that on its own, because two concurrent callers both pass it.
This index is what makes the loser fail.

Partial, and deliberately so:

* ``WHERE source_document_type = 'BANK_FEE'`` — this constraint is a statement
  about bank fees, not about every source document. Other producers legitimately
  post several journals against one document.
* ``source_document_id IS NOT NULL`` is implied by the b-tree, and matters here:
  **every existing bank-fee journal has a NULL source_document_id**, so all
  13,955 of them are outside the index and this migration cannot fail on
  historical data. It constrains new writes only.

No backfill. Populating `source_document_id` from the correlation string would
make the 12,117 duplicates collide and the migration unrunnable — and those rows
are evidence pending a Finance disposition, not rows to quietly rewrite.

CONCURRENTLY is not used: this runs under `scripts/deploy.sh` as `app_admin`
inside a migration transaction, and the index covers no existing rows, so it
takes no meaningful lock time.
"""

from __future__ import annotations

from alembic import op

revision = "20260822_bank_fee_source_identity"
down_revision = "20260821_add_notification_email_retry_state"
branch_labels = None
depends_on = None

INDEX_NAME = "uq_journal_entry_bank_fee_source"


def upgrade() -> None:
    op.create_index(
        INDEX_NAME,
        "journal_entry",
        ["organization_id", "source_document_id"],
        unique=True,
        schema="gl",
        postgresql_where="source_document_type = 'BANK_FEE'",
    )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="journal_entry", schema="gl")
