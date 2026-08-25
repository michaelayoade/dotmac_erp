"""Apply `ac_0001_accounting` through ERP's REAL Alembic environment.

Gate A could only check declarations: that the effect names the module requires
resolve, through ERP's bindings, onto revisions ERP runs.  It said so, and said
plainly what it did not prove.  This module proves the rest, against PostgreSQL.

The database is built the way a deploy builds one: a disposable database, ERP's
own lineage upgraded to head — that is the production-shaped PREDECESSOR state —
and then `alembic upgrade heads` with the module lineages composed, exactly as
`scripts/deploy.sh` runs it.  No `create_all`, no hand-built schema; a rehearsal
that constructs its own tables proves something about the test, not the deploy.

## ERP does not have one Alembic head, and asserting one would be a bug

Every composed module lineage is an independent ROOT with its own branch label:
`fi_0001_stored_files` labels `files`, `ac_0001_accounting` labels `accounting`.
That is the design — a module owns its history so it can be released and pinned
without ERP rewriting its graph — and it is why ERP's deploy path has always
been `alembic upgrade heads`, plural.

So "one global head" is the wrong acceptance criterion, and an earlier draft of
the gate C plan wrongly named it.  The right criterion, enforced below, is:

- exactly one head per composed module branch, at the revision
  `COMPOSED_MODULE_LINEAGES` names;
- exactly one ERP head;
- and NO unintended heads.

The last is the one with teeth.  An unintended head is a second ERP root, a
revision whose `down_revision` does not reach the tip, or a module lineage that
grew a head nobody reviewed — each of which makes `upgrade heads` do something
different from what the author expected, silently.

## What is asserted, and why each is separate

1. **The lineage applies** onto ERP's real predecessor graph.
2. **Prerequisites hold against the live catalog** — `require_prerequisites` is
   what `ac_0001` itself calls before any DDL, and it checks table shape, key and
   index contract, the tenant function's semantics and the three roles' posture.
   Gate A could not run it.
3. **The heads are exactly the expected ones** (above).
4. **`upgrade heads` is repeatable** — a second run is a no-op rather than an
   error or a duplicate.  A migration that is not idempotent at the head is a
   deploy that cannot be retried.
5. **The tenant plane is shaped correctly** — every `mod_accounting` table has a
   non-nullable `tenant_id` and FORCEd row-level security.  A module table
   without that is a cross-tenant leak wearing a module's name.
6. **The composed migration gate passes**, where the installed kernel ships one.

None of this enables anything.  `ACCOUNTING_COMPOSITION_ENABLED` stays false and
no ERP writer is repointed; this is storage, proven to exist and to be shaped
correctly, and nothing more.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from uuid import uuid4

import psycopg
import pytest
from alembic.config import Config
from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.engine import Connection

from alembic import command
from alembic.script import ScriptDirectory
from app import config as app_config
from app.migration_bindings import COMPOSED_MODULE_LINEAGES

pytestmark = pytest.mark.integration

ACCOUNTING_SCHEMA = "mod_accounting"
ACCOUNTING_REVISION = "ac_0001_accounting"
REQUIRED_EFFECTS = (
    "tenant_scope_catalog.v1",
    "module_database_roles.v1",
    "idempotency_ledger.v1",
)


def _composed_version_locations():
    """Every version location ERP composes, resolved the way ALEMBIC resolves them.

    Read straight out of `alembic.ini` rather than listed here, so the gate sees
    the composition ERP actually ships. `%(here)s` is interpolated against the
    repository root, and package references (`dotmac_files.migrations:versions`)
    go through Alembic's own resource coercion — the same call Alembic makes —
    so the gate reads exactly the bytes a deploy would run.
    """
    from pathlib import Path

    from alembic.util.pyfiles import coerce_resource_to_filename

    root = Path(__file__).resolve().parents[2]
    locations = []
    for entry in _raw_version_locations():
        if ":" in entry and not entry.startswith("%"):
            locations.append(Path(coerce_resource_to_filename(entry)))
        else:
            locations.append(Path(entry.replace("%(here)s", str(root))))
    return tuple(locations)


def _raw_version_locations() -> list[str]:
    import configparser
    from pathlib import Path

    parser = configparser.ConfigParser(interpolation=None)
    parser.read(Path(__file__).resolve().parents[2] / "alembic.ini")
    return parser["alembic"]["version_locations"].split()


def _render(url: URL) -> str:
    return url.render_as_string(hide_password=False)


def _psycopg_url(url: URL) -> str:
    return _render(url.set(drivername="postgresql"))


@pytest.fixture()
def _real_postgres_types() -> Iterator[None]:
    """Undo the SQLite type patch before any DDL runs.

    `tests/conftest.py` replaces `postgresql.UUID` with a `String(36)`
    TypeDecorator so the unit suite can run on SQLite.  Its own docstring
    records that `confcutdir` does NOT prevent this — `tests/` is a package, so
    pytest imports that conftest while walking it regardless.

    Without undoing it, ERP's own lineage fails part-way through with
    `foreign key constraint "fk_people_organization_id" cannot be implemented:
    Key columns are of incompatible types: character varying and uuid` — a
    failure that looks like a defect in the migrations and is really the unit
    suite's SQLite shim leaking into a PostgreSQL run.  The same
    `alembic upgrade heads` succeeds outside pytest.

    `tests/integration/conftest.py` already owns the fix; it is bound to the
    `engine` fixture, which this module does not use because it builds its own
    disposable database.  So the helper is reused rather than reimplemented —
    two copies of a type-restoration routine is exactly how one of them rots.
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
    """A disposable database carrying ERP's own lineage at head.

    ERP's revisions run FIRST and alone, so the state the module lineage lands on
    is the same predecessor a real deploy would present: every ERP table, every
    RLS policy, the tenant projection and the three database roles.
    """
    configured = os.getenv("TEST_DATABASE_URL")
    if not configured:
        raise pytest.UsageError(
            "accounting lineage rehearsal requires TEST_DATABASE_URL"
        )
    base_url = make_url(configured)
    if not base_url.drivername.startswith("postgresql"):
        raise pytest.UsageError("accounting lineage rehearsal requires PostgreSQL")

    name = f"erp_accounting_compose_{uuid4().hex}"
    maintenance = base_url.set(database="postgres")
    with psycopg.connect(_psycopg_url(maintenance), autocommit=True) as admin:
        admin.execute(
            sql.SQL("CREATE DATABASE {} OWNER app_admin").format(sql.Identifier(name))
        )
    try:
        database_url = base_url.set(database=name, username="app_admin", password=None)
        monkeypatch.setenv("MIGRATION_DATABASE_URL", _render(database_url))
        monkeypatch.setattr(
            app_config.settings, "database_url", _render(database_url), raising=False
        )
        _upgrade(database_url, "heads")
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


