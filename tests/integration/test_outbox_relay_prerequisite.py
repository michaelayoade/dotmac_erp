"""PostgreSQL proofs for ERP's ``outbox_relay.v1`` provider.

The relay's correctness is only half schema. The other half is that a
NOBYPASSRLS dispatcher reaches cross-tenant rows through two hardened functions
and by no other route — so most of these negatives break a privilege rather
than a column, and each requires the verifier to refuse for that specific
reason.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

pytestmark = pytest.mark.integration

PREREQUISITE = "outbox_relay.v1"


@pytest.fixture(autouse=True)
def _install_erp_bindings() -> Iterator[None]:
    from dotmac_kernel.prerequisites import (
        install_prerequisite_bindings,
        installed_bindings,
    )

    from app.migration_bindings import ASSEMBLY_PREREQUISITE_BINDINGS

    previous = tuple(installed_bindings())
    install_prerequisite_bindings(ASSEMBLY_PREREQUISITE_BINDINGS)
    try:
        yield
    finally:
        install_prerequisite_bindings(previous)


@contextlib.contextmanager
def _broken(engine: Engine, statement: str) -> Iterator[Connection]:
    connection = engine.connect()
    transaction = connection.begin()
    try:
        connection.execute(text(statement))
        yield connection
    finally:
        transaction.rollback()
        connection.close()


def test_migrated_erp_satisfies_the_kernel_relay_contract(engine: Engine) -> None:
    from dotmac_kernel.migrations.verify import require_prerequisites

    with engine.connect() as connection:
        require_prerequisites(connection, (PREREQUISITE,))


@pytest.mark.parametrize(
    ("statement", "expected"),
    [
        pytest.param(
            "DROP TABLE public.platform_outbox_events CASCADE",
            "does not exist",
            id="platform-plane-absent",
        ),
        pytest.param(
            "DROP INDEX public.ix_outbox_events_status_leased_at",
            "no index on",
            id="stale-lease-reclaim-unindexed",
        ),
        pytest.param(
            "DROP INDEX public.ix_platform_outbox_events_status_available_at",
            "no index on",
            id="platform-claim-unindexed",
        ),
        pytest.param(
            "ALTER TABLE public.outbox_events NO FORCE ROW LEVEL SECURITY",
            "FORCEd row-level security",
            id="tenant-plane-unforced",
        ),
        pytest.param(
            "ALTER POLICY outbox_events_tenant_isolation ON public.outbox_events "
            "USING (true)",
            "do not restrict rows",
            id="tenant-policy-always-passes",
        ),
        pytest.param(
            "ALTER TABLE public.platform_outbox_events ENABLE ROW LEVEL SECURITY",
            "must carry no row-level security",
            id="platform-plane-policied",
        ),
        pytest.param(
            "GRANT SELECT ON TABLE public.platform_outbox_events TO app_user",
            "reachable by",
            id="platform-plane-exposed-to-tenant-traffic",
        ),
        pytest.param(
            "GRANT SELECT (payload) ON TABLE public.outbox_events TO outbox_dispatcher",
            "holds table or column privilege",
            id="dispatcher-given-a-column-grant",
        ),
        pytest.param(
            "ALTER ROLE outbox_dispatcher BYPASSRLS",
            "rolbypassrls",
            id="dispatcher-can-bypass-rls",
        ),
        pytest.param(
            "DROP FUNCTION public.settle_outbox_event"
            "(uuid, text, text, timestamptz, integer, text)",
            "does not exist",
            id="settle-missing",
        ),
        pytest.param(
            "ALTER FUNCTION public.claim_outbox_batch(text, integer, integer) "
            "SECURITY INVOKER",
            "not SECURITY DEFINER",
            id="claim-downgraded-to-invoker",
        ),
        pytest.param(
            "ALTER FUNCTION public.claim_outbox_batch(text, integer, integer) "
            "SET search_path = public",
            "empty search_path",
            id="claim-path-unpinned",
        ),
        pytest.param(
            "GRANT EXECUTE ON FUNCTION public.claim_outbox_batch"
            "(text, integer, integer) TO PUBLIC",
            "granted to PUBLIC",
            id="claim-executable-by-everyone",
        ),
    ],
)
def test_each_broken_observable_is_refused_specifically(
    engine: Engine, statement: str, expected: str
) -> None:
    from dotmac_kernel.migrations.verify import (
        PrerequisiteNotSatisfiedError,
        require_prerequisites,
    )

    with _broken(engine, statement) as connection:
        with pytest.raises(PrerequisiteNotSatisfiedError, match=expected):
            require_prerequisites(connection, (PREREQUISITE,))


def test_the_dispatcher_can_only_reach_rows_through_its_functions(
    engine: Engine,
) -> None:
    """The privilege boundary, asserted directly rather than through the verifier."""
    with engine.connect() as connection:
        for table in ("public.outbox_events", "public.platform_outbox_events"):
            for role in ("outbox_dispatcher", "platform_outbox_dispatcher"):
                for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                    assert not connection.scalar(
                        text(
                            "SELECT has_table_privilege("
                            "CAST(:role AS name), CAST(:table AS text), :privilege)"
                        ),
                        {"role": role, "table": table, "privilege": privilege},
                    ), f"{role} should hold no {privilege} on {table}"

        for role, signature in (
            ("outbox_dispatcher", "public.claim_outbox_batch(text, integer, integer)"),
            (
                "platform_outbox_dispatcher",
                "public.claim_platform_outbox_batch(text, integer, integer)",
            ),
        ):
            assert connection.scalar(
                text(
                    "SELECT has_function_privilege("
                    "CAST(:role AS name), :signature, 'EXECUTE')"
                ),
                {"role": role, "signature": signature},
            ), f"{role} cannot execute its own claim function"

        # Each dispatcher is confined to its OWN plane. Cross-plane EXECUTE
        # would let the tenant drain claim platform events and vice versa.
        assert not connection.scalar(
            text(
                "SELECT has_function_privilege("
                "CAST('outbox_dispatcher' AS name), :signature, 'EXECUTE')"
            ),
            {"signature": "public.claim_platform_outbox_batch(text, integer, integer)"},
        )


def test_erps_own_business_outbox_is_untouched(engine: Engine) -> None:
    """platform.event_outbox keeps its authority; this slice adds beside it."""
    with engine.connect() as connection:
        assert connection.scalar(
            text("SELECT to_regclass('platform.event_outbox') IS NOT NULL")
        )
        assert connection.scalar(
            text("SELECT to_regclass('public.outbox_events') IS NOT NULL")
        )
        overlap = connection.scalar(
            text(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'outbox_events' "
                "AND column_name IN ('idempotency_key', 'aggregate_type', "
                "'aggregate_id', 'event_name', 'headers')"
            )
        )
        assert overlap == 0, (
            "the module relay has grown ERP business-event vocabulary — two "
            "outboxes with one vocabulary is one outbox with two writers"
        )
