"""Add durable, tenant-scoped records for the file-object orphan cleanup task.

Revision ID: 20260928_file_orphan_cleanup_records
Revises: 20260922_mr_partial_issue

Schema-only slice (2a). One row per apply invocation
(``file_orphan_cleanup_runs``) and one row per candidate key processed
(``file_orphan_cleanup_deletions``), following the ``ar`` schema's
outcome/issue composite-FK pattern in ``20260906_invoice_sync_outcomes.py``
(parent/child tables, a composite FK carrying the tenant column, and a
unique constraint on ``(id, organization_id)`` to support it). These tables
are ERP-owned records of what the cleanup task decided and did; they are NOT
part of the ``dotmac_files`` module's own schema (``mod_files``), which owns
the managed-object metadata being reconciled against.

RLS predicate: ``app/rls.py`` documents two co-primed GUCs — ERP-native
tables read ``app.current_organization_id`` directly, while shared-module
tables (like ``dotmac_files``' own) read ``app.current_tenant`` via
``public.app_current_tenant_id()``. These tables are ERP-owned, so they use
the ERP-native GUC, in the exact ``NULLIF(current_setting(..., true), '')::uuid``
form ``sync.source_correlation`` uses (``20260825_retire_dotmac_crm.py``) —
not the newer, unguarded ``current_setting(...)::uuid`` form in
``20260920_configurable_departmental_kpis.py``, which raises instead of
degrading to NULL when the GUC is unset.

``app.tasks.file_object_reconciliation.clean_tenant_file_objects`` (slice 2b)
writes these rows on every authorized apply: a "planned" outcome row is
written for every candidate key in the SAME commit as the run's "started"
row, before any delete is attempted, so the raw ``storage_key`` needed for a
restore is durable even if a delete happens but its own terminal-outcome
write never lands. ``app_user`` is granted SELECT/INSERT/UPDATE only — never
DELETE — on both tables: nothing in ERP ever deletes a cleanup record, and
granting DELETE would let a compromised or buggy tenant-scoped caller erase
its own deletion evidence.

This migration is irreversible once applied (see ``downgrade()`` below):
dropping these tables destroys the only durable evidence of what a cleanup
apply actually did, which a restore may depend on indefinitely.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260928_file_orphan_cleanup_records"
down_revision = "20260922_mr_partial_issue"
branch_labels = None
depends_on = None

RUN_TABLE = "file_orphan_cleanup_runs"
DELETION_TABLE = "file_orphan_cleanup_deletions"
SCHEMA = "public"


def _uuid() -> postgresql.UUID:
    return postgresql.UUID(as_uuid=True)


def _protect(table: str) -> None:
    qualified = f"{SCHEMA}.{table}"
    op.execute(f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {table}_tenant_isolation
            ON {qualified}
            USING (
                organization_id = NULLIF(
                    current_setting('app.current_organization_id', true), ''
                )::uuid
            )
            WITH CHECK (
                organization_id = NULLIF(
                    current_setting('app.current_organization_id', true), ''
                )::uuid
            )
        """
    )
    # No DELETE: nothing in ERP ever deletes a cleanup run or outcome row, and
    # granting DELETE would let a tenant-scoped caller erase its own
    # deletion evidence -- see the module docstring.
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON TABLE {qualified} TO app_user")


def upgrade() -> None:
    op.create_table(
        RUN_TABLE,
        sa.Column(
            "id",
            _uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", _uuid(), nullable=False),
        sa.Column("plan_digest", sa.String(length=64), nullable=False),
        sa.Column("older_than", sa.DateTime(timezone=True), nullable=False),
        sa.Column("plan_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider_code", sa.String(length=100), nullable=False),
        sa.Column("candidate_count", sa.Integer(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("invocation_id", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "outcome_counts",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(plan_digest) = 64 AND plan_digest = lower(plan_digest)",
            name="ck_file_orphan_cleanup_run_digest",
        ),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'partial_failure')",
            name="ck_file_orphan_cleanup_run_status",
        ),
        sa.CheckConstraint(
            "candidate_count >= 0",
            name="ck_file_orphan_cleanup_run_candidate_count",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["core_org.organization.organization_id"],
            name="fk_file_orphan_cleanup_run_org",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "invocation_id",
            name="uq_file_orphan_cleanup_run_org_invocation",
        ),
        sa.UniqueConstraint(
            "id",
            "organization_id",
            name="uq_file_orphan_cleanup_run_id_org",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_file_orphan_cleanup_run_org_started",
        RUN_TABLE,
        ["organization_id", "started_at"],
        schema=SCHEMA,
    )

    op.create_table(
        DELETION_TABLE,
        sa.Column(
            "id",
            _uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", _uuid(), nullable=False),
        sa.Column("run_id", _uuid(), nullable=False),
        # The raw storage key is stored deliberately: it is an opaque,
        # module-generated identifier needed to restore the object from a
        # versioned bucket if a deletion is later found to have been wrong.
        # It carries no customer content itself and is protected by the same
        # RLS policy as every other column here.
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("key_digest", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=30), nullable=False),
        sa.Column("error_class", sa.Text(), nullable=True),
        sa.Column("observed_last_modified", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "outcome IN ("
            "'planned', 'deleted', 'rechecked_referenced', 'already_absent', "
            "'rechecked_too_new', 'failed'"
            ")",
            name="ck_file_orphan_cleanup_deletion_outcome",
        ),
        sa.CheckConstraint(
            "length(key_digest) = 64 AND key_digest = lower(key_digest)",
            name="ck_file_orphan_cleanup_deletion_key_digest",
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "organization_id"],
            [
                f"{SCHEMA}.{RUN_TABLE}.id",
                f"{SCHEMA}.{RUN_TABLE}.organization_id",
            ],
            ondelete="CASCADE",
            name="fk_file_orphan_cleanup_deletion_run_org",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "run_id",
            "storage_key",
            name="uq_file_orphan_cleanup_deletion_org_run_key",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_file_orphan_cleanup_deletion_org_run",
        DELETION_TABLE,
        ["organization_id", "run_id"],
        schema=SCHEMA,
    )

    _protect(RUN_TABLE)
    _protect(DELETION_TABLE)


def downgrade() -> None:
    # Refused outright, unconditionally -- never gated on a row count. A row
    # count taken under FORCE ROW LEVEL SECURITY is not a valid guard: the
    # migration connection's role and GUC state determine what it can even
    # see, so "zero rows visible" is not evidence the tables are empty, and a
    # guard that can be silently defeated by running as the wrong role is not
    # a guard. These tables are the only durable evidence of what a cleanup
    # apply actually deleted; dropping them destroys that evidence and a
    # restore may depend on it indefinitely. If this revision must ever be
    # rolled back, do it by hand after confirming, out of band, that no
    # retained run's evidence is still needed.
    raise RuntimeError(
        "file orphan cleanup record tables cannot be downgraded: dropping "
        "them destroys the only durable evidence of what a cleanup apply "
        "actually deleted"
    )