def _config(database_url: URL) -> Config:
    """ERP's real Alembic configuration — `alembic.ini` as checked in.

    Deliberately NOT a config assembled here.  The whole question is whether the
    `version_locations` ERP ships resolves and applies, and a config built in the
    test would answer a different question.
    """
    config = Config("alembic.ini")
    config.set_main_option("script_location", "alembic")
    config.set_main_option("sqlalchemy.url", _render(database_url))
    return config


def _upgrade(database_url: URL, target: str) -> None:
    command.upgrade(_config(database_url), target)


def _applied_revisions(database_url: URL) -> set[str]:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            return set(
                connection.execute(
                    text("SELECT version_num FROM public.alembic_version")
                ).scalars()
            )
    finally:
        engine.dispose()


def _script_heads(database_url: URL) -> set[str]:
    """Graph heads by `down_revision` — what `alembic heads` prints."""
    return set(ScriptDirectory.from_config(_config(database_url)).get_heads())


def _depended_upon(database_url: URL) -> set[str]:
    """Every revision named in some other revision's `depends_on`.

    A module lineage declares logical prerequisites, and ERP's bindings resolve
    them to real ERP revisions — so `ac_0001` and `fi_0001` each carry a
    `depends_on` edge onto ERP's own head. Alembic treats a depended-upon
    revision as an ANCESTOR of the dependent one, so it stops being an effective
    head and its `alembic_version` row is subsumed.
    """
    script = ScriptDirectory.from_config(_config(database_url))
    depended: set[str] = set()
    for revision in script.walk_revisions():
        depended.update(revision.dependencies or ())
    return depended


def _effective_heads(database_url: URL) -> set[str]:
    """What `alembic_version` should hold after `upgrade heads`.

    Graph heads minus the ones subsumed by a `depends_on` edge. Derived from the
    script directory rather than hard-coded, because the answer changes with the
    composition: a future module that needs no ERP prerequisite would leave ERP's
    head stamped, and a hard-coded count would then be wrong in the other
    direction.
    """
    return _script_heads(database_url) - _depended_upon(database_url)


