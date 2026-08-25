"""`dotmac-accounting` is DECLARED and NOT COMPOSED — proven, not assumed.

`app/accounting_adoption.py` states the composition in advance so the backfill,
the shadow comparator and the eventual adoption diff all read one description.
A declaration that drifts from reality is worse than no declaration, so this
module asserts both halves:

1. **Absent means absent.** No pin in `pyproject.toml`, no import under `app/`,
   no `version_locations` entry, and the flag defaults off.  Each is a separate
   way the module could sneak into a deployment, and each fails separately.
2. **Ready, as far as declarations can show it.** The three database effects the
   module's lineage declares are bound to ERP revisions, and those bindings
   resolve onto revisions ERP actually runs (the last of them landed in PR
   #328).  If a rebase ever undid that, this fails here rather than at `alembic
   upgrade` on a first adopter.  It is a declaration-layer check: verification
   against the live catalog happens inside `require_prerequisites` at migration
   time and belongs to gate C, not here.

When the release tag exists and ERP pins it, the "absent" assertions are the
ones to delete — deliberately, in the same reviewed change that adds the pin.
The claims in `EXPECTED_MODULE_TABLES` are checked against the installed
manifest from that moment on, by `TestOnceInstalled` below.
"""

from __future__ import annotations

import ast
import configparser
import tomllib
from pathlib import Path

import pytest

from app import accounting_adoption

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = REPO_ROOT / "app"
PYPROJECT = REPO_ROOT / "pyproject.toml"
ALEMBIC_INI = REPO_ROOT / "alembic.ini"


def _version_locations() -> list[str]:
    """Read the raw value; `%(here)s` is Alembic's to interpolate, not ours."""
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(ALEMBIC_INI)
    return parser["alembic"]["version_locations"].split()


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_the_distribution_is_not_pinned() -> None:
    """Do not pin Accounting until its release tag exists.

    A pin is what makes a deploy resolve and install a migration lineage.  ERP
    pins exact versions of every dotmac distribution precisely so that no
    unreviewed lineage can arrive; an unreleased name cannot be pinned exactly,
    so it is not pinned at all.
    """
    declared = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["poetry"][
        "dependencies"
    ]
    assert accounting_adoption.DISTRIBUTION not in declared, (
        f"{accounting_adoption.DISTRIBUTION} is pinned. Adding the pin is the "
        "adoption step; it belongs in a change that also composes the lineage, "
        "completes the backfill, and deletes this assertion."
    )


def test_nothing_under_app_imports_the_module() -> None:
    """Including the declaration itself.

    `accounting_adoption.py` describes the composition; importing the package
    would make ERP depend on it to describe it, which is the coupling the
    declaration exists to avoid.
    """
    offenders = sorted(
        path.relative_to(REPO_ROOT).as_posix()
        for path in APP_ROOT.rglob("*.py")
        if any(
            module == accounting_adoption.IMPORT_PACKAGE
            or module.startswith(f"{accounting_adoption.IMPORT_PACKAGE}.")
            for module in _imported_modules(path)
        )
    )
    assert not offenders, (
        f"app/ imports {accounting_adoption.IMPORT_PACKAGE}: {offenders}"
    )


def test_alembic_does_not_compose_the_accounting_lineage() -> None:
    assert accounting_adoption.MIGRATION_VERSION_LOCATION not in _version_locations()


def test_the_lineage_guard_is_sensitive() -> None:
    """The assertion above passes over a list that happens to be clean; prove it
    would bite on a dirty one (ADR-0018)."""
    dirty = [
        "%(here)s/alembic/versions",
        accounting_adoption.MIGRATION_VERSION_LOCATION,
    ]
    assert accounting_adoption.MIGRATION_VERSION_LOCATION in dirty


def test_composition_defaults_off_and_reports_itself_honestly() -> None:
    """The flag is a deploy-time decision with a prod-safe default, and the
    state it reports is the INSTALLED state, not the intended one."""
    state = accounting_adoption.composition_state()
    assert state["installed_version"] is None
    assert state["ready"] is False


def test_asking_for_the_module_refuses_rather_than_degrading() -> None:
    """A shadow run against ERP's own tables would measure nothing and report
    success.  The refusal names which of the two gates is unmet."""
    with pytest.raises(accounting_adoption.AccountingCompositionNotReady) as excinfo:
        accounting_adoption.require_composition_ready()
    assert "not installed" in str(excinfo.value)


