"""HTTP adapters, CSV safety and template regressions for weekly purchases."""

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

from app.models.finance.ap.supplier_invoice import (
    SupplierInvoiceStatus,
    SupplierInvoiceType,
)
from app.services.inventory.weekly_purchases import PurchaseFilters, format_report_number
from app.services.inventory.weekly_purchases_web import WeeklyPurchasesWebService
from app.web.deps import get_db_for_org, require_inventory_access
from app.web.inventory_weekly_purchases import router

MODULE = "app.services.inventory.weekly_purchases_web"
TODAY = date(2026, 9, 15)


def auth(*, finance=False, invoice_permission=False):
    return SimpleNamespace(
        organization_id=uuid4(),
        has_module_access=lambda module: finance,
        has_permission=lambda permission: invoice_permission,
    )


def row():
    return {
        "line_id": uuid4(),
        "invoice_id": uuid4(),
        "invoice_date": TODAY,
        "invoice_number": "=SUM(1,1)",
        "invoice_type": SupplierInvoiceType.CREDIT_NOTE,
        "status": SupplierInvoiceStatus.POSTED,
        "supplier_name": "@Supplier",
        "item_id": uuid4(),
        "item_code": "CODE",
        "item_name": "<script>alert(1)</script>",
        "category_name": None,
        "warehouse_name": None,
        "quantity": Decimal("2"),
        "unit_price": Decimal("0.123456"),
        "currency_code": "NGN",
        "net_amount": Decimal("-0.246912"),
        "net_tax": Decimal("-0.018518"),
    }


def selected():
    return PurchaseFilters.parse(
        week_start="2026-09-15", search="cable & fibre", today=TODAY
    )


def test_csv_preserves_filters_precision_and_safe_labels():
    ws = WeeklyPurchasesWebService(Mock())
    ws.service = Mock()
    ws.service.export_rows.return_value = [row()]
    user = auth()
    with patch.object(ws, "_filters", return_value=selected()):
        response = ws.export_response(user, {"search": "cable & fibre"})
    ws.service.export_rows.assert_called_once_with(user.organization_id, selected())
    assert response.headers["cache-control"] == "no-store"
    assert "2026-09-14.csv" in response.headers["content-disposition"]
    assert response.body.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(StringIO(response.body.decode("utf-8-sig"))))
    assert len(rows) == 2
    assert len(rows[0]) == len(rows[1]) == 17
    assert rows[1][3] == "'=SUM(1,1)"
    assert rows[1][6] == "'@Supplier"
    assert rows[1][12] == "0.123456"
    assert rows[1][14:] == ["-0.246912", "-0.018518", "-0.265430"]


def test_empty_csv_has_headers_and_oversize_export_is_not_partial():
    ws = WeeklyPurchasesWebService(Mock())
    ws.service = Mock()
    with patch.object(ws, "_filters", return_value=selected()):
        ws.service.export_rows.return_value = []
        response = ws.export_response(auth(), {})
        assert len(list(csv.reader(StringIO(response.body.decode("utf-8-sig"))))) == 1
        ws.service.export_rows.side_effect = ValueError("This export exceeds 50,000 lines.")
        with pytest.raises(HTTPException) as exc:
            ws.export_response(auth(), {})
        assert exc.value.status_code == 400


def test_invalid_filters_and_missing_organization_fail_before_querying():
    ws = WeeklyPurchasesWebService(Mock())
    ws.service = Mock()
    with pytest.raises(HTTPException) as exc:
        ws.export_response(auth(), {"supplier": "invalid"})
    assert exc.value.status_code == 400
    user = auth()
    user.organization_id = None
    with pytest.raises(HTTPException) as exc:
        ws.export_response(user, {})
    assert exc.value.status_code == 403
    ws.service.export_rows.assert_not_called()


