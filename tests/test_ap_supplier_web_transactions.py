from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.services.finance.ap.web.supplier_web import SupplierWebService


@pytest.mark.asyncio
async def test_create_supplier_response_commits_on_success(monkeypatch):
    request = MagicMock()
    request.form = AsyncMock(return_value={"legal_name": "Acme Supplies"})
    auth = SimpleNamespace(organization_id=uuid4())
    db = MagicMock()

    monkeypatch.setattr(
        SupplierWebService,
        "build_supplier_input",
        staticmethod(lambda _db, _form_data, _org_id: object()),
    )
    created_supplier = SimpleNamespace(supplier_id=uuid4())
    monkeypatch.setattr(
        "app.services.finance.ap.web.supplier_web.supplier_service.create_supplier",
        lambda **_kwargs: created_supplier,
    )

    response = await SupplierWebService().create_supplier_response(request, auth, db)

    assert response.status_code == 303
    assert response.headers["location"].startswith(
        f"/finance/ap/suppliers/{created_supplier.supplier_id}"
    )
    db.commit.assert_called_once()
    db.rollback.assert_not_called()


@pytest.mark.asyncio
async def test_create_supplier_response_does_not_read_supplier_after_commit(
    monkeypatch,
):
    request = MagicMock()
    request.form = AsyncMock(return_value={"legal_name": "Acme Supplies"})
    auth = SimpleNamespace(organization_id=uuid4())
    db = MagicMock()
    supplier_id = uuid4()

    class ExpiringSupplier:
        expired = False

        @property
        def supplier_id(self):
            if self.expired:
                raise AssertionError("supplier_id was read after commit")
            return supplier_id

    supplier = ExpiringSupplier()
    db.commit.side_effect = lambda: setattr(supplier, "expired", True)

    monkeypatch.setattr(
        SupplierWebService,
        "build_supplier_input",
        staticmethod(lambda _db, _form_data, _org_id: object()),
    )
    monkeypatch.setattr(
        "app.services.finance.ap.web.supplier_web.supplier_service.create_supplier",
        lambda **_kwargs: supplier,
    )

    response = await SupplierWebService().create_supplier_response(request, auth, db)

    assert response.status_code == 303
    assert response.headers["location"].startswith(
        f"/finance/ap/suppliers/{supplier_id}"
    )
    db.commit.assert_called_once()
    db.rollback.assert_not_called()


@pytest.mark.asyncio
async def test_create_supplier_response_rolls_back_on_failure(monkeypatch):
    request = MagicMock()
    request.form = AsyncMock(return_value={"legal_name": "Acme Supplies"})
    auth = SimpleNamespace(organization_id=uuid4())
    db = MagicMock()

    monkeypatch.setattr(
        SupplierWebService,
        "build_supplier_input",
        staticmethod(lambda _db, _form_data, _org_id: object()),
    )

    def _raise(**_kwargs):
        raise ValueError("boom")

    monkeypatch.setattr(
        "app.services.finance.ap.web.supplier_web.supplier_service.create_supplier",
        _raise,
    )
    monkeypatch.setattr(
        "app.services.finance.ap.web.supplier_web.base_context",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        SupplierWebService,
        "supplier_form_context",
        lambda self, _db, _org_id, supplier_id=None: {},
    )
    monkeypatch.setattr(
        "app.services.finance.ap.web.supplier_web.templates.TemplateResponse",
        lambda _request, _template, _context: SimpleNamespace(status_code=200),
    )

    response = await SupplierWebService().create_supplier_response(request, auth, db)

    assert response.status_code == 200
    db.rollback.assert_called_once()
    db.commit.assert_not_called()


def test_supplier_typeahead_returns_no_results_for_blank_query():
    db = MagicMock()
    org_id = uuid4()
    payload = SupplierWebService.supplier_typeahead(db, str(org_id), "", limit=20)

    assert payload == {"items": []}
    db.scalars.assert_not_called()