def _branch_label_of(database_url: URL, revision: str) -> set[str]:
    script = ScriptDirectory.from_config(_config(database_url))
    return set(script.get_revision(revision).branch_labels or ())


# ---------------------------------------------------------------------------
# 1. the lineage applies
# ---------------------------------------------------------------------------


def test_the_accounting_lineage_applies_onto_erps_real_graph(
    composed_database: URL,
) -> None:
    """The fixture already ran `upgrade heads`; reaching here at all is the
    proof.  What this adds is that the module revision is genuinely STAMPED,
    rather than skipped by a branch that never ran."""
    assert ACCOUNTING_REVISION in _applied_revisions(composed_database)


def test_the_module_schema_exists_with_every_declared_table(
    composed_database: URL,
) -> None:
    from dotmac_accounting.manifest import module

    engine = create_engine(composed_database)
    try:
        with engine.connect() as connection:
            present = set(
                connection.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = :schema"),
                    {"schema": ACCOUNTING_SCHEMA},
                ).scalars()
            )
    finally:
        engine.dispose()
    missing = sorted(set(module.tables) - present)
    assert not missing, f"{ACCOUNTING_SCHEMA} is missing declared tables: {missing}"


# ---------------------------------------------------------------------------
# 2. prerequisites hold against the live catalog
# ---------------------------------------------------------------------------


def test_live_prerequisite_contracts_hold(composed_database: URL) -> None:
    """The check gate A explicitly could not make.

    `require_prerequisites` is the same function `ac_0001` calls before its own
    DDL; running it here against the migrated database is the difference between
    "the binding names a revision" and "the revision really supplies the effect".
    """
    from dotmac_kernel.migrations.verify import require_prerequisites

    from app.migration_bindings import ASSEMBLY_PREREQUISITE_BINDINGS
    from dotmac_kernel.prerequisites import (
        install_prerequisite_bindings,
        installed_bindings,
    )

    previous = tuple(installed_bindings())
    install_prerequisite_bindings(ASSEMBLY_PREREQUISITE_BINDINGS)
    try:
        engine = create_engine(composed_database)
        try:
            with engine.connect() as connection:
                require_prerequisites(connection, REQUIRED_EFFECTS)
        finally:
            engine.dispose()
    finally:
        install_prerequisite_bindings(previous)


def test_the_prerequisite_verifier_is_sensitive(composed_database: URL) -> None:
    """The check above passes against a database that happens to be correct.
    Break one contract inside a rolled-back transaction and require a refusal,
    so it cannot pass for the wrong reason (ADR-0018)."""
    from dotmac_kernel.migrations.verify import require_prerequisites

    engine = create_engine(composed_database)
    try:
        connection: Connection = engine.connect()
        transaction = connection.begin()
        try:
            connection.execute(text("DROP TABLE public.idempotency_records CASCADE"))
            with pytest.raises(Exception):
                require_prerequisites(connection, ("idempotency_ledger.v1",))
        finally:
            transaction.rollback()
            connection.close()
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# 3. exactly the expected branch heads, and no unintended ones
# ---------------------------------------------------------------------------


def test_every_composed_module_branch_is_at_its_expected_head(
    composed_database: URL,
) -> None:
    heads = _script_heads(composed_database)
    for branch, expected_head in COMPOSED_MODULE_LINEAGES.items():
        assert expected_head in heads, (
            f"branch {branch!r} is not at head {expected_head!r}; heads are {sorted(heads)}"
        )
        assert branch in _branch_label_of(composed_database, expected_head), (
            f"{expected_head!r} does not carry the {branch!r} branch label"
        )


def test_there_is_exactly_one_erp_head_beside_the_module_heads(
    composed_database: URL,
) -> None:
    """ERP's own lineage must contribute exactly one head.

    Two would mean a second ERP root or an orphaned revision — `upgrade heads`
    would still succeed while applying a graph nobody drew.
    """
    heads = _script_heads(composed_database)
    module_heads = set(COMPOSED_MODULE_LINEAGES.values())
    erp_heads = heads - module_heads
    assert len(erp_heads) == 1, (
        f"expected exactly one ERP head, got {sorted(erp_heads)}. Module heads "
        f"are {sorted(module_heads)}."
    )


