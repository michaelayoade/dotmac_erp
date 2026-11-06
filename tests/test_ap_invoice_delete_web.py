from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

import app.services.finance.ap.web.invoice_web as invoice_web_module
from app.services.finance.ap.web.invoice_web import InvoiceWebService


def test_delete_response_rolls_back_before_rendering_integrity_error(monkeypatch):
    organization_id = uuid4()
    invoice_id = uuid4()
    request = MagicMock()
    auth = SimpleNamespace(organization_id=organization_id)
    db = MagicMock()
    events: list[str] = []

    def reject_delete(*_args, **_kwargs):
        raise IntegrityError("DELETE", {}, Exception("foreign key violation"))

    monkeypatch.setattr(
        invoice_web_module.supplier_invoice_service,
        "delete_invoice",
        reject_delete,
    )
    monkeypatch.setattr(
        invoice_web_module,
        "base_context",
        lambda *_args, **_kwargs: {},
    )

    def detail_context(*_args, **_kwargs):
        events.append("detail")
        return {}

    monkeypatch.setattr(InvoiceWebService, "invoice_detail_context", detail_context)
    rendered_response = object()
    render = MagicMock(return_value=rendered_response)
    monkeypatch.setattr(invoice_web_module.templates, "TemplateResponse", render)
    db.rollback.side_effect = lambda: events.append("rollback")

    response = InvoiceWebService().delete_invoice_response(
        request,
        auth,
        db,
        str(invoice_id),
    )

    assert response is rendered_response
    assert events == ["rollback", "detail"]
    db.commit.assert_not_called()
    context = render.call_args.args[2]
    assert "still referenced by another record" in context["error"]


def test_delete_response_rolls_back_and_reraises_unexpected_error(monkeypatch):
    organization_id = uuid4()
    request = MagicMock()
    auth = SimpleNamespace(organization_id=organization_id)
    db = MagicMock()

    def fail_delete(*_args, **_kwargs):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(
        invoice_web_module.supplier_invoice_service,
        "delete_invoice",
        fail_delete,
    )

    with pytest.raises(RuntimeError, match="unexpected"):
        InvoiceWebService().delete_invoice_response(
            request,
            auth,
            db,
            str(uuid4()),
        )

    db.rollback.assert_called_once()
    db.commit.assert_not_called()
