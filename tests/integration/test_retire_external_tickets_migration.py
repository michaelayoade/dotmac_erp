"""PostgreSQL proof for selective external-ticket retirement."""

from __future__ import annotations

import importlib.util
import os
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from uuid import UUID, uuid4

import psycopg
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, URL, make_url

pytestmark = pytest.mark.integration

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATION = (
    PROJECT_ROOT / "alembic" / "versions" / "20260825_retire_external_tickets.py"
)


def _render(url: URL) -> str:
    return url.render_as_string(hide_password=False)


def _psycopg_url(url: URL) -> str:
    return _render(url.set(drivername="postgresql"))


@pytest.fixture()
def isolated_database_url() -> Iterator[URL]:
    configured = os.getenv("TEST_DATABASE_URL")
    if not configured:
        raise pytest.UsageError(
            "external ticket retirement migration requires TEST_DATABASE_URL"
        )
    base_url = make_url(configured)
    if not base_url.drivername.startswith("postgresql"):
        raise pytest.UsageError(
            "external ticket retirement migration requires PostgreSQL"
        )

    name = f"erp_external_ticket_retirement_{uuid4().hex}"
    maintenance = base_url.set(database="postgres")
    with psycopg.connect(_psycopg_url(maintenance), autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield base_url.set(database=name)
    finally:
        with psycopg.connect(_psycopg_url(maintenance), autocommit=True) as admin:
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (name,),
            )
            admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "erp_retire_external_tickets_migration", MIGRATION
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(database_url: URL, operation: str) -> None:
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                getattr(_load_migration(), operation)()
    finally:
        engine.dispose()


def _execute_batch(
    connection: Connection,
    source: str,
    parameters: dict[str, UUID] | None = None,
) -> None:
    for statement in source.split(";"):
        if statement.strip():
            connection.execute(text(statement), parameters or {})


def _create_source_catalog(connection: Connection) -> None:
    _execute_batch(
        connection,
        """
        CREATE SCHEMA support;
        CREATE SCHEMA sync;
        CREATE SCHEMA expense;
        CREATE SCHEMA exp;
        CREATE SCHEMA inv;
        CREATE SCHEMA pm;

        CREATE TABLE support.ticket (
            ticket_id uuid PRIMARY KEY,
            organization_id uuid NOT NULL,
            ticket_number varchar(50) NOT NULL,
            subject varchar(255) NOT NULL,
            erpnext_id varchar(255),
            last_synced_at timestamptz
        );
        CREATE TABLE support.ticket_comment (
            comment_id uuid PRIMARY KEY,
            ticket_id uuid NOT NULL,
            content text NOT NULL
        );
        CREATE TABLE support.ticket_attachment (
            attachment_id uuid PRIMARY KEY,
            ticket_id uuid NOT NULL,
            storage_path varchar(500) NOT NULL
        );
        CREATE TABLE support.ticket_notification (
            notification_id uuid PRIMARY KEY,
            ticket_id uuid NOT NULL,
            message text NOT NULL
        );
        CREATE TABLE sync.crm_sync_mapping (
            mapping_id uuid PRIMARY KEY,
            organization_id uuid NOT NULL,
            crm_id varchar(36) NOT NULL,
            local_entity_type varchar(50) NOT NULL,
            local_entity_id uuid NOT NULL
        );
        CREATE TABLE sync.sync_entity (
            sync_id uuid PRIMARY KEY,
            organization_id uuid NOT NULL,
            source_system varchar(50) NOT NULL,
            target_table varchar(100) NOT NULL,
            target_id uuid
        );
        CREATE TABLE expense.expense_claim (
            claim_id uuid PRIMARY KEY,
            organization_id uuid NOT NULL,
            ticket_id uuid
        );
        CREATE TABLE exp.expense_entry (
            expense_id uuid PRIMARY KEY,
            organization_id uuid NOT NULL,
            ticket_id uuid
        );
        CREATE TABLE inv.material_request (
            request_id uuid PRIMARY KEY,
            organization_id uuid NOT NULL,
            ticket_id uuid
        );
        CREATE TABLE inv.material_request_item (
            item_id uuid PRIMARY KEY,
            ticket_id uuid
        );
        CREATE TABLE pm.task (
            task_id uuid PRIMARY KEY,
            organization_id uuid NOT NULL,
            ticket_id uuid
        );
        """,
    )


