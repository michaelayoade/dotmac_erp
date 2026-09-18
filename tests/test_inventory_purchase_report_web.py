"""All-line report permissions, template states, navigation and CSV regressions."""

import csv
from datetime import date
from decimal import Decimal
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader, StrictUndefined

from app.models.finance.ap.supplier_invoice import SupplierInvoiceStatus, SupplierInvoiceType
from app.services.inventory.purchase_report import PurchaseReportFilters
from app.services.inventory.purchase_report_web import PurchaseReportWebService
from app.web.deps import get_db_for_org, require_inventory_access
from app.web.inventory_weekly_purchases import router

MODULE = "app.services.inventory.purchase_report_web"
TODAY = date(2026, 9, 18)
ROOT = Path(__file__).resolve().parents[1]


def user(*, ap=True, organization=True):
    return SimpleNamespace(
        organization_id=uuid4() if organization else None,
        has_permission=lambda permission: ap and permission == "ap:invoices:read",
        has_module_access=lambda module: True,
    )


def row():
    return {
        "line_id": uuid4(), "line_number": 1, "invoice_id": uuid4(),
        "invoice_number": "=SUM(1,1)", "invoice_date": TODAY,
        "invoice_type": SupplierInvoiceType.CREDIT_NOTE,
        "status": SupplierInvoiceStatus.POSTED, "currency_code": "NGN",
        "supplier_name": "@Supplier", "description": "<script>alert(1)</script>",
        "item_id": None, "item_code": None, "item_name": None,
        "item_link_status": "Not linked to an item", "category_name": None,
        "warehouse_name": None, "quantity": Decimal("2"),
        "unit_price": Decimal("0.123456"), "net_amount": Decimal("-0.246912"),
        "net_tax": Decimal("-0.018518"),
    }


def report_data(rows=None):
    rows = rows or []
    return {
        "purchase_rows": rows, "invoice_rows": [], "currency_totals": [],
        "summary": {"document_count": 1, "line_count": len(rows), "item_count": 0,
                    "unlinked_count": len(rows), "supplier_count": 1},
        "total_count": len(rows), "page": 1, "total_pages": 1,
    }


def context(kind="weekly", rows=None, *, anchor="2026-09-18"):
    ws = PurchaseReportWebService(Mock())
    ws.service = Mock()
    ws.service.report.return_value = report_data(rows)
    ws.service.options.return_value = {"suppliers": [], "warehouses": [], "categories": []}
    selected = PurchaseReportFilters.parse(
        period=kind, period_start=anchor, search="cable & fibre", today=TODAY
    )
    with (
        patch.object(ws, "_filters", return_value=selected),
        patch(f"{MODULE}.base_context", return_value={}),
        patch(f"{MODULE}.templates") as templates,
    ):
        templates.TemplateResponse.return_value = HTMLResponse("report")
        response = ws.report_response(Mock(), user(), {}, page=1)
        result = templates.TemplateResponse.call_args.args[2]
    assert response.headers["cache-control"] == "no-store"
    return result


@pytest.mark.parametrize("kind", ["weekly", "monthly", "quarterly", "yearly"])
@pytest.mark.parametrize("populated", [False, True])
def test_template_all_periods_empty_populated_and_description_escaping(kind, populated):
    stubs = {
        "inventory/base_inventory.html": "{% block page_title %}{% endblock %}{% block content %}{% endblock %}",
        "components/macros.html": (
            "{% macro icon_svg(name, classes='') %}{% endmacro %}"
            "{% macro status_badge(status, size='md') %}{{ status }}{% endmacro %}"
            "{% macro empty_state(title, description='', icon='') %}{{ title }} {{ description }}{% endmacro %}"
        ),
    }
    env = Environment(
        loader=ChoiceLoader([DictLoader(stubs), FileSystemLoader(str(ROOT / "templates"))]),
        autoescape=True, undefined=StrictUndefined,
    )
    html = env.get_template("inventory/report_purchases.html").render(
        **context(kind, [row()] if populated else [])
    )
    assert "Purchase Report" in html
    assert 'hx-select="#purchase-report"' in html
    assert 'id="results-container"' in html
    assert "<script>" not in html and "/inventory/items/None" not in html
    if populated:
        assert "Not linked to an item" in html
        assert "&lt;script&gt;" in html
    else:
        assert "No purchases found" in html
    for name in ("period_start", "month" if kind == "monthly" else "period"):
        assert f'name="{name}"' in html


@pytest.mark.parametrize("kind", ["weekly", "monthly", "quarterly", "yearly"])
def test_navigation_and_exports_preserve_filters(kind):
    result = context(kind)
    for key in ("previous_period_url", "current_period_url", "export_url", "invoice_export_url", "next_page_url"):
        query = parse_qs(urlparse(result[key]).query)
        assert query["search"] == ["cable & fibre"]
        assert query["period"] == [kind]
        assert "week_start" not in query
    assert result["next_period_url"] is None
    assert "search" not in parse_qs(urlparse(result["reset_url"]).query)
    assert context(kind, anchor="1900-01-01")["previous_period_url"] is None


