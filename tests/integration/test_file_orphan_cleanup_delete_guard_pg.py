"""PostgreSQL proof that runtime roles cannot erase orphan-cleanup evidence."""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.integration

TABLES = ("file_orphan_cleanup_runs", "file_orphan_cleanup_deletions")
ROW_SQL = {
    "file_orphan_cleanup_runs": (
        text("SELECT count(*) FROM public.file_orphan_cleanup_runs WHERE id = :id"),
        text("DELETE FROM public.file_orphan_cleanup_runs WHERE id = :id"),
    ),
    "file_orphan_cleanup_deletions": (
        text(
            "SELECT count(*) FROM public.file_orphan_cleanup_deletions WHERE id = :id"
        ),
        text("DELETE FROM public.file_orphan_cleanup_deletions WHERE id = :id"),
    ),
}
MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic/versions/20260930_orphan_evidence_guard.py"
)


def _migration():
    spec = importlib.util.spec_from_file_location(
        "orphan_evidence_guard", MIGRATION_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revoke_repairs_an_existing_legacy_runtime_acl(engine, monkeypatch) -> None:
    """The optional legacy role branch must execute, not merely parse."""
    with engine.connect() as connection, connection.begin() as transaction:
        if not connection.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dotmac_erp_app')"
            )
        ):
            connection.execute(text("CREATE ROLE dotmac_erp_app NOLOGIN"))

        for table in TABLES:
            qualified = f"public.{table}"
            connection.execute(
                text(
                    f"GRANT DELETE ON TABLE {qualified} TO PUBLIC, app_user, dotmac_erp_app"
                )
            )
            for role in ("app_user", "dotmac_erp_app"):
                assert connection.scalar(
                    text(
                        "SELECT has_table_privilege("
                        "CAST(:role AS name), CAST(:relation AS text), 'DELETE')"
                    ),
                    {"role": role, "relation": qualified},
                )

        migration = _migration()
        monkeypatch.setattr(
            migration.op, "execute", lambda sql: connection.execute(text(sql))
        )
        for table in TABLES:
            migration._revoke_delete(table)
            for role in ("app_user", "dotmac_erp_app"):
                assert not connection.scalar(
                    text(
                        "SELECT has_table_privilege("
                        "CAST(:role AS name), CAST(:relation AS text), 'DELETE')"
                    ),
                    {"role": role, "relation": f"public.{table}"},
                )

        assert connection.execute(
            text(
                "SELECT rolsuper, rolbypassrls FROM pg_roles "
                "WHERE rolname = 'dotmac_erp_app'"
            )
        ).one() == (False, False)
        evidence = _seed_evidence(connection)
        for table in TABLES:
            connection.execute(
                text(f"GRANT SELECT, DELETE ON TABLE public.{table} TO dotmac_erp_app")
            )
        connection.execute(text("SET LOCAL ROLE dotmac_erp_app"))
        connection.execute(
            text("SELECT set_config('app.current_organization_id', :org, true)"),
            {"org": str(evidence["organization_id"])},
        )
        for table in TABLES:
            count_sql, delete_sql = ROW_SQL[table]
            assert connection.scalar(count_sql, {"id": evidence[table]}) == 1
            assert connection.execute(delete_sql, {"id": evidence[table]}).rowcount == 0
        transaction.rollback()


