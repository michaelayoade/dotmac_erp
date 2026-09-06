"""Add durable outcomes for Self-Care invoice synchronization.

Revision ID: 20260906_invoice_sync_outcomes
Revises: 20260905_selfcare_mapping_unique
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260906_invoice_sync_outcomes"
down_revision = "20260905_selfcare_mapping_unique"
branch_labels = None
depends_on = None

OUTCOME_TABLE = "dotmac_sub_invoice_sync_outcome"
ISSUE_TABLE = "dotmac_sub_invoice_sync_issue"
SCHEMA = "ar"


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
            "length(projection_fingerprint) = 64",
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
            name="uq_sub_invoice_outcome_org_revision",
        ),
        sa.UniqueConstraint(
            "outcome_id",
            "organization_id",
            name="uq_sub_invoice_outcome_id_org",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_sub_invoice_outcome_org_source",
        OUTCOME_TABLE,
        ["organization_id", "source_invoice_id", "source_updated_at"],
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
                "ar.dotmac_sub_invoice_sync_outcome.outcome_id",
                "ar.dotmac_sub_invoice_sync_outcome.organization_id",
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
    op.drop_index(
        "ix_sub_invoice_issue_org_outcome", table_name=ISSUE_TABLE, schema=SCHEMA
    )
    op.drop_table(ISSUE_TABLE, schema=SCHEMA)
    op.drop_index(
        "ix_sub_invoice_outcome_open_blocked", table_name=OUTCOME_TABLE, schema=SCHEMA
    )
    op.drop_index(
        "ix_sub_invoice_outcome_org_source", table_name=OUTCOME_TABLE, schema=SCHEMA
    )
    op.drop_table(OUTCOME_TABLE, schema=SCHEMA)
