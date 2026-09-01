from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

from sqlalchemy import select

from app.models.finance.ap.supplier import Supplier
from app.models.finance.ap.supplier_invoice import SupplierInvoice
from app.services.finance.ap.web.invoice_web import InvoiceWebService


def test_list_invoices_context_uses_selected_supplier_without_preloading(monkeypatch):
    org_id = uuid4()
    supplier_id = uuid4()
    db = MagicMock()
    db.execute.return_value.all.return_value = []
    db.scalar.side_effect = [0, Decimal("0"), Decimal("0"), Decimal("0"), 0]

    monkeypatch.setattr(
        "app.services.finance.ap.invoice_query.build_invoice_query",
        lambda **_kwargs: select(SupplierInvoice).join(
            Supplier, SupplierInvoice.supplier_id == Supplier.supplier_id
        ),
    )

    supplier = MagicMock()
    supplier.supplier_id = supplier_id
    supplier.supplier_code = "SUP-001"
    supplier.trading_name = "Acme Supplies"
    supplier.legal_name = "Acme Supplies Ltd"
    supplier.currency_code = "USD"
    supplier.payment_terms_days = 30
    supplier.withholding_tax_code_id = None
    supplier.default_tax_code_id = None

    get_calls: list[str] = []

    def fake_get(_db, _org_id, selected_supplier_id):
        get_calls.append(str(selected_supplier_id))
        return supplier

    def fail_list(*_args, **_kwargs):
        raise AssertionError("supplier list preload should not be used")

    monkeypatch.setattr(
        "app.services.finance.ap.web.invoice_web.supplier_service.get",
        fake_get,
    )
    monkeypatch.setattr(
        "app.services.finance.ap.web.invoice_web.supplier_service.list",
        fail_list,
    )

    context = InvoiceWebService.list_invoices_context(
        db=db,
        organization_id=str(org_id),
        search=None,
        supplier_id=str(supplier_id),
        status=None,
        start_date=None,
        end_date=None,
        page=1,
    )

    assert get_calls == [str(supplier_id)]
    assert context["selected_supplier"]["supplier_id"] == str(supplier_id)
    assert context["active_filters"] == [
        {
            "name": "supplier_id",
            "value": str(supplier_id),
            "display_value": "Acme Supplies",
        }
    ]


def test_ap_invoices_template_uses_remote_supplier_typeahead():
    from pathlib import Path

    template_path = (
        Path(__file__).resolve().parent.parent
        / "templates"
        / "finance"
        / "ap"
        / "invoices.html"
    )

    with open(template_path, encoding="utf-8") as template_file:
        template = template_file.read()

    assert 'data-typeahead-url="/finance/ap/suppliers/search"' in template
    assert "data-typeahead-hidden" in template
    assert 'filter_entity_select_field("supplier_id"' not in template


def test_list_invoices_context_stats_do_not_reintroduce_supplier_invoice_from():
    org_id = uuid4()
    db = MagicMock()
    db.execute.return_value.all.return_value = []
    db.scalar.side_effect = [0, Decimal("300"), Decimal("100"), Decimal("200"), 0]

    InvoiceWebService.list_invoices_context(
        db=db,
        organization_id=str(org_id),
        search="Acme",
        supplier_id=None,
        status=None,
        start_date="2026-01-01",
        end_date="2026-01-31",
        page=1,
    )

    scalar_statements = [call.args[0] for call in db.scalar.call_args_list]
    balance_stat_sql = [str(statement) for statement in scalar_statements[1:4]]

    for sql in balance_stat_sql:
        assert ", ap.supplier_invoice" not in sql
        assert "sum(anon_1.balance_due)" in sql
        assert "JOIN ap.supplier" in sql
        assert "ap.supplier_invoice.organization_id" in sql
        assert "ap.supplier_invoice.invoice_date >=" in sql
        assert "ap.supplier_invoice.invoice_date <=" in sql
        assert "ap.supplier_invoice.status IN" in sql
        assert "ap.supplier.legal_name" in sql

    assert "anon_1.due_date <" in balance_stat_sql[1]
    assert "anon_1.due_date >=" in balance_stat_sql[2]
    assert "anon_1.due_date <=" in balance_stat_sql[2]

    pending_count_sql = str(scalar_statements[4])
    assert "ap.supplier_invoice.status =" in pending_count_sql
