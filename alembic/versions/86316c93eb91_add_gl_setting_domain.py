"""Add 'gl' value to SettingDomain PostgreSQL enum.

Enables persisting GL-level configuration (FX revaluation defaults,
period-close prerequisites, etc.) under the existing domain_settings
infrastructure.

Revision ID: 86316c93eb91
Revises: 20260506_dynamic_forms
Create Date: 2026-05-10 11:12:27.611279
"""

from __future__ import annotations

from alembic import op


revision = "86316c93eb91"
down_revision = "20260506_dynamic_forms"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ``ADD VALUE IF NOT EXISTS`` is idempotent on PostgreSQL >= 9.6 and is
    # safe to re-run if a previous attempt partially applied.  The Postgres
    # enum type for ``SettingDomain`` is named ``settingdomain`` (lowercased
    # by SQLAlchemy's default naming convention) — see prior migrations
    # such as ``20260310_add_settingdomain_expense.py``.
    #
    # ``ALTER TYPE ... ADD VALUE`` cannot run inside a transaction on
    # PostgreSQL < 12; even on >= 12 the new value is not visible to the
    # same transaction that added it. Wrap in an Alembic-managed
    # autocommit block so the DDL commits before any subsequent
    # statement tries to use the new value (matches the codebase pattern
    # in ``20260218_add_stamp_duty_support.py``,
    # ``20260208_add_numbering_sequence_types.py``, and
    # ``20260307_add_pll_dashboard_indexes.py``).
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE settingdomain ADD VALUE IF NOT EXISTS 'gl'")


def downgrade() -> None:
    # PostgreSQL cannot remove enum values cleanly without rewriting all
    # dependent rows; intentionally a no-op.
    pass
