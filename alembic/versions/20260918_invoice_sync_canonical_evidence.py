"""Separate canonical-scheme invoice sync evidence from unverifiable legacy rows.

Revision ID: 20260918_invoice_sync_canonical_evidence
Revises: 20260915_merge_workforce_kpi

Michael's ruling: every row `20260906_invoice_sync_outcomes` ever wrote used
ERP's own now-deleted, confirmed-buggy local fingerprint algorithm, not a
digest forwarded from Self-Care's canonical projection. ERP never archived
the original full projection that produced a stored `projection_fingerprint`,
and Self-Care's feed reconstructs from current state, not a historical
snapshot — a present-day re-fetch cannot prove a historical match. Those rows
are therefore preserved exactly as written, forever, but structurally
excluded from ever counting as canonical parity or cutover-success evidence.

This migration is 100% metadata-only: two table renames, matching
constraint/index renames (needed only where a name would otherwise collide
with the fresh canonical table below — CHECK/FK constraint names are unique
per relation, not per schema, so those keep their original names), a
privilege REVOKE + freeze comment on the renamed archive, and a `create_table`
for a fresh, empty pair of tables at the now-freed familiar names. There is
NO data copy, NO backfill, and NO row rewrite anywhere in this migration — see
`tests/migrations/test_invoice_sync_canonical_evidence_migration.py`, which
greps this file's source for real INSERT/UPDATE/DELETE statement forms (not
bare keywords — the REVOKE list below legitimately contains the words
"INSERT"/"UPDATE"/"DELETE" as a privilege list, not as DML) and asserts none
exist.

Canonical rows require a `digest_version` (SmallInteger, NOT NULL, no
default — every insert must be explicit) and widen the per-revision unique
constraint to `(organization_id, source_invoice_id, source_updated_at,
digest_version)`. `app.services.dotmac_sub.invoice_sync_shadow`'s cursor is
scoped to `digest_version == SUPPORTED_DIGEST_VERSION`, so it naturally
re-observes every invoice whose only existing evidence is a legacy row (at
that invoice's CURRENT revision only — Self-Care's feed cannot replay
history, which is accepted, not a defect of this migration).

PRE-DEPLOY GATE THIS MIGRATION CANNOT VERIFY FROM THE REPO ALONE: confirm no
external BI/warehouse/logical-replication consumer binds
`ar.dotmac_sub_invoice_sync_outcome` by name before this migration is ever
run against a real database. This repo's own code has no such consumer
(confirmed by static search across `app/`), but a consumer outside this repo
cannot be ruled out from here. This is a rollout question, not a design
blocker for this commit.

`downgrade()` is DESTRUCTIVE to canonical evidence only: it drops both fresh
canonical tables (discarding every canonical-scheme row recorded since this
migration ran) and restores the legacy archive to its original names,
constraint names and grants. Legacy rows are never touched by either
direction — `upgrade()` only renames and freezes them, and `downgrade()`
only un-freezes and renames them back.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260918_invoice_sync_canonical_evidence"
down_revision = "20260915_merge_workforce_kpi"
branch_labels = None
depends_on = None

SCHEMA = "ar"
OUTCOME_TABLE = "dotmac_sub_invoice_sync_outcome"
ISSUE_TABLE = "dotmac_sub_invoice_sync_issue"
LEGACY_OUTCOME_TABLE = "dotmac_sub_invoice_sync_outcome_legacy"
LEGACY_ISSUE_TABLE = "dotmac_sub_invoice_sync_issue_legacy"

FROZEN_COMMENT = (
    "FROZEN. projection_fingerprint here was computed by ERP's deleted local "
    "algorithm, not forwarded from Self-Care's canonical digest. "
    "Historically unverifiable: never counted as canonical parity or "
    "cutover evidence. Read-only to app_user."
)


def _protect(table: str) -> None:
    qualified = f"{SCHEMA}.{table}"
    op.execute(f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {table}_tenant_isolation
            ON {qualified}
            USING (organization_id = public.app_current_tenant_id())
            WITH CHECK (organization_id = public.app_current_tenant_id())
        """
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {qualified} TO app_user")


