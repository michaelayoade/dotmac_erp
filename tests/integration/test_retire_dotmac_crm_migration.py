"""PostgreSQL proof for the forward-only Dotmac CRM data retirement."""

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
MIGRATION = PROJECT_ROOT / "alembic" / "versions" / "20260825_retire_dotmac_crm.py"


def _render(url: URL) -> str:
    return url.render_as_string(hide_password=False)


def _psycopg_url(url: URL) -> str:
    return _render(url.set(drivername="postgresql"))


@pytest.fixture()
def isolated_database_url() -> Iterator[URL]:
    configured = os.getenv("TEST_DATABASE_URL")
    if not configured:
        raise pytest.UsageError("CRM retirement migration requires TEST_DATABASE_URL")
    base_url = make_url(configured)
    if not base_url.drivername.startswith("postgresql"):
        raise pytest.UsageError("CRM retirement migration requires PostgreSQL")

    name = f"erp_crm_retirement_{uuid4().hex}"
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
        "erp_retire_dotmac_crm_migration", MIGRATION
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_upgrade(database_url: URL) -> None:
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                _load_migration().upgrade()
    finally:
        engine.dispose()


def _run_downgrade(database_url: URL) -> None:
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                _load_migration().downgrade()
    finally:
        engine.dispose()


def _execute_batch(
    connection: Connection,
    source: str,
    parameters: dict[str, UUID | str] | None = None,
) -> None:
    for statement in source.split(";"):
        if statement.strip():
            connection.execute(text(statement), parameters or {})


def _create_source_catalog(connection: Connection) -> None:
    _execute_batch(
        connection,
        """
            CREATE SCHEMA sync;
            CREATE SCHEMA ar;
            CREATE SCHEMA inv;
            CREATE SCHEMA expense;
            CREATE TYPE sync.crm_entity_type AS ENUM ('TICKET');
            CREATE TYPE sync.crm_sync_status AS ENUM ('ACTIVE');

            CREATE TABLE sync.integration_config (
                config_id uuid PRIMARY KEY,
                organization_id uuid NOT NULL,
                integration_type text NOT NULL,
                api_key text,
                api_secret text,
                is_active boolean NOT NULL,
                updated_at timestamptz
            );
            CREATE TABLE public.api_keys (
                id uuid PRIMARY KEY,
                person_id uuid,
                label varchar(120),
                key_hash varchar(255) NOT NULL,
                is_active boolean NOT NULL,
                revoked_at timestamptz
            );
            CREATE TABLE public.scheduled_tasks (
                id uuid PRIMARY KEY,
                name varchar(160) NOT NULL,
                task_name varchar(200) NOT NULL,
                enabled boolean NOT NULL
            );
            CREATE TABLE sync.sync_entity (
                sync_id uuid PRIMARY KEY,
                organization_id uuid NOT NULL,
                source_system varchar(50) NOT NULL,
                source_doctype varchar(100) NOT NULL,
                source_name varchar(255) NOT NULL
            );
            CREATE TABLE sync.sync_history (
                history_id uuid PRIMARY KEY,
                organization_id uuid NOT NULL,
                source_system varchar(50) NOT NULL,
                status varchar(50) NOT NULL
            );
            CREATE TABLE sync.crm_sync_mapping (
                mapping_id uuid PRIMARY KEY,
                organization_id uuid NOT NULL,
                crm_entity_type sync.crm_entity_type NOT NULL,
                crm_id varchar(36) NOT NULL,
                local_entity_type varchar(50) NOT NULL,
                local_entity_id uuid NOT NULL,
                crm_status sync.crm_sync_status NOT NULL,
                crm_data jsonb
            );
            CREATE TABLE ar.customer (
                customer_id uuid PRIMARY KEY,
                organization_id uuid NOT NULL,
                crm_id varchar(36)
            );
            CREATE TABLE inv.material_request (
                request_id uuid PRIMARY KEY,
                organization_id uuid NOT NULL,
                request_number varchar(50) NOT NULL,
                source_system varchar(20) NOT NULL,
                crm_id varchar(36),
                CONSTRAINT uq_material_request_org_crm_id
                    UNIQUE (organization_id, crm_id)
            );
            CREATE TABLE expense.expense_claim (
                claim_id uuid PRIMARY KEY,
                organization_id uuid NOT NULL,
                claim_number varchar(30) NOT NULL,
                crm_id varchar(36),
                CONSTRAINT uq_expense_claim_org_crm_id
                    UNIQUE (organization_id, crm_id)
            );
            """,
    )


