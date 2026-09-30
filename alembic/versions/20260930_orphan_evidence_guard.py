"""Keep file-orphan cleanup evidence undeletable by runtime roles.

Revision ID: 20260930_orphan_evidence_guard
Revises: 20260929_email_delivery_payload

The original table migration granted app_user only SELECT/INSERT/UPDATE, but
production's older dotmac_erp_app role already had DELETE through its table
ACL. A permissive ALL policy would let that role erase its tenant's evidence.
Revoke the existing grants and add a restrictive DELETE policy so a later
runtime grant cannot reopen that path. app_admin retains its BYPASSRLS recovery
authority, as required by the database-role contract.
"""

from __future__ import annotations

from alembic import op

revision = "20260930_orphan_evidence_guard"
down_revision = "20260929_email_delivery_payload"
branch_labels = None
depends_on = None

TABLES = ("file_orphan_cleanup_runs", "file_orphan_cleanup_deletions")


def _revoke_delete(table: str) -> None:
    qualified = f"public.{table}"
    op.execute(f"REVOKE DELETE ON TABLE {qualified} FROM PUBLIC, app_user")
    # The legacy production login is not present in every composed database.
    # A direct REVOKE naming an absent role would abort the migration.
    op.execute(
        f"""
        DO $guard$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dotmac_erp_app') THEN
                REVOKE DELETE ON TABLE {qualified} FROM dotmac_erp_app;
            END IF;
        END
        $guard$
        """
    )


def upgrade() -> None:
    for table in TABLES:
        _revoke_delete(table)
        op.execute(
            f"""
            CREATE POLICY {table}_no_runtime_delete
                ON public.{table} AS RESTRICTIVE FOR DELETE TO PUBLIC
                USING (false)
            """
        )


def downgrade() -> None:
    # Removing either protection would recreate a deletion path into durable
    # cleanup evidence; the records migration itself is forward-fix-only.
    raise RuntimeError("file-orphan cleanup evidence DELETE guard is forward-fix-only")
