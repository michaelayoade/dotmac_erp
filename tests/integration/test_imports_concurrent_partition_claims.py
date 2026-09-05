"""Gate 3 for `imports`: two claimants RACING reach disjoint partition sets.

`docs/architecture/imports-adoption-boundary.md` lists five retirement gates.
Gate 3 is "at least two concurrent dry-run claims proving disjoint ownership",
and the word doing the work is CONCURRENT.

## Why the existing test does not close this gate

`test_accounting_lineage_composition.py` already carries a case whose name says
`two_concurrent_import_workers`. It opens two sessions and calls
`claim_partition` in ONE THREAD, one after the other. The transactions overlap —
the first session holds its row lock uncommitted while the second claims — so it
genuinely exercises `FOR UPDATE SKIP LOCKED`. That is worth having and it is
renamed, not deleted.

What it cannot do is RACE. The ordering is deterministic, so it cannot detect a
defect that only appears when two claimants execute the claim simultaneously.
Its name was broader than its cases, which is the third time in this programme a
test has been trusted for the property in its name rather than the one in its
body — the same shape as a guard matching the prose that justifies it.

## The harness has to be proved to bite

Two claimants each getting a non-empty set is not disjointness, and a harness
that never actually interleaves reports disjointness for everything. So this
file carries a NEGATIVE CONTROL: the rejected shape — a non-atomic
`SELECT`-then-`UPDATE` claim with a real time-of-check-to-time-of-use window —
driven through the SAME barrier and the SAME threads. It must COLLIDE.

If the control does not collide, the harness never raced, and the positive
result above it means nothing. That single case is the difference between "my
claim path works" and "my harness never actually raced". This programme has paid
for that distinction twice: a create-only writer whose refusal was a
`stat`-then-write, green under sequential tests; and a concurrency proof that
passed against broken code because the framework supplied isolation the code did
not.

The control runs against its OWN table, not `mod_imports`. It is validating the
harness, not the module, and it must not depend on module internals that would
make it rot when they change.

## Disjointness is asserted over the UNION

Per-claimant assertions cannot express it: the property is that no partition
appears in two claimants' sets, and that none is lost. Both halves are asserted,
because a claim path that silently dropped a partition would satisfy
disjointness alone.

## A run where one claimant takes everything

That is *technically* disjoint and proves nothing about racing. DECIDED: it is
not a failure of the SYSTEM, so it does not fail the correctness assertions —
scheduling is not a correctness property and asserting on it would make this
test flaky. Instead it is a failure of the EVIDENCE, so
`test_the_race_harness_actually_interleaves` requires that across several rounds
at least one round split the work. Correctness and evidence are asserted
separately because they fail for different reasons and want different fixes.

## Cleanup

Every database here is created per-test and DROPped in a `finally`, so a crashed
run cannot leave a claim that makes the next run's disjointness hold for the
wrong reason. That is stronger than revoking state inside a shared database.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator
from uuid import UUID, uuid4

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration

#: Enough partitions that a correct implementation can split them, and few
#: enough that the race window stays observable.
PARTITION_COUNT = 8
CLAIMANTS = 2
#: Rounds for the evidence assertions. One round can legitimately be won
#: entirely by one thread; several rounds all won by one thread means the
#: harness is not interleaving.
ROUNDS = 5


def _render(url: URL) -> str:
    return url.render_as_string(hide_password=False)


def _psycopg_url(url: URL) -> str:
    return _render(url.set(drivername="postgresql"))


@pytest.fixture()
def _real_postgres_types() -> Iterator[None]:
    """Undo the unit suite's SQLite type shim before any DDL runs.

    Reused from `tests/integration/conftest.py` rather than reimplemented — two
    copies of a type-restoration routine is exactly how one of them rots.
    """
    from tests.integration.conftest import _fix_patched_types

    restore = _fix_patched_types()
    try:
        yield
    finally:
        restore()


@pytest.fixture()
def composed_database(
    monkeypatch: pytest.MonkeyPatch, _real_postgres_types: None
) -> Iterator[URL]:
    """A disposable database at heads, carrying `mod_imports`."""
    configured = os.getenv("TEST_DATABASE_URL")
    if not configured:
        raise pytest.UsageError("gate 3 requires TEST_DATABASE_URL")
    base_url = make_url(configured)
    if not base_url.drivername.startswith("postgresql"):
        raise pytest.UsageError("gate 3 requires PostgreSQL")

    name = f"erp_imports_gate3_{uuid4().hex}"
    maintenance = base_url.set(database="postgres")
    with psycopg.connect(_psycopg_url(maintenance), autocommit=True) as admin:
        admin.execute(
            sql.SQL("CREATE DATABASE {} OWNER app_admin").format(sql.Identifier(name))
        )
    try:
        database_url = base_url.set(database=name, username="app_admin", password=None)
        monkeypatch.setenv("MIGRATION_DATABASE_URL", _render(database_url))
        # Scoped to THIS RUN: the database this fixture just created, never a
        # job-level value naming a different one.
        monkeypatch.setenv("MIGRATION_EXPECTED_DATABASE", name)
        config = Config("alembic.ini")
        config.set_main_option("script_location", "alembic")
        config.set_main_option("sqlalchemy.url", _render(database_url))
        command.upgrade(config, "heads")
        yield database_url
    finally:
        with psycopg.connect(_psycopg_url(maintenance), autocommit=True) as admin:
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (name,),
            )
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(name))
            )


def _claim_all_real(database_url: URL, tenant_id: UUID, run_id: UUID, barrier):
    """Claim until exhausted, through the module's own `claim_partition`."""
    from dotmac_imports import claim_partition

    engine = create_engine(database_url)
    claimed: list[int] = []
    try:
        session = Session(engine)
        try:
            barrier.wait(timeout=30)
            while True:
                partition = claim_partition(session, tenant_id=tenant_id, run_id=run_id)
                if partition is None:
                    break
                claimed.append(partition.ordinal)
                # Commit each claim so the other claimant can observe it. Holding
                # them open would make SKIP LOCKED trivially disjoint and would
                # test the transaction boundary rather than the claim.
                session.commit()
        finally:
            session.rollback()
            session.close()
    finally:
        engine.dispose()
    return claimed


