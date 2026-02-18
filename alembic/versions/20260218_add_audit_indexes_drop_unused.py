"""Add audit_log indexes for query performance and drop unused indexes.

The audit.audit_log table has 101K seq scans reading 5.1B tuples but only
73 index scans — adding a (organization_id, occurred_at) index and a
(table_schema, table_name, occurred_at) index will cover the most common
query patterns (entity history lookup and org-scoped time range queries).

Also drops unused indexes (0 index scans) that waste ~158MB of disk space.
Primary keys and unique constraints are preserved even if unused.

Revision ID: 20260218_add_audit_indexes_drop_unused
Revises: 20260216_extend_kpi_status_enum
Create Date: 2026-02-18
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260218_add_audit_indexes_drop_unused"
down_revision = "20260216_extend_kpi_status_enum"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Add audit_log performance indexes ───────────────────────
    # Most common query: org-scoped time range lookups
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_audit_org_occurred
        ON audit.audit_log (organization_id, occurred_at DESC)
    """)
    # Entity history: table + record + time
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_audit_table_occurred
        ON audit.audit_log (table_schema, table_name, occurred_at DESC)
    """)

    # ── Drop unused non-essential indexes ────────────────────────
    # Only drop indexes that are NOT primary keys or unique constraints.
    # These all have 0 index scans in pg_stat_user_indexes.

    # idx_notification_org_created: 42MB, 0 scans
    # (covered by ix_notification_created_at and org_id filter)
    op.execute("DROP INDEX IF EXISTS public.idx_notification_org_created")

    # ix_notification_organization_id: 3.8MB, 0 scans
    # (covered by ix_notification_recipient_unread which is the main query path)
    op.execute("DROP INDEX IF EXISTS public.ix_notification_organization_id")

    # idx_gl_journal_source_doc: 20MB, 0 scans
    op.execute("DROP INDEX IF EXISTS gl.idx_gl_journal_source_doc")

    # idx_batch_correlation: 7.5MB, 0 scans
    op.execute("DROP INDEX IF EXISTS gl.idx_batch_correlation")

    # idx_jel_dimensions: 4.6MB, 0 scans
    op.execute("DROP INDEX IF EXISTS gl.idx_jel_dimensions")

    # idx_batch_status: 1.9MB, 0 scans
    op.execute("DROP INDEX IF EXISTS gl.idx_batch_status")

    # idx_outbox_aggregate: 4.7MB, 0 scans
    op.execute("DROP INDEX IF EXISTS platform.idx_outbox_aggregate")

    # idx_outbox_correlation: 2.2MB, 0 scans
    op.execute("DROP INDEX IF EXISTS platform.idx_outbox_correlation")

    # idx_invoice_org_status_created: 2.8MB, 0 scans
    op.execute("DROP INDEX IF EXISTS ar.idx_invoice_org_status_created")

    # idx_invoice_line_lot: 1.3MB, 0 scans
    op.execute("DROP INDEX IF EXISTS ar.idx_invoice_line_lot")

    # idx_ar_invoice_line_obligation: 1.3MB, 0 scans
    op.execute("DROP INDEX IF EXISTS ar.idx_ar_invoice_line_obligation")

    # idx_audit_record: 6.3MB, 0 scans (superceded by new idx_audit_table_occurred)
    op.execute("DROP INDEX IF EXISTS audit.idx_audit_record")


def downgrade() -> None:
    # Re-create dropped indexes (best effort — original CREATE INDEX statements)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_notification_org_created
        ON public.notification (organization_id, created_at)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_notification_organization_id
        ON public.notification (organization_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_gl_journal_source_doc
        ON gl.journal_entry (source_module, source_document_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_batch_correlation
        ON gl.posting_batch (correlation_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_jel_dimensions
        ON gl.journal_entry_line (cost_center_id, project_id, segment_id, business_unit_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_batch_status
        ON gl.posting_batch (status)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_outbox_aggregate
        ON platform.event_outbox (aggregate_type, aggregate_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_outbox_correlation
        ON platform.event_outbox (correlation_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_invoice_org_status_created
        ON ar.invoice (organization_id, status, created_at)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_invoice_line_lot
        ON ar.invoice_line (lot_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_ar_invoice_line_obligation
        ON ar.invoice_line (performance_obligation_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_audit_record
        ON audit.audit_log (table_schema, table_name, record_id)
    """)

    # Drop new audit indexes
    op.execute("DROP INDEX IF EXISTS audit.idx_audit_org_occurred")
    op.execute("DROP INDEX IF EXISTS audit.idx_audit_table_occurred")
