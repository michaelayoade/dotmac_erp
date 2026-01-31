"""merge_email_module_and_other_heads

Revision ID: d98bd2539ace
Revises: 20260131_add_audit_events_org_id, 20260131_demotion, 20260131_mandatory_training, 20260131_update_email_module_enum
Create Date: 2026-01-31 12:19:26.405884

"""

from alembic import op
import sqlalchemy as sa


revision = 'd98bd2539ace'
down_revision = ('20260131_add_audit_events_org_id', '20260131_demotion', '20260131_mandatory_training', '20260131_update_email_module_enum')
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
