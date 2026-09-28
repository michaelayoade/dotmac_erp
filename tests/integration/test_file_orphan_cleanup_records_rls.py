"""PostgreSQL proof for the file-object orphan cleanup record tables' tenant boundary.

Mirrors ``tests/integration/test_source_correlation_rls.py``: two
organizations, the ``app_user`` runtime role, and both the SELECT-visibility
and cross-org INSERT/UPDATE-rejection checks. This module proves the tables
and their RLS policy directly with raw SQL; it does not exercise the Celery
task's own recording calls — see
``tests/services/test_file_object_reconciliation.py`` for those.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.integration


def _organization_values(suffix: str) -> dict[str, object]:
    return {
        "organization_id": uuid4(),
        "organization_code": f"RLS-{suffix}-{uuid4().hex[:8].upper()}",
        "legal_name": f"Orphan cleanup RLS {suffix}",
    }


def _insert_organization(connection, organization: dict[str, object]) -> None:
    connection.execute(
        text(
            """
            INSERT INTO core_org.organization (
                organization_id, organization_code, legal_name,
                functional_currency_code, presentation_currency_code,
                fiscal_year_end_month, fiscal_year_end_day, is_active
            ) VALUES (
                :organization_id, :organization_code, :legal_name,
                'NGN', 'NGN', 12, 31, true
            )
            """
        ),
        organization,
    )


def _insert_run(connection, *, organization_id, invocation_id: str) -> object:
    run_id = uuid4()
    connection.execute(
        text(
            """
            INSERT INTO public.file_orphan_cleanup_runs (
                id, organization_id, plan_digest, older_than, plan_observed_at,
                provider_code, candidate_count, actor, invocation_id, status
            ) VALUES (
                :id, :organization_id, :plan_digest, :older_than, :plan_observed_at,
                'erp_s3', 1, 'operator@example.com', :invocation_id, 'completed'
            )
            """
        ),
        {
            "id": run_id,
            "organization_id": organization_id,
            "plan_digest": "a" * 64,
            "older_than": datetime(2026, 9, 1, tzinfo=UTC),
            "plan_observed_at": datetime(2026, 9, 20, tzinfo=UTC),
            "invocation_id": invocation_id,
        },
    )
    return run_id


def test_app_user_reads_only_the_selected_tenants_cleanup_runs(engine) -> None:
    first = _organization_values("A")
    second = _organization_values("B")

    connection = engine.connect()
    transaction = connection.begin()
    try:
        for organization in (first, second):
            _insert_organization(connection, organization)
        first_run_id = _insert_run(
            connection, organization_id=first["organization_id"], invocation_id="run-a"
        )
        _insert_run(
            connection, organization_id=second["organization_id"], invocation_id="run-b"
        )
        connection.execute(
            text(
                """
                INSERT INTO public.file_orphan_cleanup_deletions (
                    id, organization_id, run_id, storage_key, key_digest, outcome
                ) VALUES (
                    :id, :organization_id, :run_id, :storage_key, :key_digest, 'deleted'
                )
                """
            ),
            {
                "id": uuid4(),
                "organization_id": first["organization_id"],
                "run_id": first_run_id,
                "storage_key": f"tenants/{first['organization_id']}/files/{uuid4()}",
                "key_digest": "b" * 64,
            },
        )

        connection.execute(text("SET LOCAL ROLE app_user"))
        connection.execute(
            text("SELECT set_config('app.current_organization_id', :org, true)"),
            {"org": str(first["organization_id"])},
        )

        visible_runs = list(
            connection.execute(
                text("SELECT organization_id FROM public.file_orphan_cleanup_runs")
            ).scalars()
        )
        assert visible_runs == [first["organization_id"]]
        assert second["organization_id"] not in visible_runs

        visible_deletions = list(
            connection.execute(
                text("SELECT organization_id FROM public.file_orphan_cleanup_deletions")
            ).scalars()
        )
        assert visible_deletions == [first["organization_id"]]

        attempted_run_id = uuid4()
        savepoint = connection.begin_nested()
        with pytest.raises(DBAPIError):
            connection.execute(
                text(
                    """
                    INSERT INTO public.file_orphan_cleanup_runs (
                        id, organization_id, plan_digest, older_than,
                        plan_observed_at, provider_code, candidate_count, actor,
                        invocation_id, status
                    ) VALUES (
                        :id, :organization_id, :plan_digest, :older_than,
                        :plan_observed_at, 'erp_s3', 1, 'operator@example.com',
                        'rejected-run', 'completed'
                    )
                    """
                ),
                {
                    "id": attempted_run_id,
                    "organization_id": second["organization_id"],
                    "plan_digest": "c" * 64,
                    "older_than": datetime(2026, 9, 1, tzinfo=UTC),
                    "plan_observed_at": datetime(2026, 9, 20, tzinfo=UTC),
                },
            )
        savepoint.rollback()

        savepoint = connection.begin_nested()
        with pytest.raises(DBAPIError):
            connection.execute(
                text(
                    "UPDATE public.file_orphan_cleanup_runs "
                    "SET organization_id = :other WHERE organization_id = :current"
                ),
                {
                    "other": second["organization_id"],
                    "current": first["organization_id"],
                },
            )
        savepoint.rollback()

        unchanged = list(
            connection.execute(
                text("SELECT organization_id FROM public.file_orphan_cleanup_runs")
            ).scalars()
        )
        assert unchanged == [first["organization_id"]]
    finally:
        transaction.rollback()
        connection.close()


def test_app_user_cannot_insert_a_cross_tenant_deletion_row(engine) -> None:
    """Mirrors the runs-table cross-tenant INSERT refusal above, but for
    ``file_orphan_cleanup_deletions``: with the session pinned to the FIRST
    organization, an INSERT naming the SECOND organization's id is refused by
    the table's own ``WITH CHECK`` clause, independently of the runs table's."""
    first = _organization_values("C")
    second = _organization_values("D")

    connection = engine.connect()
    transaction = connection.begin()
    try:
        for organization in (first, second):
            _insert_organization(connection, organization)
        first_run_id = _insert_run(
            connection, organization_id=first["organization_id"], invocation_id="run-c"
        )
        second_run_id = _insert_run(
            connection, organization_id=second["organization_id"], invocation_id="run-d"
        )

        connection.execute(text("SET LOCAL ROLE app_user"))
        connection.execute(
            text("SELECT set_config('app.current_organization_id', :org, true)"),
            {"org": str(first["organization_id"])},
        )

        # A deletion row honestly tagged with the pinned tenant and its own
        # run succeeds -- the negative case below isn't vacuous.
        connection.execute(
            text(
                """
                INSERT INTO public.file_orphan_cleanup_deletions (
                    id, organization_id, run_id, storage_key, key_digest, outcome
                ) VALUES (
                    :id, :organization_id, :run_id, :storage_key, :key_digest, 'deleted'
                )
                """
            ),
            {
                "id": uuid4(),
                "organization_id": first["organization_id"],
                "run_id": first_run_id,
                "storage_key": f"tenants/{first['organization_id']}/files/{uuid4()}",
                "key_digest": "d" * 64,
            },
        )

        savepoint = connection.begin_nested()
        with pytest.raises(DBAPIError):
            connection.execute(
                text(
                    """
                    INSERT INTO public.file_orphan_cleanup_deletions (
                        id, organization_id, run_id, storage_key, key_digest, outcome
                    ) VALUES (
                        :id, :organization_id, :run_id, :storage_key, :key_digest,
                        'deleted'
                    )
                    """
                ),
                {
                    "id": uuid4(),
                    "organization_id": second["organization_id"],
                    "run_id": second_run_id,
                    "storage_key": (
                        f"tenants/{second['organization_id']}/files/{uuid4()}"
                    ),
                    "key_digest": "e" * 64,
                },
            )
        savepoint.rollback()

        unchanged = list(
            connection.execute(
                text(
                    "SELECT organization_id FROM public.file_orphan_cleanup_deletions "
                    "WHERE run_id IN (:first_run, :second_run)"
                ),
                {"first_run": first_run_id, "second_run": second_run_id},
            ).scalars()
        )
        assert unchanged == [first["organization_id"]]
    finally:
        transaction.rollback()
        connection.close()
