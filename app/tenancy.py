"""ERP's explicit Organization-to-Tenant identity adapter (E8 slice 3).

``core_org.organization`` remains the tenancy authority. Shared modules speak
in tenant scope, so the assembly maps an Organization to that scope without a
second identifier, mapping table, or writer: the Organization UUID *is* the
Tenant UUID. The hosted ``public.tenants`` projection preserves that identity
rather than allocating another one.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from dotmac_kernel.cache import TenantScope


@dataclass(frozen=True, slots=True)
class OrganizationTenantContext:
    """The two names for one ERP tenancy identity during convergence."""

    organization_id: UUID
    tenant_id: UUID

    @classmethod
    def for_organization(cls, organization_id: UUID) -> OrganizationTenantContext:
        """Map one ERP Organization to shared-module tenant scope.

        The runtime type check is deliberate: request strings must be parsed at
        their adapter before they can establish database security context.
        """
        if not isinstance(organization_id, UUID):
            raise TypeError("organization_id must be a UUID")
        return cls(organization_id=organization_id, tenant_id=organization_id)

    @property
    def tenant_scope(self) -> TenantScope:
        """Return the shared-module scope for this ERP organization."""
        return TenantScope(self.tenant_id)


__all__ = ["OrganizationTenantContext"]
