"""merge GRN (dev) and mobile-push migration heads

Revision ID: c9a85528bfb5
Revises: 20260609_ap_store_receipt_approval, 20260610_push_devices
Create Date: 2026-06-13 11:47:00.754667

"""

revision = "20260613_merge_grn_push"
down_revision = ('20260609_ap_store_receipt_approval', '20260610_push_devices')
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
