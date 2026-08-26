"""Where ERP's `dotmac-tax` adoption actually stands: NOT COMPOSED.

The sibling of `app/accounting_adoption.py`, deliberately much smaller, because
much less has happened.  For Accounting the pin and the lineage are in place and
only the flag is off.  For Tax:

- `dotmac-tax` is **not** a dependency (it is absent from `pyproject.toml` and
  from `poetry.lock`);
- its `tx` lineage is **not** in `alembic.ini`'s `version_locations`;
- `mod_tax` does not exist in any ERP database; and
- no ERP writer, reader, calculator or filing path has been repointed.

Ledger item C1 delivered typed adapters without composition. The staged C2
consumer now names the module's proposed a3 public determination result
directly, while importing it lazily so this branch does not claim an unreleased
dependency. The exact pin, lock and lineage composition are a separate final
change after an authoritative release oracle exists.

Pinning, composing the lineage, backfilling policy data and switching a cohort's
writer are C2/C3/C4 — each with its own gate in
`docs/architecture/dotmac-tax-adoption-boundary.md` § "Composition and release
gates".  Supplying a module's tables does not cut anything over, and neither
does supplying an adapter.
"""

from __future__ import annotations

import os
from typing import Final

#: The distribution and import names ERP will eventually depend on.  Named as
#: constants so the "is it absent?" assertions have one literal to check.
DISTRIBUTION: Final = "dotmac-tax"
IMPORT_PACKAGE: Final = "dotmac_tax"
MODULE_CODE: Final = "tax"

#: The version of the published contract these adapters were written against.
#: Recorded so that a future pin, the lock and the mirrored contract are checked
#: against ONE literal.  Recording a version is NOT a claim that it is pinned,
#: published or adopted: this repository holds no authoritative external oracle
#: (release run, peeled tag, deployment run) for it, and per the cross-repository
#: engineering governance in `AGENTS.md` a repository-local claim may only be
#: derived from repository-local facts.  What IS checkable here — and what
#: `composition_state()` reports — is whether the distribution is installed.
CONTRACT_VERSION: Final = "0.1.0a2"

#: Public read-result contract this staged consumer is written against. This is
#: a TARGET, not a publication or adoption claim. Do not turn it into a package
#: pin until the release run/install-back/peeled-tag oracle proves a3 exists.
READ_CONTRACT_TARGET_VERSION: Final = "0.1.0a3"

#: The Alembic version location ERP would compose at C2.  Present as a literal
#: so a test can assert `alembic.ini` does NOT contain it yet.
MIGRATION_VERSION_LOCATION: Final = "dotmac_tax.migrations:versions"

#: The single deploy-time knob, prod-safe default.  Read at import and never
#: per-request: "who owns tax determination?" must have one answer for the life
#: of a process.
COMPOSITION_ENABLED: Final[bool] = (
    os.getenv("TAX_COMPOSITION_ENABLED", "false").lower() == "true"
)


class TaxCompositionNotReady(RuntimeError):
    """Raised when something reaches for the module before the gates are met."""


def _module_version() -> str | None:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version(DISTRIBUTION)
    except PackageNotFoundError:
        return None


def composition_state() -> dict[str, object]:
    """A flat, loggable description of the INSTALLED state, not the intended one.

    The interesting failure is a deployment that believes it composed the module
    and did not.
    """
    installed = _module_version()
    return {
        "distribution": DISTRIBUTION,
        "contract_version": CONTRACT_VERSION,
        "read_contract_target_version": READ_CONTRACT_TARGET_VERSION,
        "enabled": COMPOSITION_ENABLED,
        "installed_version": installed,
        "ready": COMPOSITION_ENABLED and installed is not None,
    }


def require_composition_ready() -> str:
    """Return the installed module version, or refuse to proceed.

    The two failure modes stay distinct because the operator response differs: a
    missing distribution is a deploy that never installed the wheel, a disabled
    flag is a deploy that installed it and has not been told to use it.  Neither
    degrades into "carry on against ERP's own calculator" — that is how a
    rehearsal silently measures nothing.
    """
    installed = _module_version()
    if installed is None:
        raise TaxCompositionNotReady(
            f"{DISTRIBUTION} is not installed. ERP has not pinned it; C1 "
            "delivers adapters only. See docs/architecture/"
            "dotmac-tax-adoption-boundary.md."
        )
    if not COMPOSITION_ENABLED:
        raise TaxCompositionNotReady(
            f"{DISTRIBUTION}=={installed} is installed but "
            "TAX_COMPOSITION_ENABLED is false. Composition is a deliberate "
            "deploy-time decision, not a default."
        )
    return installed


__all__ = [
    "COMPOSITION_ENABLED",
    "CONTRACT_VERSION",
    "DISTRIBUTION",
    "IMPORT_PACKAGE",
    "MIGRATION_VERSION_LOCATION",
    "MODULE_CODE",
    "READ_CONTRACT_TARGET_VERSION",
    "TaxCompositionNotReady",
    "composition_state",
    "require_composition_ready",
]
