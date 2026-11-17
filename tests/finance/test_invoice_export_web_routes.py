"""Invoice web actions use XLSX defaults without changing other bulk exports."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4
import json

import pytest
from fastapi import Request, Response

pytest.importorskip("dotmac_files")
pytest.importorskip("dotmac_kernel")

from app.services.finance.ap import invoice_bulk as ap_bulk  # noqa: E402
from app.services.finance.ap.web import ap_web_service  # noqa: E402
from app.services.finance.ar import invoice_bulk as ar_bulk  # noqa: E402
from app.services.finance.ar.web import ar_web_service  # noqa: E402
from app.services.finance.rpt.report_instance import ReportInstanceService  # noqa: E402
from app.web.finance import ap as ap_routes  # noqa: E402
from app.web.finance import ar as ar_routes  # noqa: E402


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "web_service,bulk_module,factory_name",
    [
        (ap_web_service, ap_bulk, "get_ap_invoice_bulk_service"),
        (ar_web_service, ar_bulk, "get_ar_invoice_bulk_service"),
    ],
)
@pytest.mark.parametrize("requested_format,expected", [(None, "xlsx"), ("csv", "csv")])
async def test_bulk_invoice_route_default_and_explicit_format(
    monkeypatch, web_service, bulk_module, factory_name, requested_format, expected
):
    invoice_id = uuid4()
    body = {"ids": [str(invoice_id)]}
    if requested_format:
        body["format"] = requested_format
    request = SimpleNamespace(json=AsyncMock(return_value=body))
    auth = SimpleNamespace(organization_id=uuid4(), user_id=uuid4())
    service = SimpleNamespace(bulk_export=AsyncMock(return_value="file"))
    monkeypatch.setattr(bulk_module, factory_name, lambda *args: service)

    result = await web_service.bulk_export_invoices_response(request, auth, MagicMock())

    assert result == "file"
    service.bulk_export.assert_awaited_once_with([invoice_id], expected)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "module,web_service", [(ap_routes, ap_web_service), (ar_routes, ar_web_service)]
)
async def test_immediate_invoice_route_passes_format_and_applied_filters(
    monkeypatch, module, web_service
):
    export = AsyncMock(return_value="file")
    monkeypatch.setattr(web_service, "export_all_invoices_response", export)
    request = Request({"type": "http", "query_string": b"date_from=2026-01-01"})
    auth, db = SimpleNamespace(), MagicMock()
    kwargs = dict(request=request, search="Acme", status="POSTED", auth=auth, db=db)
    if module is ap_routes:
        result = await module.export_all_ap_invoices(
            supplier_id=str(uuid4()),
            start_date="2026-01-01",
            end_date="2026-01-31",
            format="csv",
            invoice_type="CREDIT_NOTE",
            **kwargs,
        )
        assert export.await_args.args[4:6] == ("2026-01-01", "2026-01-31")
        assert export.await_args.args[-2:] == ("csv", "CREDIT_NOTE")
    else:
        result = await module.export_all_invoices(
            customer_id=str(uuid4()),
            start_date="",
            end_date="2026-01-31",
            format="csv",
            **kwargs,
        )
        assert export.await_args.args[4:6] == ("2026-01-01", "2026-01-31")
        assert export.await_args.args[-1] == "csv"
    assert result == "file"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "module,route_name,party_key",
    [
        (ap_routes, "queue_ap_invoices_export", "supplier_id"),
        (ar_routes, "queue_invoices_export", "customer_id"),
    ],
)
async def test_queued_invoice_route_preserves_filters_and_defaults_to_xlsx(
    monkeypatch, module, route_name, party_key
):
    queued = MagicMock(return_value=SimpleNamespace(instance_id=uuid4()))
    monkeypatch.setattr(module, "queue_background_export", queued)
    bulk_service = SimpleNamespace(count_export_rows=MagicMock(return_value=5001))
    factory_name = (
        "get_ap_invoice_bulk_service"
        if module is ap_routes
        else "get_ar_invoice_bulk_service"
    )
    monkeypatch.setattr(module, factory_name, lambda *args: bulk_service)
    auth = SimpleNamespace(organization_id=uuid4(), user_id=uuid4())
    kwargs = {
        "search": "Acme",
        "status": "POSTED",
        party_key: str(uuid4()),
        "start_date": "2026-01-01",
        "end_date": "2026-01-31",
        "format": "xlsx",
        "auth": auth,
        "db": MagicMock(),
    }
    if module is ar_routes:
        kwargs["request"] = Request({"type": "http", "query_string": b""})
    response = await getattr(module, route_name)(**kwargs)
    assert response.status_code == 202
    assert json.loads(response.body)["status_url"].endswith("/status")
    assert queued.call_args.kwargs["output_format"] == "xlsx"
    assert queued.call_args.kwargs["parameters"] == {
        "search": "Acme",
        "status": "POSTED",
        party_key: kwargs[party_key],
        "start_date": "2026-01-01",
        "end_date": "2026-01-31",
        **({"invoice_type": ""} if module is ap_routes else {}),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "module,route_name,party_key",
    [
        (ap_routes, "queue_ap_invoices_export", "supplier_id"),
        (ar_routes, "queue_invoices_export", "customer_id"),
    ],
)
async def test_small_invoice_export_returns_xlsx_without_queue(
    monkeypatch, module, route_name, party_key
):
    workbook = Response(
        b"PK",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    bulk_service = SimpleNamespace(
        count_export_rows=MagicMock(return_value=5000),
        export_all=AsyncMock(return_value=workbook),
    )
    factory_name = (
        "get_ap_invoice_bulk_service"
        if module is ap_routes
        else "get_ar_invoice_bulk_service"
    )
    monkeypatch.setattr(module, factory_name, lambda *args: bulk_service)
    queued = MagicMock()
    monkeypatch.setattr(module, "queue_background_export", queued)
    auth = SimpleNamespace(organization_id=uuid4(), user_id=uuid4())
    kwargs = {
        "search": "INV-123",
        "status": "POSTED",
        party_key: str(uuid4()),
        "start_date": "2026-01-01",
        "end_date": "2026-01-31",
        "format": "xlsx",
        "auth": auth,
        "db": MagicMock(),
    }
    if module is ar_routes:
        kwargs["request"] = Request({"type": "http", "query_string": b""})

    response = await getattr(module, route_name)(**kwargs)

    assert response is workbook
    assert bulk_service.count_export_rows.call_args.args[0:2] == (
        "INV-123",
        "POSTED",
    )
    bulk_service.export_all.assert_awaited_once()
    queued.assert_not_called()


def test_ar_customer_names_are_loaded_once_for_current_organization():
    organization_id, customer_id = uuid4(), uuid4()
    db = MagicMock()
    db.execute.return_value.all.return_value = [
        (customer_id, "Trading Name", "Legal Name")
    ]
    service = ar_bulk.ARInvoiceBulkService(db, organization_id)
    assert service._resolve_customer_name(customer_id) == "Trading Name"
    assert service._resolve_customer_name(customer_id) == "Trading Name"
    db.execute.assert_called_once()
    assert organization_id in db.execute.call_args.args[0].compile().params.values()


def test_ap_invoices_can_create_a_queued_report_definition():
    definition = ReportInstanceService._standard_definition("AP_INVOICES")

    assert definition is not None
    assert definition["report_code"] == "AP_INVOICES"
    assert definition["data_source_type"] == "AP"
