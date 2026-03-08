"""Add feature_flag_registry table.

Creates the registry for dynamic feature flag definitions.
Seeds with the 13 existing hardcoded flags.

Revision ID: a1b2c3d4e5f6
Revises: None (standalone — safe to merge)
"""

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a1b2c3d4e5f6"
down_revision = None
branch_labels = None
depends_on = None

# Enum types
feature_flag_status = postgresql.ENUM(
    "ACTIVE", "DEPRECATED", "ARCHIVED", name="featureflagstatus", create_type=False
)
feature_flag_category = postgresql.ENUM(
    "MODULE",
    "FINANCE",
    "COMPLIANCE",
    "INTEGRATION",
    "EXPERIMENTAL",
    name="featureflagcategory",
    create_type=False,
)

# Seed data: the 13 existing flags migrated from hardcoded constants
SEED_FLAGS = [
    # -- MODULE category --
    {
        "flag_key": "enable_inventory",
        "label": "Inventory",
        "description": "Track inventory items, stock levels, warehouses, and stock movements",
        "category": "MODULE",
        "default_enabled": True,
        "sort_order": 10,
    },
    {
        "flag_key": "enable_fixed_assets",
        "label": "Fixed Assets",
        "description": "Manage fixed assets, depreciation schedules, and disposal",
        "category": "MODULE",
        "default_enabled": True,
        "sort_order": 20,
    },
    {
        "flag_key": "enable_procurement",
        "label": "Procurement",
        "description": "Purchase orders, vendor management, and procurement workflows",
        "category": "MODULE",
        "default_enabled": True,
        "sort_order": 30,
    },
    # -- FINANCE category --
    {
        "flag_key": "enable_multi_currency",
        "label": "Multi-Currency",
        "description": "Support multiple currencies in transactions, invoices, and reporting",
        "category": "FINANCE",
        "default_enabled": True,
        "sort_order": 10,
    },
    {
        "flag_key": "enable_bank_reconciliation",
        "label": "Bank Reconciliation",
        "description": "Match bank statement entries with ledger transactions",
        "category": "FINANCE",
        "default_enabled": True,
        "sort_order": 20,
    },
    {
        "flag_key": "enable_recurring_transactions",
        "label": "Recurring Transactions",
        "description": "Automatically generate invoices, bills, and journal entries on a schedule",
        "category": "FINANCE",
        "default_enabled": True,
        "sort_order": 30,
    },
    {
        "flag_key": "enable_budgeting",
        "label": "Budgeting",
        "description": "Budget planning, allocation, and variance analysis",
        "category": "FINANCE",
        "default_enabled": False,
        "sort_order": 40,
    },
    {
        "flag_key": "enable_project_accounting",
        "label": "Project Accounting",
        "description": "Track costs and revenue by project with project-level P&L",
        "category": "FINANCE",
        "default_enabled": False,
        "sort_order": 50,
    },
    {
        "flag_key": "enable_leases",
        "label": "Leases",
        "description": "IFRS 16 lease accounting, right-of-use assets, and lease liability management",
        "category": "FINANCE",
        "default_enabled": False,
        "sort_order": 60,
    },
    {
        "flag_key": "enable_stock_reservation",
        "label": "Stock Reservation",
        "description": "Reserve inventory quantities against sales orders before fulfillment",
        "category": "FINANCE",
        "default_enabled": False,
        "sort_order": 70,
    },
    # -- COMPLIANCE category --
    {
        "flag_key": "enable_ipsas",
        "label": "IPSAS Accounting",
        "description": "International Public Sector Accounting Standards compliance",
        "category": "COMPLIANCE",
        "default_enabled": False,
        "sort_order": 10,
    },
    {
        "flag_key": "enable_fund_accounting",
        "label": "Fund Accounting",
        "description": "Track restricted and unrestricted funds for government and non-profit entities",
        "category": "COMPLIANCE",
        "default_enabled": False,
        "sort_order": 20,
    },
    # -- INTEGRATION category --
    {
        "flag_key": "enable_service_hooks",
        "label": "Service Hooks",
        "description": "Extensibility hooks for custom integrations and event-driven workflows",
        "category": "INTEGRATION",
        "default_enabled": False,
        "sort_order": 10,
    },
]


def upgrade() -> None:
    # Create enum types
    feature_flag_status.create(op.get_bind(), checkfirst=True)
    feature_flag_category.create(op.get_bind(), checkfirst=True)

    # Create table
    op.create_table(
        "feature_flag_registry",
        sa.Column(
            "flag_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            default=uuid.uuid4,
        ),
        sa.Column("flag_key", sa.String(120), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "category",
            feature_flag_category,
            nullable=False,
            server_default="MODULE",
        ),
        sa.Column(
            "status",
            feature_flag_status,
            nullable=False,
            server_default="ACTIVE",
        ),
        sa.Column(
            "default_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("owner", sa.String(120), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "created_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("people.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.UniqueConstraint("flag_key", name="uq_feature_flag_registry_key"),
    )

    op.create_index(
        "ix_feature_flag_registry_category",
        "feature_flag_registry",
        ["category"],
    )
    op.create_index(
        "ix_feature_flag_registry_status",
        "feature_flag_registry",
        ["status"],
    )

    # Seed existing flags
    table = sa.table(
        "feature_flag_registry",
        sa.column("flag_id", postgresql.UUID(as_uuid=True)),
        sa.column("flag_key", sa.String),
        sa.column("label", sa.String),
        sa.column("description", sa.Text),
        sa.column("category", sa.String),
        sa.column("default_enabled", sa.Boolean),
        sa.column("sort_order", sa.Integer),
        sa.column("status", sa.String),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )

    now = datetime.now(UTC)
    rows = []
    for flag in SEED_FLAGS:
        rows.append(
            {
                "flag_id": uuid.uuid4(),
                "flag_key": flag["flag_key"],
                "label": flag["label"],
                "description": flag["description"],
                "category": flag["category"],
                "default_enabled": flag["default_enabled"],
                "sort_order": flag["sort_order"],
                "status": "ACTIVE",
                "created_at": now,
                "updated_at": now,
            }
        )

    op.bulk_insert(table, rows)


def downgrade() -> None:
    op.drop_index("ix_feature_flag_registry_status")
    op.drop_index("ix_feature_flag_registry_category")
    op.drop_table("feature_flag_registry")
    feature_flag_category.drop(op.get_bind(), checkfirst=True)
    feature_flag_status.drop(op.get_bind(), checkfirst=True)
