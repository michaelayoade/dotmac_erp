"""add_offer_portal_token

Revision ID: 20260206_add_offer_portal_token
Revises: e0696f5adbeb
Create Date: 2026-02-06
"""

import sqlalchemy as sa

from alembic import op

revision = "20260206_add_offer_portal_token"
down_revision = "e0696f5adbeb"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "job_offer",
        sa.Column("candidate_access_token", sa.String(length=120), nullable=True),
        schema="recruit",
    )
    op.add_column(
        "job_offer",
        sa.Column(
            "candidate_access_expires", sa.DateTime(timezone=True), nullable=True
        ),
        schema="recruit",
    )
    op.create_index(
        "ix_recruit_job_offer_candidate_access_token",
        "job_offer",
        ["candidate_access_token"],
        unique=False,
        schema="recruit",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_recruit_job_offer_candidate_access_token",
        table_name="job_offer",
        schema="recruit",
    )
    op.drop_column("job_offer", "candidate_access_expires", schema="recruit")
    op.drop_column("job_offer", "candidate_access_token", schema="recruit")
