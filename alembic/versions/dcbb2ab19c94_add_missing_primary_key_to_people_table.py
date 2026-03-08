"""Add missing primary key to people table.

The people table was created without a PRIMARY KEY constraint on the `id` column.
The ORM model defines it as primary_key=True, but the DB constraint was never applied.
This prevents other tables from declaring FOREIGN KEY references to people(id).

Revision ID: dcbb2ab19c94
Revises: 6561f90419c7
Create Date: 2026-03-08
"""

from alembic import op
from sqlalchemy import inspect

revision = "dcbb2ab19c94"
down_revision = "6561f90419c7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    pk = insp.get_pk_constraint("people")
    if not pk or not pk.get("constrained_columns"):
        op.create_primary_key("pk_people", "people", ["id"])


def downgrade() -> None:
    op.drop_constraint("pk_people", "people", type_="primary")
