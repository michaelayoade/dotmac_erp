"""The invoice lock serializes two real PostgreSQL checkout attempts."""

from __future__ import annotations

import threading
import time
import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.db.session_context import tenant_scope_for_session
from app.models.finance.ar.customer import Customer, CustomerType
from app.models.finance.ar.invoice import Invoice, InvoiceStatus, InvoiceType
from app.models.finance.core_org.organization import Organization
from app.models.finance.payments.payment_intent import PaymentIntent
from app.services.finance.payments import payment_service
from app.services.finance.payments.payment_service import PaymentService
from app.services.finance.payments.paystack_client import PaystackConfig

pytestmark = pytest.mark.integration


def test_two_sessions_mint_only_one_reference_for_an_invoice(engine, monkeypatch):
    """The second checkout waits for the first intent commit, then refuses.

    The positive control observes PostgreSQL reporting the first backend as
    the second backend's blocker. A stand-in SQL assertion cannot prove that
    the invoice row lock covers the active-intent lookup and first commit.
    """
    assert engine.dialect.name == "postgresql"
    sessions = sessionmaker(bind=engine)
    organization_id = uuid.uuid4()
    customer_id = uuid.uuid4()
    invoice_id = uuid.uuid4()

    with sessions() as setup:
        setup.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        setup.add(
            Organization(
                organization_id=organization_id,
                organization_code=f"INTENT-{uuid.uuid4().hex[:8].upper()}",
                legal_name="Invoice Lock Canary",
                functional_currency_code="NGN",
                presentation_currency_code="NGN",
                fiscal_year_end_month=12,
                fiscal_year_end_day=31,
                is_active=True,
            )
        )
        setup.flush()
        setup.add(
            Customer(
                customer_id=customer_id,
                organization_id=organization_id,
                customer_code=f"C-{uuid.uuid4().hex[:8]}",
                customer_type=CustomerType.COMPANY,
                legal_name="Invoice Lock Customer",
                ar_control_account_id=uuid.uuid4(),
                currency_code="NGN",
                primary_contact={"email": "payer@example.test"},
            )
        )
        setup.flush()
        setup.add(
            Invoice(
                invoice_id=invoice_id,
                organization_id=organization_id,
                customer_id=customer_id,
                invoice_number=f"LOCK-{uuid.uuid4().hex[:8]}",
                invoice_type=InvoiceType.STANDARD,
                invoice_date=date.today(),
                due_date=date.today(),
                currency_code="NGN",
                subtotal=Decimal("100.00"),
                tax_amount=Decimal("0"),
                total_amount=Decimal("100.00"),
                amount_paid=Decimal("0"),
                functional_currency_amount=Decimal("100.00"),
                status=InvoiceStatus.POSTED,
                ar_control_account_id=uuid.uuid4(),
                created_by_user_id=uuid.uuid4(),
            )
        )
        setup.commit()

    first_before_commit = threading.Event()
    first_ready = threading.Event()
    release_first = threading.Event()
    second_connected = threading.Event()
    first_commit_seen = False
    backend_ids: dict[str, int] = {}
    outcomes: dict[str, object] = {}
    provider_references: list[str] = []

    class PausingSession(Session):
        def commit(self) -> None:
            nonlocal first_commit_seen
            if not first_commit_seen:
                first_commit_seen = True
                first_before_commit.set()
                first_ready.set()
                if not release_first.wait(timeout=30):
                    raise TimeoutError("first checkout was never released")
            super().commit()

    class FakePaystackClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def initialize_transaction(self, **kwargs):
            provider_references.append(kwargs["reference"])
            return SimpleNamespace(
                access_code="canary-access-code",
                authorization_url="https://example.test/authorize",
            )

    monkeypatch.setattr(
        payment_service, "PaystackClient", lambda _config: FakePaystackClient()
    )
    monkeypatch.setattr(
        payment_service, "resolve_value", lambda *_args, **_kwargs: None
    )

    def attempt(name: str, factory: sessionmaker) -> None:
        with factory() as db:
            with tenant_scope_for_session(db, organization_id):
                try:
                    backend_ids[name] = int(db.scalar(text("SELECT pg_backend_pid()")))
                    assert db.info["organization_id"] == organization_id
                    if name == "second":
                        second_connected.set()
                    intent = PaymentService(
                        db, organization_id
                    ).create_invoice_payment_intent(
                        invoice_id=invoice_id,
                        callback_url="https://example.test/callback",
                        paystack_config=cast(PaystackConfig, object()),
                    )
                    outcomes[name] = intent
                except BaseException as exc:  # noqa: BLE001 - asserted by main thread
                    outcomes[name] = exc
                    if name == "first":
                        first_ready.set()
                finally:
                    db.rollback()

    first_factory = sessionmaker(bind=engine, class_=PausingSession)
    threads: list[threading.Thread] = []
    surviving_count: int | None = None
    try:
        first = threading.Thread(
            target=attempt, args=("first", first_factory), daemon=True
        )
        threads.append(first)
        first.start()
        assert first_ready.wait(timeout=15), "first checkout never produced a result"
        assert first_before_commit.is_set(), outcomes.get("first")

        second = threading.Thread(
            target=attempt, args=("second", sessions), daemon=True
        )
        threads.append(second)
        second.start()
        assert second_connected.wait(timeout=15), "second checkout never connected"

        deadline = time.monotonic() + 15
        blocked_by_first = False
        while time.monotonic() < deadline:
            with engine.connect() as observer:
                blockers = observer.scalar(
                    text("SELECT pg_blocking_pids(:backend_id)"),
                    {"backend_id": backend_ids["second"]},
                )
            if backend_ids["first"] in (blockers or []):
                blocked_by_first = True
                break
            time.sleep(0.05)
        assert blocked_by_first, (
            "PostgreSQL never showed the invoice row lock blocking checkout two"
        )
    finally:
        release_first.set()
        for thread in threads:
            thread.join(timeout=30)
        with sessions() as check:
            check.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            surviving_count = check.scalar(
                select(func.count(PaymentIntent.intent_id)).where(
                    PaymentIntent.organization_id == organization_id,
                    PaymentIntent.source_type == "INVOICE",
                    PaymentIntent.source_id == invoice_id,
                )
            )
        with sessions() as cleanup:
            cleanup.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            cleanup.execute(text("SET LOCAL lock_timeout = '5s'"))
            cleanup.execute(
                text(
                    "DELETE FROM payments.payment_intent WHERE organization_id = :org"
                ),
                {"org": organization_id},
            )
            cleanup.execute(
                text("DELETE FROM ar.invoice WHERE invoice_id = :invoice"),
                {"invoice": invoice_id},
            )
            cleanup.execute(
                text("DELETE FROM ar.customer WHERE customer_id = :customer"),
                {"customer": customer_id},
            )
            cleanup.execute(
                text("DELETE FROM core_org.organization WHERE organization_id = :org"),
                {"org": organization_id},
            )
            cleanup.commit()

    assert all(not thread.is_alive() for thread in threads), (
        "a checkout blocked indefinitely"
    )
    assert isinstance(outcomes.get("first"), PaymentIntent), outcomes.get("first")
    assert isinstance(outcomes.get("second"), HTTPException), outcomes.get("second")
    assert cast(HTTPException, outcomes["second"]).status_code == 409
    assert len(provider_references) == 1
    assert surviving_count == 1
