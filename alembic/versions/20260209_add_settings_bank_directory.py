"""Add org bank directory under settings schema.

Revision ID: 20260209_add_settings_bank_directory
Revises: 20260208_merge_heads
Create Date: 2026-02-09
"""

from __future__ import annotations

import csv
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260209_add_settings_bank_directory"
down_revision = "20260208_merge_heads"
branch_labels = None
depends_on = None


def _load_bank_rows() -> list[tuple[str, str]]:
    csv_path = Path(__file__).resolve().parents[2] / "app" / "data" / "bank_names.csv"
    if not csv_path.exists():
        raise FileNotFoundError(
            f"Bank directory seed file missing at {csv_path}. "
            "Include it in deploy artifacts or update the migration."
        )

    rows: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            bank_name = (raw.get("Bank Name") or "").strip()
            bank_sort_code = (raw.get("Bank Sort Code") or "").strip()
            if not bank_name or not bank_sort_code:
                continue
            key = (bank_name.lower(), bank_sort_code)
            if key in seen:
                continue
            seen.add(key)
            rows.append((bank_name, bank_sort_code))
    return rows


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS settings")
    op.create_table(
        "org_bank_directory",
        sa.Column(
            "org_bank_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("core_org.organization.organization_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("bank_name", sa.String(200), nullable=False),
        sa.Column("bank_sort_code", sa.String(20), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "organization_id",
            "bank_name",
            name="uq_org_bank_directory_org_bank_name",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "bank_sort_code",
            name="uq_org_bank_directory_org_bank_sort_code",
        ),
        schema="settings",
    )
    op.create_index(
        "ix_org_bank_directory_org",
        "org_bank_directory",
        ["organization_id"],
        schema="settings",
    )

    conn = op.get_bind()
    org_ids = [
        row[0]
        for row in conn.execute(
            sa.text("SELECT organization_id FROM core_org.organization")
        ).all()
    ]
    bank_rows = _load_bank_rows()
    if not org_ids or not bank_rows:
        return

    table = sa.Table(
        "org_bank_directory",
        sa.MetaData(),
        sa.Column("org_bank_id", postgresql.UUID(as_uuid=True)),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True)),
        sa.Column("bank_name", sa.String),
        sa.Column("bank_sort_code", sa.String),
        sa.Column("is_active", sa.Boolean),
        schema="settings",
    )
    data = []
    for org_id in org_ids:
        for bank_name, bank_sort_code in bank_rows:
            data.append(
                {
                    "organization_id": org_id,
                    "bank_name": bank_name,
                    "bank_sort_code": bank_sort_code,
                    "is_active": True,
                }
            )

    insert_stmt = postgresql.insert(table).values(data).on_conflict_do_nothing()
    conn.execute(insert_stmt)


def downgrade() -> None:
    op.drop_index(
        "ix_org_bank_directory_org",
        table_name="org_bank_directory",
        schema="settings",
    )
    op.drop_table("org_bank_directory", schema="settings")