def _seed_source_catalog(connection: Connection) -> dict[str, UUID]:
    ids = {
        "organization": uuid4(),
        "erpnext_ticket": uuid4(),
        "crm_ticket": uuid4(),
        "local_ticket": uuid4(),
        "external_comment": uuid4(),
        "local_comment": uuid4(),
        "attachment": uuid4(),
        "notification": uuid4(),
        "crm_mapping": uuid4(),
        "comment_mapping": uuid4(),
        "external_claim": uuid4(),
        "local_claim": uuid4(),
        "external_expense": uuid4(),
        "local_expense": uuid4(),
        "external_request": uuid4(),
        "local_request": uuid4(),
        "external_item": uuid4(),
        "local_item": uuid4(),
        "external_task": uuid4(),
        "local_task": uuid4(),
    }
    _execute_batch(
        connection,
        """
        INSERT INTO support.ticket
            (ticket_id, organization_id, ticket_number, subject,
             erpnext_id, last_synced_at)
        VALUES
            (:erpnext_ticket, :organization, 'EXT-ERP', 'ERPNext ticket',
             'ISSUE-10', now()),
            (:crm_ticket, :organization, 'EXT-CRM', 'CRM ticket', NULL, NULL),
            (:local_ticket, :organization, 'ERP-LOCAL', 'Local ticket',
             NULL, NULL);

        INSERT INTO support.ticket_comment (comment_id, ticket_id, content)
        VALUES
            (:external_comment, :erpnext_ticket, 'external timeline'),
            (:local_comment, :local_ticket, 'local timeline');
        INSERT INTO support.ticket_attachment
            (attachment_id, ticket_id, storage_path)
        VALUES (:attachment, :erpnext_ticket, 'archive/object-key');
        INSERT INTO support.ticket_notification
            (notification_id, ticket_id, message)
        VALUES (:notification, :crm_ticket, 'external update');

        INSERT INTO sync.crm_sync_mapping
            (mapping_id, organization_id, crm_id,
             local_entity_type, local_entity_id)
        VALUES
            (:crm_mapping, :organization, 'external-crm-ticket',
             'ticket', :crm_ticket);
        INSERT INTO sync.sync_entity
            (sync_id, organization_id, source_system, target_table, target_id)
        VALUES
            (:comment_mapping, :organization, 'crm',
             'support.ticket_comment', :external_comment);

        INSERT INTO expense.expense_claim
            (claim_id, organization_id, ticket_id)
        VALUES
            (:external_claim, :organization, :erpnext_ticket),
            (:local_claim, :organization, :local_ticket);
        INSERT INTO exp.expense_entry
            (expense_id, organization_id, ticket_id)
        VALUES
            (:external_expense, :organization, :crm_ticket),
            (:local_expense, :organization, :local_ticket);
        INSERT INTO inv.material_request
            (request_id, organization_id, ticket_id)
        VALUES
            (:external_request, :organization, :erpnext_ticket),
            (:local_request, :organization, :local_ticket);
        INSERT INTO inv.material_request_item (item_id, ticket_id)
        VALUES
            (:external_item, :crm_ticket),
            (:local_item, :local_ticket);
        INSERT INTO pm.task (task_id, organization_id, ticket_id)
        VALUES
            (:external_task, :organization, :crm_ticket),
            (:local_task, :organization, :local_ticket);
        """,
        ids,
    )
    return ids