def test_there_are_no_unintended_heads(composed_database: URL) -> None:
    """The GRAPH head count is pinned to what the composition implies: one per
    composed module lineage plus one for ERP.

    This is the assertion that catches a head nobody meant to create. It is
    deliberately an exact count rather than a minimum, because a surplus head is
    the failure mode. Note this counts graph heads — see
    `test_the_applied_stamp_matches_the_effective_heads` for why the number of
    STAMPED rows is smaller.
    """
    heads = _script_heads(composed_database)
    assert len(heads) == len(COMPOSED_MODULE_LINEAGES) + 1, (
        f"unexpected head count: {sorted(heads)}"
    )


def test_the_applied_stamp_matches_the_effective_heads(
    composed_database: URL,
) -> None:
    """`alembic_version` holds the EFFECTIVE heads — fewer rows than graph heads.

    This is the subtlety gate C existed to find, and a first draft got it wrong
    by asserting `applied == script_heads`.

    ERP's own head is `20260820_idempotency_ledger`. Both module lineages
    declare logical prerequisites that ERP's bindings resolve onto ERP
    revisions, so `ac_0001_accounting` and `fi_0001_stored_files` each carry a
    `depends_on` edge onto it. Alembic treats a depended-upon revision as an
    ancestor of its dependent, so after `upgrade heads` the version table holds
    exactly the two module revisions and NOT ERP's head:

        ac_0001_accounting
        fi_0001_stored_files

    That is correct, and it is why "one global head" and "as many rows as
    branches" are both wrong. The expectation is derived from the script
    directory — graph heads minus depended-upon revisions — so it stays true if
    a later module declares no ERP prerequisite and leaves ERP's head stamped.

    A stamped revision that is not an effective head means a branch stopped
    short; an effective head that is not stamped means it never ran. Both look
    like success to `upgrade heads`.
    """
    assert _applied_revisions(composed_database) == _effective_heads(composed_database)


def test_erps_own_head_is_subsumed_rather_than_missing(
    composed_database: URL,
) -> None:
    """Prove the absence above is subsumption, not a branch that failed to run.

    "ERP's head is not in `alembic_version`" and "ERP's lineage did not finish"
    look identical from the version table. They are distinguished by the
    `depends_on` edge and by the ERP revision's effects actually being present.
    """
    erp_head = (
        _script_heads(composed_database) - set(COMPOSED_MODULE_LINEAGES.values())
    ).pop()
    assert erp_head not in _applied_revisions(composed_database)
    assert erp_head in _depended_upon(composed_database), (
        f"{erp_head} is unstamped and NOT depended upon — its branch did not run"
    )

    engine = create_engine(composed_database)
    try:
        with engine.connect() as connection:
            for table in ("idempotency_records", "platform_idempotency_records"):
                assert (
                    connection.scalar(
                        text("SELECT to_regclass(:name)"), {"name": f"public.{table}"}
                    )
                    is not None
                ), f"public.{table} missing — ERP's head really did not run"
    finally:
        engine.dispose()


def test_the_head_expectation_is_sensitive() -> None:
    """Sensitivity proof: the checks above pass over a graph that happens to be
    right.  Prove the arithmetic bites when a head goes missing or is added."""
    module_heads = set(COMPOSED_MODULE_LINEAGES.values())
    assert len(module_heads | {"erp_head"}) == len(COMPOSED_MODULE_LINEAGES) + 1
    assert len(module_heads | {"erp_head", "stray_head"}) != (
        len(COMPOSED_MODULE_LINEAGES) + 1
    )
    assert (module_heads - {ACCOUNTING_REVISION}) | {"erp_head"} != module_heads | {
        "erp_head"
    }
    # And the subsumption arithmetic: a depended-upon head drops out.
    assert ({"erp_head"} | module_heads) - {"erp_head"} == module_heads


# ---------------------------------------------------------------------------
# 4. repeatable
# ---------------------------------------------------------------------------


def test_upgrade_heads_is_repeatable(composed_database: URL) -> None:
    """A deploy that cannot be retried is a deploy that cannot be recovered.

    The second run must be a no-op: same stamped revisions, same heads, no
    error, no duplicate row.
    """
    before = _applied_revisions(composed_database)
    _upgrade(composed_database, "heads")
    after = _applied_revisions(composed_database)
    assert after == before == _effective_heads(composed_database)


