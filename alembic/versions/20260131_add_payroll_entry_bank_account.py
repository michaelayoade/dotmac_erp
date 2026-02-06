"""add_payroll_entry_bank_account

Revision ID: 20260131_add_payroll_entry_bank_account
Revises: e0696f5adbeb
Create Date: 2026-01-31

"""

from alembic import op
import sqlalchemy as sa


revision = "20260131_add_payroll_entry_bank_account"
down_revision = "e0696f5adbeb"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("payroll_entry", schema="payroll"):
        return

    columns = {
        col["name"] for col in inspector.get_columns("payroll_entry", schema="payroll")
    }
    if "bank_account_id" not in columns:
        op.add_column(
            "payroll_entry",
            sa.Column("bank_account_id", sa.UUID(as_uuid=True), nullable=True),
            schema="payroll",
        )

    fks = {
        fk["name"]
        for fk in inspector.get_foreign_keys("payroll_entry", schema="payroll")
        if fk.get("name")
    }
    if "fk_payroll_entry_bank_account" not in fks:
        op.create_foreign_key(
            "fk_payroll_entry_bank_account",
            "payroll_entry",
            "bank_accounts",
            ["bank_account_id"],
            ["bank_account_id"],
            source_schema="payroll",
            referent_schema="banking",
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("payroll_entry", schema="payroll"):
        return

    fks = {
        fk["name"]
        for fk in inspector.get_foreign_keys("payroll_entry", schema="payroll")
        if fk.get("name")
    }
    if "fk_payroll_entry_bank_account" in fks:
        op.drop_constraint(
            "fk_payroll_entry_bank_account",
            "payroll_entry",
            schema="payroll",
            type_="foreignkey",
        )

    columns = {
        col["name"] for col in inspector.get_columns("payroll_entry", schema="payroll")
    }
    if "bank_account_id" in columns:
        op.drop_column("payroll_entry", "bank_account_id", schema="payroll")
