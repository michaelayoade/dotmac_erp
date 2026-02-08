"""Add categorization fields to bank statement lines.

Revision ID: 20260207_add_categorization_fields
Revises: 20260207_add_expense_claim_requested_approver_id
Create Date: 2026-02-07
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260207_add_categorization_fields"
down_revision = "20260207_add_expense_claim_requested_approver_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Create the categorization_status enum type if it doesn't exist
    existing_enums = [e["name"] for e in inspector.get_enums(schema="banking")]
    if "categorization_status" not in existing_enums:
        categorization_status = sa.Enum(
            "SUGGESTED",
            "ACCEPTED",
            "REJECTED",
            "AUTO_APPLIED",
            "FLAGGED",
            name="categorization_status",
            schema="banking",
        )
        categorization_status.create(bind)

    # Add columns to bank_statement_lines if they don't exist
    if inspector.has_table("bank_statement_lines", schema="banking"):
        columns = {
            col["name"]
            for col in inspector.get_columns("bank_statement_lines", schema="banking")
        }

        if "categorization_status" not in columns:
            op.add_column(
                "bank_statement_lines",
                sa.Column(
                    "categorization_status",
                    sa.Enum(
                        "SUGGESTED",
                        "ACCEPTED",
                        "REJECTED",
                        "AUTO_APPLIED",
                        "FLAGGED",
                        name="categorization_status",
                        schema="banking",
                    ),
                    nullable=True,
                ),
                schema="banking",
            )

        if "suggested_account_id" not in columns:
            op.add_column(
                "bank_statement_lines",
                sa.Column(
                    "suggested_account_id",
                    sa.dialects.postgresql.UUID(as_uuid=True),
                    sa.ForeignKey("gl.account.account_id"),
                    nullable=True,
                ),
                schema="banking",
            )

        if "suggested_rule_id" not in columns:
            op.add_column(
                "bank_statement_lines",
                sa.Column(
                    "suggested_rule_id",
                    sa.dialects.postgresql.UUID(as_uuid=True),
                    sa.ForeignKey("banking.transaction_rule.rule_id"),
                    nullable=True,
                ),
                schema="banking",
            )

        if "suggested_confidence" not in columns:
            op.add_column(
                "bank_statement_lines",
                sa.Column(
                    "suggested_confidence",
                    sa.Integer(),
                    nullable=True,
                ),
                schema="banking",
            )

        if "suggested_match_reason" not in columns:
            op.add_column(
                "bank_statement_lines",
                sa.Column(
                    "suggested_match_reason",
                    sa.String(200),
                    nullable=True,
                ),
                schema="banking",
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("bank_statement_lines", schema="banking"):
        columns = {
            col["name"]
            for col in inspector.get_columns("bank_statement_lines", schema="banking")
        }

        for col_name in [
            "suggested_match_reason",
            "suggested_confidence",
            "suggested_rule_id",
            "suggested_account_id",
            "categorization_status",
        ]:
            if col_name in columns:
                op.drop_column("bank_statement_lines", col_name, schema="banking")

    existing_enums = [e["name"] for e in inspector.get_enums(schema="banking")]
    if "categorization_status" in existing_enums:
        sa.Enum(name="categorization_status", schema="banking").drop(bind)
