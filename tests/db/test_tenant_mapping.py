"""E8 slice 3: ERP Organization identity maps to kernel tenant context."""

from __future__ import annotations

from uuid import uuid4

import pytest


def test_organization_and_tenant_use_one_identity() -> None:
    """The adapter must not create a second id or a mapping-table authority."""
    from app.tenancy import OrganizationTenantContext

    organization_id = uuid4()

    context = OrganizationTenantContext.for_organization(organization_id)

    assert context.organization_id == organization_id
    assert context.tenant_id == organization_id


def test_mapping_rejects_an_untyped_identifier() -> None:
    """Scope identity enters as a UUID, never an unchecked transport string."""
    from app.tenancy import OrganizationTenantContext

    with pytest.raises(TypeError, match="organization_id must be a UUID"):
        OrganizationTenantContext.for_organization("not-a-uuid")  # type: ignore[arg-type]