def test_required_prerequisites_are_already_bound_to_erp_revisions() -> None:
    """The prerequisite bindings RESOLVE — which is weaker than verified.

    ERP hosts `public.tenants` itself and can never run kernel `0001`, so a
    module needing a tenant FK target, the three database roles and the
    at-most-once ledger installs here only because ERP supplies all three
    effects from revisions it actually runs.  If a rebase dropped one, the
    failure belongs here and not at `alembic upgrade` on a first adopter.

    What this does NOT prove: that the bound revisions really supply the effects
    in a database.  `require_prerequisites` checks that against the live catalog
    at migration time — table shape, key and index contract, the tenant
    function's semantics, the three roles' posture — and it has not run for
    Accounting, because the lineage has never been composed here.  This test
    covers the declaration layer only, and gate C covers the rest.
    """
    from dotmac_kernel.prerequisites import (
        install_prerequisite_bindings,
        resolve_depends_on,
    )

    from app.migration_bindings import ASSEMBLY_PREREQUISITE_BINDINGS

    install_prerequisite_bindings(ASSEMBLY_PREREQUISITE_BINDINGS)
    edges = set(resolve_depends_on(accounting_adoption.REQUIRED_PREREQUISITES))
    assert edges == {
        "20260813_tenant_projection",
        "20260814_database_roles",
        "20260820_idempotency_ledger",
    }
    assert not any(edge.startswith("0001_") for edge in edges)


def test_the_relation_map_covers_every_gl_relation_exactly_once() -> None:
    """A relation missing from the map is a table nobody decided about — the
    exact gap that turns into "we forgot the batches" during a cutover."""
    from tests.architecture.test_accounting_gl_boundary import GL_MODELS

    assert set(accounting_adoption.RELATION_OWNERSHIP) == set(GL_MODELS.values())


def test_every_gl_relation_has_exactly_one_of_three_outcomes() -> None:
    """Migrating, retained, or retiring-with-its-writer — and never two of them.

    Two outcomes are not enough.  A relation with no module counterpart is not
    automatically one ERP keeps: `gl.posting_batch` has no module table AND no
    surviving ERP writer once the poster is sealed.  Collapsing that third case
    into "kept" promises a writer that will not exist; collapsing it into
    "migrating" promises a table that will not exist.  This partition is what the
    writer ledger's `keep_local` invariant reads.
    """
    migrating = set(accounting_adoption.migrating_relations())
    retained = set(accounting_adoption.RETAINED_ERP_RELATIONS)
    retiring = set(accounting_adoption.relations_retiring_with_their_writer())

    assert migrating | retained | retiring == set(
        accounting_adoption.RELATION_OWNERSHIP
    )
    assert not migrating & retained
    assert not migrating & retiring
    assert not retained & retiring


def test_the_three_outcomes_hold_the_relations_they_are_supposed_to() -> None:
    assert set(accounting_adoption.RETAINED_ERP_RELATIONS) == {
        "gl.account_balance",
        "gl.balance_refresh_queue",
        "gl.budget",
        "gl.budget_line",
    }
    assert accounting_adoption.relations_retiring_with_their_writer() == (
        "gl.posting_batch",
    )


def test_a_retained_relation_really_has_no_module_counterpart() -> None:
    """ERP cannot keep writing a relation the module also owns — that is two
    writers over one fact, which is the whole thing a cutover exists to end."""
    for relation in accounting_adoption.RETAINED_ERP_RELATIONS:
        assert accounting_adoption.RELATION_OWNERSHIP[relation] is None, relation


def test_module_relation_lookup_fails_loudly_on_an_unknown_relation() -> None:
    assert accounting_adoption.module_relation_for("gl.journal_entry") == (
        "journal_entries"
    )
    assert accounting_adoption.module_relation_for("gl.budget") is None
    with pytest.raises(KeyError):
        accounting_adoption.module_relation_for("gl.journal_entries")


def test_migrating_relations_are_ordered_by_dependency() -> None:
    """The backfill and the comparator both iterate this; an account must exist
    before a line references it, and a period before a journal is dated into it."""
    order = accounting_adoption.migrating_relations()
    assert order.index("gl.account_category") < order.index("gl.account")
    assert order.index("gl.account") < order.index("gl.journal_entry_line")
    assert order.index("gl.fiscal_year") < order.index("gl.fiscal_period")
    assert order.index("gl.fiscal_period") < order.index("gl.journal_entry")
    assert order.index("gl.journal_entry") < order.index("gl.journal_entry_line")
    assert order.index("gl.journal_entry_line") < order.index("gl.posted_ledger_line")


class TestOnceInstalled:
    """Checks that only mean something with the distribution present.

    Skipped rather than failed while it is absent — that IS the current state,
    and a red suite that everyone learns to ignore protects nothing.  From the
    adoption change onward CI installs the pinned wheel, so the skip cannot hide
    a real failure.
    """

    @pytest.fixture(autouse=True)
    def _require_accounting(self) -> None:
        pytest.importorskip(accounting_adoption.IMPORT_PACKAGE)

    def test_the_declared_tables_match_the_modules_own_manifest(self) -> None:
        """`EXPECTED_MODULE_TABLES` is a claim about someone else's artifact.
        The moment the artifact is here, the claim is checked against it."""
        from dotmac_accounting.manifest import module

        assert set(module.tables) == set(accounting_adoption.EXPECTED_MODULE_TABLES)

    def test_the_declared_prerequisites_match_the_modules_own_manifest(self) -> None:
        from dotmac_accounting.manifest import module

        assert set(module.requires) == set(accounting_adoption.REQUIRED_PREREQUISITES)

    def test_the_declared_module_code_matches(self) -> None:
        from dotmac_accounting.manifest import module

        assert module.code == accounting_adoption.MODULE_CODE
