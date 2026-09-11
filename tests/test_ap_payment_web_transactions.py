from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.finance.ap.supplier import Supplier
from app.models.finance.ap.supplier_payment import SupplierPayment
from app.services.finance.ap.web.payment_web import PaymentWebService


def test_list_payments_context_includes_summary_stats(monkeypatch):
    org_id = uuid4()
    db = MagicMock()
    db.execute.return_value.all.return_value = []
    db.scalar.side_effect = [0, 0, 0, 0]

    monkeypatch.setattr(
        "app.services.finance.ap.payment_query.build_payment_query",
        lambda **_kwargs: select(SupplierPayment).join(
            Supplier, SupplierPayment.supplier_id == Supplier.supplier_id
        ),
    )
    monkeypatch.setattr(
        "app.services.finance.ap.web.payment_web.supplier_service.list",
        lambda *_args, **_kwargs: [],
    )

    context = PaymentWebService.list_payments_context(
        db=db,
        organization_id=str(org_id),
        search=None,
        supplier_id=None,
        status=None,
        start_date=None,
        end_date=None,
        page=1,
    )

    assert context["this_month_total"]
    assert context["posted_count"] == 0
    assert context["draft_count"] == 0


@pytest.mark.asyncio
async def test_create_payment_response_commits_on_success(monkeypatch):
    request = MagicMock()
    request.headers = {"content-type": "application/json"}
    request.json = AsyncMock(
        return_value={"supplier_id": str(uuid4()), "allocations": []}
    )
    auth = SimpleNamespace(organization_id=uuid4(), person_id=uuid4())
    db = MagicMock()

    monkeypatch.setattr(
        PaymentWebService,
        "build_payment_input",
        staticmethod(lambda _db, _data, _org_id: object()),
    )
    monkeypatch.setattr(
        "app.services.finance.ap.web.payment_web.supplier_payment_service.create_payment",
        lambda **_kwargs: SimpleNamespace(payment_id=uuid4()),
    )

    response = await PaymentWebService().create_payment_response(request, auth, db)

    assert response["success"] is True
    db.commit.assert_called_once()
    db.rollback.assert_not_called()


@pytest.mark.asyncio
async def test_create_payment_response_returns_fragment_for_htmx_failures(monkeypatch):
    request = MagicMock()
    request.headers = {
        "content-type": "application/x-www-form-urlencoded",
        "HX-Request": "true",
    }
    request.form = AsyncMock(return_value={"supplier_id": str(uuid4())})
    auth = SimpleNamespace(organization_id=uuid4(), person_id=uuid4())
    db = MagicMock()

    def _raise(_db, _data, _org_id):
        raise ValueError("bad <input>")

    monkeypatch.setattr(
        PaymentWebService,
        "build_payment_input",
        staticmethod(_raise),
    )
    template_response = MagicMock()
    monkeypatch.setattr(
        "app.services.finance.ap.web.payment_web.templates.TemplateResponse",
        template_response,
    )

    response = await PaymentWebService().create_payment_response(request, auth, db)

    assert response.status_code == 400
    response_body = response.body.decode()
    assert (
        "Unable to save payment. Please check the details and try again."
        in response_body
    )
    assert "bad &lt;input&gt;" not in response_body
    db.rollback.assert_called_once()
    template_response.assert_not_called()