def report_context(ws, *, finance=False, invoice_permission=False, rows=None):
    ws.service = Mock()
    ws.service.report.return_value = {
        "purchase_rows": rows or [],
        "summary": {
            "document_count": 1,
            "item_count": 1,
            "supplier_count": 1,
            "line_count": len(rows or []),
        },
        "currency_totals": [],
        "total_count": len(rows or []),
        "page": 1,
        "total_pages": 1,
    }
    ws.service.options.return_value = {
        "suppliers": [], "warehouses": [], "categories": []
    }
    request = Mock()
    user = auth(finance=finance, invoice_permission=invoice_permission)
    with (
        patch.object(ws, "_filters", return_value=selected()),
        patch(f"{MODULE}.base_context", return_value={}),
        patch(f"{MODULE}.templates") as templates,
    ):
        templates.TemplateResponse.return_value = HTMLResponse("report")
        response = ws.report_response(request, user, {}, page=1)
        context = templates.TemplateResponse.call_args.args[2]
    assert response.headers["cache-control"] == "no-store"
    ws.service.report.assert_called_once_with(user.organization_id, selected(), page=1)
    return context


def test_page_navigation_and_export_keep_filters_and_invoice_access():
    context = report_context(WeeklyPurchasesWebService(Mock()), finance=True)
    assert context["can_view_invoice"] is False
    for name in (
        "previous_week_url", "current_week_url", "export_url", "next_page_url"
    ):
        assert parse_qs(urlparse(context[name]).query)["search"] == ["cable & fibre"]
    assert context["next_week_url"] is None
    assert "search" not in parse_qs(urlparse(context["reset_url"]).query)
    context = report_context(
        WeeklyPurchasesWebService(Mock()), finance=True, invoice_permission=True
    )
    assert context["can_view_invoice"] is True


@pytest.mark.parametrize("has_rows", [False, True])
def test_template_empty_state_and_autoescaping(has_rows):
    context = report_context(
        WeeklyPurchasesWebService(Mock()), rows=[row()] if has_rows else []
    )
    # Isolate this template from host-shell/private UI package dependencies.
    stubs = {
        "inventory/base_inventory.html": "{% block content %}{% endblock %}",
        "components/macros.html": (
            "{% macro icon_svg(name, classes='') %}{% endmacro %}"
            "{% macro status_badge(status, size='md') %}{{ status }}{% endmacro %}"
            "{% macro empty_state(title, description='', icon='') %}"
            "{{ title }} {{ description }}{% endmacro %}"
        ),
    }
    template_dir = Path(__file__).resolve().parents[1] / "templates"
    env = Environment(
        loader=ChoiceLoader([DictLoader(stubs), FileSystemLoader(str(template_dir))]),
        autoescape=True,
        undefined=StrictUndefined,
    )
    context["format_report_number"] = format_report_number
    html = env.get_template("inventory/report_weekly_purchases.html").render(**context)
    assert 'id="purchase-report"' in html
    assert 'id="results-container"' in html
    assert 'hx-select="#purchase-report"' in html
    assert "<script>alert(1)</script>" not in html
    assert "/finance/ap/invoices/" not in html
    if has_rows:
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    else:
        assert "No purchases" in html


def test_both_http_endpoints_use_permissions_and_tenant_db_dependency():
    app = FastAPI()
    app.include_router(router)
    user, db = auth(), Mock()
    permission = router.dependencies[0].dependency
    app.dependency_overrides[permission] = lambda: user
    app.dependency_overrides[require_inventory_access] = lambda: user
    app.dependency_overrides[get_db_for_org] = lambda: db
    with patch("app.web.inventory_weekly_purchases.WeeklyPurchasesWebService") as factory:
        factory.return_value.report_response.return_value = HTMLResponse("page")
        factory.return_value.export_response.return_value = HTMLResponse("csv")
        with TestClient(app) as client:
            for suffix in ("", "/export"):
                response = client.get(
                    f"/inventory/reports/weekly-purchases{suffix}?search=cable"
                )
                assert response.status_code == 200
            factory.assert_called_with(db)
            assert factory.return_value.export_response.call_args.kwargs["filters"] == {
                "week_start": None,
                "supplier": None,
                "warehouse": None,
                "category": None,
                "search": "cable",
            }
            assert client.get("/inventory/reports/weekly-purchases?page=0").status_code == 422
            assert client.get(
                "/inventory/reports/weekly-purchases?search=" + "x" * 101
            ).status_code == 422

            def forbidden():
                raise HTTPException(status_code=403)

            for dependency in (permission, require_inventory_access):
                app.dependency_overrides[dependency] = forbidden
                for suffix in ("", "/export"):
                    assert client.get(
                        f"/inventory/reports/weekly-purchases{suffix}"
                    ).status_code == 403
                app.dependency_overrides[dependency] = lambda: user