def _seed_source_catalog(connection: Connection) -> dict[str, UUID | str]:
    ids: dict[str, UUID | str] = {
        "organization": uuid4(),
        "crm_config": uuid4(),
        "other_config": uuid4(),
        "crm_key": uuid4(),
        "other_key": uuid4(),
        "crm_schedule": uuid4(),
        "other_schedule": uuid4(),
        "crm_entity": uuid4(),
        "sub_entity": uuid4(),
        "crm_history": uuid4(),
        "sub_history": uuid4(),
        "mapping": uuid4(),
        "customer": uuid4(),
        "crm_material": uuid4(),
        "sub_material": uuid4(),
        "expense": uuid4(),
        "crm_key_hash": "hash-that-must-not-change",
        "sub_source_id": "proven-sub-request",
    }
    _execute_batch(
        connection,
        """
            INSERT INTO sync.integration_config
                (config_id, organization_id, integration_type, api_key,
                 api_secret, is_active)
            VALUES
                (:crm_config, :organization, 'DOTMAC_CRM',
                 'held-value-a', 'held-value-b', true),
                (:other_config, :organization, 'ERPNEXT',
                 'held-value-c', 'held-value-d', true);

            INSERT INTO public.api_keys
                (id, person_id, label, key_hash, is_active)
            VALUES
                (:crm_key, NULL, 'dotmac-crm-service-retired',
                 :crm_key_hash, true),
                (:other_key, :organization, 'erp-service-current',
                 'unrelated-hash', true);

            INSERT INTO public.scheduled_tasks
                (id, name, task_name, enabled)
            VALUES
                (:crm_schedule, 'CRM poll', 'app.tasks.crm.sync_all', true),
                (:other_schedule, 'Sub poll', 'app.tasks.dotmac_sub.sync_all', true);

            INSERT INTO sync.sync_entity
                (sync_id, organization_id, source_system,
                 source_doctype, source_name)
            VALUES
                (:crm_entity, :organization, 'crm', 'Ticket', 'CRM-10'),
                (:sub_entity, :organization, 'dotmac_sub', 'Ticket', 'SUB-10');

            INSERT INTO sync.sync_history
                (history_id, organization_id, source_system, status)
            VALUES
                (:crm_history, :organization, 'dotmac_crm', 'COMPLETED'),
                (:sub_history, :organization, 'dotmac_sub', 'COMPLETED');

            INSERT INTO sync.crm_sync_mapping
                (mapping_id, organization_id, crm_entity_type, crm_id,
                 local_entity_type, local_entity_id, crm_status, crm_data)
            VALUES
                (:mapping, :organization, 'TICKET', 'ambiguous-mapping',
                 'ticket', :crm_entity, 'ACTIVE', '{"cached": true}'::jsonb);

            INSERT INTO ar.customer
                (customer_id, organization_id, crm_id)
            VALUES (:customer, :organization, 'ambiguous-customer');

            INSERT INTO inv.material_request
                (request_id, organization_id, request_number,
                 source_system, crm_id)
            VALUES
                (:crm_material, :organization, 'MAT-CRM',
                 'crm', 'historical-crm-request'),
                (:sub_material, :organization, 'MAT-SUB',
                 'sub', :sub_source_id);

            INSERT INTO expense.expense_claim
                (claim_id, organization_id, claim_number, crm_id)
            VALUES
                (:expense, :organization, 'EXP-10', 'ambiguous-expense');
            """,
        ids,
    )
    return ids


