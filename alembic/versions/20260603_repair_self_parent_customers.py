"""Repair self-referential customer parent links (parent_customer_id = customer_id).

The Splynx customer sync (``app/services/splynx/sync/_customers.py``) resolves a
customer's parent from its Splynx ``partner_id``. A reseller carries
``splynx_partner_id`` equal to its own ``partner_id``, so when the reseller
itself re-syncs, ``_resolve_partner_parent`` returns the reseller's own
``customer_id`` and it was assigned as its own parent — a self-referential
cycle.

This left 20 reseller "main accounts" with ``parent_customer_id = customer_id``.
That breaks sub-account/hierarchy views (the account lists itself as its own
child) and risks infinite loops in any recursive walk. Resellers are top-level
accounts, so the correct state is ``parent_customer_id IS NULL`` (their genuine
sub-accounts already point to them).

The sync is hardened in the same change to skip ``parent == self``, so this
will not recur. ``CustomerService.update_customer`` already rejected
self-reference; ``create_customer`` cannot self-reference (PK is generated
server-side).

Idempotent: the ``WHERE parent_customer_id = customer_id`` predicate matches
zero rows on re-run. Not org-scoped — repairs the condition across all orgs.

Revision ID: 20260603_repair_self_parent_customers
Revises: 20260525_ap_invoice_auto_receipt, 20260526_add_fa_gl_recon
Create Date: 2026-06-03
"""

from __future__ import annotations

from alembic import op


revision = "20260603_repair_self_parent_customers"
down_revision = (
    "20260525_ap_invoice_auto_receipt",
    "20260526_add_fa_gl_recon",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ar.customer is RLS-protected; the migration role has no org context, so a
    # plain UPDATE matches zero rows. Bypass RLS for this repair so the fix
    # reaches the affected rows across all organizations. SET LOCAL is scoped to
    # this migration's transaction.
    op.execute("SET LOCAL app.bypass_rls = 'true'")
    op.execute(
        """
        UPDATE ar.customer
        SET parent_customer_id = NULL
        WHERE parent_customer_id = customer_id
        """
    )


def downgrade() -> None:
    # No-op: a self-referential parent link is invalid data, never a state
    # worth restoring.
    pass
