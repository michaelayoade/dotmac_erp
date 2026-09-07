"""Provision cross-module automation permissions.

Revision ID: 20260907_automation_rbac
Revises: 20260906_invoice_sync_outcomes
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260907_automation_rbac"
down_revision: str | None = "20260906_invoice_sync_outcomes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PERMISSIONS: tuple[tuple[str, str], ...] = (
    ("automation:read", "View the cross-module automation center"),
    ("automation:create", "Create workflow and custom-field drafts"),
    ("automation:update", "Edit workflows, schedules, and custom fields"),
    ("automation:publish", "Activate, pause, and run automations"),
    ("automation:test", "Simulate automation rules"),
    ("automation:retry", "Retry failed automation executions"),
)

READ_SOURCES = (
    "automation:recurring:read",
    "automation:workflows:read",
    "automation:templates:read",
)
MANAGE_SOURCES = (
    "automation:recurring:manage",
    "automation:workflows:manage",
    "automation:templates:manage",
)


def upgrade() -> None:
    conn = op.get_bind()
    for key, description in PERMISSIONS:
        conn.exec_driver_sql(
            """
            INSERT INTO permissions (
                id, key, description, is_active, created_at, updated_at
            )
            VALUES (gen_random_uuid(), %s, %s, true, NOW(), NOW())
            ON CONFLICT (key) DO NOTHING
            """,
            (key, description),
        )

    grants: dict[str, tuple[str, ...]] = {
        "automation:read": (*READ_SOURCES, *MANAGE_SOURCES),
        "automation:create": MANAGE_SOURCES,
        "automation:update": MANAGE_SOURCES,
        "automation:publish": MANAGE_SOURCES,
        "automation:test": MANAGE_SOURCES,
        "automation:retry": MANAGE_SOURCES,
    }
    for target, sources in grants.items():
        conn.exec_driver_sql(
            """
            INSERT INTO role_permissions (id, role_id, permission_id)
            SELECT gen_random_uuid(), existing.role_id, target.id
            FROM role_permissions AS existing
            JOIN permissions AS source ON source.id = existing.permission_id
            JOIN permissions AS target ON target.key = %s
            WHERE source.key = ANY(%s)
            ON CONFLICT (role_id, permission_id) DO NOTHING
            """,
            (target, list(sources)),
        )


def downgrade() -> None:
    # Grants may be changed by administrators after deployment. Removing them
    # would be destructive, so this additive RBAC migration has no downgrade.
    pass
