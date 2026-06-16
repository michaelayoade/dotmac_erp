from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.services.finance.ar.web.customer_web import CustomerWebService


@pytest.mark.asyncio
async def test_create_customer_response_commits_and_redirects(monkeypatch):
    request = MagicMock()
    request.form = AsyncMock(return_value={"customer_name": "Test Customer"})
    auth = SimpleNamespace(organization_id=uuid4())
    db = MagicMock()
    customer_id = uuid4()

    monkeypatch.setattr(
        CustomerWebService,
        "build_customer_input",
        staticmethod(lambda *_args, **_kwargs: object()),
    )
    monkeypatch.setattr(
        "app.services.finance.ar.web.customer_web.customer_service.create_customer",
        lambda **_kwargs: SimpleNamespace(customer_id=customer_id),
    )

    response = await CustomerWebService().create_customer_response(request, auth, db)

    assert response.status_code == 303
    assert response.headers["location"] == (
        f"/finance/ar/customers/{customer_id}?success=Customer+created+successfully"
    )
    db.commit.assert_called_once_with()
    db.rollback.assert_not_called()


@pytest.mark.asyncio
async def test_create_customer_response_uses_customer_id_before_commit(monkeypatch):
    request = MagicMock()
    request.form = AsyncMock(return_value={"customer_name": "Test Customer"})
    auth = SimpleNamespace(organization_id=uuid4())
    db = MagicMock()
    customer_id = uuid4()
    committed = False

    class CustomerStub:
        @property
        def customer_id(self):
            if committed:
                raise AssertionError("customer_id was accessed after commit")
            return customer_id

    def commit():
        nonlocal committed
        committed = True

    db.commit.side_effect = commit
    monkeypatch.setattr(
        CustomerWebService,
        "build_customer_input",
        staticmethod(lambda *_args, **_kwargs: object()),
    )
    monkeypatch.setattr(
        "app.services.finance.ar.web.customer_web.customer_service.create_customer",
        lambda **_kwargs: CustomerStub(),
    )

    response = await CustomerWebService().create_customer_response(request, auth, db)

    assert response.status_code == 303
    assert response.headers["location"] == (
        f"/finance/ar/customers/{customer_id}?success=Customer+created+successfully"
    )
    db.commit.assert_called_once_with()
    db.rollback.assert_not_called()


@pytest.mark.asyncio
async def test_create_customer_response_rolls_back_on_error(monkeypatch):
    request = MagicMock()
    request.form = AsyncMock(return_value={"customer_name": "Broken Customer"})
    auth = SimpleNamespace(organization_id=uuid4())
    db = MagicMock()

    monkeypatch.setattr(
        CustomerWebService,
        "build_customer_input",
        staticmethod(lambda *_args, **_kwargs: object()),
    )
    monkeypatch.setattr(
        "app.services.finance.ar.web.customer_web.customer_service.create_customer",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("boom")),
    )
    monkeypatch.setattr(
        "app.services.finance.ar.web.customer_web.base_context",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        CustomerWebService,
        "customer_form_context",
        staticmethod(lambda *_args, **_kwargs: {}),
    )
    monkeypatch.setattr(
        "app.services.finance.ar.web.customer_web.templates.TemplateResponse",
        lambda *_args, **kwargs: SimpleNamespace(context=kwargs),
    )

    await CustomerWebService().create_customer_response(request, auth, db)

    db.rollback.assert_called_once_with()
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_update_customer_response_commits_and_redirects(monkeypatch):
    request = MagicMock()
    request.form = AsyncMock(return_value={"customer_name": "Updated Customer"})
    auth = SimpleNamespace(organization_id=uuid4())
    db = MagicMock()
    customer_id = uuid4()

    monkeypatch.setattr(
        CustomerWebService,
        "build_customer_input",
        staticmethod(lambda *_args, **_kwargs: object()),
    )
    monkeypatch.setattr(
        "app.services.finance.ar.web.customer_web.customer_service.update_customer",
        lambda **_kwargs: None,
    )

    response = await CustomerWebService().update_customer_response(
        request, auth, db, str(customer_id)
    )

    assert response.status_code == 303
    assert (
        response.headers["location"]
        == "/finance/ar/customers?success=Customer+updated+successfully"
    )
    db.commit.assert_called_once_with()
    db.rollback.assert_not_called()


@pytest.mark.asyncio
async def test_update_customer_response_rolls_back_on_error(monkeypatch):
    request = MagicMock()
    request.form = AsyncMock(return_value={"customer_name": "Broken Update"})
    auth = SimpleNamespace(organization_id=uuid4())
    db = MagicMock()
    customer_id = uuid4()

    monkeypatch.setattr(
        CustomerWebService,
        "build_customer_input",
        staticmethod(lambda *_args, **_kwargs: object()),
    )
    monkeypatch.setattr(
        "app.services.finance.ar.web.customer_web.customer_service.update_customer",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("boom")),
    )
    monkeypatch.setattr(
        "app.services.finance.ar.web.customer_web.base_context",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        CustomerWebService,
        "customer_form_context",
        staticmethod(lambda *_args, **_kwargs: {}),
    )
    monkeypatch.setattr(
        "app.services.finance.ar.web.customer_web.templates.TemplateResponse",
        lambda *_args, **kwargs: SimpleNamespace(context=kwargs),
    )

    await CustomerWebService().update_customer_response(
        request, auth, db, str(customer_id)
    )

    db.rollback.assert_called_once_with()
    db.commit.assert_not_called()
