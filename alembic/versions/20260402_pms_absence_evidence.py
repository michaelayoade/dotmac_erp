"""Add structured approved-absence evidence payload to perf.appraisal.

Revision ID: 20260402_pms_absence_evidence
Revises: 20260401_pms_governance_phase1
Create Date: 2026-04-02
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260402_pms_absence_evidence"
down_revision = "20260401_pms_governance_phase1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("appraisal", schema="perf"):
        return

    cols = {c["name"] for c in inspector.get_columns("appraisal", schema="perf")}
    if "approved_absence_evidence" not in cols:
        op.add_column(
            "appraisal",
            sa.Column("approved_absence_evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            schema="perf",
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("appraisal", schema="perf"):
        return

    cols = {c["name"] for c in inspector.get_columns("appraisal", schema="perf")}
    if "approved_absence_evidence" in cols:
        op.drop_column("appraisal", "approved_absence_evidence", schema="perf")
