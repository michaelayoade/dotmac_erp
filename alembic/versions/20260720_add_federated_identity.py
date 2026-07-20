"""Add ERP-owned OIDC identity bindings.

Revision ID: 20260720_federated_identity
Revises: current migration heads
Create Date: 2026-07-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260720_federated_identity"
down_revision: str | tuple[str, ...] = (
    "20260707_dept_template_perspective",
    "20260712_encrypt_secret_settings",
    "20260715_dept_discipline_workflow",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "federated_identities",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("issuer", sa.String(length=512), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("last_authenticated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "issuer",
            "subject",
            name="uq_federated_identities_issuer_subject",
        ),
        sa.UniqueConstraint(
            "person_id",
            "issuer",
            name="uq_federated_identities_person_issuer",
        ),
    )
    op.create_index(
        op.f("ix_federated_identities_person_id"),
        "federated_identities",
        ["person_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_federated_identities_person_id"),
        table_name="federated_identities",
    )
    op.drop_table("federated_identities")