def test_retirement_archives_evidence_and_removes_live_crm_state(
    isolated_database_url: URL,
) -> None:
    engine = create_engine(isolated_database_url)
    try:
        with engine.begin() as connection:
            _create_source_catalog(connection)
            ids = _seed_source_catalog(connection)
    finally:
        engine.dispose()

    _run_upgrade(isolated_database_url)

    engine = create_engine(isolated_database_url)
    try:
        with engine.connect() as connection:
            crm_config = connection.execute(
                text(
                    "SELECT is_active, api_key, api_secret "
                    "FROM sync.integration_config WHERE config_id = :id"
                ),
                {"id": ids["crm_config"]},
            ).one()
            assert crm_config == (False, None, None)
            other_config = connection.execute(
                text(
                    "SELECT is_active, api_key, api_secret "
                    "FROM sync.integration_config WHERE config_id = :id"
                ),
                {"id": ids["other_config"]},
            ).one()
            assert other_config == (True, "held-value-c", "held-value-d")

            crm_key = connection.execute(
                text(
                    "SELECT is_active, revoked_at IS NOT NULL, key_hash "
                    "FROM public.api_keys WHERE id = :id"
                ),
                {"id": ids["crm_key"]},
            ).one()
            assert crm_key == (False, True, ids["crm_key_hash"])
            assert connection.scalar(
                text("SELECT is_active FROM public.api_keys WHERE id = :id"),
                {"id": ids["other_key"]},
            )

            assert (
                connection.scalar(text("SELECT to_regclass('sync.crm_sync_mapping')"))
                is None
            )
            assert (
                connection.scalar(text("SELECT to_regtype('sync.crm_entity_type')"))
                is None
            )
            assert (
                connection.scalar(text("SELECT to_regtype('sync.crm_sync_status')"))
                is None
            )
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM public.scheduled_tasks WHERE id = :id"),
                    {"id": ids["crm_schedule"]},
                )
                == 0
            )
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM public.scheduled_tasks WHERE id = :id"),
                    {"id": ids["other_schedule"]},
                )
                == 1
            )

            assert connection.scalar(text("SELECT count(*) FROM sync.sync_entity")) == 1
            assert (
                connection.scalar(text("SELECT source_system FROM sync.sync_entity"))
                == "sub"
            )
            assert (
                connection.scalar(text("SELECT count(*) FROM sync.sync_history")) == 1
            )
            assert (
                connection.scalar(text("SELECT source_system FROM sync.sync_history"))
                == "sub"
            )

            columns = connection.execute(
                text(
                    "SELECT table_schema, table_name, column_name "
                    "FROM information_schema.columns "
                    "WHERE (table_schema, table_name) IN "
                    "(('ar', 'customer'), ('inv', 'material_request'), "
                    " ('expense', 'expense_claim'))"
                )
            ).all()
            assert all(column != "crm_id" for _, _, column in columns)

            material_rows = connection.execute(
                text(
                    "SELECT request_number, source_system, source_id "
                    "FROM inv.material_request ORDER BY request_number"
                )
            ).all()
            assert material_rows == [
                ("MAT-CRM", "crm", None),
                ("MAT-SUB", "sub", ids["sub_source_id"]),
            ]
            source_lengths = connection.execute(
                text(
                    "SELECT table_schema, character_maximum_length "
                    "FROM information_schema.columns "
                    "WHERE column_name = 'source_id' "
                    "AND (table_schema, table_name) IN "
                    "(('inv', 'material_request'), "
                    " ('expense', 'expense_claim')) "
                    "ORDER BY table_schema"
                )
            ).all()
            assert source_lengths == [("expense", 120), ("inv", 120)]
            assert (
                connection.scalar(text("SELECT source_id FROM expense.expense_claim"))
                is None
            )

            archive_counts = dict(
                connection.execute(
                    text(
                        "SELECT source_relation, count(*) "
                        "FROM archive.retired_crm_records "
                        "GROUP BY source_relation"
                    )
                ).all()
            )
            assert archive_counts == {
                "ar.customer.crm_id": 1,
                "expense.expense_claim.crm_id": 1,
                "inv.material_request.crm_id": 2,
                "sync.crm_sync_mapping": 1,
                "sync.sync_entity:crm": 1,
                "sync.sync_history:crm": 1,
            }
            archived_schedule = connection.execute(
                text(
                    "SELECT source_primary_key, payload->>'task_name' "
                    "FROM archive.retired_crm_scheduled_tasks"
                )
            ).one()
            assert archived_schedule == (
                str(ids["crm_schedule"]),
                "app.tasks.crm.sync_all",
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM archive.retired_crm_records "
                        "WHERE payload ? 'key_hash'"
                    )
                )
                == 0
            )

            archive_security = connection.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity "
                    "FROM pg_class "
                    "WHERE oid = 'archive.retired_crm_records'::regclass"
                )
            ).one()
            assert archive_security == (True, True)
            platform_archive_security = connection.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity "
                    "FROM pg_class WHERE oid = "
                    "'archive.retired_crm_scheduled_tasks'::regclass"
                )
            ).one()
            assert platform_archive_security == (False, False)
            archive_scope_columns = connection.execute(
                text(
                    "SELECT table_name, is_nullable "
                    "FROM information_schema.columns "
                    "WHERE table_schema = 'archive' "
                    "AND column_name = 'organization_id'"
                )
            ).all()
            assert archive_scope_columns == [("retired_crm_records", "NO")]
            public_schema_usage = connection.scalar(
                text(
                    "SELECT count(*) FROM pg_namespace AS namespace, "
                    "LATERAL aclexplode(COALESCE(namespace.nspacl, "
                    "acldefault('n', namespace.nspowner))) AS acl "
                    "WHERE namespace.nspname = 'archive' "
                    "AND acl.grantee = 0 AND acl.privilege_type = 'USAGE'"
                )
            )
            assert public_schema_usage == 0
            public_table_select = connection.scalar(
                text(
                    "SELECT count(*) FROM pg_class AS relation, "
                    "pg_namespace AS namespace, "
                    "LATERAL aclexplode(COALESCE(relation.relacl, "
                    "acldefault('r', relation.relowner))) AS acl "
                    "WHERE relation.relnamespace = namespace.oid "
                    "AND namespace.nspname = 'archive' "
                    "AND relation.relname IN "
                    "('retired_crm_records', 'retired_crm_scheduled_tasks') "
                    "AND acl.grantee = 0 AND acl.privilege_type = 'SELECT'"
                )
            )
            assert public_table_select == 0
    finally:
        engine.dispose()


def test_retirement_refuses_an_incomplete_source_catalog_before_mutation(
    isolated_database_url: URL,
) -> None:
    engine = create_engine(isolated_database_url)
    ids: dict[str, UUID | str]
    try:
        with engine.begin() as connection:
            _create_source_catalog(connection)
            ids = _seed_source_catalog(connection)
            connection.execute(text("DROP TABLE sync.sync_history"))
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="exact current-head source catalog"):
        _run_upgrade(isolated_database_url)

    engine = create_engine(isolated_database_url)
    try:
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT to_regnamespace('archive')")) is None
            crm_config = connection.execute(
                text(
                    "SELECT is_active, api_key, api_secret "
                    "FROM sync.integration_config WHERE config_id = :id"
                ),
                {"id": ids["crm_config"]},
            ).one()
            assert crm_config == (True, "held-value-a", "held-value-b")
            assert connection.scalar(
                text("SELECT is_active FROM public.api_keys WHERE id = :id"),
                {"id": ids["crm_key"]},
            )
    finally:
        engine.dispose()


def test_retirement_is_forward_only(isolated_database_url: URL) -> None:
    with pytest.raises(RuntimeError, match="forward-only"):
        _run_downgrade(isolated_database_url)
