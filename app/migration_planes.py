"""ERP's explicit persistence-plane selections for composed modules.

Prerequisite bindings say which database effects ERP already supplies.  They do
not choose which half of a selectable module ERP intends to install.  Numbering
is ERP's first such module: this assembly composes only its tenant plane, while
the independently deployed vendor control plane may compose its platform plane.

Selecting storage does not move numbering authority.  ERP's legacy allocators
and their callers remain untouched until a later, series-by-series cutover.
"""

from __future__ import annotations

from typing import Final

from dotmac_kernel.planes import ModulePlane, ModulePlaneSelection

ASSEMBLY_MODULE_PLANES: Final[tuple[ModulePlaneSelection, ...]] = (
    ModulePlaneSelection(module="numbering", planes=(ModulePlane.TENANT,)),
)

__all__ = ["ASSEMBLY_MODULE_PLANES"]
