from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.services.finance.ap.web.invoice_web import InvoiceWebService


@pytest.mark.asyncio
async def test_create_invoice_response_commits_on_success(monkeypatch):
    request = MagicMock()
    request.headers = {"content-type": "application/json"}
    request.json = AsyncMock(
        return_value={"supplier_id": str(uuid4()), "lines": []}
    )
    auth = SimpleNamespace(organization_id=uuid4(), person_id=uuid4())
    db = MagicMock()

    monkeypatch.setattr(
        InvoiceWebService,
        "build_invoice_input",
        staticmethod(lambda _db, _data, _org_id: object()),
    )
    monkeypatch.setattr(
        "app.services.finance.ap.web.invoice_web.supplier_invoice_service.create_invoice",
        lambda **_kwargs: SimpleNamespace(invoice_id=uuid4()),
    )

    response = await InvoiceWebService().create_invoice_response(request, auth, db)

    assert response["success"] is True
    db.commit.assert_called_once()
    db.rollback.assert_not_called()


@pytest.mark.asyncio
async def test_create_invoice_response_rolls_back_on_failure(monkeypatch):
    request = MagicMock()
    request.headers = {"content-type": "application/json"}
    request.json = AsyncMock(
        return_value={"supplier_id": str(uuid4()), "lines": []}
    )
    auth = SimpleNamespace(organization_id=uuid4(), person_id=uuid4())
    db = MagicMock()

    monkeypatch.setattr(
        InvoiceWebService,
        "build_invoice_input",
        staticmethod(lambda _db, _data, _org_id: object()),
    )

    def _raise(**_kwargs):
        raise ValueError("boom")

    monkeypatch.setattr(
        "app.services.finance.ap.web.invoice_web.supplier_invoice_service.create_invoice",
        _raise,
    )

    response = await InvoiceWebService().create_invoice_response(request, auth, db)

    assert response.status_code == 400
    db.rollback.assert_called_once()
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_update_invoice_response_commits_on_success(monkeypatch):
    request = MagicMock()
    request.headers = {"content-type": "application/json"}
    request.json = AsyncMock(
        return_value={"supplier_id": str(uuid4()), "lines": []}
    )
    auth = SimpleNamespace(organization_id=uuid4())
    db = MagicMock()

    monkeypatch.setattr(
        InvoiceWebService,
        "build_invoice_input",
        staticmethod(lambda _db, _data, _org_id: object()),
    )
    monkeypatch.setattr(
        "app.services.finance.ap.web.invoice_web.supplier_invoice_service.update_invoice",
        lambda **_kwargs: SimpleNamespace(invoice_id=uuid4()),
    )

    response = await InvoiceWebService().update_invoice_response(
        request, auth, db, str(uuid4())
    )

    payload = json.loads(response.body)
    assert payload["success"] is True
    assert payload["invoice_id"]
    db.commit.assert_called_once()
    db.rollback.assert_not_called()