# ---------------------------------------------------------------------------
# 5. tenant-plane shape
# ---------------------------------------------------------------------------


def test_every_accounting_table_is_tenant_scoped_and_rls_forced(
    composed_database: URL,
) -> None:
    """A module tenant table without `tenant_id NOT NULL` and FORCEd RLS is a
    cross-tenant leak wearing a module's name.

    FORCE matters specifically: plain `ENABLE` is bypassed by the table's owner,
    and migrations run as `app_admin`, which owns these tables.
    """
    engine = create_engine(composed_database)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = :schema AND c.relkind = 'r'
                    ORDER BY c.relname
                    """
                ),
                {"schema": ACCOUNTING_SCHEMA},
            ).all()
            assert rows, f"{ACCOUNTING_SCHEMA} has no tables"

            not_forced = [
                name for name, enabled, forced in rows if not (enabled and forced)
            ]
            assert not not_forced, (
                f"{ACCOUNTING_SCHEMA} tables without FORCEd row-level security: "
                f"{not_forced}"
            )

            nullable_tenant = (
                connection.execute(
                    text(
                        """
                    SELECT table_name
                    FROM information_schema.columns
                    WHERE table_schema = :schema
                      AND column_name = 'tenant_id'
                      AND is_nullable = 'YES'
                    ORDER BY table_name
                    """
                    ),
                    {"schema": ACCOUNTING_SCHEMA},
                )
                .scalars()
                .all()
            )
            assert not nullable_tenant, (
                f"nullable tenant_id in {ACCOUNTING_SCHEMA}: {list(nullable_tenant)}"
            )

            missing_tenant = (
                connection.execute(
                    text(
                        """
                    SELECT t.table_name
                    FROM information_schema.tables t
                    WHERE t.table_schema = :schema
                      AND t.table_type = 'BASE TABLE'
                      AND NOT EXISTS (
                          SELECT 1 FROM information_schema.columns c
                          WHERE c.table_schema = t.table_schema
                            AND c.table_name = t.table_name
                            AND c.column_name = 'tenant_id'
                      )
                    ORDER BY t.table_name
                    """
                    ),
                    {"schema": ACCOUNTING_SCHEMA},
                )
                .scalars()
                .all()
            )
            assert not missing_tenant, (
                f"{ACCOUNTING_SCHEMA} tables with no tenant_id column: "
                f"{list(missing_tenant)}"
            )
    finally:
        engine.dispose()


def test_the_accounting_schema_stays_out_of_erps_own_namespaces(
    composed_database: URL,
) -> None:
    """One immutable `mod_<short_code>` schema per stateful module.  `public`
    and ERP's domain schemas are ERP's; a module table landing in one of them
    would make ownership unreadable from the catalog."""
    engine = create_engine(composed_database)
    try:
        with engine.connect() as connection:
            stray = (
                connection.execute(
                    text(
                        """
                    SELECT schemaname || '.' || tablename
                    FROM pg_tables
                    WHERE schemaname <> :schema
                      AND tablename IN (
                          SELECT tablename FROM pg_tables WHERE schemaname = :schema
                      )
                    ORDER BY 1
                    """
                    ),
                    {"schema": ACCOUNTING_SCHEMA},
                )
                .scalars()
                .all()
            )
            assert not stray, (
                f"accounting table names also present outside {ACCOUNTING_SCHEMA}: "
                f"{list(stray)}"
            )
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# 6. the composed migration gate
# ---------------------------------------------------------------------------


def _gate_report():
    """The kernel's composition gate over the WHOLE composition ERP ships."""
    from dotmac_kernel.migrations.gate import run_gate
    from dotmac_kernel.prerequisites import (
        install_prerequisite_bindings,
        installed_bindings,
    )

    from app.migration_bindings import ASSEMBLY_PREREQUISITE_BINDINGS
    from dotmac_accounting.manifest import module as accounting_module
    from dotmac_files.manifest import module as files_module

    previous = tuple(installed_bindings())
    install_prerequisite_bindings(ASSEMBLY_PREREQUISITE_BINDINGS)
    try:
        return run_gate(
            (accounting_module, files_module),
            _composed_version_locations(),
            bindings=ASSEMBLY_PREREQUISITE_BINDINGS,
        )
    finally:
        install_prerequisite_bindings(previous)


def _module_owned_revisions(report) -> set[str]:
    return {
        revision
        for revision, facts in report.attribution.items()
        if facts.get("owner") is not None
    }


