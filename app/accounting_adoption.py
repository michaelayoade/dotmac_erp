"""ERP's DECLARED, DISABLED composition of `dotmac-accounting`.

`dotmac-accounting` is the accepted tenant owner for the chart of accounts,
fiscal calendar, accounting dimensions, journal lifecycle, balanced posting,
linked reversals, period open/close/reopen/lock, and immutable posted-ledger
evidence.  ERP is the qualifying product-first source for all of it, and ERP
remains the LIVE authority for every one of those decisions today.

## State: COMPOSED AND DISABLED (gate C)

The distribution is now pinned exactly (`0.1.0a1`) and its lineage is listed in
`alembic.ini`'s `version_locations`, so `mod_accounting` is created by
`alembic upgrade heads` and its prerequisites are verified against the live
catalog.  **That is storage, and storage alone.**

`COMPOSITION_ENABLED` stays false, nothing under `app/` imports the package, no
ERP writer has been repointed, no backfill has run, and no decision has moved.
Supplying a module's tables does not cut anything over — the same rule ERP
already applies to `idempotency_ledger.v1`, whose storage has existed since
`20260820_idempotency_ledger` while ADR-0001 keeps the endpoint-response cache
as ratcheted transitional state.

What this module is for:

- the legacy extractor and the rehearsal comparator read the relation mapping
  from ONE place instead of each carrying a copy;
- `tests/architecture/test_accounting_composition.py` asserts the composition is
  real (exact pin, installed manifest, composed lineage) and still inert;
- the claims below are checked against the installed distribution rather than
  taken on trust.

Read `docs/architecture/accounting-adoption-boundary.md` for the ordered gates.

## The one thing this file must not become

A second opinion about what Accounting owns.  Every table name under
`EXPECTED_MODULE_TABLES` is a claim about someone else's artifact, and now that
the artifact is installed those claims are checked against
`dotmac_accounting`'s own manifest on every run.  A claim that turns out wrong
is a failing test, not a silent divergence.
"""

from __future__ import annotations

import os
from typing import Final

from dotmac_kernel.prerequisites import (
    IDEMPOTENCY_LEDGER_V1,
    MODULE_DATABASE_ROLES_V1,
    TENANT_SCOPE_CATALOG_V1,
)

#: The distribution and import names ERP will eventually depend on.  Present as
#: constants so the "is it absent?" test names them once.
DISTRIBUTION: Final = "dotmac-accounting"
IMPORT_PACKAGE: Final = "dotmac_accounting"
MODULE_CODE: Final = "accounting"

#: The exact version ERP pins and expects to have installed.  Declared here so
#: the pin, the lock and the running environment are checked against ONE literal
#: rather than three that drift apart.  Gate B is settled by the annotated tag
#: `dotmac-accounting-v0.1.0a1`, which peels to Starter `20d24703`.
EXPECTED_VERSION: Final = "0.1.0a1"

#: The single knob that turns composition on, with a prod-safe default.  It is
#: read at import and never per-request: composition is a deploy-time fact, and a
#: value that can change under a running process would mean two answers to "who
#: owns posting?" inside one deployment.
#:
#: The pin and the lineage are now in place; the flag is what still separates
#: "the tables exist" from "the module decides anything", and it stays false
#: until a clean bootstrap, behavioural rehearsal and authorized cutover say
#: otherwise.
COMPOSITION_ENABLED: Final[bool] = (
    os.getenv("ACCOUNTING_COMPOSITION_ENABLED", "false").lower() == "true"
)

#: The Alembic version location ERP composes.  Recorded here so the assertion
#: has a literal to look for rather than a substring guess.
MIGRATION_VERSION_LOCATION: Final = "dotmac_accounting.migrations:versions"

#: The database effects the module's lineage declares it needs.  ERP binds all
#: three in `app/migration_bindings.py` — to `20260813_tenant_projection`,
#: `20260814_database_roles` and `20260820_idempotency_ledger` respectively — and
#: a test proves they RESOLVE onto those revisions.  A module names EFFECTS,
#: never a foreign revision; see that module's docstring for why ERP is the
#: reason that indirection exists.
#:
#: Resolution is not verification.  `require_prerequisites` checks the effects
#: against the LIVE catalog at migration time, and that has not run for
#: Accounting because the lineage has never been composed here.  Read this as
#: "nothing is known to be outstanding", not "the migrations are done".
REQUIRED_PREREQUISITES: Final[tuple[str, ...]] = (
    TENANT_SCOPE_CATALOG_V1.name,
    MODULE_DATABASE_ROLES_V1.name,
    IDEMPOTENCY_LEDGER_V1.name,
)

