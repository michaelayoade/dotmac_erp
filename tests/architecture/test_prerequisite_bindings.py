"""ERP's prerequisite bindings must name revisions ERP actually runs.

A binding is a string in a file. The live verifier catches one that is wrong
about the DATABASE; these catch one that is wrong on its face — naming a
revision this assembly does not contain, or leaving an effect unbound so a
composed module would fail at `alembic upgrade` instead of here.

This is the ERP half of the ADR-0006 D1 amendment. The kernel half — a module
declaring `requires` and never naming a foreign revision — lives in the starter.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from dotmac_kernel.prerequisites import (
    IDEMPOTENCY_LEDGER_V1,
    MODULE_DATABASE_ROLES_V1,
    OUTBOX_RELAY_V1,
    PARTY_PERSON_CATALOG_V1,
    TENANT_SCOPE_CATALOG_V1,
    install_prerequisite_bindings,
    resolve_depends_on,
)

from app.migration_bindings import ASSEMBLY_PREREQUISITE_BINDINGS

REPO_ROOT = Path(__file__).resolve().parents[2]
VERSIONS = REPO_ROOT / "alembic" / "versions"


def _erp_revisions() -> set[str]:
    """Every revision id in ERP's own lineage, read statically."""
    found: set[str] = set()
    for path in VERSIONS.glob("*.py"):
        match = re.search(
            r'^revision(?::\s*str)?\s*=\s*["\']([^"\']+)',
            path.read_text(encoding="utf-8"),
            re.M,
        )
        if match:
            found.add(match.group(1))
    return found


@pytest.fixture(autouse=True)
def _install() -> None:
    install_prerequisite_bindings(ASSEMBLY_PREREQUISITE_BINDINGS)


def test_erp_binds_every_effect_it_truthfully_hosts() -> None:
    """The first pair is exactly what `fi_0001_stored_files`
    requires — a tenant catalogue to point a foreign key at, and roles to grant
    to. The ledger effect is the separate ADR-0001 composition decision and
    supplies both storage planes without implying a caller cutover. The party
    and relay effects are the two assembly providers closed by ADR-0004."""
    assert {b.prerequisite for b in ASSEMBLY_PREREQUISITE_BINDINGS} == {
        TENANT_SCOPE_CATALOG_V1.name,
        MODULE_DATABASE_ROLES_V1.name,
        IDEMPOTENCY_LEDGER_V1.name,
        PARTY_PERSON_CATALOG_V1.name,
        OUTBOX_RELAY_V1.name,
    }


@pytest.mark.parametrize(
    "binding", ASSEMBLY_PREREQUISITE_BINDINGS, ids=lambda b: b.prerequisite
)
def test_every_bound_revision_exists_in_erps_lineage(binding) -> None:
    """A binding naming a revision ERP does not contain would resolve to an
    Alembic edge pointing at nothing — a deploy-time failure for something
    decidable here."""
    assert binding.provider_revision in _erp_revisions(), (
        f"{binding.prerequisite} is bound to {binding.provider_revision!r}, "
        "which is not a revision in alembic/versions"
    )


def test_the_bindings_resolve_to_erps_own_revisions() -> None:
    """The whole point: ERP supplies every effect from its OWN lineage, so no
    kernel revision is named anywhere in the resolution."""
    resolved = resolve_depends_on(
        (
            TENANT_SCOPE_CATALOG_V1.name,
            MODULE_DATABASE_ROLES_V1.name,
            IDEMPOTENCY_LEDGER_V1.name,
            PARTY_PERSON_CATALOG_V1.name,
            OUTBOX_RELAY_V1.name,
        )
    )
    assert resolved == (
        "20260813_tenant_projection",
        "20260814_database_roles",
        "20260820_idempotency_ledger",
        "20260824_party_person_projection",
        "20260824_outbox_relay",
    )
    assert not any(r.startswith("0001_") for r in resolved), (
        "ERP must never resolve a prerequisite to a kernel revision — it cannot "
        "run the kernel lineage at all"
    )


def test_no_binding_names_the_kernel_lineage() -> None:
    """The permanent negative canary says kernel `0001` can never run here. A
    binding pointing at it would be a claim the canary directly refutes."""
    for binding in ASSEMBLY_PREREQUISITE_BINDINGS:
        assert binding.provider_owner != "kernel"
        assert "initial_tenant_schema" not in binding.provider_revision
