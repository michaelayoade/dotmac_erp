"""balance_due becomes a generated column on ar.invoice and ap.supplier_invoice

ADR-0016, stage 1. `balance_due` has always been `total_amount - amount_paid`,
but it was a Python `@property` — which meant it could not appear in a WHERE,
an ORDER BY or a SUM. So callers wrote the subtraction out by hand instead:
**75 occurrences across ~30 files** at the time of writing.

Making it a column gives one definition that the ORM and SQL share. It is
`GENERATED ALWAYS AS ... STORED`, so there is no writer and no way to set it
wrong — the drift-impossibility this ADR is about, obtained for the
arithmetic only. The dust tolerance stays a setting applied in the query; a
threshold is policy and does not belong in DDL.

## Operational note

`ALTER TABLE ... ADD COLUMN ... GENERATED ALWAYS AS (...) STORED` REWRITES the
table and takes an ACCESS EXCLUSIVE lock for the duration. On large
`ar.invoice` / `ap.supplier_invoice` tables this is not an online operation —
run it in a maintenance window, not mid-day.

The column is NOT NULL by construction: both operands are NOT NULL, so the
expression cannot yield NULL.

## Indexes

One partial index per table on `balance_due > 0`. Aging, dunning and the
"outstanding" dashboards all ask for unsettled documents, and a partial index
keeps it small — settled rows are the majority and are never the answer.

Revision ID: 20260811_balance_due_generated
Revises: 20260810_material_source
Create Date: 2026-08-11
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260811_balance_due_generated"
down_revision = "20260810_material_source"
branch_labels = None
depends_on = None

_TABLES = (
    ("ar", "invoice", "ix_ar_invoice_balance_due_outstanding"),
    ("ap", "supplier_invoice", "ix_ap_supplier_invoice_balance_due_outstanding"),
)


def upgrade() -> None:
    for schema, table, index_name in _TABLES:
        op.execute(
            sa.text(
                f"ALTER TABLE {schema}.{table} "  # noqa: S608
                f"ADD COLUMN balance_due NUMERIC(20, 6) "
                f"GENERATED ALWAYS AS (total_amount - amount_paid) STORED"
            )
        )
        # Partial: settled documents are the majority and are never what
        # aging, dunning or an outstanding-balance dashboard is looking for.
        op.execute(
            sa.text(
                f"CREATE INDEX {index_name} ON {schema}.{table} (balance_due) "  # noqa: S608
                f"WHERE balance_due > 0"
            )
        )


def downgrade() -> None:
    for schema, table, index_name in _TABLES:
        op.execute(sa.text(f"DROP INDEX IF EXISTS {schema}.{index_name}"))
        op.execute(sa.text(f"ALTER TABLE {schema}.{table} DROP COLUMN balance_due"))
