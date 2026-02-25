"""Add reconciliation match rules and field-level change tracking.

Phase 2 Odoo adaptation:
- Feature A: reconciliation_match_rule + reconciliation_match_log tables
  + source_type/source_id on bank_statement_line_matches
- Feature B: field_change_log table in audit schema

Revision ID: 20260224_add_recon_match_rules_and_field_tracking
Revises: 20260224_add_fiscal_position
Create Date: 2026-02-24
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260224_add_recon_match_rules_and_field_tracking"
down_revision: Union[str, Sequence[str], None] = "20260224_add_fiscal_position"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # ── Feature A: Reconciliation Match Rules ─────────────────────

    # 1. reconciliation_match_rule
    if not inspector.has_table("reconciliation_match_rule", schema="banking"):
        op.create_table(
            "reconciliation_match_rule",
            sa.Column(
                "rule_id",
                postgresql.UUID(as_uuid=True),
                server_default=sa.text("gen_random_uuid()"),
                nullable=False,
            ),
            sa.Column(
                "organization_id",
                postgresql.UUID(as_uuid=True),
                nullable=False,
            ),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("source_doc_type", sa.String(50), nullable=False),
            sa.Column(
                "priority",
                sa.Integer(),
                nullable=False,
                server_default="100",
            ),
            sa.Column(
                "is_system",
                sa.Boolean(),
                nullable=False,
                server_default="false",
            ),
            sa.Column(
                "is_active",
                sa.Boolean(),
                nullable=False,
                server_default="true",
            ),
            sa.Column(
                "conditions",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=True,
            ),
            sa.Column(
                "match_debit",
                sa.Boolean(),
                nullable=False,
                server_default="true",
            ),
            sa.Column(
                "match_credit",
                sa.Boolean(),
                nullable=False,
                server_default="true",
            ),
            sa.Column("amount_tolerance_cents", sa.Integer(), nullable=True),
            sa.Column("date_window_days", sa.Integer(), nullable=True),
            sa.Column(
                "action_type",
                sa.String(30),
                nullable=False,
                server_default="MATCH",
            ),
            sa.Column(
                "min_confidence",
                sa.Integer(),
                nullable=False,
                server_default="90",
            ),
            sa.Column(
                "writeoff_account_id",
                postgresql.UUID(as_uuid=True),
                nullable=True,
            ),
            sa.Column("journal_label_template", sa.String(200), nullable=True),
            sa.Column(
                "match_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
            sa.Column(
                "last_matched_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.PrimaryKeyConstraint("rule_id"),
            sa.ForeignKeyConstraint(
                ["organization_id"],
                ["core_org.organization.organization_id"],
            ),
            sa.ForeignKeyConstraint(
                ["writeoff_account_id"],
                ["gl.account.account_id"],
            ),
            sa.UniqueConstraint(
                "organization_id", "name", name="uq_recon_match_rule_name"
            ),
            schema="banking",
        )
        op.create_index(
            "ix_recon_match_rule_org",
            "reconciliation_match_rule",
            ["organization_id"],
            schema="banking",
        )

    # 2. reconciliation_match_log
    if not inspector.has_table("reconciliation_match_log", schema="banking"):
        op.create_table(
            "reconciliation_match_log",
            sa.Column(
                "log_id",
                postgresql.UUID(as_uuid=True),
                server_default=sa.text("gen_random_uuid()"),
                nullable=False,
            ),
            sa.Column(
                "organization_id",
                postgresql.UUID(as_uuid=True),
                nullable=False,
            ),
            sa.Column(
                "rule_id",
                postgresql.UUID(as_uuid=True),
                nullable=True,
            ),
            sa.Column(
                "statement_line_id",
                postgresql.UUID(as_uuid=True),
                nullable=False,
            ),
            sa.Column("source_doc_type", sa.String(50), nullable=False),
            sa.Column(
                "source_doc_id",
                postgresql.UUID(as_uuid=True),
                nullable=True,
            ),
            sa.Column(
                "journal_line_id",
                postgresql.UUID(as_uuid=True),
                nullable=True,
            ),
            sa.Column(
                "confidence_score",
                sa.Integer(),
                nullable=False,
                server_default="100",
            ),
            sa.Column("explanation", sa.Text(), nullable=False),
            sa.Column("action_taken", sa.String(30), nullable=False),
            sa.Column(
                "matched_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column(
                "confirmed_by_id",
                postgresql.UUID(as_uuid=True),
                nullable=True,
            ),
            sa.Column(
                "confirmed_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
            sa.PrimaryKeyConstraint("log_id"),
            sa.ForeignKeyConstraint(
                ["rule_id"],
                ["banking.reconciliation_match_rule.rule_id"],
            ),
            schema="banking",
        )
        op.create_index(
            "ix_recon_match_log_org_date",
            "reconciliation_match_log",
            ["organization_id", "matched_at"],
            schema="banking",
        )
        op.create_index(
            "ix_recon_match_log_rule",
            "reconciliation_match_log",
            ["rule_id"],
            schema="banking",
        )
        op.create_index(
            "ix_recon_match_log_line",
            "reconciliation_match_log",
            ["statement_line_id"],
            schema="banking",
        )

    # 3. Add source_type + source_id to bank_statement_line_matches
    existing_cols = {
        c["name"]
        for c in inspector.get_columns("bank_statement_line_matches", schema="banking")
    }
    if "source_type" not in existing_cols:
        op.add_column(
            "bank_statement_line_matches",
            sa.Column("source_type", sa.String(50), nullable=True),
            schema="banking",
        )
    if "source_id" not in existing_cols:
        op.add_column(
            "bank_statement_line_matches",
            sa.Column(
                "source_id",
                postgresql.UUID(as_uuid=True),
                nullable=True,
            ),
            schema="banking",
        )

    # ── Feature B: Field-Level Change Tracking ────────────────────

    # Ensure audit schema exists
    op.execute(sa.text("CREATE SCHEMA IF NOT EXISTS audit"))

    if not inspector.has_table("field_change_log", schema="audit"):
        op.create_table(
            "field_change_log",
            sa.Column(
                "log_id",
                postgresql.UUID(as_uuid=True),
                server_default=sa.text("gen_random_uuid()"),
                nullable=False,
            ),
            sa.Column(
                "organization_id",
                postgresql.UUID(as_uuid=True),
                nullable=False,
            ),
            sa.Column("entity_type", sa.String(80), nullable=False),
            sa.Column("entity_id", sa.String(60), nullable=False),
            sa.Column("field_name", sa.String(80), nullable=False),
            sa.Column("field_label", sa.String(120), nullable=True),
            sa.Column("old_value", sa.Text(), nullable=True),
            sa.Column("new_value", sa.Text(), nullable=True),
            sa.Column("old_display", sa.Text(), nullable=True),
            sa.Column("new_display", sa.Text(), nullable=True),
            sa.Column(
                "changed_by_user_id",
                postgresql.UUID(as_uuid=True),
                nullable=True,
            ),
            sa.Column(
                "changed_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column("change_source", sa.String(30), nullable=True),
            sa.Column("request_id", sa.String(60), nullable=True),
            sa.PrimaryKeyConstraint("log_id"),
            schema="audit",
        )
        op.create_index(
            "ix_field_change_entity",
            "field_change_log",
            ["entity_type", "entity_id"],
            schema="audit",
        )
        op.create_index(
            "ix_field_change_org_date",
            "field_change_log",
            ["organization_id", "changed_at"],
            schema="audit",
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Feature B
    audit_tables = inspector.get_table_names(schema="audit")
    if "field_change_log" in audit_tables:
        op.drop_table("field_change_log", schema="audit")

    # Feature A
    banking_tables = inspector.get_table_names(schema="banking")
    if "bank_statement_line_matches" in banking_tables:
        columns = [
            c["name"]
            for c in inspector.get_columns(
                "bank_statement_line_matches", schema="banking"
            )
        ]
        if "source_id" in columns:
            op.drop_column("bank_statement_line_matches", "source_id", schema="banking")
        if "source_type" in columns:
            op.drop_column(
                "bank_statement_line_matches", "source_type", schema="banking"
            )
    if "reconciliation_match_log" in banking_tables:
        op.drop_table("reconciliation_match_log", schema="banking")
    if "reconciliation_match_rule" in banking_tables:
        op.drop_table("reconciliation_match_rule", schema="banking")
