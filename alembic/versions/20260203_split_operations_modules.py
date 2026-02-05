"""Split operations module permissions and settings domains.

Revision ID: 20260203_split_operations_modules
Revises: 20260203_create_ipsas_schema
Create Date: 2026-02-03
"""
from alembic import op
import sqlalchemy as sa

revision = "20260203_split_operations_modules"
down_revision = "20260203_create_ipsas_schema"
branch_labels = None
depends_on = None


def _add_settingdomain_value(value: str) -> None:
    op.execute(f"ALTER TYPE settingdomain ADD VALUE IF NOT EXISTS '{value}'")


def _ensure_permission(key: str, description: str) -> None:
    op.execute(
        """
        INSERT INTO permissions (id, key, description, is_active, created_at, updated_at)
        SELECT gen_random_uuid(), '{key}', '{description}', TRUE, NOW(), NOW()
        WHERE NOT EXISTS (
            SELECT 1 FROM permissions WHERE key = '{key}'
        )
        """.format(key=key, description=description.replace("'", "''"))
    )


def _grant_permission_from_role_source(source_key: str, target_key: str) -> None:
    op.execute(
        """
        INSERT INTO role_permissions (id, role_id, permission_id)
        SELECT gen_random_uuid(), rp.role_id, p_target.id
        FROM role_permissions rp
        JOIN permissions p_source ON p_source.id = rp.permission_id
        JOIN permissions p_target ON p_target.key = '{target_key}'
        WHERE p_source.key = '{source_key}'
          AND NOT EXISTS (
              SELECT 1
              FROM role_permissions rp2
              WHERE rp2.role_id = rp.role_id
                AND rp2.permission_id = p_target.id
          )
        ON CONFLICT DO NOTHING
        """.format(source_key=source_key, target_key=target_key)
    )


def _grant_permission_from_prefix(prefix: str, target_key: str) -> None:
    op.execute(
        """
        INSERT INTO role_permissions (id, role_id, permission_id)
        SELECT DISTINCT gen_random_uuid(), rp.role_id, p_target.id
        FROM role_permissions rp
        JOIN permissions p_source ON p_source.id = rp.permission_id
        JOIN permissions p_target ON p_target.key = '{target_key}'
        WHERE p_source.key LIKE '{prefix}%%'
          AND NOT EXISTS (
              SELECT 1
              FROM role_permissions rp2
              WHERE rp2.role_id = rp.role_id
                AND rp2.permission_id = p_target.id
          )
        ON CONFLICT DO NOTHING
        """.format(prefix=prefix, target_key=target_key)
    )


def upgrade() -> None:
    # Expand settings domains
    for value in ["support", "inventory", "projects", "fleet", "procurement", "settings"]:
        _add_settingdomain_value(value)

    # Migrate legacy operations settings to module domains
    # Only run if 'operations' exists as a settingdomain value
    bind = op.get_bind()
    has_operations = bind.execute(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM pg_enum WHERE enumlabel = 'operations' "
            "AND enumtypid = (SELECT oid FROM pg_type WHERE typname = 'settingdomain'))"
        )
    ).scalar()
    if has_operations:
        op.execute(
            """
            UPDATE domain_settings
            SET domain = 'support'
            WHERE domain = 'operations' AND key LIKE 'support_%%'
            """
        )
        op.execute(
            """
            UPDATE domain_settings
            SET domain = 'inventory'
            WHERE domain = 'operations' AND key LIKE 'inventory_%%'
            """
        )
        op.execute(
            """
            UPDATE domain_settings
            SET domain = 'projects'
            WHERE domain = 'operations' AND key LIKE 'project_%%'
            """
        )

    # Rename inventory permissions from inv:* to inventory:*
    op.execute(
        "UPDATE permissions SET key = REPLACE(key, 'inv:', 'inventory:') WHERE key LIKE 'inv:%'"
    )

    # Ensure module access permissions exist
    module_permissions = [
        ("inventory:access", "Access inventory module"),
        ("inventory:dashboard", "View inventory dashboard"),
        ("fleet:access", "Access fleet module"),
        ("fleet:dashboard", "View fleet dashboard"),
        ("support:access", "Access support module"),
        ("support:dashboard", "View support dashboard"),
        ("procurement:access", "Access procurement module"),
        ("procurement:dashboard", "View procurement dashboard"),
        ("projects:access", "Access projects module"),
        ("projects:dashboard", "View projects dashboard"),
        ("settings:access", "Access settings module"),
        ("settings:dashboard", "View settings dashboard"),
    ]
    for key, description in module_permissions:
        _ensure_permission(key, description)

    # Map legacy operations access to module access
    for key, _ in module_permissions:
        _grant_permission_from_role_source("operations:access", key)
    for key in [
        "inventory:dashboard",
        "fleet:dashboard",
        "support:dashboard",
        "procurement:dashboard",
        "projects:dashboard",
        "settings:dashboard",
    ]:
        _grant_permission_from_role_source("operations:dashboard", key)

    # Grant module access to roles with module-specific permissions
    _grant_permission_from_prefix("inventory:", "inventory:access")
    _grant_permission_from_prefix("inventory:", "inventory:dashboard")
    _grant_permission_from_prefix("support:", "support:access")
    _grant_permission_from_prefix("support:", "support:dashboard")
    _grant_permission_from_prefix("tasks:", "projects:access")
    _grant_permission_from_prefix("tasks:", "projects:dashboard")
    _grant_permission_from_prefix("projects:", "projects:access")
    _grant_permission_from_prefix("projects:", "projects:dashboard")


def downgrade() -> None:
    # No safe downgrade for enum expansion and permission remaps.
    pass
