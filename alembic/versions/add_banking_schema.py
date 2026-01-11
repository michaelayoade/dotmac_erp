"""Add banking schema and tables.

Revision ID: add_banking_schema
Revises: make_person_org_required
Create Date: 2025-02-04
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "add_banking_schema"
down_revision = "make_person_org_required"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS banking")

    from app.db import Base
    import app.models.ifrs  # noqa: F401 - register models

    bind = op.get_bind()
    Base.metadata.create_all(
        bind=bind,
        tables=[t for t in Base.metadata.sorted_tables if t.schema == "banking"],
    )


def downgrade() -> None:
    from app.db import Base
    import app.models.ifrs  # noqa: F401

    bind = op.get_bind()

    banking_tables = [
        t for t in reversed(Base.metadata.sorted_tables) if t.schema == "banking"
    ]

    for table in banking_tables:
        table.drop(bind=bind, checkfirst=True)

    op.execute("DROP SCHEMA IF EXISTS banking CASCADE")