def _seed_run(database_url: URL) -> tuple[UUID, UUID]:
    """One dry run with `PARTITION_COUNT` partitions, ready to be claimed."""
    from dotmac_imports import (
        ColumnMapping,
        PartitionDescriptor,
        SourceDocument,
        create_dry_run,
        register_partition_plan,
    )

    tenant_id = uuid4()
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO public.tenants (id, slug, name, is_active)
                    VALUES (:id, :slug, 'Gate 3 tenant', true)
                    """
                ),
                {"id": tenant_id, "slug": f"gate3-{tenant_id.hex}"},
            )
        source = SourceDocument(file_id=uuid4(), checksum_sha256="0" * 64)
        with Session(engine) as setup:
            run = create_dry_run(
                setup,
                tenant_id=tenant_id,
                kind="finance.customer_master.v1",
                source=source,
                mapping=ColumnMapping((("Display Name", "display_name"),)),
            )
            register_partition_plan(
                setup,
                tenant_id=tenant_id,
                run_id=run.id,
                source=source,
                descriptors=tuple(
                    PartitionDescriptor(i, i, 1, uuid4(), f"{i:064x}", 10)
                    for i in range(PARTITION_COUNT)
                ),
            )
            run_id = run.id
            setup.commit()
    finally:
        engine.dispose()
    return tenant_id, run_id


def _race_real(database_url: URL) -> dict[int, list[int]]:
    tenant_id, run_id = _seed_run(database_url)
    barrier = threading.Barrier(CLAIMANTS)
    results: dict[int, list[int]] = {}
    failures: list[BaseException] = []

    def run(index: int) -> None:
        try:
            results[index] = _claim_all_real(database_url, tenant_id, run_id, barrier)
        except BaseException as exc:  # noqa: BLE001 - re-raised below
            failures.append(exc)
            barrier.abort()

    threads = [threading.Thread(target=run, args=(i,)) for i in range(CLAIMANTS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    assert all(not t.is_alive() for t in threads), "a claimant thread hung"
    if failures:
        raise failures[0]
    return results


# ── the gate ────────────────────────────────────────────────────────────────


def test_two_racing_claimants_reach_disjoint_partition_sets(
    composed_database: URL,
) -> None:
    """GATE 3. Both claimants start at the same instant and claim to exhaustion.

    Asserted over the UNION, both halves:

    * **disjoint** — no partition is claimed by two claimants. A claim path that
      handed the same work to both would corrupt the import twice over.
    * **complete** — every partition is claimed by someone. Disjointness alone
      is satisfied by a path that silently drops partitions, which is the
      quieter and worse failure.
    """
    results = _race_real(composed_database)

    first, second = results[0], results[1]
    assert set(first).isdisjoint(set(second)), (
        f"two racing claimants took the same partition(s): "
        f"{sorted(set(first) & set(second))}"
    )
    assert len(first) + len(second) == PARTITION_COUNT, (
        "a partition was claimed twice or lost; counts must sum to the plan"
    )
    assert set(first) | set(second) == set(range(PARTITION_COUNT)), (
        "the union of claims is not the whole partition plan"
    )


def test_the_race_harness_actually_interleaves(composed_database: URL) -> None:
    """EVIDENCE, not correctness — and they are separated deliberately.

    One round can legitimately be won entirely by one thread; scheduling is not
    a correctness property and asserting on it in the gate above would make the
    gate flaky. But several rounds ALL won by one thread means the barrier is
    not releasing two claimants into a real race, and the gate above would then
    be reporting disjointness it never tested.
    """
    split_rounds = 0
    for _ in range(ROUNDS):
        results = _race_real(composed_database)
        if results[0] and results[1]:
            split_rounds += 1

    assert split_rounds > 0, (
        f"in {ROUNDS} rounds one claimant took every partition every time. The "
        f"harness is not interleaving, so the disjointness result above is not "
        f"evidence of anything."
    )


# ── negative control: the harness must catch a non-atomic claim ─────────────


def _control_claim(database_url: URL, table: str, barrier, index: int) -> list[int]:
    """THE REJECTED SHAPE. Non-atomic on purpose: check, pause, then take.

    No `FOR UPDATE`, no `SKIP LOCKED`, and a real gap between the read and the
    write. This is the classic time-of-check-to-time-of-use claim, and it is the
    exact defect `claim_partition` exists to avoid.
    """
    engine = create_engine(database_url)
    taken: list[int] = []
    try:
        with engine.connect() as connection:
            barrier.wait(timeout=30)
            while True:
                row = connection.execute(
                    text(
                        f"SELECT id FROM {table} WHERE claimed_by IS NULL "  # noqa: S608
                        "ORDER BY id LIMIT 1"
                    )
                ).fetchone()
                if row is None:
                    break
                # The window. Both claimants have now read the same id.
                time.sleep(0.02)
                connection.execute(
                    text(
                        f"UPDATE {table} SET claimed_by = :who WHERE id = :id"  # noqa: S608
                    ),
                    {"who": f"claimant-{index}", "id": row[0]},
                )
                connection.commit()
                taken.append(int(row[0]))
    finally:
        engine.dispose()
    return taken


def test_the_harness_catches_a_non_atomic_claim(composed_database: URL) -> None:
    """THE NEGATIVE CONTROL, and it is the row that makes the gate mean anything.

    If a deliberately non-atomic claim ALSO comes out disjoint under this
    harness, the harness never raced and the gate above proves nothing.

    It runs against its own table rather than `mod_imports`: this validates the
    HARNESS, not the module, and a control that reached into module internals
    would rot the moment those changed.

    The assertion is that the collision is OBSERVED — two claimants recording
    the same id — not merely that the naive code exists.
    """
    table = "public.gate3_control_claims"
    engine = create_engine(composed_database)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    f"CREATE TABLE {table} ("  # noqa: S608
                    "id integer PRIMARY KEY, claimed_by text)"
                )
            )
            for i in range(PARTITION_COUNT):
                connection.execute(
                    text(f"INSERT INTO {table} (id) VALUES (:id)"),  # noqa: S608
                    {"id": i},
                )
    finally:
        engine.dispose()

    collided = False
    for _ in range(ROUNDS):
        # Reset between rounds so each is a fresh race.
        engine = create_engine(composed_database)
        try:
            with engine.begin() as connection:
                connection.execute(text(f"UPDATE {table} SET claimed_by = NULL"))  # noqa: S608
        finally:
            engine.dispose()

        barrier = threading.Barrier(CLAIMANTS)
        results: dict[int, list[int]] = {}

        def run(index: int, barrier=barrier, results=results) -> None:
            try:
                results[index] = _control_claim(
                    composed_database, table, barrier, index
                )
            except BaseException:  # noqa: BLE001 - a hung control is a failure
                barrier.abort()
                raise

        threads = [threading.Thread(target=run, args=(i,)) for i in range(CLAIMANTS)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        if len(results) == CLAIMANTS and set(results[0]) & set(results[1]):
            collided = True
            break

    assert collided, (
        "a NON-ATOMIC claim came out disjoint under this harness in every "
        f"round of {ROUNDS}. The harness is not producing a real race, so the "
        "disjointness the gate reports is not evidence that `claim_partition` "
        "is atomic — it would report the same for code that is not."
    )