#: ERP relation -> the module relation that will own it.
#:
#: `None` means the module has no counterpart.  That is NOT the same as "ERP
#: keeps it" — see `RETAINED_ERP_RELATIONS` below.  Three outcomes exist, not
#: two, and collapsing the third into either of the others is how a relation
#: ends up with no writer or with two.
RELATION_OWNERSHIP: Final[dict[str, str | None]] = {
    "gl.account_category": "account_categories",
    "gl.account": "accounts",
    "gl.fiscal_year": "fiscal_years",
    "gl.fiscal_period": "fiscal_periods",
    "gl.journal_entry": "journal_entries",
    "gl.journal_entry_line": "journal_lines",
    "gl.posted_ledger_line": "posted_ledger_lines",
    "gl.account_balance": None,
    "gl.balance_refresh_queue": None,
    "gl.budget": None,
    "gl.budget_line": None,
    "gl.posting_batch": None,
}

#: The relations ERP GOES ON WRITING after the cutover.
#:
#: This is the third outcome `RELATION_OWNERSHIP` alone cannot express.  A
#: relation with no module counterpart is either retained here, or it retires
#: with the writer that produced it:
#:
#: - **Retained.**  `gl.account_balance` and `gl.balance_refresh_queue` are a
#:   derived cache with a canonical rebuild writer
#:   (`rebuild_balances_for_period`).  A cache is rebuilt from the new source of
#:   truth, never migrated into it, and must never become the only copy of a
#:   balance.  `gl.budget` / `gl.budget_line` are budgeting, which ADR-0041 does
#:   not place in Accounting — posting against a budget is a different decision
#:   from recording one.
#: - **Retires with its writer.**  `gl.posting_batch` is ERP's batching envelope
#:   around a posting run, written by `LedgerPostingService` and by nothing else.
#:   The module posts per journal with `period_events` plus the immutable ledger
#:   as its evidence, so the batch record has nowhere to migrate TO — and once
#:   the poster is sealed there is nothing left to write it.  Calling it
#:   "retained" would promise an ERP writer that will not exist; calling it
#:   "migrating" would promise a module table that does not exist.  It is
#:   neither, and saying so is the point of this set.
RETAINED_ERP_RELATIONS: Final[frozenset[str]] = frozenset(
    {
        "gl.account_balance",
        "gl.balance_refresh_queue",
        "gl.budget",
        "gl.budget_line",
    }
)

#: Module relations with no ERP counterpart — these are CLEAN BOOTSTRAP INPUTS
#: ERP must synthesise, not rows it can copy. ERP carries dimensions as fixed columns on
#: `gl.journal_entry_line` (business unit, cost centre, project, segment) and
#: carries period status as a column on `gl.fiscal_period` rather than as an
#: event stream. Both are shape changes the bootstrap has to perform, and naming
#: them here is what stops the bootstrap from being written as a straight copy.
MODULE_ONLY_TABLES: Final[tuple[str, ...]] = (
    "accounting_dimensions",
    "accounting_dimension_values",
    "journal_line_dimensions",
    "posted_ledger_dimensions",
    "period_events",
)

#: Every module relation ERP claims will exist, in one set, for the composition
#: test to check against the installed manifest once there is one.
EXPECTED_MODULE_TABLES: Final[frozenset[str]] = frozenset(
    {table for table in RELATION_OWNERSHIP.values() if table is not None}
) | frozenset(MODULE_ONLY_TABLES)

#: The GL decisions ERP KEEPS after the cutover, named in the caller ledger's
#: vocabulary (`tests/architecture/test_accounting_gl_boundary.py::GL_SERVICES`).
#:
#: Only one: the derived balance cache.  Its inputs change — it will be rebuilt
#: from module-owned posted lines instead of ERP-owned ones — but the DECISION
#: "what is this account's balance for this period" stays an ERP-side projection
#: with a canonical rebuild writer, so its callers do not move.  Everything else
#: on the GL surface is a decision Accounting takes over.
#:
#: This set is what makes a `keep_local` caller row enforceable rather than a
#: label: a row claiming `keep_local` while depending on a decision outside this
#: set is a migrating caller parked in the wrong bucket, and the ledger test
#: fails it.
RETAINED_GL_DECISIONS: Final[frozenset[str]] = frozenset({"gl.balances"})

#: ADR-0003's complete admission vocabulary for the clean installation. A new
#: data class is an architecture decision, not an importer convenience.
CLEAN_INSTALL_INPUT_CLASSES: Final[frozenset[str]] = frozenset(
    {
        "reconciled_master",
        "open_operational_item",
        "approved_accounting_opening",
        "continuity_identity",
    }
)

