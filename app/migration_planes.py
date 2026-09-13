"""ERP's explicit persistence-plane selections for composed modules.

Prerequisite bindings say which database effects ERP already supplies.  They do
not choose which half of a selectable module ERP intends to install.  Each
selectable module gets its own selection below, explained on its own terms —
one module's rationale is not a template for another's.

Numbering composes only its tenant plane: this assembly owns the tenant-side
tables, while the independently deployed vendor control plane may separately
compose numbering's platform plane. Selecting storage does not move numbering
authority — ERP's legacy allocators and their callers remain untouched until a
later, series-by-series cutover.

Files composes the FULL set, tenant and platform, even though ERP's `app_user`
role is denied the platform table and nothing under `app/` writes it. ERP
already treats `mod_files.platform_stored_files` as a composed, existing fact:
it is declared in `app/runtime_admission.py`'s `platform_tables`, carries a
tested ACL disposition in `app/privilege_manifest.py`, is asserted by name
under ADR-0023, and `docs/inventories/erp-privilege-census-2026-09-04.json`
shows four live grants on it in production. Selecting tenant-only here would
not be conservative — it would hand `dotmac-files` 0.1.0a4's `fi_0002`
migration a table it can legally DROP under an `ACCESS EXCLUSIVE` lock, and no
row-count oracle exists anywhere in this repository to show that table is
empty (the migration's own non-empty refusal is a safeguard, not a substitute
for that evidence). Retaining the platform plane activates no new writer, so
the full set costs nothing today. A future tenant-only retirement is a
separate slice: it needs a row inventory, migration evidence, and removal of
the existing platform-plane assertions before it can be made.
"""

from __future__ import annotations

from typing import Final

from dotmac_kernel.planes import ModulePlane, ModulePlaneSelection

ASSEMBLY_MODULE_PLANES: Final[tuple[ModulePlaneSelection, ...]] = (
    ModulePlaneSelection(module="numbering", planes=(ModulePlane.TENANT,)),
    ModulePlaneSelection(
        module="files", planes=(ModulePlane.TENANT, ModulePlane.PLATFORM)
    ),
)

__all__ = ["ASSEMBLY_MODULE_PLANES"]
