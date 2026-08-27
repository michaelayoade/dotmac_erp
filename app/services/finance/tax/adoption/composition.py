"""ERP's `dotmac-tax` composition declaration: installed, migrated, disabled.

C2 pins the released a3 artifact and composes its independent ``tx`` lineage.
That creates module-owned storage and lets the adapters consume the released
public contract. It does not backfill policy, run a shadow, repoint a writer or
move tax authority. ``TAX_COMPOSITION_ENABLED`` therefore remains false by
default and every runtime owner remains ERP's legacy path.
"""

from __future__ import annotations

import os
from typing import Final

#: The exact released distribution and public import ERP composes.
DISTRIBUTION: Final = "dotmac-tax"
IMPORT_PACKAGE: Final = "dotmac_tax"
MODULE_CODE: Final = "tax"

#: Published contract and artifact version proved by release run 32898397980;
#: annotated tag ``dotmac-tax-v0.1.0a3`` peels to Starter commit
#: ``531f7f8c37ce2fdf41ecbf2f9a7a9940264a18f9``.
CONTRACT_VERSION: Final = "0.1.0a3"

#: The released lineage and exact reviewed head ERP composes.
MIGRATION_VERSION_LOCATION: Final = "dotmac_tax.migrations:versions"
LINEAGE_HEAD: Final = "tx_0003_result_fingerprint"

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
        "enabled": COMPOSITION_ENABLED,
        "installed_version": installed,
        "ready": COMPOSITION_ENABLED and installed == CONTRACT_VERSION,
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
            f"{DISTRIBUTION} is not installed although ERP pins it at C2. See "
            "docs/architecture/"
            "dotmac-tax-adoption-boundary.md."
        )
    if installed != CONTRACT_VERSION:
        raise TaxCompositionNotReady(
            f"{DISTRIBUTION}=={installed} is installed but ERP reviewed "
            f"{CONTRACT_VERSION}; refusing contract or migration drift"
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
    "LINEAGE_HEAD",
    "MIGRATION_VERSION_LOCATION",
    "MODULE_CODE",
    "TaxCompositionNotReady",
    "composition_state",
    "require_composition_ready",
]