def test_migration_archives_only_external_tickets_and_preserves_local_links(
    isolated_database_url: URL,
) -> None:
    engine = create_engine(isolated_database_url)
    try:
        with engine.begin() as connection:
            _create_source_catalog(connection)
            ids = _seed_source_catalog(connection)
    finally:
        engine.dispose()

    _run(isolated_database_url, "upgrade")

    engine = create_engine(isolated_database_url)
    try:
        with engine.connect() as connection:
            tickets = connection.execute(
                text("SELECT ticket_id, ticket_number FROM support.ticket")
            ).all()
            assert tickets == [(ids["local_ticket"], "ERP-LOCAL")]

            columns = {
                row[0]
                for row in connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = 'support' "
                        "AND table_name = 'ticket'"
                    )
                )
            }
            assert "erpnext_id" not in columns
            assert "last_synced_at" not in columns

            archived = connection.execute(
                text(
                    "SELECT ticket_id, jsonb_array_length(comment_payloads), "
                    "jsonb_array_length(attachment_payloads), "
                    "jsonb_array_length(notification_payloads), "
                    "jsonb_array_length(crm_mapping_payloads), "
                    "jsonb_array_length(sync_mapping_payloads) "
                    "FROM archive.retired_external_ticket ORDER BY ticket_id"
                )
            ).all()
            assert archived == sorted(
                [
                    (ids["erpnext_ticket"], 1, 1, 0, 0, 1),
                    (ids["crm_ticket"], 0, 0, 1, 1, 0),
                ],
                key=lambda row: row[0],
            )

            assert (
                connection.scalar(text("SELECT count(*) FROM support.ticket_comment"))
                == 1
            )
            assert (
                connection.scalar(text("SELECT ticket_id FROM support.ticket_comment"))
                == ids["local_ticket"]
            )
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM support.ticket_attachment")
                )
                == 0
            )
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM support.ticket_notification")
                )
                == 0
            )
            assert (
                connection.scalar(text("SELECT count(*) FROM sync.crm_sync_mapping"))
                == 0
            )
            assert connection.scalar(text("SELECT count(*) FROM sync.sync_entity")) == 0

            claim_links = connection.execute(
                text(
                    "SELECT claim_id, ticket_id, external_work_reference "
                    "FROM expense.expense_claim ORDER BY claim_id"
                )
            ).all()
            assert claim_links == sorted(
                [
                    (
                        ids["external_claim"],
                        None,
                        f"retired-external-ticket:{ids['erpnext_ticket']}",
                    ),
                    (ids["local_claim"], ids["local_ticket"], None),
                ],
                key=lambda row: row[0],
            )
            expense_links = connection.execute(
                text(
                    "SELECT expense_id, ticket_id, external_work_reference "
                    "FROM exp.expense_entry ORDER BY expense_id"
                )
            ).all()
            assert expense_links == sorted(
                [
                    (
                        ids["external_expense"],
                        None,
                        f"retired-external-ticket:{ids['crm_ticket']}",
                    ),
                    (ids["local_expense"], ids["local_ticket"], None),
                ],
                key=lambda row: row[0],
            )

            for query, external_id, local_id in (
                (
                    "SELECT request_id, ticket_id FROM inv.material_request",
                    ids["external_request"],
                    ids["local_request"],
                ),
                (
                    "SELECT item_id, ticket_id FROM inv.material_request_item",
                    ids["external_item"],
                    ids["local_item"],
                ),
                (
                    "SELECT task_id, ticket_id FROM pm.task",
                    ids["external_task"],
                    ids["local_task"],
                ),
            ):
                links = dict(connection.execute(text(query)).all())
                assert links[external_id] is None
                assert links[local_id] == ids["local_ticket"]

            security = connection.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity "
                    "FROM pg_class WHERE oid = "
                    "'archive.retired_external_ticket'::regclass"
                )
            ).one()
            assert security == (True, True)
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_policy "
                        "WHERE polrelid = "
                        "'archive.retired_external_ticket'::regclass"
                    )
                )
                == 1
            )
            public_table_select = connection.scalar(
                text(
                    "SELECT count(*) FROM pg_class AS relation, "
                    "LATERAL aclexplode(COALESCE(relation.relacl, "
                    "acldefault('r', relation.relowner))) AS acl "
                    "WHERE relation.oid = "
                    "'archive.retired_external_ticket'::regclass "
                    "AND acl.grantee = 0 AND acl.privilege_type = 'SELECT'"
                )
            )
            assert public_table_select == 0
    finally:
        engine.dispose()


def test_migration_is_forward_only(isolated_database_url: URL) -> None:
    with pytest.raises(RuntimeError, match="forward-only"):
        _run(isolated_database_url, "downgrade")
