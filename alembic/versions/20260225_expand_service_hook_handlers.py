"""Expand service hook handler enum values.

Revision ID: 20260225_expand_service_hook_handlers
Revises: 20260225_add_service_hooks
Create Date: 2026-02-25
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op

revision: str = "20260225_expand_service_hook_handlers"
down_revision: Union[str, Sequence[str], None] = "20260225_add_service_hooks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _ensure_enum_value(enum_schema: str, enum_name: str, enum_value: str) -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_enum e
                JOIN pg_type t ON t.oid = e.enumtypid
                JOIN pg_namespace n ON n.oid = t.typnamespace
                WHERE n.nspname = '{enum_schema}'
                  AND t.typname = '{enum_name}'
                  AND e.enumlabel = '{enum_value}'
            ) THEN
                ALTER TYPE {enum_schema}.{enum_name} ADD VALUE '{enum_value}';
            END IF;
        END $$;
        """
    )


def upgrade() -> None:
    _ensure_enum_value("platform", "hook_handler_type", "NOTIFICATION")
    _ensure_enum_value("platform", "hook_handler_type", "EMAIL")
    _ensure_enum_value("platform", "hook_handler_type", "INTERNAL_SERVICE")


def downgrade() -> None:
    # PostgreSQL does not support removing enum labels in-place safely.
    # No-op downgrade by design.
    pass