@pytest.mark.parametrize("ap,organization", [(False, True), (True, False)])
def test_ap_gate_before_any_report_or_export_query(ap, organization):
    ws = PurchaseReportWebService(Mock())
    ws.service = Mock()
    for action in (
        lambda: ws.report_response(Mock(), user(ap=ap, organization=organization), {}),
        lambda: ws.export_response(user(ap=ap, organization=organization), {}),
        lambda: ws.export_response(user(ap=ap, organization=organization), {}, "invoices"),
    ):
        with pytest.raises(HTTPException) as exc:
            action()
        assert exc.value.status_code == 403
    ws.service.report.assert_not_called()
    ws.service.export_rows.assert_not_called()


def test_line_csv_and_invoice_csv_preserve_precision_and_do_not_repeat_header_totals():
    ws = PurchaseReportWebService(Mock())
    ws.service = Mock()
    ws.service.export_rows.return_value = [row()]
    selected = PurchaseReportFilters.parse(period="yearly", period_start="2026-01-01", today=TODAY)
    with patch.object(ws, "_filters", return_value=selected):
        response = ws.export_response(user(), {})
    rows = list(csv.DictReader(StringIO(response.body.decode("utf-8-sig"))))
    assert len(rows) == 1
    assert "Stored invoice total" not in rows[0]
    assert rows[0]["Invoice number"] == "'=SUM(1,1)"
    assert rows[0]["Net amount including tax"] == "-0.265430"
    assert rows[0]["Invoice description"] == "<script>alert(1)</script>"
    assert rows[0]["Period"] == "yearly"
    assert response.headers["cache-control"] == "no-store"
    ws.service.export_rows.return_value = [{
        "invoice_date": TODAY, "invoice_number": "=1+1", "currency_code": "NGN",
        "matching_line_count": 1, "full_line_count": 2, "matching_total": Decimal("10"),
        "full_line_total": Decimal("25"), "invoice_total": Decimal("26"), "difference": Decimal("1"),
    }]
    with patch.object(ws, "_filters", return_value=selected):
        response = ws.export_response(user(), {}, "invoices")
    records = list(csv.DictReader(StringIO(response.body.decode("utf-8-sig"))))
    assert len(records) == 1 and records[0]["Stored invoice total"] == "26"
    assert records[0]["Reconciliation difference"] == "1"
    assert records[0]["Invoice number"] == "'=1+1"
    ws.service.export_rows.side_effect = ValueError("Export exceeds limit")
    with pytest.raises(HTTPException) as exc:
        ws.export_response(user(), {})
    assert exc.value.status_code == 400


@pytest.mark.parametrize("endpoint", ["purchases", "purchases/export", "weekly-purchases", "weekly-purchases/export"])
def test_both_url_families_require_ap_permission(endpoint):
    app = FastAPI()
    app.include_router(router)
    principal = user(ap=False)
    app.dependency_overrides[router.dependencies[0].dependency] = lambda: principal
    app.dependency_overrides[require_inventory_access] = lambda: principal
    app.dependency_overrides[get_db_for_org] = lambda: Mock()
    with TestClient(app) as client:
        assert client.get(f"/inventory/reports/{endpoint}").status_code == 403


def test_canonical_http_period_params_and_legacy_week_mapping():
    app = FastAPI()
    app.include_router(router)
    principal = user()
    app.dependency_overrides[router.dependencies[0].dependency] = lambda: principal
    app.dependency_overrides[require_inventory_access] = lambda: principal
    app.dependency_overrides[get_db_for_org] = lambda: Mock()
    with (
        patch(f"{MODULE}.base_context", return_value={}),
        patch(f"{MODULE}.templates") as templates,
        patch(f"{MODULE}.PurchaseReportService") as query,
        TestClient(app) as client,
    ):
        templates.TemplateResponse.return_value = HTMLResponse("Purchase Report")
        query.return_value.report.return_value = report_data()
        query.return_value.options.return_value = {"suppliers": [], "warehouses": [], "categories": []}
        query.return_value.export_rows.return_value = []
        for kind in ("weekly", "monthly", "quarterly", "yearly"):
            response = client.get(f"/inventory/reports/purchases?period={kind}&period_start=2024-02-15&search=cable")
            assert response.status_code == 200
            filters = query.return_value.report.call_args.args[1]
            assert filters.period.kind == kind and filters.search == "cable"
        response = client.get("/inventory/reports/weekly-purchases?week_start=2024-02-15&search=delivery")
        assert response.status_code == 200
        filters = query.return_value.report.call_args.args[1]
        assert filters.period.start == date(2024, 2, 12) and filters.search == "delivery"
        assert client.get("/inventory/reports/purchases/export?view=invoices&period=yearly&period_start=2024-01-01").status_code == 200
        assert client.get("/inventory/reports/purchases?period=invalid").status_code == 400
        assert client.get("/inventory/reports/purchases?page=0").status_code == 422
        assert client.get("/inventory/reports/purchases?search=" + "x" * 101).status_code == 422
