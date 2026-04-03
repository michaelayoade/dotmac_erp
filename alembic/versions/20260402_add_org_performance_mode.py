"""Add org-level performance_mode enum field.

Revision ID: 20260402_add_org_performance_mode
Revises: 20260402_add_unique_material_request_crm_id, 20260402_pms_absence_evidence
Create Date: 2026-04-02
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260402_add_org_performance_mode"
down_revision = (
    "20260402_add_unique_material_request_crm_id",
    "20260402_pms_absence_evidence",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    existing_enums = {enum["name"] for enum in inspector.get_enums(schema="core_org")}
    if "performance_mode" not in existing_enums:
        postgresql.ENUM(
            "PRIVATE",
            "GOVERNMENT_PMS",
            "HYBRID",
            name="performance_mode",
            schema="core_org",
        ).create(bind, checkfirst=True)

    if inspector.has_table("organization", schema="core_org"):
        cols = {
            column["name"]
            for column in inspector.get_columns("organization", schema="core_org")
        }
        if "performance_mode" not in cols:
            op.add_column(
                "organization",
                sa.Column(
                    "performance_mode",
                    postgresql.ENUM(
                        "PRIVATE",
                        "GOVERNMENT_PMS",
                        "HYBRID",
                        name="performance_mode",
                        schema="core_org",
                        create_type=False,
                    ),
                    nullable=False,
                    server_default=sa.text("'PRIVATE'"),
                ),
                schema="core_org",
            )
        # Phase 2 backward-compatible mapping:
        # legacy pms_ohcsf_enabled=true => GOVERNMENT_PMS (unless already non-private)
        op.execute(
            """
            UPDATE core_org.organization
            SET performance_mode = 'GOVERNMENT_PMS'::core_org.performance_mode
            WHERE COALESCE(pms_ohcsf_enabled, false) = true
              AND performance_mode = 'PRIVATE'::core_org.performance_mode
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("organization", schema="core_org"):
        cols = {
            column["name"]
            for column in inspector.get_columns("organization", schema="core_org")
        }
        if "performance_mode" in cols:
            op.drop_column("organization", "performance_mode", schema="core_org")

    postgresql.ENUM(
        "PRIVATE",
        "GOVERNMENT_PMS",
        "HYBRID",
        name="performance_mode",
        schema="core_org",
    ).drop(bind, checkfirst=True)
