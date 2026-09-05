"""Restore the unique index required by concurrent sales-report refresh.

Revision ID: 20260905_repair_sales_mv_index
Revises: 20260902_staff_access_projection

Some installations have the populated view but no usable unique index, despite
having applied the original analysis-cube migration. Repair that schema drift
without replacing the view, its data, its ownership, or its refresh function.
Index creation is transactional and may wait for running refreshes; deploy in a
maintenance window. Duplicate invoice rows require investigation, not deletion.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260905_repair_sales_mv_index"
down_revision = "20260902_staff_access_projection"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    view = (
        bind.execute(
            sa.text(
                "SELECT oid, relispopulated FROM pg_class "
                "WHERE oid = to_regclass('rpt.sales_analysis_mv') AND relkind = 'm'"
            )
        )
        .mappings()
        .one_or_none()
    )
    if view is None:
        raise RuntimeError("rpt.sales_analysis_mv must exist before index repair")

    # Match PostgreSQL's concurrent-refresh prerequisites, rather than trusting
    # an index name or CREATE INDEX IF NOT EXISTS on an invalid existing index.
    usable = bind.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM pg_index i "
            "JOIN pg_class c ON c.oid = i.indexrelid "
            "JOIN pg_am am ON am.oid = c.relam "
            "WHERE i.indrelid = :view_oid AND i.indisunique AND i.indisvalid "
            "AND i.indimmediate AND i.indpred IS NULL AND i.indexprs IS NULL "
            "AND i.indnatts > 0 AND 0 < ALL(i.indkey::smallint[]) "
            "AND am.amname = 'btree')"
        ),
        {"view_oid": view["oid"]},
    )
    if usable:
        return

    # This is a schema-wide migration: invoice_id is globally unique in the
    # source definition, so restricting this check to one tenant would be wrong.
    if view["relispopulated"]:
        duplicate = bind.scalar(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM rpt.sales_analysis_mv "
                "GROUP BY invoice_id HAVING count(*) > 1)"
            )
        )
        if duplicate:
            raise RuntimeError(
                "Cannot repair rpt.sales_analysis_mv: duplicate invoice_id rows; "
                "investigate the view definition and source data before retrying"
            )

    existing = (
        bind.execute(
            sa.text(
                "SELECT c.oid, i.indrelid FROM pg_class c "
                "LEFT JOIN pg_index i ON i.indexrelid = c.oid "
                "WHERE c.oid = to_regclass('rpt.uq_sales_analysis_mv_invoice_id')"
            )
        )
        .mappings()
        .one_or_none()
    )
    if existing is not None:
        if existing["indrelid"] != view["oid"]:
            raise RuntimeError(
                "rpt.uq_sales_analysis_mv_invoice_id belongs to another relation; "
                "resolve the name collision before repairing the sales view"
            )
        # Only replace an unusable canonical index attached to this exact view.
        op.execute("DROP INDEX rpt.uq_sales_analysis_mv_invoice_id")

    op.execute(
        "CREATE UNIQUE INDEX uq_sales_analysis_mv_invoice_id "
        "ON rpt.sales_analysis_mv (invoice_id)"
    )


def downgrade() -> None:
    # The parent lineage already requires this index. Removing a repaired or
    # pre-existing index on downgrade would reintroduce the production failure.
    pass
