"""Single writer for ERP Organization projections into the tenant catalogue.

``core_org.organization`` remains authoritative. The kernel ``Tenant`` row is
an assembly-owned projection used by stateful shared modules; this service is
the only runtime writer of the fields ERP owns on that projection.

The service mutates and returns ORM rows but never commits or rolls back. The
ERP entry point that changed the Organization owns the transaction, so source
and projection succeed or fail together.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol
from uuid import UUID

from dotmac_kernel.models import Tenant
from sqlalchemy.orm import Session

from app.tenancy import OrganizationTenantContext

TENANT_SLUG_PREFIX = "erp-"
MAX_TENANT_NAME_LENGTH = 120


class OrganizationProjectionSource(Protocol):
    """The product-owned facts that the platform tenant catalogue projects."""

    organization_id: UUID
    legal_name: str
    is_active: bool


class TenantProjectionError(RuntimeError):
    """An Organization cannot be represented truthfully as a kernel Tenant."""


class TenantProjectionMissingError(TenantProjectionError):
    """Retirement was requested before the Organization had a tenant row."""


def tenant_slug(organization_id: UUID) -> str:
    """Return the immutable platform slug for one ERP Organization.

    ERP's ``Organization.slug`` is an editable product URL (for example the
    careers portal). Reusing it as platform identity would make an unrelated
    content edit rename the tenant. UUID-derived slugs are stable, unique and
    remain well inside the kernel's 63-character contract.
    """

    context = OrganizationTenantContext.for_organization(organization_id)
    return f"{TENANT_SLUG_PREFIX}{context.tenant_id}"


def _tenant_name(legal_name: str) -> str:
    if not isinstance(legal_name, str):
        raise TenantProjectionError("organization legal_name must be a string")
    name = legal_name.strip()
    if not name:
        raise TenantProjectionError("organization legal_name must not be blank")
    if len(name) > MAX_TENANT_NAME_LENGTH:
        raise TenantProjectionError(
            "organization legal_name exceeds the kernel Tenant.name limit "
            f"of {MAX_TENANT_NAME_LENGTH} characters"
        )
    return name


def tenant_projection_drift(
    organization: OrganizationProjectionSource,
    tenant: Tenant,
) -> tuple[str, ...]:
    """Name every ERP-owned projection field that differs from its source."""

    context = OrganizationTenantContext.for_organization(organization.organization_id)
    expected = {
        "id": context.tenant_id,
        "slug": tenant_slug(context.organization_id),
        "name": _tenant_name(organization.legal_name),
        "is_active": bool(organization.is_active),
        "deleted_at": None,
    }
    return tuple(
        field for field, value in expected.items() if getattr(tenant, field) != value
    )


def reconcile_organization_tenant(
    db: Session,
    organization: OrganizationProjectionSource,
) -> Tenant:
    """Create or repair the Tenant projection in the caller's transaction.

    ``suspended_at`` is deliberately preserved: it is not an Organization
    attribute and therefore is not an ERP-owned projection field.
    """

    context = OrganizationTenantContext.for_organization(organization.organization_id)
    name = _tenant_name(organization.legal_name)
    slug = tenant_slug(context.organization_id)
    tenant = db.get(Tenant, context.tenant_id)
    if tenant is None:
        tenant = Tenant(
            id=context.tenant_id,
            slug=slug,
            name=name,
            is_active=bool(organization.is_active),
        )
        db.add(tenant)
        return tenant

    tenant.slug = slug
    tenant.name = name
    tenant.is_active = bool(organization.is_active)
    tenant.deleted_at = None
    return tenant


def retire_organization_tenant(db: Session, organization_id: UUID) -> Tenant:
    """Tombstone a projection without deleting module-owned tenant history."""

    context = OrganizationTenantContext.for_organization(organization_id)
    tenant = db.get(Tenant, context.tenant_id)
    if tenant is None:
        raise TenantProjectionMissingError(
            f"tenant projection is missing for organization {organization_id}"
        )
    tenant.is_active = False
    tenant.deleted_at = datetime.now(timezone.utc)
    return tenant


__all__ = [
    "MAX_TENANT_NAME_LENGTH",
    "TENANT_SLUG_PREFIX",
    "OrganizationProjectionSource",
    "TenantProjectionError",
    "TenantProjectionMissingError",
    "reconcile_organization_tenant",
    "retire_organization_tenant",
    "tenant_projection_drift",
    "tenant_slug",
]