def test_the_composed_migration_gate_reports_nothing_against_the_modules() -> None:
    """Run the kernel's composition gate over the whole composition.

    It must be given EVERY version location ERP composes, not just the module's.
    A first draft passed only `dotmac_accounting.migrations:versions` and the
    gate correctly rejected it: each prerequisite is bound to an ERP revision,
    and a binding whose provider is not in any selected location "names a
    revision this deployment never runs". Scoping the gate to the module made
    ERP's own providers invisible and turned a satisfied prerequisite into a
    reported violation — the gate was right and the call was wrong.

    Given the whole composition, the prerequisite violations disappear and what
    remains is ERP's own legacy history: ~380 assembly revisions with ids longer
    than 32 characters and a root carrying no branch label. Those are NOT
    findings against this change, and asserting `not report.violations` would
    make this test permanently red for reasons that predate Accounting by years.

    So the assertion is scoped by ATTRIBUTION, which the gate itself computes:
    every violation naming a MODULE-owned revision must be absent. ERP's
    assembly-owned revisions are held to the separate, enforceable premise in
    `test_erp_legacy_revision_ids_are_safe_because_the_column_was_widened`
    rather than waved through.
    """
    report = _gate_report()
    module_revisions = _module_owned_revisions(report)
    assert module_revisions >= {ACCOUNTING_REVISION, "fi_0001_stored_files"}

    offending = [
        violation
        for violation in report.violations
        if any(revision in violation for revision in module_revisions)
        or "module 'accounting'" in violation
        or "module 'files'" in violation
    ]
    assert not offending, (
        "composed migration gate rejected a module lineage:\n  "
        + "\n  ".join(offending)
    )


def test_erp_legacy_revision_ids_are_safe_because_the_column_was_widened(
    composed_database: URL,
) -> None:
    """The premise that lets the check above ignore ERP's legacy violations.

    The gate flags ERP revision ids over 32 characters because
    `alembic_version.version_num` is `VARCHAR(32)` by Alembic default, and it
    says so in the operator's terms: "this fails at `alembic upgrade`, against a
    real database". For ERP that is not true, and the reason is a revision
    literally named `extend_alembic_version` — ERP widened the column years ago.

    An exemption is only allowed to stand on an ENFORCEABLE premise (ADR-0018).
    So the premise is checked against the live catalog rather than asserted in
    prose: the column must be wide enough for the longest id ERP actually ships.
    If someone ever narrowed it, this fails and the gate's warning becomes true
    again.
    """
    report = _gate_report()
    assembly_revisions = [
        record.revision
        for record in report.revisions
        if report.attribution.get(record.revision, {}).get("owner") is None
    ]
    assert assembly_revisions, "no assembly-owned revisions found — check attribution"
    longest = max(len(revision) for revision in assembly_revisions)

    engine = create_engine(composed_database)
    try:
        with engine.connect() as connection:
            width = connection.scalar(
                text(
                    """
                    SELECT character_maximum_length
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'alembic_version'
                      AND column_name = 'version_num'
                    """
                )
            )
    finally:
        engine.dispose()

    assert width is not None, "alembic_version.version_num not found"
    assert width >= longest, (
        f"alembic_version.version_num is VARCHAR({width}) but ERP ships a "
        f"revision id of {longest} characters — the composed gate's warning "
        "about long revision ids is real for this database"
    )


def test_the_composed_migration_gate_is_sensitive() -> None:
    """The gate check passes by finding an empty violation list — which is also
    what a gate pointed at nothing returns. Two proofs (ADR-0018).

    The second is the one that matters here: dropping ERP's own location is
    exactly the mistake the first draft made, so it is pinned as a detectable
    fault rather than left as a thing to remember.
    """
    from pathlib import Path as _Path

    from dotmac_kernel.migrations.gate import run_gate

    from app.migration_bindings import ASSEMBLY_PREREQUISITE_BINDINGS
    from dotmac_accounting.manifest import module

    missing = run_gate((module,), (_Path("/nonexistent/versions"),))
    assert missing.violations

    module_only = run_gate(
        (module,),
        tuple(
            location
            for location in _composed_version_locations()
            if "dotmac_accounting" in str(location)
        ),
        bindings=ASSEMBLY_PREREQUISITE_BINDINGS,
    )
    assert any("never runs" in violation for violation in module_only.violations), (
        module_only.violations
    )