def upgrade() -> None:
    # --- A. Archive the live tables (rename only — no data movement) -------
    op.execute(f"ALTER TABLE {SCHEMA}.{OUTCOME_TABLE} RENAME TO {LEGACY_OUTCOME_TABLE}")
    op.execute(f"ALTER TABLE {SCHEMA}.{ISSUE_TABLE} RENAME TO {LEGACY_ISSUE_TABLE}")

    # Unique constraints and indexes collide with the fresh canonical table's
    # names below, so they are renamed. CHECK/FK constraint names and RLS
    # policy names are unique per relation, not per schema, so they keep
    # their original names.
    op.execute(
        f"ALTER TABLE {SCHEMA}.{LEGACY_OUTCOME_TABLE} "
        "RENAME CONSTRAINT uq_sub_invoice_outcome_org_revision "
        "TO uq_sub_invoice_outcome_legacy_org_revision"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.{LEGACY_OUTCOME_TABLE} "
        "RENAME CONSTRAINT uq_sub_invoice_outcome_id_org "
        "TO uq_sub_invoice_outcome_legacy_id_org"
    )
    op.execute(
        f"ALTER INDEX {SCHEMA}.dotmac_sub_invoice_sync_outcome_pkey "
        "RENAME TO dotmac_sub_invoice_sync_outcome_legacy_pkey"
    )
    op.execute(
        f"ALTER INDEX {SCHEMA}.ix_sub_invoice_outcome_org_source "
        "RENAME TO ix_sub_invoice_outcome_legacy_org_source"
    )
    op.execute(
        f"ALTER INDEX {SCHEMA}.ix_sub_invoice_outcome_open_blocked "
        "RENAME TO ix_sub_invoice_outcome_legacy_open_blocked"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.{LEGACY_ISSUE_TABLE} "
        "RENAME CONSTRAINT uq_sub_invoice_issue_outcome_fingerprint "
        "TO uq_sub_invoice_issue_legacy_outcome_fingerprint"
    )
    op.execute(
        f"ALTER INDEX {SCHEMA}.dotmac_sub_invoice_sync_issue_pkey "
        "RENAME TO dotmac_sub_invoice_sync_issue_legacy_pkey"
    )
    op.execute(
        f"ALTER INDEX {SCHEMA}.ix_sub_invoice_issue_org_outcome "
        "RENAME TO ix_sub_invoice_issue_legacy_org_outcome"
    )

    # Freeze: app_user keeps SELECT (forensic/audit reads, still tenant-RLS
    # scoped) but loses every write privilege. app_admin (BYPASSRLS, owns the
    # table — see tests/unit/test_migration_authority_policies.py) remains
    # able to touch it in a future migration if ever genuinely required; that
    # escape hatch is not used here.
    op.execute(
        f"REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON TABLE "
        f"{SCHEMA}.{LEGACY_OUTCOME_TABLE} FROM app_user"
    )
    op.execute(
        f"REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON TABLE "
        f"{SCHEMA}.{LEGACY_ISSUE_TABLE} FROM app_user"
    )
    op.execute(
        f"COMMENT ON TABLE {SCHEMA}.{LEGACY_OUTCOME_TABLE} IS '{FROZEN_COMMENT}'"
    )

    # --- B. A fresh, empty canonical table at the freed familiar name ------
    op.create_table(
        OUTCOME_TABLE,
        sa.Column(
            "outcome_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_invoice_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("contract_version", sa.String(length=80), nullable=False),
        sa.Column("source_kind", sa.String(length=40), nullable=False),
        sa.Column("disposition", sa.String(length=24), nullable=False),
        sa.Column("projection_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("digest_version", sa.SmallInteger(), nullable=False),
        sa.Column("issue_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("occurrence_count", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "disposition IN ('ready', 'blocked', 'not_applicable')",
            name="ck_sub_invoice_outcome_disposition",
        ),
        sa.CheckConstraint(
            "contract_version = 'invoice-accounting-sync.v2'",
            name="ck_sub_invoice_outcome_contract",
        ),
        sa.CheckConstraint(
            "source_kind IN ('native', 'splynx_legacy')",
            name="ck_sub_invoice_outcome_source_kind",
        ),
        sa.CheckConstraint(
            "length(projection_fingerprint) = 64 AND "
            "projection_fingerprint = lower(projection_fingerprint)",
            name="ck_sub_invoice_outcome_fingerprint",
        ),
        sa.CheckConstraint(
            "(disposition = 'blocked' AND issue_count > 0) OR "
            "(disposition <> 'blocked' AND issue_count = 0)",
            name="ck_sub_invoice_outcome_issue_count",
        ),
        sa.CheckConstraint(
            "occurrence_count > 0", name="ck_sub_invoice_outcome_occurrences"
        ),
        sa.CheckConstraint(
            "digest_version >= 1", name="ck_sub_invoice_outcome_digest_version"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["core_org.organization.organization_id"],
            name="fk_sub_invoice_outcome_org",
        ),
        sa.PrimaryKeyConstraint("outcome_id"),
        sa.UniqueConstraint(
            "organization_id",
            "source_invoice_id",
            "source_updated_at",
            "digest_version",
            name="uq_sub_invoice_outcome_org_revision_digest",
        ),
        sa.UniqueConstraint(
            "outcome_id",
            "organization_id",
            name="uq_sub_invoice_outcome_id_org",
        ),
        schema=SCHEMA,
    )
    op.execute(
        f"COMMENT ON COLUMN {SCHEMA}.{OUTCOME_TABLE}.projection_fingerprint IS "
        "'forwarded verbatim from Self-Care''s canonical digest; never "
        "computed locally.'"
    )
    # Cursor-oriented replacement for ix_sub_invoice_outcome_org_source,
    # matching how invoice_sync_shadow._latest_canonical_position actually
    # queries (organization + digest_version, newest revision first).
    op.create_index(
        "ix_sub_invoice_outcome_cursor",
        OUTCOME_TABLE,
        [
            "organization_id",
            "digest_version",
            sa.text("source_updated_at DESC"),
            sa.text("source_invoice_id DESC"),
        ],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_sub_invoice_outcome_open_blocked",
        OUTCOME_TABLE,
        ["organization_id", "last_seen_at"],
        schema=SCHEMA,
        postgresql_where=sa.text("disposition = 'blocked' AND resolved_at IS NULL"),
    )

    op.create_table(
        ISSUE_TABLE,
        sa.Column(
            "issue_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("outcome_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("issue_code", sa.String(length=80), nullable=False),
        sa.Column("source_line_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("expected_amount", sa.Numeric(20, 6), nullable=True),
        sa.Column("actual_amount", sa.Numeric(20, 6), nullable=True),
        sa.Column("issue_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "expected_amount IS NOT NULL OR actual_amount IS NOT NULL "
            "OR source_line_id IS NOT NULL OR issue_code <> ''",
            name="ck_sub_invoice_issue_has_evidence",
        ),
        sa.ForeignKeyConstraint(
            ["outcome_id", "organization_id"],
            [
                f"{SCHEMA}.{OUTCOME_TABLE}.outcome_id",
                f"{SCHEMA}.{OUTCOME_TABLE}.organization_id",
            ],
            ondelete="CASCADE",
            name="fk_sub_invoice_issue_outcome_org",
        ),
        sa.PrimaryKeyConstraint("issue_id"),
        sa.UniqueConstraint(
            "outcome_id",
            "issue_fingerprint",
            name="uq_sub_invoice_issue_outcome_fingerprint",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_sub_invoice_issue_org_outcome",
        ISSUE_TABLE,
        ["organization_id", "outcome_id"],
        schema=SCHEMA,
    )

    _protect(OUTCOME_TABLE)
    _protect(ISSUE_TABLE)


def downgrade() -> None:
    # DESTRUCTIVE to canonical evidence only: every canonical-scheme row
    # recorded since this migration ran is discarded here. Legacy rows are
    # never touched — only unfrozen and renamed back to their original names.
    op.drop_index(
        "ix_sub_invoice_issue_org_outcome", table_name=ISSUE_TABLE, schema=SCHEMA
    )
    op.drop_table(ISSUE_TABLE, schema=SCHEMA)
    op.drop_index(
        "ix_sub_invoice_outcome_open_blocked", table_name=OUTCOME_TABLE, schema=SCHEMA
    )
    op.drop_index(
        "ix_sub_invoice_outcome_cursor", table_name=OUTCOME_TABLE, schema=SCHEMA
    )
    op.drop_table(OUTCOME_TABLE, schema=SCHEMA)

    # Privileges do NOT automatically restore when a table is renamed back —
    # the REVOKE in upgrade() does not auto-reverse, so it must be explicitly
    # re-granted before the archive resumes its original name/role.
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE "
        f"{SCHEMA}.{LEGACY_OUTCOME_TABLE} TO app_user"
    )
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE "
        f"{SCHEMA}.{LEGACY_ISSUE_TABLE} TO app_user"
    )
    op.execute(f"COMMENT ON TABLE {SCHEMA}.{LEGACY_OUTCOME_TABLE} IS NULL")

    op.execute(
        f"ALTER TABLE {SCHEMA}.{LEGACY_OUTCOME_TABLE} "
        "RENAME CONSTRAINT uq_sub_invoice_outcome_legacy_org_revision "
        "TO uq_sub_invoice_outcome_org_revision"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.{LEGACY_OUTCOME_TABLE} "
        "RENAME CONSTRAINT uq_sub_invoice_outcome_legacy_id_org "
        "TO uq_sub_invoice_outcome_id_org"
    )
    op.execute(
        f"ALTER INDEX {SCHEMA}.dotmac_sub_invoice_sync_outcome_legacy_pkey "
        "RENAME TO dotmac_sub_invoice_sync_outcome_pkey"
    )
    op.execute(
        f"ALTER INDEX {SCHEMA}.ix_sub_invoice_outcome_legacy_org_source "
        "RENAME TO ix_sub_invoice_outcome_org_source"
    )
    op.execute(
        f"ALTER INDEX {SCHEMA}.ix_sub_invoice_outcome_legacy_open_blocked "
        "RENAME TO ix_sub_invoice_outcome_open_blocked"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.{LEGACY_ISSUE_TABLE} "
        "RENAME CONSTRAINT uq_sub_invoice_issue_legacy_outcome_fingerprint "
        "TO uq_sub_invoice_issue_outcome_fingerprint"
    )
    op.execute(
        f"ALTER INDEX {SCHEMA}.dotmac_sub_invoice_sync_issue_legacy_pkey "
        "RENAME TO dotmac_sub_invoice_sync_issue_pkey"
    )
    op.execute(
        f"ALTER INDEX {SCHEMA}.ix_sub_invoice_issue_legacy_org_outcome "
        "RENAME TO ix_sub_invoice_issue_org_outcome"
    )

    op.execute(f"ALTER TABLE {SCHEMA}.{LEGACY_ISSUE_TABLE} RENAME TO {ISSUE_TABLE}")
    op.execute(f"ALTER TABLE {SCHEMA}.{LEGACY_OUTCOME_TABLE} RENAME TO {OUTCOME_TABLE}")
