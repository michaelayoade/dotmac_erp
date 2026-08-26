from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import sessionmaker

from app.db.session_context import prime_tenant_context
from app.models.auth import FederatedIdentity
from app.models.finance.core_org.organization import Organization
from app.models.person import Person
from app.services.external_identity import ERPExternalIdentityAuthority
from app.services.external_identity import ExternalIdentityConflict


def _bind_concurrently(
    engine: Engine,
    *,
    organization_id: UUID,
    person_id: UUID,
    barrier: threading.Barrier,
) -> UUID | None:
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as db:
        prime_tenant_context(db, organization_id)
        barrier.wait(timeout=20)
        try:
            change = ERPExternalIdentityAuthority(db).bind(
                organization_id=organization_id,
                person_id=person_id,
                provider_binding="primary",
                issuer="https://idp.example.test/realms/erp",
                subject="concurrent-stable-subject",
            )
        except ExternalIdentityConflict:
            db.rollback()
            return None
        else:
            db.commit()
            return change.binding.id


def test_concurrent_identical_binding_is_one_row_not_an_aborted_transaction(
    engine: Engine,
) -> None:
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    organization_id = uuid4()
    person_id = uuid4()
    with factory() as setup:
        prime_tenant_context(setup, organization_id)
        setup.add(
            Organization(
                organization_id=organization_id,
                organization_code="OIDC-RACE",
                legal_name="OIDC Race Test",
                functional_currency_code="USD",
                presentation_currency_code="USD",
                fiscal_year_end_month=12,
                fiscal_year_end_day=31,
                is_active=True,
            )
        )
        setup.add(
            Person(
                id=person_id,
                organization_id=organization_id,
                first_name="Concurrent",
                last_name="Identity",
                email="oidc-race@example.test",
                is_active=True,
            )
        )
        setup.commit()
    barrier = threading.Barrier(2)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            ids = [
                future.result(timeout=45)
                for future in (
                    pool.submit(
                        _bind_concurrently,
                        engine,
                        organization_id=organization_id,
                        person_id=person_id,
                        barrier=barrier,
                    )
                    for _ in range(2)
                )
            ]
        with factory() as verify:
            prime_tenant_context(verify, organization_id)
            count = verify.scalar(
                select(func.count())
                .select_from(FederatedIdentity)
                .where(
                    FederatedIdentity.organization_id == organization_id,
                    FederatedIdentity.issuer == "https://idp.example.test/realms/erp",
                    FederatedIdentity.subject == "concurrent-stable-subject",
                )
            )
        assert sum(item is None for item in ids) == 1
        assert len({item for item in ids if item is not None}) == 1
        assert count == 1
    finally:
        with factory() as cleanup:
            prime_tenant_context(cleanup, organization_id)
            cleanup.execute(
                delete(FederatedIdentity).where(
                    FederatedIdentity.organization_id == organization_id
                )
            )
            cleanup.execute(delete(Person).where(Person.id == person_id))
            cleanup.execute(
                delete(Organization).where(
                    Organization.organization_id == organization_id
                )
            )
            cleanup.commit()
