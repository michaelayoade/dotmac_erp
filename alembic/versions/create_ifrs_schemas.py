"""Create IFRS schemas and tables.

Revision ID: create_ifrs_schemas
Revises: add_organization_to_person
Create Date: 2025-01-09

This migration creates all IFRS PostgreSQL schemas and tables for the
IFRS-compliant accounting system.

Schemas:
- audit: Audit trail and approvals (4 tables)
- platform: Event-driven architecture (3 tables)
- core_org: Organization structure (6 tables)
- core_fx: Foreign exchange (4 tables)
- core_config: System configuration (2 tables)
- gl: General Ledger (11 tables)
- ar: Accounts Receivable (11 tables)
- ap: Accounts Payable (11 tables)
- fa: Fixed Assets (9 tables)
- lease: Leases (5 tables)
- inv: Inventory (9 tables)
- fin_inst: Financial Instruments (5 tables)
- tax: Tax (6 tables)
- cons: Consolidation (6 tables)
- rpt: Reporting (5 tables)
"""
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = "create_ifrs_schemas"
down_revision = "add_organization_to_person"
branch_labels = None
depends_on = None

# All IFRS schemas
SCHEMAS = [
    "ap",
    "ar",
    "audit",
    "cons",
    "core_config",
    "core_fx",
    "core_org",
    "fa",
    "fin_inst",
    "gl",
    "inv",
    "lease",
    "platform",
    "rpt",
    "tax",
]


def upgrade() -> None:
    # Create all schemas
    for schema in SCHEMAS:
        op.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")

    # Import models to get metadata (must be done inside function)
    from app.db import Base
    import app.models.ifrs  # noqa: F401 - registers models

    # Get connection and create tables using SQLAlchemy metadata
    bind = op.get_bind()

    # Create all tables in the correct order (respecting foreign keys)
    # SQLAlchemy's create_all handles dependency ordering
    Base.metadata.create_all(
        bind=bind,
        tables=[
            t for t in Base.metadata.sorted_tables
            if t.schema in SCHEMAS
        ],
    )


def downgrade() -> None:
    # Import models to get metadata
    from app.db import Base
    import app.models.ifrs  # noqa: F401

    bind = op.get_bind()

    # Drop all tables in reverse order
    tables_to_drop = [
        t for t in reversed(Base.metadata.sorted_tables)
        if t.schema in SCHEMAS
    ]

    for table in tables_to_drop:
        op.execute(f"DROP TABLE IF EXISTS {table.schema}.{table.name} CASCADE")

    # Drop all schemas
    for schema in reversed(SCHEMAS):
        op.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