#: Legacy accounting history stays in the read-only ERP archive. Naming the
#: relations here gives bootstrap guards a shared fail-closed vocabulary and
#: prevents a future operator from treating "not in the pack" as sufficient.
CLEAN_INSTALL_FORBIDDEN_HISTORY_RELATIONS: Final[frozenset[str]] = frozenset(
    {
        "gl.journal_entry",
        "gl.journal_entry_line",
        "gl.posted_ledger_line",
        "gl.posting_batch",
    }
)


class AccountingCompositionNotReady(RuntimeError):
    """Raised when something asks for the module before the gates are met."""


def module_relation_for(erp_relation: str) -> str | None:
    """The module relation that will own `erp_relation`, or `None` if ERP keeps it.

    Raises for a relation that is not a GL relation at all, so a typo in a
    bootstrap or comparator surfaces as a name error rather than as a silently
    skipped table.
    """
    try:
        return RELATION_OWNERSHIP[erp_relation]
    except KeyError:
        raise KeyError(
            f"{erp_relation!r} is not an ERP general-ledger relation; "
            f"known relations are {sorted(RELATION_OWNERSHIP)}"
        ) from None


def migrating_relations() -> tuple[str, ...]:
    """The ERP relations whose authority moves, in a stable order.

    Ordered by dependency so a clean bootstrap or comparison can iterate it
    directly: categories and accounts, then the fiscal calendar, then journals,
    lines and the posted ledger.
    """
    return tuple(
        relation for relation, owner in RELATION_OWNERSHIP.items() if owner is not None
    )


def relations_retiring_with_their_writer() -> tuple[str, ...]:
    """Relations that neither migrate nor stay — they end when their writer does.

    A separate accessor rather than a set literal, so the arithmetic stays in one
    place: every GL relation is exactly one of migrating, retained, or this.
    """
    return tuple(
        relation
        for relation, owner in RELATION_OWNERSHIP.items()
        if owner is None and relation not in RETAINED_ERP_RELATIONS
    )


def composition_state() -> dict[str, object]:
    """A flat, loggable description of where this adoption actually stands.

    Deliberately reports the INSTALLED state rather than the intended one: the
    interesting failure is a deployment that believes it composed the module and
    did not.
    """
    installed = _module_version()
    return {
        "distribution": DISTRIBUTION,
        "enabled": COMPOSITION_ENABLED,
        "installed_version": installed,
        "ready": COMPOSITION_ENABLED and installed is not None,
    }


def _module_version() -> str | None:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version(DISTRIBUTION)
    except PackageNotFoundError:
        return None


def require_composition_ready() -> str:
    """Return the installed module version, or refuse to proceed.

    The two failure modes are kept distinct because the operator response
    differs: a missing pin is a deploy that never installed the wheel, while a
    disabled flag is a deploy that installed it and has not been told to use it.
    Neither is allowed to degrade into "carry on against ERP's own tables" —
    that is precisely how a rehearsal silently measures nothing.
    """
    installed = _module_version()
    if installed is None:
        raise AccountingCompositionNotReady(
            f"{DISTRIBUTION} is not installed. ERP does not pin it until its "
            "release tag exists; see docs/architecture/"
            "accounting-adoption-boundary.md."
        )
    if not COMPOSITION_ENABLED:
        raise AccountingCompositionNotReady(
            f"{DISTRIBUTION}=={installed} is installed but "
            "ACCOUNTING_COMPOSITION_ENABLED is false. Composition is a "
            "deliberate deploy-time decision, not a default."
        )
    return installed


__all__ = [
    "AccountingCompositionNotReady",
    "COMPOSITION_ENABLED",
    "CLEAN_INSTALL_FORBIDDEN_HISTORY_RELATIONS",
    "CLEAN_INSTALL_INPUT_CLASSES",
    "DISTRIBUTION",
    "EXPECTED_MODULE_TABLES",
    "EXPECTED_VERSION",
    "IMPORT_PACKAGE",
    "MIGRATION_VERSION_LOCATION",
    "MODULE_CODE",
    "MODULE_ONLY_TABLES",
    "RELATION_OWNERSHIP",
    "RETAINED_ERP_RELATIONS",
    "RETAINED_GL_DECISIONS",
    "REQUIRED_PREREQUISITES",
    "composition_state",
    "migrating_relations",
    "relations_retiring_with_their_writer",
    "module_relation_for",
    "require_composition_ready",
]
