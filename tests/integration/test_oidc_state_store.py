from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import UUID, uuid4

from dotmac_auth_oidc import LoginState
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.session_context import prime_tenant_context
from app.services.oidc_state_store import PostgresOIDCStateStore


def _consume(
    engine: Engine,
    *,
    organization_id: UUID,
    state_id: str,
    barrier: threading.Barrier,
) -> bool:
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as db:
        prime_tenant_context(db, organization_id)
        store = PostgresOIDCStateStore(
            db,
            organization_id=organization_id,
            provider_binding="primary",
        )
        barrier.wait(timeout=20)
        claimed = store.take(state_id)
        db.commit()
        return claimed is not None


def test_exactly_one_concurrent_callback_consumes_the_state(
    engine: Engine,
    db: Session,
    organization,
) -> None:
    organization_id = organization.organization_id
    state_id = f"state-{uuid4()}"
    prime_tenant_context(db, organization_id)
    PostgresOIDCStateStore(
        db,
        organization_id=organization_id,
        provider_binding="primary",
    ).put(
        LoginState(
            state_id=state_id,
            nonce="nonce",
            code_verifier="verifier",
            redirect_uri="https://erp.example.test/auth/oidc/callback",
            issued_at=int(datetime.now(UTC).timestamp()),
            return_to="/",
        ),
        ttl_seconds=600,
    )
    db.commit()
    barrier = threading.Barrier(2)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [
            future.result(timeout=45)
            for future in (
                pool.submit(
                    _consume,
                    engine,
                    organization_id=organization_id,
                    state_id=state_id,
                    barrier=barrier,
                )
                for _ in range(2)
            )
        ]

    assert sorted(results) == [False, True]
