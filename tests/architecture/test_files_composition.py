"""ERP composes `dotmac-files`: its FIRST foreign migration lineage.

Every other revision ERP runs was authored in this repository. `fi_0001` is not:
it ships inside the `dotmac-files` wheel, is owned by that distribution, and is
read from the installed package rather than copied here. That makes three things
newly falsifiable, and this module asserts all three.

1. **The lineage is reachable.** `alembic.ini`'s `version_locations` must
   actually resolve `dotmac_files.migrations:versions` through the installed
   distribution. A typo here is invisible until `alembic upgrade` finds no such
   revision.
2. **The kernel lineage stays out.** Kernel `0001_initial_tenant_schema` creates
   `public.tenants` unconditionally as its first table; ERP hosts that table in
   its own lineage and can never run it. Listing the kernel's versions directory
   would put permanently-unappliable revisions into ERP's revision map.
3. **The binding resolves onto ERP's own revisions.** `fi_0001` names EFFECTS,
   not revisions, and `app/migration_bindings.py` answers with revisions ERP
   actually runs — so the concrete Alembic edge must come out as the tenant
   projection and the role adoption, never kernel `0001`.

The bindings' own shape is asserted in `test_prerequisite_bindings.py`. This
file is about what happens when they are COMPOSED with a real foreign lineage.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
KERNEL_LINEAGE = "dotmac_kernel.migrations"


def _version_locations() -> list[str]:
    """Read the RAW value — interpolation off.

    `version_locations` contains `%(here)s`, which Alembic injects at runtime
    and a plain `ConfigParser` does not know about; interpolating raises
    `InterpolationMissingOptionError` before any assertion runs. Nothing here
    needs the substituted path anyway: these checks are about which lineages are
    listed, and `dotmac_files.migrations:versions` is already literal.
    """
    import configparser

    parser = configparser.ConfigParser(interpolation=None)
    parser.read(ALEMBIC_INI)
    return parser["alembic"]["version_locations"].split()


def test_files_is_pinned_exactly_like_every_other_dotmac_distribution() -> None:
    """ERP pins exact versions; `test_kernel_compatibility.py` rejects range
    drift on the kernel, and a module is held to the same rule.

    A range would let a deploy resolve a `mod_files` lineage nobody reviewed —
    the one kind of dependency drift that changes the DATABASE rather than the
    code.
    """
    declared = tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )["tool"]["poetry"]["dependencies"]["dotmac-files"]
    assert declared["source"] == "forgejo"
    version = declared["version"]
    assert not any(character in version for character in "^~><*"), (
        f"dotmac-files must be pinned exactly, got {version!r}"
    )


def test_alembic_composes_the_files_lineage() -> None:
    assert "dotmac_files.migrations:versions" in _version_locations()


def test_alembic_never_composes_the_kernel_lineage() -> None:
    """The permanent negative canary, asserted on configuration.

    `tests/integration/test_kernel_lineage_rehearsal.py` proves the kernel
    lineage cannot RUN here. This proves ERP never even offers it to Alembic —
    a revision map holding an unappliable revision is a trap for the next
    person to run `alembic heads`.
    """
    offenders = [
        location for location in _version_locations() if KERNEL_LINEAGE in location
    ]
    assert not offenders, f"ERP must not compose the kernel lineage: {offenders}"


def test_the_kernel_lineage_guard_is_sensitive() -> None:
    """Sensitivity proof (ADR-0018): the check above passes over a list that
    happens to be clean, so prove it bites on a dirty one."""
    dirty = ["%(here)s/alembic/versions", "dotmac_kernel.migrations:versions"]
    assert [location for location in dirty if KERNEL_LINEAGE in location]


class TestComposedLineage:
    """These need `dotmac-files` installed, which is the point.

    Skipped rather than failed when the distribution is absent so the suite
    stays runnable mid-adoption; CI installs the pinned wheel, so there the
    skip cannot hide a real failure.
    """

    @pytest.fixture(autouse=True)
    def _require_files(self) -> None:
        pytest.importorskip("dotmac_files")

    def test_the_version_location_resolves_to_the_installed_distribution(
        self,
    ) -> None:
        """Not "is the string right" — "does the string resolve to a directory
        holding the revision ERP expects".

        A representation of the path proves nothing; this reads the artifact
        Alembic itself will read.
        """
        from alembic.util.pyfiles import coerce_resource_to_filename

        resolved = Path(coerce_resource_to_filename("dotmac_files.migrations:versions"))
        assert resolved.is_dir()
        assert (resolved / "fi_0001_stored_files.py").is_file()

    # The composed migration gate is deliberately NOT run here. It enforces
    # MODULE-lineage conventions — one branch label per root, revision ids
    # within `alembic_version.version_num`'s 32 chars — and ERP's own 150+
    # legacy revisions predate all of them, so pointing it at this repository
    # reports ERP's history rather than anything about `dotmac-files`. The
    # starter already proves `fi_0001` passes that gate, against the assembly
    # whose conventions it describes. What is ERP-specific, and what the
    # remaining tests cover, is that the lineage RESOLVES here and binds onto
    # revisions ERP actually runs.

    def test_fi_0001_resolves_its_edges_onto_erps_own_revisions(self) -> None:
        """`depends_on` is regenerated from ERP's bindings at script load.

        The module names no foreign revision, so the concrete Alembic edge must
        come out as ERP's OWN revisions — the tenant projection and the role
        adoption — and never the kernel's `0001`.
        """
        from dotmac_kernel.prerequisites import (
            install_prerequisite_bindings,
            resolve_depends_on,
        )

        from app.migration_bindings import ASSEMBLY_PREREQUISITE_BINDINGS

        install_prerequisite_bindings(ASSEMBLY_PREREQUISITE_BINDINGS)
        edges = set(
            resolve_depends_on(("tenant_scope_catalog.v1", "module_database_roles.v1"))
        )
        assert edges == {"20260813_tenant_projection", "20260814_database_roles"}
        assert "0001_initial_tenant_schema" not in edges

