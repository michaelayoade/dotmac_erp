"""Record honest mobile push outcomes without replaying historical sends.

Revision ID: 20260918_push_delivery_outcomes
Revises: 20260915_merge_workforce_kpi
"""

from alembic import op
import sqlalchemy as sa

revision = "20260918_push_delivery_outcomes"
down_revision = "20260915_merge_workforce_kpi"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "notification",
        sa.Column(
            "push_status",
            sa.String(24),
            nullable=False,
            server_default="pending",
        ),
        schema="public",
    )
    op.add_column(
        "notification",
        sa.Column("push_retry_count", sa.Integer(), nullable=False, server_default="0"),
        schema="public",
    )
    op.add_column(
        "notification",
        sa.Column("push_next_retry_at", sa.DateTime(), nullable=True),
        schema="public",
    )
    # Historical true values are ambiguous. Preserve the old anti-replay flag,
    # but do not retroactively claim successful provider acceptance.
    op.execute(
        "UPDATE public.notification SET push_status = 'legacy_processed' WHERE push_sent = true"
    )


def downgrade() -> None:
    # Old workers only understand push_sent. Preserve terminal suppression on
    # rollback so no-device, failed or expired records are not re-broadcast.
    op.execute(
        "UPDATE public.notification SET push_sent = true WHERE push_status IN ('no_device', 'failed', 'expired', 'partial', 'legacy_processed')"
    )
    op.drop_column("notification", "push_next_retry_at", schema="public")
    op.drop_column("notification", "push_retry_count", schema="public")
    op.drop_column("notification", "push_status", schema="public")
