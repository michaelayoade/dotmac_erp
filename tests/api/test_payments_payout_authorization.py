"""A read-only principal cannot reach the money call on the payout route.

``POST /api/v1/payments/transfers/{intent_id}/initiate`` is the one route in
this repository where an HTTP request causes a real Paystack ``POST /transfer``
— money leaves the account. It used to share a guard with the bank-lookup
routes whose admit list contained ``payments:read``, so a principal holding
only that read permission could execute a disbursement.

Both tests below run the SAME wiring and differ in exactly one thing: the
permission the caller holds. That is what makes the pair meaningful —

* the negative test proves the refusal happens in the guard, BEFORE
  ``PaymentService.initiate_expense_transfer`` is reached (a 403 raised after
  the money call would be worthless), and
* the positive control proves the money call IS reachable under this wiring
  when the caller holds ``payments:transfer:initiate`` — so the negative test
  is not passing merely because the route was broken or unreachable.

On the unfixed parent the negative test fails: the read-only caller is
admitted, the handler runs, and ``initiate_expense_transfer`` is called once.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.finance.payments.payment_intent import PaymentIntentStatus
from app.services.finance.payments import PaymentService
from app.services.finance.platform.authorization import AuthorizationService

ORG_ID = uuid4()
PERSON_ID = uuid4()
INTENT_ID = uuid4()

INITIATE_PATH = f"/api/v1/payments/transfers/{INTENT_ID}/initiate"


def _pending_outbound_intent() -> SimpleNamespace:
    """An intent in exactly the state the route requires, so nothing short of
    the guard can turn the request away."""
    return SimpleNamespace(
        intent_id=INTENT_ID,
        organization_id=ORG_ID,
        status=PaymentIntentStatus.PENDING,
        direction=SimpleNamespace(value="OUTBOUND"),
        transfer_code="TRF_test",
        amount=1000,
        currency_code="NGN",
    )


def _client(scopes: list[str]) -> TestClient:
    """Mount the payments router with a caller holding exactly ``scopes``.

    Only ``require_tenant_auth`` / ``require_organization_id`` /
    ``get_db_with_org`` are overridden — the authorization guard under test
    runs for real.
    """
    from app.api.deps import (
        get_db_with_org,
        require_organization_id,
        require_tenant_auth,
    )
    from app.api.finance.payments import router as payments_router

    app = FastAPI()
    app.include_router(payments_router, prefix="/api/v1")
    app.dependency_overrides[require_tenant_auth] = lambda: {
        "organization_id": str(ORG_ID),
        "person_id": str(PERSON_ID),
        "roles": [],
        "scopes": scopes,
    }
    app.dependency_overrides[require_organization_id] = lambda: ORG_ID
    app.dependency_overrides[get_db_with_org] = lambda: MagicMock()
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def payout_route(monkeypatch: pytest.MonkeyPatch):
    """Everything the payout route touches, stubbed, with the money call spied.

    Yields the ``initiate_expense_transfer`` spy. Every DB-backed permission
    path is denied so the caller's token scope is the only thing that decides.
    """
    monkeypatch.setattr(
        AuthorizationService, "check_permission", lambda *_a, **_k: False
    )
    monkeypatch.setattr(
        AuthorizationService, "check_any_permission", lambda *_a, **_k: False
    )
    monkeypatch.setattr(
        "app.api.finance.payments.has_live_admin_grant", lambda *_a, **_k: False
    )
    monkeypatch.setattr(
        "app.api.finance.payments.set_payment_tenant_context", lambda *_a, **_k: None
    )
    # Transfers enabled, so the route cannot 400 out ahead of the money call.
    monkeypatch.setattr(
        "app.api.finance.payments.resolve_value", lambda *_a, **_k: True
    )
    monkeypatch.setattr(
        "app.api.finance.payments.get_paystack_config", lambda *_a, **_k: MagicMock()
    )

    completed = _pending_outbound_intent()
    completed.status = PaymentIntentStatus.PROCESSING

    with (
        patch.object(
            PaymentService,
            "get_intent_by_id",
            return_value=_pending_outbound_intent(),
        ),
        patch.object(
            PaymentService,
            "initiate_expense_transfer",
            return_value=completed,
        ) as money_call,
        patch.object(
            PaymentService,
            "build_transfer_result",
            return_value={
                "completed_immediately": False,
                "claim_status": "APPROVED",
                "message": "queued",
            },
        ),
    ):
        yield money_call


def test_read_only_principal_is_refused_before_the_money_call(payout_route) -> None:
    """403, and ``initiate_expense_transfer`` was never entered.

    The call-count assertion is the point: a 403 returned after the transfer
    had already been sent would still be a disbursement.
    """
    response = _client(["payments:read"]).post(INITIATE_PATH)

    assert response.status_code == 403
    assert payout_route.call_count == 0, (
        "the outbound transfer was reached by a read-only principal — the "
        "guard refused too late to matter"
    )


def test_the_blanket_finance_module_scope_is_refused_before_the_money_call(
    payout_route,
) -> None:
    """``finance:access`` is a module-access scope granted to a dozen roles and
    is one of the few scopes JWTs actually carry. It short-circuited the old
    guard, which made it the broadest live route to a disbursement."""
    response = _client(["finance:access"]).post(INITIATE_PATH)

    assert response.status_code == 403
    assert payout_route.call_count == 0


def test_the_transfer_permission_reaches_the_money_call(payout_route) -> None:
    """Positive control: the guard is not simply refusing everyone, and the
    money call is genuinely reachable under this wiring."""
    response = _client(["payments:transfer:initiate"]).post(INITIATE_PATH)

    assert response.status_code == 200, response.text
    assert payout_route.call_count == 1


def test_bank_lookup_still_admits_the_read_permission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The split must not have taken the read routes away from readers.

    ``GET /payments/banks`` is a pure provider lookup; ``payments:read`` is
    correct there and still works. Asserting the response is NOT 403 keeps
    this test about authorization — the Paystack client is stubbed out, but a
    2xx/5xx distinction is not this test's business.
    """
    monkeypatch.setattr(
        AuthorizationService, "check_any_permission", lambda *_a, **_k: False
    )
    monkeypatch.setattr(
        "app.api.finance.payments.set_payment_tenant_context", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        "app.api.finance.payments.get_paystack_config", lambda *_a, **_k: MagicMock()
    )

    client_cm = MagicMock()
    client_cm.__enter__.return_value.list_banks.return_value = []
    client_cm.__exit__.return_value = False

    with patch("app.services.finance.payments.PaystackClient", return_value=client_cm):
        response = _client(["payments:read"]).get("/api/v1/payments/banks")

    assert response.status_code != 403
