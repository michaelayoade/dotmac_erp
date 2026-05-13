"""reconcile hr.position columns + replay is_deleted drop on stale environments

Recovers environments where ``20260512_position_org`` ran in its PRE-REBASE
shape (which created ``hr.position.is_deleted``/``deleted_at``/``deleted_by_id``
and skipped ``95ab1bf7b754_drop_is_deleted_phase1``) and the DB was stamped
forward to ``20260512_position_org`` without ever applying the drop-is_deleted
phase-1 migration.

This migration is idempotent: every DDL is guarded by a column-existence
check via SQLAlchemy's ``inspect``. Fresh environments that already migrated
through the post-rebase chain (``95ab1bf7b754 -> 20260512_position_org``)
will see every step as a no-op.

Why this exists:
  - A bug-fix PR (#71) restored two earlier migrations and re-pointed
    ``86316c93eb91``'s ``down_revision`` to fix a broken chain.
  - In parallel, the position migration (``20260512_position_org``) was
    rebased during PR #67 review to chain off ``95ab1bf7b754`` and to drop
    ``is_deleted``/``deleted_at``/``deleted_by_id`` in favour of
    ``is_active``.
  - Any environment that ran the OLD position migration (before the rebase)
    ended up at ``hr.position`` with the wrong column set, and never ran
    ``95ab1bf7b754`` because it was skipped by the broken chain.
  - This migration replays the necessary structural changes so all
    environments converge on the same schema regardless of which version
    of ``20260512_position_org`` they originally ran.

Revision ID: 20260513_reconcile
Revises: 20260512_position_org
Create Date: 2026-05-13 06:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260513_reconcile"
down_revision = "20260512_position_org"
branch_labels = None
depends_on = None


# Tables that gained ``is_active`` as a NEW column under
# ``95ab1bf7b754_drop_is_deleted_phase1.py``. Each will only be touched here
# if it doesn't already have the column.
_TABLES_NEEDING_IS_ACTIVE = [
    ("hr", "employee_document"),
    ("hr", "employee_qualification"),
    ("hr", "employee_certification"),
    ("hr", "employee_dependent"),
    ("pm", "pm_comment"),
    ("support", "ticket_attachment"),
    ("support", "ticket_comment"),
    ("fleet", "vehicle_incident"),
]

# Tables that had both ``is_deleted`` AND ``deleted_at`` (SoftDeleteMixin),
# plus ``deleted_by_id``. Includes the 4 tables that had only ``is_deleted``
# directly — those get only the ``is_deleted`` drop.
_TABLES_WITH_DELETED_AT = {
    ("hr", "employee"),
    ("hr", "department"),
    ("hr", "designation"),
    ("hr", "employee_document"),
    ("hr", "employee_qualification"),
    ("hr", "employee_certification"),
    ("hr", "employee_dependent"),
    ("hr", "skill"),
    ("hr", "competency"),
    ("hr", "job_description"),
    ("hr", "disciplinary_case"),
    ("pm", "task"),
    ("fleet", "vehicle"),
    ("fleet", "vehicle_incident"),
}

# Same lifecycle mapping as 95ab1bf7b754: when ``is_deleted=true``, set the
# target column/value pair so the lifecycle state is preserved before drop.
_PER_TABLE_MIGRATION = [
    ("hr", "employee", "status = 'TERMINATED'"),
    ("hr", "department", "is_active = false"),
    ("hr", "designation", "is_active = false"),
    ("hr", "employee_document", "is_active = false"),
    ("hr", "employee_qualification", "is_active = false"),
    ("hr", "employee_certification", "is_active = false"),
    ("hr", "employee_dependent", "is_active = false"),
    ("hr", "skill", "is_active = false"),
    ("hr", "competency", "is_active = false"),
    ("hr", "job_description", "status = 'archived'"),
    ("hr", "disciplinary_case", "status = 'WITHDRAWN'"),
    ("pm", "task", "status = 'CANCELLED'"),
    ("pm", "pm_comment", "is_active = false"),
    ("support", "ticket", "status = 'CLOSED'"),
    ("support", "ticket_attachment", "is_active = false"),
    ("support", "ticket_comment", "is_active = false"),
    ("fleet", "vehicle", "status = 'DISPOSED'"),
    ("fleet", "vehicle_incident", "is_active = false"),
]


def _columns(inspector, schema: str, table: str) -> set[str]:
    try:
        return {col["name"] for col in inspector.get_columns(table, schema=schema)}
    except Exception:
        return set()


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    # ------------------------------------------------------------------
    # Phase A: reconcile hr.position itself
    # ------------------------------------------------------------------
    position_cols = _columns(inspector, "hr", "position")

    if position_cols and "is_active" not in position_cols:
        op.add_column(
            "position",
            sa.Column(
                "is_active",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("true"),
            ),
            schema="hr",
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_hr_position_active "
            "ON hr.position(organization_id, is_active)"
        )

    op.execute("DROP INDEX IF EXISTS hr.idx_hr_position_deleted")
    for col in ("is_deleted", "deleted_at", "deleted_by_id"):
        if col in position_cols:
            op.drop_column("position", col, schema="hr")

    # ------------------------------------------------------------------
    # Phase B: replay is_deleted drop on tables that still have it.
    # Every step is no-op when the column is already gone (fresh chain).
    # ------------------------------------------------------------------
    for schema, table in _TABLES_NEEDING_IS_ACTIVE:
        cols = _columns(inspector, schema, table)
        if cols and "is_active" not in cols:
            op.add_column(
                table,
                sa.Column(
                    "is_active",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.text("true"),
                ),
                schema=schema,
            )

    # Re-inspect after potential ADD COLUMN above so the subsequent existence
    # checks see the live state.
    inspector = sa.inspect(op.get_bind())

    for schema, table, set_clause in _PER_TABLE_MIGRATION:
        cols = _columns(inspector, schema, table)
        if "is_deleted" in cols:
            op.execute(
                f"UPDATE {schema}.{table} SET {set_clause} WHERE is_deleted = true"
            )

    for schema, table, _set in _PER_TABLE_MIGRATION:
        cols = _columns(inspector, schema, table)
        if "is_deleted" in cols:
            op.drop_column(table, "is_deleted", schema=schema)
        if (schema, table) in _TABLES_WITH_DELETED_AT and "deleted_at" in cols:
            op.drop_column(table, "deleted_at", schema=schema)

    inspector = sa.inspect(op.get_bind())
    for schema, table in _TABLES_WITH_DELETED_AT:
        cols = _columns(inspector, schema, table)
        if "deleted_by_id" in cols:
            op.drop_column(table, "deleted_by_id", schema=schema)


def downgrade() -> None:
    """No downgrade.

    This migration only reconciles divergent environments to a single target
    schema. Reversing it would require knowing which path each environment
    arrived on, which is exactly the ambiguity this migration was created
    to remove.
    """
    pass
