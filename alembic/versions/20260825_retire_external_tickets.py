"""Retire external ticket projections while preserving ERP-owned tickets.

Revision ID: 20260825_retire_ext_tickets
Revises: 20260825_weekly_meeting_reports
Create Date: 2026-08-25

External provenance is established only from explicit database evidence: the
ticket's ERPNext provenance columns or a sync mapping that targets the ticket
or one of its comments.  Ticket-number patterns are never treated as proof.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260825_retire_ext_tickets"
down_revision = "20260825_weekly_meeting_reports"
branch_labels = None
depends_on = None

_ARCHIVE_SCHEMA = "archive"
_ARCHIVE_TABLE = "retired_external_ticket"
_ARCHIVE_RELATION = f"{_ARCHIVE_SCHEMA}.{_ARCHIVE_TABLE}"
_ONLINE_ROLES = ("app_user", "platform_api")

_REQUIRED_TABLES = (
    ("support", "ticket"),
    ("support", "ticket_comment"),
    ("support", "ticket_attachment"),
    ("support", "ticket_notification"),
    ("sync", "crm_sync_mapping"),
    ("sync", "sync_entity"),
    ("expense", "expense_claim"),
    ("exp", "expense_entry"),
    ("inv", "material_request"),
    ("inv", "material_request_item"),
    ("pm", "task"),
)


def _has_table(bind: sa.Connection, schema: str, table: str) -> bool:
    return sa.inspect(bind).has_table(table, schema=schema)


def _columns(bind: sa.Connection, schema: str, table: str) -> set[str]:
    return {
        column["name"] for column in sa.inspect(bind).get_columns(table, schema=schema)
    }


def _constraints(bind: sa.Connection, schema: str, table: str) -> set[str]:
    inspector = sa.inspect(bind)
    return {
        constraint["name"]
        for constraint in (
            inspector.get_check_constraints(table, schema=schema)
            + inspector.get_unique_constraints(table, schema=schema)
        )
        if constraint.get("name")
    }


def _require_source_catalog(bind: sa.Connection) -> None:
    missing = [
        f"{schema}.{table}"
        for schema, table in _REQUIRED_TABLES
        if not _has_table(bind, schema, table)
    ]
    if missing:
        raise RuntimeError(
            "external ticket retirement requires the current ERP catalog; "
            f"missing tables: {', '.join(missing)}"
        )

    ticket_columns = _columns(bind, "support", "ticket")
    missing_provenance = {"erpnext_id", "last_synced_at"} - ticket_columns
    if missing_provenance:
        raise RuntimeError(
            "external ticket retirement cannot classify source rows; "
            "support.ticket is missing provenance columns: "
            + ", ".join(sorted(missing_provenance))
        )


def _create_sealed_archive(bind: sa.Connection) -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {_ARCHIVE_SCHEMA}")
    op.execute(f"REVOKE ALL PRIVILEGES ON SCHEMA {_ARCHIVE_SCHEMA} FROM PUBLIC")

    if not _has_table(bind, _ARCHIVE_SCHEMA, _ARCHIVE_TABLE):
        op.create_table(
            _ARCHIVE_TABLE,
            sa.Column(
                "ticket_id",
                postgresql.UUID(as_uuid=True),
                primary_key=True,
                nullable=False,
            ),
            sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("ticket_payload", postgresql.JSONB(), nullable=False),
            sa.Column("comment_payloads", postgresql.JSONB(), nullable=False),
            sa.Column("attachment_payloads", postgresql.JSONB(), nullable=False),
            sa.Column("notification_payloads", postgresql.JSONB(), nullable=False),
            sa.Column("crm_mapping_payloads", postgresql.JSONB(), nullable=False),
            sa.Column("sync_mapping_payloads", postgresql.JSONB(), nullable=False),
            sa.Column("expense_claim_links", postgresql.JSONB(), nullable=False),
            sa.Column("expense_entry_links", postgresql.JSONB(), nullable=False),
            sa.Column("material_request_links", postgresql.JSONB(), nullable=False),
            sa.Column("material_item_links", postgresql.JSONB(), nullable=False),
            sa.Column("task_links", postgresql.JSONB(), nullable=False),
            sa.Column(
                "retired_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            schema=_ARCHIVE_SCHEMA,
        )
        op.create_index(
            "idx_retired_external_ticket_org",
            _ARCHIVE_TABLE,
            ["organization_id"],
            schema=_ARCHIVE_SCHEMA,
        )

    op.execute(f"ALTER TABLE {_ARCHIVE_RELATION} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_ARCHIVE_RELATION} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"DROP POLICY IF EXISTS retired_external_ticket_tenant_isolation "
        f"ON {_ARCHIVE_RELATION}"
    )
    op.execute(
        f"""
        CREATE POLICY retired_external_ticket_tenant_isolation
            ON {_ARCHIVE_RELATION}
            USING (
                organization_id::text = NULLIF(
                    current_setting('app.current_organization_id', true), ''
                )
            )
            WITH CHECK (
                organization_id::text = NULLIF(
                    current_setting('app.current_organization_id', true), ''
                )
            )
        """
    )
    op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {_ARCHIVE_RELATION} FROM PUBLIC")
    for role in _ONLINE_ROLES:
        op.execute(
            f"""
            DO $revoke$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                    REVOKE ALL PRIVILEGES
                        ON SCHEMA {_ARCHIVE_SCHEMA} FROM {role};
                    REVOKE ALL PRIVILEGES
                        ON TABLE {_ARCHIVE_RELATION} FROM {role};
                END IF;
            END
            $revoke$
            """
        )


def _select_external_tickets(bind: sa.Connection) -> None:
    bind.execute(
        sa.text(
            """
            CREATE TEMPORARY TABLE retired_external_ticket_ids
                ON COMMIT DROP AS
            SELECT ticket.ticket_id, ticket.organization_id
              FROM support.ticket AS ticket
             WHERE ticket.erpnext_id IS NOT NULL
                OR ticket.last_synced_at IS NOT NULL
                OR EXISTS (
                    SELECT 1
                      FROM sync.crm_sync_mapping AS mapping
                     WHERE lower(mapping.local_entity_type) = 'ticket'
                       AND mapping.local_entity_id = ticket.ticket_id
                )
                OR EXISTS (
                    SELECT 1
                      FROM sync.sync_entity AS mapping
                     WHERE mapping.target_table = 'support.ticket'
                       AND mapping.target_id = ticket.ticket_id
                )
                OR EXISTS (
                    SELECT 1
                      FROM support.ticket_comment AS comment
                      JOIN sync.sync_entity AS mapping
                        ON mapping.target_table = 'support.ticket_comment'
                       AND mapping.target_id = comment.comment_id
                     WHERE comment.ticket_id = ticket.ticket_id
                )
            """
        )
    )
    bind.execute(
        sa.text("ALTER TABLE retired_external_ticket_ids ADD PRIMARY KEY (ticket_id)")
    )


def _archive_external_tickets(bind: sa.Connection) -> None:
    bind.execute(
        sa.text(
            """
            INSERT INTO archive.retired_external_ticket (
                ticket_id,
                organization_id,
                ticket_payload,
                comment_payloads,
                attachment_payloads,
                notification_payloads,
                crm_mapping_payloads,
                sync_mapping_payloads,
                expense_claim_links,
                expense_entry_links,
                material_request_links,
                material_item_links,
                task_links
            )
            SELECT
                ticket.ticket_id,
                ticket.organization_id,
                to_jsonb(ticket),
                COALESCE((
                    SELECT jsonb_agg(to_jsonb(comment) ORDER BY comment.comment_id)
                      FROM support.ticket_comment AS comment
                     WHERE comment.ticket_id = ticket.ticket_id
                ), '[]'::jsonb),
                COALESCE((
                    SELECT jsonb_agg(
                               to_jsonb(attachment)
                               ORDER BY attachment.attachment_id
                           )
                      FROM support.ticket_attachment AS attachment
                     WHERE attachment.ticket_id = ticket.ticket_id
                ), '[]'::jsonb),
                COALESCE((
                    SELECT jsonb_agg(
                               to_jsonb(notification)
                               ORDER BY notification.notification_id
                           )
                      FROM support.ticket_notification AS notification
                     WHERE notification.ticket_id = ticket.ticket_id
                ), '[]'::jsonb),
                COALESCE((
                    SELECT jsonb_agg(to_jsonb(mapping) ORDER BY mapping.mapping_id)
                      FROM sync.crm_sync_mapping AS mapping
                     WHERE lower(mapping.local_entity_type) = 'ticket'
                       AND mapping.local_entity_id = ticket.ticket_id
                ), '[]'::jsonb),
                COALESCE((
                    SELECT jsonb_agg(to_jsonb(mapping) ORDER BY mapping.sync_id)
                      FROM sync.sync_entity AS mapping
                     WHERE (
                            mapping.target_table = 'support.ticket'
                            AND mapping.target_id = ticket.ticket_id
                           )
                        OR (
                            mapping.target_table = 'support.ticket_comment'
                            AND mapping.target_id IN (
                                SELECT comment.comment_id
                                  FROM support.ticket_comment AS comment
                                 WHERE comment.ticket_id = ticket.ticket_id
                            )
                           )
                ), '[]'::jsonb),
                COALESCE((
                    SELECT jsonb_agg(
                               jsonb_build_object(
                                   'claim_id', claim.claim_id,
                                   'ticket_id', claim.ticket_id
                               ) ORDER BY claim.claim_id
                           )
                      FROM expense.expense_claim AS claim
                     WHERE claim.ticket_id = ticket.ticket_id
                ), '[]'::jsonb),
                COALESCE((
                    SELECT jsonb_agg(
                               jsonb_build_object(
                                   'expense_id', expense.expense_id,
                                   'ticket_id', expense.ticket_id
                               ) ORDER BY expense.expense_id
                           )
                      FROM exp.expense_entry AS expense
                     WHERE expense.ticket_id = ticket.ticket_id
                ), '[]'::jsonb),
                COALESCE((
                    SELECT jsonb_agg(
                               jsonb_build_object(
                                   'request_id', request.request_id,
                                   'ticket_id', request.ticket_id
                               ) ORDER BY request.request_id
                           )
                      FROM inv.material_request AS request
                     WHERE request.ticket_id = ticket.ticket_id
                ), '[]'::jsonb),
                COALESCE((
                    SELECT jsonb_agg(
                               jsonb_build_object(
                                   'item_id', item.item_id,
                                   'ticket_id', item.ticket_id
                               ) ORDER BY item.item_id
                           )
                      FROM inv.material_request_item AS item
                     WHERE item.ticket_id = ticket.ticket_id
                ), '[]'::jsonb),
                COALESCE((
                    SELECT jsonb_agg(
                               jsonb_build_object(
                                   'task_id', task.task_id,
                                   'ticket_id', task.ticket_id
                               ) ORDER BY task.task_id
                           )
                      FROM pm.task AS task
                     WHERE task.ticket_id = ticket.ticket_id
                ), '[]'::jsonb)
              FROM support.ticket AS ticket
              JOIN retired_external_ticket_ids AS retired
                ON retired.ticket_id = ticket.ticket_id
            ON CONFLICT (ticket_id) DO UPDATE
            SET organization_id = EXCLUDED.organization_id,
                ticket_payload = EXCLUDED.ticket_payload,
                comment_payloads = EXCLUDED.comment_payloads,
                attachment_payloads = EXCLUDED.attachment_payloads,
                notification_payloads = EXCLUDED.notification_payloads,
                crm_mapping_payloads = EXCLUDED.crm_mapping_payloads,
                sync_mapping_payloads = EXCLUDED.sync_mapping_payloads,
                expense_claim_links = EXCLUDED.expense_claim_links,
                expense_entry_links = EXCLUDED.expense_entry_links,
                material_request_links = EXCLUDED.material_request_links,
                material_item_links = EXCLUDED.material_item_links,
                task_links = EXCLUDED.task_links
            """
        )
    )

    source_count = bind.scalar(
        sa.text("SELECT count(*) FROM retired_external_ticket_ids")
    )
    archive_count = bind.scalar(
        sa.text(
            """
            SELECT count(*)
              FROM archive.retired_external_ticket AS archive
              JOIN retired_external_ticket_ids AS retired
                ON retired.ticket_id = archive.ticket_id
            """
        )
    )
    if int(source_count or 0) != int(archive_count or 0):
        raise RuntimeError(
            "external ticket archive parity failed: "
            f"source={source_count}, archive={archive_count}"
        )


def _assert_array_parity(
    bind: sa.Connection,
    *,
    source_sql: str,
    archive_column: str,
) -> None:
    source_count = bind.scalar(sa.text(source_sql))
    archive_count = bind.scalar(
        sa.text(
            f"""
            SELECT COALESCE(sum(jsonb_array_length(archive.{archive_column})), 0)
              FROM archive.retired_external_ticket AS archive
              JOIN retired_external_ticket_ids AS retired
                ON retired.ticket_id = archive.ticket_id
            """
        )
    )
    if int(source_count or 0) != int(archive_count or 0):
        raise RuntimeError(
            f"external ticket {archive_column} archive parity failed: "
            f"source={source_count}, archive={archive_count}"
        )


def _verify_dependent_archives(bind: sa.Connection) -> None:
    checks = (
        (
            "SELECT count(*) FROM support.ticket_comment AS source "
            "JOIN retired_external_ticket_ids AS retired "
            "ON retired.ticket_id = source.ticket_id",
            "comment_payloads",
        ),
        (
            "SELECT count(*) FROM support.ticket_attachment AS source "
            "JOIN retired_external_ticket_ids AS retired "
            "ON retired.ticket_id = source.ticket_id",
            "attachment_payloads",
        ),
        (
            "SELECT count(*) FROM support.ticket_notification AS source "
            "JOIN retired_external_ticket_ids AS retired "
            "ON retired.ticket_id = source.ticket_id",
            "notification_payloads",
        ),
        (
            "SELECT count(*) FROM sync.crm_sync_mapping AS source "
            "JOIN retired_external_ticket_ids AS retired "
            "ON retired.ticket_id = source.local_entity_id "
            "WHERE lower(source.local_entity_type) = 'ticket'",
            "crm_mapping_payloads",
        ),
        (
            "SELECT count(*) FROM sync.sync_entity AS source "
            "WHERE (source.target_table = 'support.ticket' AND source.target_id "
            "IN (SELECT ticket_id FROM retired_external_ticket_ids)) OR "
            "(source.target_table = 'support.ticket_comment' AND source.target_id "
            "IN (SELECT comment.comment_id FROM support.ticket_comment AS comment "
            "JOIN retired_external_ticket_ids AS retired "
            "ON retired.ticket_id = comment.ticket_id))",
            "sync_mapping_payloads",
        ),
        (
            "SELECT count(*) FROM expense.expense_claim AS source "
            "JOIN retired_external_ticket_ids AS retired "
            "ON retired.ticket_id = source.ticket_id",
            "expense_claim_links",
        ),
        (
            "SELECT count(*) FROM exp.expense_entry AS source "
            "JOIN retired_external_ticket_ids AS retired "
            "ON retired.ticket_id = source.ticket_id",
            "expense_entry_links",
        ),
        (
            "SELECT count(*) FROM inv.material_request AS source "
            "JOIN retired_external_ticket_ids AS retired "
            "ON retired.ticket_id = source.ticket_id",
            "material_request_links",
        ),
        (
            "SELECT count(*) FROM inv.material_request_item AS source "
            "JOIN retired_external_ticket_ids AS retired "
            "ON retired.ticket_id = source.ticket_id",
            "material_item_links",
        ),
        (
            "SELECT count(*) FROM pm.task AS source "
            "JOIN retired_external_ticket_ids AS retired "
            "ON retired.ticket_id = source.ticket_id",
            "task_links",
        ),
    )
    for source_sql, archive_column in checks:
        _assert_array_parity(
            bind,
            source_sql=source_sql,
            archive_column=archive_column,
        )


def _add_external_work_references(bind: sa.Connection) -> None:
    for schema, table, constraint_name in (
        ("expense", "expense_claim", "ck_expense_claim_one_work_reference"),
        ("exp", "expense_entry", "ck_expense_entry_one_work_reference"),
    ):
        if "external_work_reference" not in _columns(bind, schema, table):
            op.add_column(
                table,
                sa.Column(
                    "external_work_reference",
                    sa.String(255),
                    nullable=True,
                    comment=(
                        "Opaque reference to work owned by an external application"
                    ),
                ),
                schema=schema,
            )

        id_column = "claim_id" if table == "expense_claim" else "expense_id"
        bind.execute(
            sa.text(
                f"""
                UPDATE {schema}.{table} AS source
                   SET external_work_reference = COALESCE(
                           source.external_work_reference,
                           'retired-external-ticket:' || source.ticket_id::text
                       ),
                       ticket_id = NULL
                  FROM retired_external_ticket_ids AS retired
                 WHERE source.ticket_id = retired.ticket_id
                   AND source.{id_column} IS NOT NULL
                """
            )
        )

        if constraint_name not in _constraints(bind, schema, table):
            op.create_check_constraint(
                constraint_name,
                table,
                "ticket_id IS NULL OR external_work_reference IS NULL",
                schema=schema,
            )


def _remove_retired_ticket_links(bind: sa.Connection) -> None:
    _add_external_work_references(bind)
    for qualified in (
        "inv.material_request",
        "inv.material_request_item",
        "pm.task",
    ):
        bind.execute(
            sa.text(
                f"""
                UPDATE {qualified} AS source
                   SET ticket_id = NULL
                  FROM retired_external_ticket_ids AS retired
                 WHERE source.ticket_id = retired.ticket_id
                """
            )
        )


def _delete_retired_ticket_rows(bind: sa.Connection) -> None:
    bind.execute(
        sa.text(
            """
            DELETE FROM sync.sync_entity AS mapping
             WHERE (
                    mapping.target_table = 'support.ticket'
                    AND mapping.target_id IN (
                        SELECT ticket_id FROM retired_external_ticket_ids
                    )
                   )
                OR (
                    mapping.target_table = 'support.ticket_comment'
                    AND mapping.target_id IN (
                        SELECT comment.comment_id
                          FROM support.ticket_comment AS comment
                          JOIN retired_external_ticket_ids AS retired
                            ON retired.ticket_id = comment.ticket_id
                    )
                   )
            """
        )
    )
    bind.execute(
        sa.text(
            """
            DELETE FROM sync.crm_sync_mapping AS mapping
             USING retired_external_ticket_ids AS retired
             WHERE lower(mapping.local_entity_type) = 'ticket'
               AND mapping.local_entity_id = retired.ticket_id
            """
        )
    )
    for qualified in (
        "support.ticket_attachment",
        "support.ticket_notification",
        "support.ticket_comment",
    ):
        bind.execute(
            sa.text(
                f"""
                DELETE FROM {qualified} AS source
                 USING retired_external_ticket_ids AS retired
                 WHERE source.ticket_id = retired.ticket_id
                """
            )
        )
    bind.execute(
        sa.text(
            """
            DELETE FROM support.ticket AS ticket
             USING retired_external_ticket_ids AS retired
             WHERE ticket.ticket_id = retired.ticket_id
            """
        )
    )
    remaining = bind.scalar(
        sa.text(
            """
            SELECT count(*)
              FROM support.ticket AS ticket
              JOIN retired_external_ticket_ids AS retired
                ON retired.ticket_id = ticket.ticket_id
            """
        )
    )
    if int(remaining or 0):
        raise RuntimeError(
            f"external ticket retirement left {remaining} projected tickets live"
        )


def _drop_ticket_provenance(bind: sa.Connection) -> None:
    columns = _columns(bind, "support", "ticket")
    if "erpnext_id" in columns:
        op.drop_column("ticket", "erpnext_id", schema="support")
    if "last_synced_at" in columns:
        op.drop_column("ticket", "last_synced_at", schema="support")
    op.execute(
        "COMMENT ON TABLE support.ticket IS 'ERP-owned internal support tickets'"
    )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("external ticket retirement requires PostgreSQL")

    _require_source_catalog(bind)
    _create_sealed_archive(bind)
    _select_external_tickets(bind)
    _archive_external_tickets(bind)
    _verify_dependent_archives(bind)
    _remove_retired_ticket_links(bind)
    _delete_retired_ticket_rows(bind)
    _drop_ticket_provenance(bind)


def downgrade() -> None:
    raise RuntimeError(
        "20260825_retire_ext_tickets is forward-only: external ticket "
        "projections and their relationships can only be restored from an "
        "approved pre-migration backup."
    )
