"""Track notifications sent for pending attendance exceptions."""

import sqlalchemy as sa
from alembic import op

revision = "20261125_att_exception_review"
down_revision = "20261104_sub_vendor_supplier_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "attendance_request",
        sa.Column("review_notification_state", sa.JSON(none_as_null=True)),
        schema="attendance",
    )


def downgrade() -> None:
    op.drop_column(
        "attendance_request", "review_notification_state", schema="attendance"
    )
