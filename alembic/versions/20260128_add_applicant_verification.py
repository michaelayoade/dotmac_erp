"""Add applicant verification fields for status tracking.

Revision ID: 20260128_add_applicant_verification
Revises: 20260128_add_organization_slug
Create Date: 2026-01-28

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260128_add_applicant_verification"
down_revision = "20260128_add_organization_slug"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add verification fields to job_applicant table
    op.add_column(
        "job_applicant",
        sa.Column(
            "email_verified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="Whether applicant email has been verified for status tracking",
        ),
        schema="recruit",
    )
    op.add_column(
        "job_applicant",
        sa.Column(
            "verification_token",
            sa.String(100),
            nullable=True,
            comment="Token for verifying email to check application status",
        ),
        schema="recruit",
    )
    op.add_column(
        "job_applicant",
        sa.Column(
            "verification_token_expires",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Expiration time for verification token",
        ),
        schema="recruit",
    )
    # Index for token lookups
    op.create_index(
        "ix_recruit_job_applicant_verification_token",
        "job_applicant",
        ["verification_token"],
        schema="recruit",
        postgresql_where=sa.text("verification_token IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_recruit_job_applicant_verification_token",
        table_name="job_applicant",
        schema="recruit",
    )
    op.drop_column("job_applicant", "verification_token_expires", schema="recruit")
    op.drop_column("job_applicant", "verification_token", schema="recruit")
    op.drop_column("job_applicant", "email_verified", schema="recruit")
