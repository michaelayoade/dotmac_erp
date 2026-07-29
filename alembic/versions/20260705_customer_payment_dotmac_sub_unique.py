"""Idempotency backstop: unique (organization_id, dotmac_sub_id) on ar.customer_payment.

Prevents the dotmac_sub payment sync (poll + webhook) from double-posting cash
when two runs race: the app-level "select-then-insert" dedup has no DB backstop,
so overlapping syncs can both insert the same upstream payment. A partial unique
index closes that window (NULL dotmac_sub_id = manually-entered payments, left
unconstrained).

If the table already contains duplicate (organization_id, dotmac_sub_id) groups,
this migration ABORTS with the offending keys rather than silently dropping cash
records — a human must reconcile which payment is real first.

Revision ID: 20260705_customer_payment_dotmac_sub_unique
Revises: 20260704_add_ncc_staff_fields
Create Date: 2026-07-05
"""

import sqlalchemy as sa

from alembic import op

revision = "20260705_customer_payment_dotmac_sub_unique"
down_revision = "20260704_add_ncc_staff_fields"
branch_labels = None
depends_on = None

_INDEX = "uq_customer_payment_dotmac_sub_id"
_SCHEMA = "ar"
_TABLE = "customer_payment"


def _has_index(inspector, name: str) -> bool:
    if not inspector.has_table(_TABLE, schema=_SCHEMA):
        return False
    return any(
        ix["name"] == name for ix in inspector.get_indexes(_TABLE, schema=_SCHEMA)
    )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table(_TABLE, schema=_SCHEMA):
        return
    if _has_index(inspector, _INDEX):
        return

    # Guard: refuse to create the constraint over existing duplicates.
    dupes = bind.execute(
        sa.text(
            f"SELECT organization_id, dotmac_sub_id, COUNT(*) AS n "  # noqa: S608
            f"FROM {_SCHEMA}.{_TABLE} "
            "WHERE dotmac_sub_id IS NOT NULL "
            "GROUP BY organization_id, dotmac_sub_id "
            "HAVING COUNT(*) > 1"
        )
    ).fetchall()
    if dupes:
        preview = ", ".join(
            f"(org={row[0]}, dotmac_sub_id={row[1]}, n={row[2]})" for row in dupes[:20]
        )
        raise RuntimeError(
            f"Cannot add {_INDEX}: {len(dupes)} duplicate (organization_id, "
            f"dotmac_sub_id) group(s) already exist in {_SCHEMA}.{_TABLE}. "
            f"Reconcile the duplicate payments first (keep one, void the rest), "
            f"then re-run the migration. Offending keys: {preview}"
        )

    op.create_index(
        _INDEX,
        _TABLE,
        ["organization_id", "dotmac_sub_id"],
        unique=True,
        schema=_SCHEMA,
        postgresql_where=sa.text("dotmac_sub_id IS NOT NULL"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _has_index(inspector, _INDEX):
        op.drop_index(_INDEX, table_name=_TABLE, schema=_SCHEMA)
