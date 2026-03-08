"""add missing PKs and FK constraints on reconciliation_line and match_log

Revision ID: 0ee821d92395
Revises: 20260308_add_banking_recon_policy_profiles
Create Date: 2026-03-08 13:10:55.570281

"""

from alembic import op
import sqlalchemy as sa

revision = "0ee821d92395"
down_revision = "20260308_add_banking_recon_policy_profiles"
branch_labels = None
depends_on = None


def _has_pk(inspector: sa.engine.reflection.Inspector, table: str, schema: str) -> bool:
    """Check if a table already has a primary key constraint."""
    pk = inspector.get_pk_constraint(table, schema=schema)
    return bool(pk and pk.get("constrained_columns"))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # ── Fix missing PK on gl.journal_entry_line ──
    if inspector.has_table("journal_entry_line", schema="gl"):
        if not _has_pk(inspector, "journal_entry_line", "gl"):
            op.create_primary_key(
                "pk_journal_entry_line",
                "journal_entry_line",
                ["line_id"],
                schema="gl",
            )

    # ── Fix missing PK on banking.bank_statement_lines ──
    if inspector.has_table("bank_statement_lines", schema="banking"):
        if not _has_pk(inspector, "bank_statement_lines", "banking"):
            op.create_primary_key(
                "pk_bank_statement_lines",
                "bank_statement_lines",
                ["line_id"],
                schema="banking",
            )

    # FK: banking.bank_reconciliation_lines.journal_line_id -> gl.journal_entry_line.line_id
    if inspector.has_table("bank_reconciliation_lines", schema="banking"):
        existing_fks = {
            fk["name"]
            for fk in inspector.get_foreign_keys(
                "bank_reconciliation_lines", schema="banking"
            )
            if fk["name"]
        }
        fk_name = "fk_recon_line_journal_line_id"
        if fk_name not in existing_fks:
            op.create_foreign_key(
                fk_name,
                "bank_reconciliation_lines",
                "journal_entry_line",
                ["journal_line_id"],
                ["line_id"],
                source_schema="banking",
                referent_schema="gl",
                ondelete="SET NULL",
            )

    # FK: banking.reconciliation_match_log.statement_line_id -> banking.bank_statement_lines.line_id
    if inspector.has_table("reconciliation_match_log", schema="banking"):
        existing_fks = {
            fk["name"]
            for fk in inspector.get_foreign_keys(
                "reconciliation_match_log", schema="banking"
            )
            if fk["name"]
        }
        fk_name = "fk_match_log_statement_line_id"
        if fk_name not in existing_fks:
            op.create_foreign_key(
                fk_name,
                "reconciliation_match_log",
                "bank_statement_lines",
                ["statement_line_id"],
                ["line_id"],
                source_schema="banking",
                referent_schema="banking",
                ondelete="CASCADE",
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("reconciliation_match_log", schema="banking"):
        existing_fks = {
            fk["name"]
            for fk in inspector.get_foreign_keys(
                "reconciliation_match_log", schema="banking"
            )
            if fk["name"]
        }
        if "fk_match_log_statement_line_id" in existing_fks:
            op.drop_constraint(
                "fk_match_log_statement_line_id",
                "reconciliation_match_log",
                schema="banking",
                type_="foreignkey",
            )

    if inspector.has_table("bank_reconciliation_lines", schema="banking"):
        existing_fks = {
            fk["name"]
            for fk in inspector.get_foreign_keys(
                "bank_reconciliation_lines", schema="banking"
            )
            if fk["name"]
        }
        if "fk_recon_line_journal_line_id" in existing_fks:
            op.drop_constraint(
                "fk_recon_line_journal_line_id",
                "bank_reconciliation_lines",
                schema="banking",
                type_="foreignkey",
            )

    # Note: We do NOT drop the PKs in downgrade — they should have existed from the start
