"""Organization is authoritative; ``tenants`` is its repairable projection."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from dotmac_kernel.models import Tenant, TenantDomain
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.services.tenant_projection import (
    TenantProjectionError,
    reconcile_organization_tenant,
    retire_organization_tenant,
    tenant_projection_drift,
    tenant_slug,
)


@pytest.fixture()
def tenant_db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Tenant.__table__.create(engine)
    TenantDomain.__table__.create(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def _organization(
    *,
    organization_id: UUID | None = None,
    legal_name: str = "Dotmac Technologies Ltd",
    is_active: bool = True,
    slug: str | None = "editable-product-slug",
) -> SimpleNamespace:
    return SimpleNamespace(
        organization_id=organization_id or uuid4(),
        organization_code="DOTMAC",
        legal_name=legal_name,
        is_active=is_active,
        slug=slug,
    )


def test_projection_creates_one_kernel_tenant_without_committing(
    tenant_db: Session,
) -> None:
    organization = _organization()

    tenant = reconcile_organization_tenant(tenant_db, organization)
    tenant_db.flush()

    assert tenant.id == organization.organization_id
    assert tenant.slug == f"erp-{organization.organization_id}"
    assert tenant.name == organization.legal_name
    assert tenant.is_active is True
    assert tenant.suspended_at is None
    assert tenant.deleted_at is None
    assert tenant_db.in_transaction()


def test_projection_is_idempotent_and_repairs_owned_fields(
    tenant_db: Session,
) -> None:
    organization = _organization()
    first = reconcile_organization_tenant(tenant_db, organization)
    tenant_db.flush()
    first.suspended_at = first.created_at
    organization.legal_name = "Dotmac Networks"
    organization.is_active = False

    second = reconcile_organization_tenant(tenant_db, organization)
    tenant_db.flush()

    assert second.id == first.id
    assert second.name == "Dotmac Networks"
    assert second.is_active is False
    assert second.suspended_at == first.created_at
    assert tenant_db.scalar(select(func.count(Tenant.id))) == 1
    assert tenant_projection_drift(organization, second) == ()


def test_product_slug_does_not_rewrite_stable_platform_identity(
    tenant_db: Session,
) -> None:
    organization = _organization(slug="careers-abuja")
    first = reconcile_organization_tenant(tenant_db, organization)
    tenant_db.flush()
    organization.slug = "careers-lagos"

    second = reconcile_organization_tenant(tenant_db, organization)

    assert first.slug == second.slug == tenant_slug(organization.organization_id)


def test_retirement_keeps_the_tenant_tombstone_for_module_references(
    tenant_db: Session,
) -> None:
    organization = _organization()
    reconcile_organization_tenant(tenant_db, organization)
    tenant_db.flush()

    retired = retire_organization_tenant(tenant_db, organization.organization_id)
    tenant_db.flush()

    assert retired is not None
    assert retired.is_active is False
    assert retired.deleted_at is not None
    assert tenant_db.get(Tenant, organization.organization_id) is retired


def test_reconciliation_restores_a_tombstone_when_source_exists(
    tenant_db: Session,
) -> None:
    organization = _organization()
    reconcile_organization_tenant(tenant_db, organization)
    tenant_db.flush()
    retire_organization_tenant(tenant_db, organization.organization_id)
    tenant_db.flush()

    restored = reconcile_organization_tenant(tenant_db, organization)

    assert restored.is_active is True
    assert restored.deleted_at is None


@pytest.mark.parametrize("name", ["", "   ", "x" * 121])
def test_invalid_kernel_name_fails_before_a_partial_projection(
    tenant_db: Session, name: str
) -> None:
    organization = _organization(legal_name=name)

    with pytest.raises(TenantProjectionError):
        reconcile_organization_tenant(tenant_db, organization)

    assert tenant_db.get(Tenant, organization.organization_id) is None
