"""Add contact fields to support ticket.

Add contact_email, contact_phone, billing_address, shipping_address
fields to allow storing contact info directly on tickets (can be
auto-populated from customer or manually entered).

Revision ID: 20260124_ticket_contact
Revises:
Create Date: 2026-01-24

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '20260124_ticket_contact'
down_revision: Union[str, None] = "799a0ecebdd4"  # Fixed: connect to initial schema
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add contact fields to ticket table
    op.add_column(
        'ticket',
        sa.Column('contact_email', sa.String(255), nullable=True,
                  comment='Contact email for this ticket (may differ from customer record)'),
        schema='support'
    )
    op.add_column(
        'ticket',
        sa.Column('contact_phone', sa.String(50), nullable=True,
                  comment='Contact phone for this ticket'),
        schema='support'
    )
    op.add_column(
        'ticket',
        sa.Column('contact_address', sa.Text(), nullable=True,
                  comment='Contact address for this ticket'),
        schema='support'
    )


def downgrade() -> None:
    op.drop_column('ticket', 'contact_address', schema='support')
    op.drop_column('ticket', 'contact_phone', schema='support')
    op.drop_column('ticket', 'contact_email', schema='support')