def _seed_evidence(connection) -> dict[str, object]:
    organization_id = uuid4()
    run_id = uuid4()
    deletion_id = uuid4()
    connection.execute(
        text(
            "INSERT INTO core_org.organization ("
            "organization_id, organization_code, legal_name, "
            "functional_currency_code, presentation_currency_code, "
            "fiscal_year_end_month, fiscal_year_end_day, is_active) "
            "VALUES (:id, :code, 'Cleanup DELETE guard', 'NGN', 'NGN', 12, 31, true)"
        ),
        {"id": organization_id, "code": f"RLS-{uuid4().hex[:12].upper()}"},
    )
    connection.execute(
        text(
            "INSERT INTO public.file_orphan_cleanup_runs ("
            "id, organization_id, plan_digest, older_than, plan_observed_at, "
            "provider_code, candidate_count, actor, invocation_id, status) "
            "VALUES (:id, :org, :digest, :older, :observed, 'erp_s3', 1, "
            "'operator@example.com', :invocation, 'completed')"
        ),
        {
            "id": run_id,
            "org": organization_id,
            "digest": "a" * 64,
            "older": datetime(2026, 9, 1, tzinfo=UTC),
            "observed": datetime(2026, 9, 20, tzinfo=UTC),
            "invocation": f"guard-{uuid4()}",
        },
    )
    connection.execute(
        text(
            "INSERT INTO public.file_orphan_cleanup_deletions ("
            "id, organization_id, run_id, storage_key, key_digest, outcome) "
            "VALUES (:id, :org, :run, :key, :digest, 'deleted')"
        ),
        {
            "id": deletion_id,
            "org": organization_id,
            "run": run_id,
            "key": f"tenants/{organization_id}/files/{uuid4()}",
            "digest": "b" * 64,
        },
    )
    return {
        "organization_id": organization_id,
        "file_orphan_cleanup_runs": run_id,
        "file_orphan_cleanup_deletions": deletion_id,
    }


@pytest.mark.parametrize("table", TABLES)
def test_runtime_delete_is_denied_even_after_a_later_grant(engine, table: str) -> None:
    with engine.connect() as connection, connection.begin() as transaction:
        count_sql, delete_sql = ROW_SQL[table]
        evidence = _seed_evidence(connection)
        policy = connection.execute(
            text(
                "SELECT rel.relrowsecurity, rel.relforcerowsecurity, "
                "policy.polcmd, policy.polpermissive, policy.polroles, "
                "pg_get_expr(policy.polqual, policy.polrelid) "
                "FROM pg_class rel JOIN pg_policy policy ON policy.polrelid = rel.oid "
                "WHERE rel.oid = CAST(:relation AS regclass) "
                "AND policy.polname = :policy"
            ),
            {
                "relation": f"public.{table}",
                "policy": f"{table}_no_runtime_delete",
            },
        ).one()
        assert policy == (True, True, "d", False, [0], "false")
        assert not connection.scalar(
            text(
                "SELECT has_table_privilege("
                "'app_user'::name, CAST(:relation AS text), 'DELETE')"
            ),
            {"relation": f"public.{table}"},
        )

        connection.execute(text("SET LOCAL ROLE app_user"))
        connection.execute(
            text("SELECT set_config('app.current_organization_id', :org, true)"),
            {"org": str(evidence["organization_id"])},
        )
        assert (
            connection.scalar(
                count_sql,
                {"id": evidence[table]},
            )
            == 1
        )
        savepoint = connection.begin_nested()
        with pytest.raises(DBAPIError) as denied:
            connection.execute(
                delete_sql,
                {"id": evidence[table]},
            )
        assert denied.value.orig.sqlstate == "42501"
        savepoint.rollback()

        connection.execute(text("RESET ROLE"))
        connection.execute(text(f"GRANT DELETE ON TABLE public.{table} TO app_user"))
        connection.execute(text("SET LOCAL ROLE app_user"))
        result = connection.execute(
            delete_sql,
            {"id": evidence[table]},
        )
        assert result.rowcount == 0
        connection.execute(text("RESET ROLE"))
        assert (
            connection.scalar(
                count_sql,
                {"id": evidence[table]},
            )
            == 1
        )

        # app_admin is the explicit BYPASSRLS migration/recovery principal.
        connection.execute(text("SET LOCAL ROLE app_admin"))
        assert (
            connection.execute(
                delete_sql,
                {"id": evidence[table]},
            ).rowcount
            == 1
        )
        transaction.rollback()
