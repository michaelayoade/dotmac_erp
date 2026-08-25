from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal
from datetime import date
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from starlette.datastructures import FormData
from starlette.datastructures import UploadFile

from app.services.operations.inv_web import (
    OperationsInventoryWebService,
    StockMovementRequestContext,
)
from app.web.inventory import router as inventory_router


def test_low_stock_report_route_is_registered() -> None:
    route = next(
        route
        for route in inventory_router.routes
        if route.path == "/inventory/reports/low-stock"
    )

    assert "GET" in route.methods


def test_low_stock_report_response_uses_existing_tenant_context(monkeypatch) -> None:
    service = OperationsInventoryWebService()
    request = MagicMock()
    db = MagicMock()
    org_id = uuid.uuid4()
    auth = MagicMock(organization_id=org_id)
    captured: dict[str, object] = {}

    def fake_low_stock_context(**kwargs):
        captured["context_kwargs"] = kwargs
        return {
            "items": [],
            "total_items": 0,
            "critical_count": 0,
            "low_count": 0,
            "warning_count": 0,
            "total_suggested_value": "₦0.00",
            "include_below_minimum": False,
        }

    def fake_template_response(request_arg, template_name, context):
        captured["request"] = request_arg
        captured["template_name"] = template_name
        captured["context"] = context
        return "response"

    monkeypatch.setattr(
        "app.services.operations.inv_web.base_context",
        lambda request_arg, auth_arg, title, section: {
            "title": title,
            "section": section,
        },
    )
    monkeypatch.setattr(
        "app.services.inventory.web.InventoryWebService.low_stock_dashboard_context",
        fake_low_stock_context,
    )
    monkeypatch.setattr(
        "app.services.operations.inv_web.templates.TemplateResponse",
        fake_template_response,
    )

    response = service.low_stock_report_response(
        request=request,
        auth=auth,
        db=db,
        include_below_minimum=False,
    )

    assert response == "response"
    assert captured["request"] is request
    assert captured["template_name"] == "inventory/report_low_stock.html"
    assert captured["context_kwargs"] == {
        "db": db,
        "organization_id": str(org_id),
        "include_below_minimum": False,
    }
    assert captured["context"] == {
        "title": "Low Stock Alert",
        "section": "reports",
        "items": [],
        "total_items": 0,
        "critical_count": 0,
        "low_count": 0,
        "warning_count": 0,
        "total_suggested_value": "₦0.00",
        "include_below_minimum": False,
    }


def test_extract_uploads_returns_multiple_images() -> None:
    first = UploadFile(
        filename="one.png",
        file=BytesIO(b"one"),
        headers={"content-type": "image/png"},
    )
    second = UploadFile(
        filename="two.webp",
        file=BytesIO(b"two"),
        headers={"content-type": "image/webp"},
    )

    uploads = OperationsInventoryWebService._extract_uploads(
        FormData([("images", first), ("images", second)]),
        "images",
    )

    assert uploads == [first, second]


def test_validate_return_image_uploads_accepts_supported_images() -> None:
    upload = UploadFile(
        filename="evidence.png",
        file=BytesIO(b"image-bytes"),
        headers={"content-type": "image/png"},
    )

    OperationsInventoryWebService._validate_return_image_uploads([upload])


def test_validate_return_image_uploads_rejects_non_images() -> None:
    upload = UploadFile(
        filename="evidence.pdf",
        file=BytesIO(b"pdf-bytes"),
        headers={"content-type": "application/pdf"},
    )

    with pytest.raises(ValueError, match="Only image files are allowed"):
        OperationsInventoryWebService._validate_return_image_uploads([upload])


def test_inventory_valuation_report_response_uses_inventory_template(
    monkeypatch,
) -> None:
    service = OperationsInventoryWebService()
    request = MagicMock()
    db = MagicMock()
    org_id = uuid.uuid4()
    auth = MagicMock(organization_id=org_id)

    captured: dict[str, object] = {}

    def fake_template_response(request_arg, template_name, context):
        captured["request"] = request_arg
        captured["template_name"] = template_name
        captured["context"] = context
        return "response"

    monkeypatch.setattr(
        "app.services.operations.inv_web.base_context",
        lambda request_arg, auth_arg, title, section: {
            "title": title,
            "section": section,
            "organization_id": str(auth_arg.organization_id),
        },
    )
    monkeypatch.setattr(
        "app.services.finance.rpt.inventory_valuation."
        "inventory_valuation_reconciliation_context",
        lambda db_arg, organization_id, **kwargs: {
            "has_data": True,
            "fiscal_period_id": "period-1",
            "inventory_total": "NGN 100.00",
            "gl_total": "NGN 100.00",
            "difference": "NGN 0.00",
            "is_balanced": True,
            "valuation_rows": [],
            "valuation_row_count": 0,
            "valuation_mismatch_count": 0,
        },
    )
    monkeypatch.setattr(
        "app.services.operations.inv_web.templates.TemplateResponse",
        fake_template_response,
    )

    response = service.inventory_valuation_report_response(request, auth, db)

    assert response == "response"
    assert captured["request"] is request
    assert captured["template_name"] == "inventory/report_inventory_valuation.html"
    assert captured["context"] == {
        "title": "Inventory Valuation",
        "section": "reports",
        "organization_id": str(org_id),
        "has_data": True,
        "fiscal_period_id": "period-1",
        "inventory_total": "NGN 100.00",
        "gl_total": "NGN 100.00",
        "difference": "NGN 0.00",
        "is_balanced": True,
        "valuation_rows": [],
        "valuation_row_count": 0,
        "valuation_mismatch_count": 0,
    }


def test_wac_breakdown_report_response_uses_breakdown_template(monkeypatch) -> None:
    service = OperationsInventoryWebService()
    request = MagicMock()
    db = MagicMock()
    org_id = uuid.uuid4()
    item_id = uuid.uuid4()
    warehouse_id = uuid.uuid4()
    auth = MagicMock(organization_id=org_id)

    captured: dict[str, object] = {}

    def fake_template_response(request_arg, template_name, context):
        captured["request"] = request_arg
        captured["template_name"] = template_name
        captured["context"] = context
        return "response"

    monkeypatch.setattr(
        "app.services.operations.inv_web.base_context",
        lambda request_arg, auth_arg, title, section: {
            "title": title,
            "section": section,
            "organization_id": str(auth_arg.organization_id),
        },
    )
    monkeypatch.setattr(
        "app.services.finance.rpt.inventory_valuation.wac_breakdown_context",
        lambda db_arg, organization_id, **kwargs: {
            "item_id": kwargs["item_id"],
            "warehouse_id": kwargs["warehouse_id"],
            "item_code": "ITEM-001",
            "item_name": "Tracked Item",
            "warehouse_name": "Stores",
            "quantity_on_hand": "10",
            "current_wac": "NGN 12.00",
            "inventory_value": "NGN 120.00",
            "gl_value": "NGN 120.00",
            "difference": "NGN 0.00",
            "is_balanced": True,
            "wac_breakdown_rows": [],
            "wac_breakdown_row_count": 0,
        },
    )
    monkeypatch.setattr(
        "app.services.operations.inv_web.templates.TemplateResponse",
        fake_template_response,
    )

    response = service.wac_breakdown_report_response(
        request,
        auth,
        db,
        str(item_id),
        str(warehouse_id),
    )

    assert response == "response"
    assert captured["request"] is request
    assert captured["template_name"] == "inventory/report_wac_breakdown.html"
    assert captured["context"]["title"] == "WAC Breakdown"
    assert captured["context"]["item_id"] == str(item_id)
    assert captured["context"]["warehouse_id"] == str(warehouse_id)


def test_inventory_valuation_mismatch_notifies_admin_and_inventory_manager(
    monkeypatch,
) -> None:
    service = OperationsInventoryWebService()
    org_id = uuid.uuid4()
    period_id = uuid.uuid4()
    recipient_id = uuid.uuid4()
    auth = MagicMock(organization_id=org_id)
    db = MagicMock()
    db.scalars.return_value.all.return_value = [recipient_id]

    captured: dict[str, object] = {}

    def fake_create_if_not_sent_since(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return MagicMock()

    monkeypatch.setattr(
        "app.services.notification.NotificationService.create_if_not_sent_since",
        fake_create_if_not_sent_since,
    )

    sent = service._notify_inventory_valuation_mismatch(
        db,
        auth,
        {
            "has_data": True,
            "is_balanced": False,
            "fiscal_period_id": str(period_id),
            "difference": "NGN 10.00",
            "valuation_mismatch_count": 2,
        },
    )

    assert sent == 1
    assert db.commit.called
    kwargs = captured["kwargs"]
    assert kwargs["organization_id"] == org_id
    assert kwargs["recipient_id"] == recipient_id
    assert kwargs["entity_id"] == period_id
    assert kwargs["title"] == "Inventory valuation mismatch detected"
    assert kwargs["channel"].value == "IN_APP"
    assert kwargs["action_url"] == "/inventory/reports/valuation"


def test_export_inventory_valuation_csv_response_exports_summary_rows(
    monkeypatch,
) -> None:
    service = OperationsInventoryWebService()
    org_id = uuid.uuid4()
    auth = MagicMock(organization_id=org_id)
    db = MagicMock()

    monkeypatch.setattr(
        "app.services.finance.rpt.inventory_valuation."
        "inventory_valuation_reconciliation_context",
        lambda db_arg, organization_id: {
            "has_data": True,
            "fiscal_period_id": "period-1",
            "inventory_total": "NGN 120.00",
            "gl_total": "NGN 120.00",
            "difference": "NGN 0.00",
            "is_balanced": True,
            "valuation_rows": [
                {
                    "item_code": "ITEM-001",
                    "item_name": "Tracked Item",
                    "warehouse_name": "Stores",
                    "quantity_on_hand": "10",
                    "current_wac": "NGN 12.00",
                    "inventory_value": "NGN 120.00",
                    "gl_value": "NGN 120.00",
                    "difference": "NGN 0.00",
                    "is_balanced": True,
                }
            ],
            "valuation_row_count": 1,
            "valuation_mismatch_count": 0,
        },
    )

    response = service.export_inventory_valuation_csv_response(auth, db)

    body = response.body.decode()
    assert response.media_type == "text/csv"
    assert (
        'filename="inventory_valuation_summary_period-1.csv"'
        in response.headers["Content-Disposition"]
    )
    assert "Inventory Valuation Summary" in body
    assert "Inventory Value,NGN 120.00" in body
    assert "Item Code,Item Name,Warehouse,Quantity On Hand" in body
    assert (
        "ITEM-001,Tracked Item,Stores,10,NGN 12.00,NGN 120.00,NGN 120.00,NGN 0.00,Matched"
        in body
    )


def test_export_wac_breakdown_csv_response_exports_selected_item(monkeypatch) -> None:
    service = OperationsInventoryWebService()
    org_id = uuid.uuid4()
    item_id = uuid.uuid4()
    warehouse_id = uuid.uuid4()
    auth = MagicMock(organization_id=org_id)
    db = MagicMock()

    detail_row = MagicMock()
    detail_row.item_id = item_id
    detail_row.warehouse_id = warehouse_id
    detail_row.item_code = "ITEM-001"
    detail_row.item_name = "Tracked Item"
    detail_row.warehouse_name = "Stores"

    breakdown_row = MagicMock()
    breakdown_row.transaction_date = date(2026, 5, 1)
    breakdown_row.transaction_type = "RECEIPT"
    breakdown_row.reference = "MAT-001"
    breakdown_row.quantity_in = Decimal("2")
    breakdown_row.quantity_out = Decimal("0")
    breakdown_row.unit_cost = Decimal("10")
    breakdown_row.value_in = Decimal("20")
    breakdown_row.value_out = Decimal("0")
    breakdown_row.quantity_after = Decimal("2")
    breakdown_row.wac_after = Decimal("10")
    breakdown_row.total_value_after = Decimal("20")

    monkeypatch.setattr(
        "app.services.inventory.valuation_reconciliation."
        "ValuationReconciliationService.reconcile",
        lambda self, organization_id: MagicMock(fiscal_period_id=uuid.uuid4()),
    )
    monkeypatch.setattr(
        "app.services.inventory.valuation_reconciliation."
        "ValuationReconciliationService.detail_rows",
        lambda self, organization_id, fiscal_period_id, limit=100: [detail_row],
    )
    monkeypatch.setattr(
        "app.services.inventory.wac_valuation.WACValuationService.breakdown_rows",
        lambda self, organization_id, selected_item_id, selected_warehouse_id, limit=250: [
            breakdown_row
        ],
    )

    response = service.export_wac_breakdown_csv_response(
        auth,
        db,
        item_id=str(item_id),
        warehouse_id=str(warehouse_id),
    )

    body = response.body.decode()
    assert response.media_type == "text/csv"
    assert (
        'filename="wac_breakdown_ITEM-001_' in response.headers["Content-Disposition"]
    )
    assert "Item Code,Item Name,Warehouse,Transaction Date" in body
    assert (
        "ITEM-001,Tracked Item,Stores,2026-05-01,Receipt,MAT-001,2,0,10,20,0,2,10,20"
        in body
    )


def test_export_wac_breakdown_pdf_response_returns_pdf(monkeypatch) -> None:
    service = OperationsInventoryWebService()
    org_id = uuid.uuid4()
    item_id = uuid.uuid4()
    warehouse_id = uuid.uuid4()
    auth = MagicMock(organization_id=org_id)
    db = MagicMock()

    captured: dict[str, object] = {}

    def fake_export_rows(self, auth_arg, db_arg, item_id=None, warehouse_id=None):
        return (
            [
                {
                    "item_code": "ITEM-001",
                    "item_name": "Tracked Item",
                    "warehouse_name": "Stores",
                    "transaction_date": "2026-05-01",
                    "transaction_type": "Receipt",
                    "reference": "MAT-001",
                    "quantity_in": "2",
                    "quantity_out": "0",
                    "unit_cost": "10",
                    "value_in": "20",
                    "value_out": "0",
                    "quantity_after": "2",
                    "wac_after": "10",
                    "total_value_after": "20",
                }
            ],
            "wac_breakdown_ITEM-001_stores",
        )

    def fake_render(self, report_name, organization_id, context):
        captured["report_name"] = report_name
        captured["organization_id"] = organization_id
        captured["context"] = context
        return b"%PDF-1.4"

    monkeypatch.setattr(
        OperationsInventoryWebService,
        "_wac_breakdown_export_rows",
        fake_export_rows,
    )
    monkeypatch.setattr(
        "app.services.finance.rpt.pdf.ReportPDFService.render",
        fake_render,
    )
    response = service.export_wac_breakdown_pdf_response(
        auth,
        db,
        item_id=str(item_id),
        warehouse_id=str(warehouse_id),
    )

    assert response.media_type == "application/pdf"
    assert response.body == b"%PDF-1.4"
    assert (
        response.headers["Content-Disposition"]
        == 'attachment; filename="wac_breakdown_ITEM-001_stores.pdf"'
    )
    assert captured["report_name"] == "wac_breakdown"
    assert captured["organization_id"] == str(org_id)
    assert captured["context"]["scope_label"] == "Selected Item"
    assert captured["context"]["row_count"] == 1


def test_export_wac_breakdown_pdf_response_requires_selected_item() -> None:
    from fastapi import HTTPException

    service = OperationsInventoryWebService()
    auth = MagicMock(organization_id=uuid.uuid4())
    db = MagicMock()

    with pytest.raises(HTTPException) as exc_info:
        service.export_wac_breakdown_pdf_response(auth, db)

    assert exc_info.value.status_code == 400
    assert "selected item and warehouse" in exc_info.value.detail


def test_fifo_layers_report_response_uses_fifo_template(monkeypatch) -> None:
    service = OperationsInventoryWebService()
    request = MagicMock()
    db = MagicMock()
    org_id = uuid.uuid4()
    auth = MagicMock(organization_id=org_id)

    captured: dict[str, object] = {}

    def fake_template_response(request_arg, template_name, context):
        captured["request"] = request_arg
        captured["template_name"] = template_name
        captured["context"] = context
        return "response"

    monkeypatch.setattr(
        "app.services.operations.inv_web.base_context",
        lambda request_arg, auth_arg, title, section: {
            "title": title,
            "section": section,
            "organization_id": str(auth_arg.organization_id),
        },
    )
    monkeypatch.setattr(
        "app.services.operations.inv_web.templates.TemplateResponse",
        fake_template_response,
    )

    class _FakeScalarResult:
        def __init__(self, values):
            self._values = values

        def all(self):
            return self._values

    warehouse_obj = MagicMock()
    warehouse_obj.warehouse_id = uuid.uuid4()
    warehouse_obj.warehouse_name = "Main Warehouse"
    warehouse_obj.warehouse_code = "MAIN"

    item_obj = MagicMock()
    item_obj.item_id = uuid.uuid4()
    item_obj.item_code = "ITEM-001"
    item_obj.item_name = "FIFO Item"

    lot_obj = MagicMock()
    lot_obj.received_date = "2026-04-01"
    lot_obj.lot_number = "FIFO-20260401-ABC123"
    lot_obj.allocation_reference = "GRN-001"

    balance_obj = MagicMock()
    balance_obj.quantity_on_hand = 10
    balance_obj.quantity_available = 8
    balance_obj.quantity_allocated = 2

    db.scalars.side_effect = [
        _FakeScalarResult([warehouse_obj]),
        _FakeScalarResult([item_obj]),
    ]
    db.scalar.side_effect = [1, 1, 10, 250]
    db.execute.return_value.all.return_value = [
        (balance_obj, lot_obj, item_obj, warehouse_obj)
    ]

    response = service.fifo_layers_report_response(request, auth, db)

    assert response == "response"
    assert captured["request"] is request
    assert captured["template_name"] == "inventory/report_fifo_layers.html"
    context = captured["context"]
    assert isinstance(context, dict)
    assert context["title"] == "FIFO Layers"
    assert context["section"] == "reports"
    assert context["summary"]["total_layers"] == 1
    assert len(context["layers"]) == 1


def test_stock_aging_report_response_uses_aging_template(monkeypatch) -> None:
    service = OperationsInventoryWebService()
    request = MagicMock()
    db = MagicMock()
    org_id = uuid.uuid4()
    auth = MagicMock(organization_id=org_id)

    captured: dict[str, object] = {}

    def fake_template_response(request_arg, template_name, context):
        captured["request"] = request_arg
        captured["template_name"] = template_name
        captured["context"] = context
        return "response"

    monkeypatch.setattr(
        "app.services.operations.inv_web.base_context",
        lambda request_arg, auth_arg, title, section: {
            "title": title,
            "section": section,
            "organization_id": str(auth_arg.organization_id),
        },
    )
    monkeypatch.setattr(
        "app.services.operations.inv_web.templates.TemplateResponse",
        fake_template_response,
    )

    class _FakeScalarResult:
        def __init__(self, values):
            self._values = values

        def all(self):
            return self._values

    warehouse_obj = MagicMock()
    warehouse_obj.warehouse_id = uuid.uuid4()
    warehouse_obj.warehouse_name = "Main Warehouse"
    warehouse_obj.warehouse_code = "MAIN"

    item_obj = MagicMock()
    item_obj.item_id = uuid.uuid4()
    item_obj.item_code = "ITEM-001"
    item_obj.item_name = "Tracked Item"

    lot_obj = MagicMock()
    lot_obj.received_date = date(2026, 3, 1)
    lot_obj.lot_number = "LOT-001"
    lot_obj.allocation_reference = "GRN-001"

    balance_obj = MagicMock()
    balance_obj.quantity_on_hand = 10

    db.scalars.side_effect = [
        _FakeScalarResult([warehouse_obj]),
        _FakeScalarResult([item_obj]),
    ]
    db.execute.return_value.all.return_value = [
        (balance_obj, lot_obj, item_obj, warehouse_obj)
    ]

    response = service.stock_aging_report_response(request, auth, db)

    assert response == "response"
    assert captured["request"] is request
    assert captured["template_name"] == "inventory/report_stock_aging.html"
    context = captured["context"]
    assert isinstance(context, dict)
    assert context["title"] == "Stock Aging"
    assert context["section"] == "reports"
    assert "summary" in context


def test_stock_movement_report_response_uses_movement_template(monkeypatch) -> None:
    service = OperationsInventoryWebService()
    request = MagicMock()
    db = MagicMock()
    org_id = uuid.uuid4()
    auth = MagicMock(organization_id=org_id)

    captured: dict[str, object] = {}

    def fake_template_response(request_arg, template_name, context):
        captured["request"] = request_arg
        captured["template_name"] = template_name
        captured["context"] = context
        return "response"

    monkeypatch.setattr(
        "app.services.operations.inv_web.base_context",
        lambda request_arg, auth_arg, title, section: {
            "title": title,
            "section": section,
            "organization_id": str(auth_arg.organization_id),
        },
    )
    monkeypatch.setattr(
        "app.services.operations.inv_web.templates.TemplateResponse",
        fake_template_response,
    )

    class _FakeScalarResult:
        def __init__(self, values):
            self._values = values

        def all(self):
            return self._values

    warehouse_obj = MagicMock()
    warehouse_obj.warehouse_id = uuid.uuid4()
    warehouse_obj.warehouse_name = "Main Warehouse"
    warehouse_obj.warehouse_code = "MAIN"

    item_obj = MagicMock()
    item_obj.item_id = uuid.uuid4()
    item_obj.item_code = "ITEM-001"
    item_obj.item_name = "Tracked Item"

    txn_obj = MagicMock()
    txn_obj.transaction_type.value = "RECEIPT"
    txn_obj.quantity = 10
    txn_obj.unit_cost = 25
    txn_obj.total_cost = 250
    txn_obj.reference = "GRN-001"
    txn_obj.transaction_date = None
    txn_obj.transaction_id = uuid.uuid4()
    txn_obj.source_document_type = None
    txn_obj.source_document_id = None

    db.scalars.side_effect = [
        _FakeScalarResult([warehouse_obj]),
        _FakeScalarResult([item_obj]),
    ]
    db.execute.return_value.all.return_value = [
        (txn_obj, item_obj, warehouse_obj, None)
    ]

    response = service.stock_movement_report_response(request, auth, db)

    assert response == "response"
    assert captured["template_name"] == "inventory/report_stock_movement.html"
    context = captured["context"]
    assert isinstance(context, dict)
    assert context["title"] == "Stock Movement"
    assert context["summary"]["total_rows"] == 1
    assert len(context["movement_rows"]) == 1
    assert context["movement_rows"][0]["request_context"] is None


def test_stock_movement_request_context_prefers_line_links_and_falls_back_to_header():
    service = OperationsInventoryWebService()
    db = MagicMock()
    org_id = uuid.uuid4()
    request_id = uuid.uuid4()
    header_project_id = uuid.uuid4()
    line_project_id = uuid.uuid4()
    header_ticket_id = uuid.uuid4()
    line_ticket_id = uuid.uuid4()

    overridden_line = MagicMock(
        item_id=uuid.uuid4(),
        project_id=line_project_id,
        ticket_id=line_ticket_id,
    )
    fallback_line = MagicMock(
        item_id=uuid.uuid4(),
        project_id=None,
        ticket_id=None,
    )
    material_request = MagicMock(
        request_id=request_id,
        request_number="MR-00042",
        project_id=header_project_id,
        ticket_id=header_ticket_id,
        items=[overridden_line, fallback_line],
    )
    projects = [
        MagicMock(
            project_id=header_project_id,
            project_code="PRJ-HEADER",
            project_name="Header Project",
        ),
        MagicMock(
            project_id=line_project_id,
            project_code="PRJ-LINE",
            project_name="Line Project",
        ),
    ]
    tickets = [
        MagicMock(
            ticket_id=header_ticket_id,
            ticket_number="TKT-HEADER",
            subject="Header Ticket",
        ),
        MagicMock(
            ticket_id=line_ticket_id,
            ticket_number="TKT-LINE",
            subject="Line Ticket",
        ),
    ]
    overridden_txn = MagicMock(
        transaction_id=uuid.uuid4(),
        source_document_type="MATERIAL_REQUEST",
        source_document_id=request_id,
        source_document_line_id=overridden_line.item_id,
    )
    fallback_txn = MagicMock(
        transaction_id=uuid.uuid4(),
        source_document_type="MATERIAL_REQUEST",
        source_document_id=request_id,
        source_document_line_id=fallback_line.item_id,
    )

    class _FakeScalarResult:
        def __init__(self, values):
            self._values = values

        def unique(self):
            return self

        def all(self):
            return self._values

    db.scalars.side_effect = [
        _FakeScalarResult([material_request]),
        _FakeScalarResult(projects),
        _FakeScalarResult(tickets),
    ]

    contexts = service._stock_movement_request_contexts(
        db,
        org_id,
        [overridden_txn, fallback_txn],
    )

    assert contexts[overridden_txn.transaction_id] == StockMovementRequestContext(
        request_number="MR-00042",
        project_code="PRJ-LINE",
        project_name="Line Project",
        ticket_number="TKT-LINE",
        ticket_subject="Line Ticket",
    )
    assert contexts[fallback_txn.transaction_id] == StockMovementRequestContext(
        request_number="MR-00042",
        project_code="PRJ-HEADER",
        project_name="Header Project",
        ticket_number="TKT-HEADER",
        ticket_subject="Header Ticket",
    )
    assert all(
        "organization_id" in str(call.args[0]) for call in db.scalars.call_args_list
    )


def test_export_stock_movement_pdf_response_returns_pdf(monkeypatch) -> None:
    service = OperationsInventoryWebService()
    org_id = uuid.uuid4()
    auth = MagicMock(organization_id=org_id)
    db = MagicMock()

    from app.models.inventory.inventory_transaction import TransactionType

    item = MagicMock()
    item.item_id = uuid.uuid4()
    item.item_code = "ITEM-PDF"
    item.item_name = "PDF Movement Item"

    warehouse = MagicMock()
    warehouse.warehouse_id = uuid.uuid4()
    warehouse.warehouse_code = "MAIN"
    warehouse.warehouse_name = "Main Warehouse"

    txn = MagicMock()
    txn.transaction_date = date(2026, 3, 10)
    txn.transaction_type = TransactionType.RECEIPT
    txn.quantity = Decimal("10")
    txn.unit_cost = Decimal("25")
    txn.total_cost = Decimal("250")
    txn.reference = "GRN-001"
    txn.transaction_id = uuid.uuid4()
    txn.source_document_type = None
    txn.source_document_id = None

    class _FakeExecuteResult:
        def all(self):
            return [(txn, item, warehouse, None)]

    db.execute.return_value = _FakeExecuteResult()
    captured: dict[str, object] = {}

    def fake_render(self, report_name, organization_id, context):
        captured["report_name"] = report_name
        captured["organization_id"] = organization_id
        captured["context"] = context
        return b"%PDF-1.4 stock movement"

    monkeypatch.setattr(
        "app.services.finance.rpt.pdf.ReportPDFService.render",
        fake_render,
    )
    request_context = StockMovementRequestContext(
        request_number="MR-00042",
        project_code="PRJ-012",
        project_name="Abuja Fibre Expansion",
        ticket_number="TKT-1058",
        ticket_subject="Replace damaged customer ONT",
    )
    monkeypatch.setattr(
        service,
        "_stock_movement_request_contexts",
        lambda db_arg, org_id_arg, transactions: {txn.transaction_id: request_context},
    )

    response = service.export_stock_movement_pdf_response(
        auth=auth,
        db=db,
        transaction_type="RECEIPT",
        search="GRN",
    )

    assert response.media_type == "application/pdf"
    assert response.body == b"%PDF-1.4 stock movement"
    assert (
        response.headers["Content-Disposition"]
        == 'attachment; filename="stock_movement_receipt.pdf"'
    )
    assert captured["report_name"] == "stock_movement"
    assert captured["organization_id"] == str(org_id)
    context = captured["context"]
    assert context["row_count"] == 1
    assert context["scope_label"] == 'Receipt, Search "GRN"'
    assert context["summary"]["total_value"] == Decimal("250")
    assert context["movement_rows"][0]["request_context"] == request_context


def test_stock_movement_pdf_renders_material_request_context_column() -> None:
    project_root = Path(__file__).resolve().parents[3]
    pdf = (
        project_root / "templates/finance/reports/stock_movement_pdf.html"
    ).read_text(encoding="utf-8")

    assert "Request For" in pdf
    assert "row.request_context.request_number" in pdf
    assert "row.request_context.ticket_number" in pdf
    assert "row.request_context.ticket_subject" in pdf
    assert "row.request_context.project_code" in pdf
    assert "row.request_context.project_name" in pdf


def test_yearly_stock_movement_report_calculates_opening_and_closing(
    monkeypatch,
) -> None:
    service = OperationsInventoryWebService()
    request = MagicMock()
    db = MagicMock()
    org_id = uuid.uuid4()
    auth = MagicMock(organization_id=org_id)

    captured: dict[str, object] = {}

    def fake_template_response(request_arg, template_name, context):
        captured["request"] = request_arg
        captured["template_name"] = template_name
        captured["context"] = context
        return "response"

    monkeypatch.setattr(
        "app.services.operations.inv_web.base_context",
        lambda request_arg, auth_arg, title, section: {
            "title": title,
            "section": section,
            "organization_id": str(auth_arg.organization_id),
        },
    )
    monkeypatch.setattr(
        "app.services.operations.inv_web.templates.TemplateResponse",
        fake_template_response,
    )

    from datetime import datetime, timezone
    from decimal import Decimal
    from types import SimpleNamespace

    from app.models.inventory.inventory_transaction import TransactionType

    warehouse_obj = MagicMock()
    warehouse_obj.warehouse_id = uuid.uuid4()
    warehouse_obj.organization_id = org_id
    warehouse_obj.warehouse_name = "Main Warehouse"
    warehouse_obj.warehouse_code = "MAIN"

    item_obj = MagicMock()
    item_obj.item_id = uuid.uuid4()
    item_obj.organization_id = org_id
    item_obj.item_code = "ITEM-001"
    item_obj.item_name = "Tracked Item"

    opening_txn = SimpleNamespace(
        transaction_type=TransactionType.RECEIPT,
        transaction_date=datetime(2025, 12, 20, tzinfo=timezone.utc),
        created_at=datetime(2025, 12, 20, tzinfo=timezone.utc),
        quantity=Decimal("100"),
        quantity_before=Decimal("0"),
        quantity_after=Decimal("100"),
        source_document_type="MANUAL",
        reference="OPENING",
    )
    purchase_txn = SimpleNamespace(
        transaction_type=TransactionType.RECEIPT,
        transaction_date=datetime(2026, 1, 5, tzinfo=timezone.utc),
        created_at=datetime(2026, 1, 5, tzinfo=timezone.utc),
        quantity=Decimal("50"),
        quantity_before=Decimal("100"),
        quantity_after=Decimal("150"),
        source_document_type="GOODS_RECEIPT",
        reference="GRN-001",
    )
    issue_txn = SimpleNamespace(
        transaction_type=TransactionType.ISSUE,
        transaction_date=datetime(2026, 2, 5, tzinfo=timezone.utc),
        created_at=datetime(2026, 2, 5, tzinfo=timezone.utc),
        quantity=Decimal("30"),
        quantity_before=Decimal("150"),
        quantity_after=Decimal("120"),
        source_document_type="MATERIAL_REQUEST",
        reference="MR-001",
    )

    class _FakeScalarResult:
        def __init__(self, values):
            self._values = values

        def all(self):
            return self._values

    db.scalars.side_effect = [
        _FakeScalarResult([warehouse_obj]),
        _FakeScalarResult([item_obj]),
    ]
    db.execute.return_value.all.return_value = [
        (opening_txn, item_obj, warehouse_obj),
        (purchase_txn, item_obj, warehouse_obj),
        (issue_txn, item_obj, warehouse_obj),
    ]

    response = service.yearly_stock_movement_report_response(
        request, auth, db, year="2026"
    )

    assert response == "response"
    assert captured["template_name"] == "inventory/report_yearly_stock_movement.html"
    context = captured["context"]
    assert isinstance(context, dict)
    assert context["title"] == "Yearly Stock Movement"
    assert context["summary"]["total_rows"] == 1
    row = context["yearly_rows"][0]
    assert row["opening_qty"] == Decimal("100")
    assert row["quantity_in"] == Decimal("50")
    assert row["purchase_qty"] == Decimal("50")
    assert row["issued_qty"] == Decimal("30")
    assert row["quantity_out"] == Decimal("30")
    assert row["closing_qty"] == Decimal("120")


def test_yearly_stock_movement_report_filters_by_month(monkeypatch) -> None:
    service = OperationsInventoryWebService()
    request = MagicMock()
    db = MagicMock()
    org_id = uuid.uuid4()
    auth = MagicMock(organization_id=org_id)

    captured: dict[str, object] = {}

    def fake_template_response(request_arg, template_name, context):
        captured["template_name"] = template_name
        captured["context"] = context
        return "response"

    monkeypatch.setattr(
        "app.services.operations.inv_web.base_context",
        lambda request_arg, auth_arg, title, section: {
            "title": title,
            "section": section,
            "organization_id": str(auth_arg.organization_id),
        },
    )
    monkeypatch.setattr(
        "app.services.operations.inv_web.templates.TemplateResponse",
        fake_template_response,
    )

    from datetime import datetime, timezone
    from decimal import Decimal
    from types import SimpleNamespace

    from app.models.inventory.inventory_transaction import TransactionType

    warehouse_obj = MagicMock()
    warehouse_obj.warehouse_id = uuid.uuid4()
    warehouse_obj.organization_id = org_id
    warehouse_obj.warehouse_name = "Main Warehouse"
    warehouse_obj.warehouse_code = "MAIN"

    item_obj = MagicMock()
    item_obj.item_id = uuid.uuid4()
    item_obj.organization_id = org_id
    item_obj.item_code = "ITEM-001"
    item_obj.item_name = "Tracked Item"

    opening_txn = SimpleNamespace(
        transaction_type=TransactionType.RECEIPT,
        transaction_date=datetime(2025, 12, 20, tzinfo=timezone.utc),
        created_at=datetime(2025, 12, 20, tzinfo=timezone.utc),
        quantity=Decimal("100"),
        quantity_before=Decimal("0"),
        quantity_after=Decimal("100"),
        source_document_type="MANUAL",
        reference="OPENING",
    )
    january_txn = SimpleNamespace(
        transaction_type=TransactionType.RECEIPT,
        transaction_date=datetime(2026, 1, 5, tzinfo=timezone.utc),
        created_at=datetime(2026, 1, 5, tzinfo=timezone.utc),
        quantity=Decimal("50"),
        quantity_before=Decimal("100"),
        quantity_after=Decimal("150"),
        source_document_type="GOODS_RECEIPT",
        reference="GRN-001",
    )
    february_txn = SimpleNamespace(
        transaction_type=TransactionType.ISSUE,
        transaction_date=datetime(2026, 2, 5, tzinfo=timezone.utc),
        created_at=datetime(2026, 2, 5, tzinfo=timezone.utc),
        quantity=Decimal("30"),
        quantity_before=Decimal("150"),
        quantity_after=Decimal("120"),
        source_document_type="MATERIAL_REQUEST",
        reference="MR-001",
    )

    class _FakeScalarResult:
        def __init__(self, values):
            self._values = values

        def all(self):
            return self._values

    db.scalars.side_effect = [
        _FakeScalarResult([warehouse_obj]),
        _FakeScalarResult([item_obj]),
    ]
    db.execute.return_value.all.return_value = [
        (opening_txn, item_obj, warehouse_obj),
        (january_txn, item_obj, warehouse_obj),
        (february_txn, item_obj, warehouse_obj),
    ]

    response = service.yearly_stock_movement_report_response(
        request, auth, db, year="2026", month="2"
    )

    assert response == "response"
    assert captured["template_name"] == "inventory/report_yearly_stock_movement.html"
    context = captured["context"]
    assert context["month"] == "2"
    row = context["yearly_rows"][0]
    assert row["opening_qty"] == Decimal("150")
    assert row["quantity_in"] == Decimal("0")
    assert row["issued_qty"] == Decimal("30")
    assert row["closing_qty"] == Decimal("120")


@pytest.mark.asyncio
async def test_bulk_record_count_lines_response_uses_checked_lines_only(
    monkeypatch,
) -> None:
    service = OperationsInventoryWebService()
    request = MagicMock()
    request.form = AsyncMock(
        return_value=FormData(
            [
                ("selected_line_ids", "11111111-1111-1111-1111-111111111111"),
                ("selected_line_ids", "22222222-2222-2222-2222-222222222222"),
                ("counted_quantity_11111111-1111-1111-1111-111111111111", "12.5"),
                ("counted_quantity_22222222-2222-2222-2222-222222222222", "8"),
                ("counted_quantity_33333333-3333-3333-3333-333333333333", "99"),
            ]
        )
    )
    auth = MagicMock(organization_id=uuid.uuid4(), user_id=uuid.uuid4())
    db = MagicMock()

    captured: dict[str, object] = {}

    def fake_record_count_bulk(
        db, organization_id, count_id, inputs, counted_by_user_id
    ):
        captured["db"] = db
        captured["organization_id"] = organization_id
        captured["count_id"] = count_id
        captured["inputs"] = inputs
        captured["counted_by_user_id"] = counted_by_user_id
        return []

    monkeypatch.setattr(
        "app.services.inventory.count.InventoryCountService.record_count_bulk",
        fake_record_count_bulk,
    )

    response = await service.bulk_record_count_lines_response(
        request=request,
        count_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        auth=auth,
        db=db,
    )

    assert response.status_code == 303
    assert (
        response.headers["location"]
        == "/inventory/counts/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    )
    inputs = captured["inputs"]
    assert len(inputs) == 2
    assert inputs[0].counted_quantity == Decimal("12.5")
    assert inputs[1].counted_quantity == Decimal("8")


@pytest.mark.asyncio
@pytest.mark.parametrize("counted_quantity", ["not-a-number", ""])
async def test_record_count_line_rejects_invalid_quantity_before_service_call(
    monkeypatch,
    counted_quantity: str,
) -> None:
    service = OperationsInventoryWebService()
    count_id = uuid.uuid4()
    line_id = uuid.uuid4()
    request = MagicMock()
    request.form = AsyncMock(
        return_value=FormData([("counted_quantity", counted_quantity)])
    )
    auth = MagicMock(organization_id=uuid.uuid4(), user_id=uuid.uuid4())
    db = MagicMock()
    record_count = MagicMock()
    monkeypatch.setattr(
        "app.services.inventory.count.InventoryCountService.record_count",
        record_count,
    )

    with pytest.raises(HTTPException) as exc:
        await service.record_count_line_response(
            request=request,
            count_id=str(count_id),
            line_id=str(line_id),
            auth=auth,
            db=db,
        )

    assert exc.value.status_code == 400
    record_count.assert_not_called()
    db.get.assert_not_called()


@pytest.mark.asyncio
async def test_record_count_line_rejects_invalid_line_id_before_service_call(
    monkeypatch,
) -> None:
    service = OperationsInventoryWebService()
    request = MagicMock()
    request.form = AsyncMock(return_value=FormData([("counted_quantity", "4")]))
    auth = MagicMock(organization_id=uuid.uuid4(), user_id=uuid.uuid4())
    db = MagicMock()
    record_count = MagicMock()
    monkeypatch.setattr(
        "app.services.inventory.count.InventoryCountService.record_count",
        record_count,
    )

    with pytest.raises(HTTPException) as exc:
        await service.record_count_line_response(
            request=request,
            count_id=str(uuid.uuid4()),
            line_id="not-a-uuid",
            auth=auth,
            db=db,
        )

    assert exc.value.status_code == 400
    record_count.assert_not_called()
    db.get.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("bad_line_id", "bad_quantity"),
    [
        ("not-a-uuid", "7"),
        ("22222222-2222-2222-2222-222222222222", "not-a-number"),
        ("22222222-2222-2222-2222-222222222222", ""),
    ],
)
async def test_bulk_count_rejects_one_invalid_selected_line_before_service_call(
    monkeypatch,
    bad_line_id: str,
    bad_quantity: str,
) -> None:
    service = OperationsInventoryWebService()
    count_id = uuid.uuid4()
    valid_line_id = "11111111-1111-1111-1111-111111111111"
    request = MagicMock()
    request.form = AsyncMock(
        return_value=FormData(
            [
                ("selected_line_ids", valid_line_id),
                ("selected_line_ids", bad_line_id),
                (f"counted_quantity_{valid_line_id}", "5"),
                (f"counted_quantity_{bad_line_id}", bad_quantity),
            ]
        )
    )
    auth = MagicMock(organization_id=uuid.uuid4(), user_id=uuid.uuid4())
    db = MagicMock()
    record_bulk = MagicMock()
    monkeypatch.setattr(
        "app.services.inventory.count.InventoryCountService.record_count_bulk",
        record_bulk,
    )

    with pytest.raises(HTTPException) as exc:
        await service.bulk_record_count_lines_response(
            request=request,
            count_id=str(count_id),
            auth=auth,
            db=db,
        )

    assert exc.value.status_code == 400
    record_bulk.assert_not_called()


@pytest.mark.parametrize(
    ("response_method", "service_method"),
    [
        ("start_count_response", "start_count"),
        ("complete_count_response", "complete_count"),
        ("post_count_response", "post_count"),
    ],
)
def test_count_transition_failure_escapes_the_web_adapter(
    monkeypatch,
    response_method: str,
    service_method: str,
) -> None:
    service = OperationsInventoryWebService()
    auth = MagicMock(organization_id=uuid.uuid4(), user_id=uuid.uuid4())
    failure = RuntimeError("count mutation failed")
    monkeypatch.setattr(
        f"app.services.inventory.count.InventoryCountService.{service_method}",
        MagicMock(side_effect=failure),
    )

    with pytest.raises(RuntimeError, match="count mutation failed"):
        getattr(service, response_method)(
            count_id=str(uuid.uuid4()),
            auth=auth,
            db=MagicMock(),
        )


@pytest.mark.asyncio
async def test_record_count_failure_escapes_the_web_adapter(monkeypatch) -> None:
    service = OperationsInventoryWebService()
    count_id = uuid.uuid4()
    line_id = uuid.uuid4()
    line = SimpleNamespace(
        count_id=count_id,
        item_id=uuid.uuid4(),
        warehouse_id=uuid.uuid4(),
        lot_id=None,
    )
    request = MagicMock()
    request.form = AsyncMock(return_value=FormData([("counted_quantity", "3")]))
    auth = MagicMock(organization_id=uuid.uuid4(), user_id=uuid.uuid4())
    db = MagicMock()
    db.get.return_value = line
    monkeypatch.setattr(
        "app.services.inventory.count.InventoryCountService.record_count",
        MagicMock(side_effect=RuntimeError("record failed")),
    )

    with pytest.raises(RuntimeError, match="record failed"):
        await service.record_count_line_response(
            request=request,
            count_id=str(count_id),
            line_id=str(line_id),
            auth=auth,
            db=db,
        )


@pytest.mark.asyncio
async def test_bulk_count_failure_escapes_the_web_adapter(monkeypatch) -> None:
    service = OperationsInventoryWebService()
    line_id = uuid.uuid4()
    request = MagicMock()
    request.form = AsyncMock(
        return_value=FormData(
            [
                ("selected_line_ids", str(line_id)),
                (f"counted_quantity_{line_id}", "2"),
            ]
        )
    )
    auth = MagicMock(organization_id=uuid.uuid4(), user_id=uuid.uuid4())
    monkeypatch.setattr(
        "app.services.inventory.count.InventoryCountService.record_count_bulk",
        MagicMock(side_effect=RuntimeError("bulk failed")),
    )

    with pytest.raises(RuntimeError, match="bulk failed"):
        await service.bulk_record_count_lines_response(
            request=request,
            count_id=str(uuid.uuid4()),
            auth=auth,
            db=MagicMock(),
        )


def test_second_count_adjustment_failure_reaches_request_rollback(monkeypatch) -> None:
    from app.models.inventory.inventory_count import CountStatus, InventoryCount
    from app.models.inventory.item import Item
    from app.services.inventory.transaction import inventory_transaction_service
    from app.web import deps as web_deps

    org_id = uuid.uuid4()
    count_id = uuid.uuid4()
    user_id = uuid.uuid4()
    first_item_id = uuid.uuid4()
    second_item_id = uuid.uuid4()
    count = SimpleNamespace(
        count_id=count_id,
        organization_id=org_id,
        status=CountStatus.COMPLETED,
        count_date=date(2026, 8, 25),
        fiscal_period_id=uuid.uuid4(),
        count_number="CNT-ROLLBACK",
        posted_by_user_id=None,
        posted_at=None,
    )
    lines = [
        SimpleNamespace(
            line_id=uuid.uuid4(),
            count_id=count_id,
            item_id=item_id,
            warehouse_id=uuid.uuid4(),
            variance_quantity=Decimal("1"),
            unit_cost=Decimal("10"),
            uom="EACH",
            location_id=None,
            lot_id=None,
            reason_code="COUNT",
        )
        for item_id in (first_item_id, second_item_id)
    ]
    items = {
        first_item_id: SimpleNamespace(currency_code="NGN"),
        second_item_id: SimpleNamespace(currency_code="NGN"),
    }

    class _ScalarResult:
        def all(self):
            return lines

    class _TransactionalSession:
        def __init__(self):
            self.info = {}
            self.pending_adjustments: list[object] = []
            self.commit_calls = 0
            self.rollback_calls = 0
            self.closed = False

        def get(self, model, key):
            if model is InventoryCount and key == count_id:
                return count
            if model is Item:
                return items.get(key)
            return None

        def scalars(self, _statement):
            return _ScalarResult()

        def flush(self):
            return None

        def commit(self):
            self.commit_calls += 1

        def rollback(self):
            self.rollback_calls += 1
            self.pending_adjustments.clear()

        def close(self):
            self.closed = True

    db = _TransactionalSession()

    @contextmanager
    def _tenant_scope(_db, _org_id):
        yield

    monkeypatch.setattr(web_deps, "SessionLocal", lambda: db)
    monkeypatch.setattr(web_deps, "tenant_scope_for_session", _tenant_scope)
    adjustment_calls = 0

    def _create_adjustment(**kwargs):
        nonlocal adjustment_calls
        adjustment_calls += 1
        kwargs["db"].pending_adjustments.append(kwargs["input"])
        if adjustment_calls == 2:
            raise RuntimeError("second adjustment failed")

    auth = MagicMock(organization_id=org_id, user_id=user_id)
    dependency = web_deps.get_db_for_org(auth=auth)
    request_db = next(dependency)

    with patch.object(
        inventory_transaction_service,
        "create_adjustment",
        _create_adjustment,
    ):
        try:
            OperationsInventoryWebService().post_count_response(
                count_id=str(count_id),
                auth=auth,
                db=request_db,
            )
        except RuntimeError as exc:
            with pytest.raises(RuntimeError, match="second adjustment failed"):
                dependency.throw(exc)
        else:  # pragma: no cover - the canary must observe the planted failure
            pytest.fail("second adjustment failure was swallowed")

    assert adjustment_calls == 2
    assert db.rollback_calls == 1
    assert db.commit_calls == 0
    assert db.pending_adjustments == []
    assert count.status == CountStatus.COMPLETED
    assert count.posted_by_user_id is None
    assert count.posted_at is None
    assert db.closed is True


def test_export_count_csv_response_returns_csv_for_posted_count() -> None:
    service = OperationsInventoryWebService()
    count_id = uuid.uuid4()
    item_id = uuid.uuid4()
    warehouse_id = uuid.uuid4()
    auth = MagicMock(organization_id=uuid.uuid4())
    db = MagicMock()

    count = MagicMock()
    count.count_id = count_id
    count.organization_id = auth.organization_id
    count.count_number = "CNT-00042"
    count.count_date = date(2026, 5, 4)
    count.status.value = "POSTED"

    line = MagicMock()
    line.item_id = item_id
    line.warehouse_id = warehouse_id
    line.system_quantity = Decimal("10")
    line.counted_quantity = Decimal("8")
    line.final_quantity = Decimal("8")
    line.variance_quantity = Decimal("-2")
    line.variance_value = Decimal("-50")
    line.reason_code = "DAMAGE"
    line.notes = "Broken cartons"

    item = MagicMock()
    item.item_id = item_id
    item.item_code = "ITEM-001"
    item.item_name = "Test Item"

    warehouse = MagicMock()
    warehouse.warehouse_id = warehouse_id
    warehouse.warehouse_name = "Main Warehouse"

    db.get.return_value = count

    class _FakeScalarResult:
        def __init__(self, values):
            self._values = values

        def all(self):
            return self._values

    db.scalars.side_effect = [
        _FakeScalarResult([item]),
        _FakeScalarResult([warehouse]),
    ]

    from app.models.inventory.inventory_count import CountStatus
    from app.services.inventory.count import InventoryCountService

    count.status = CountStatus.POSTED

    list_lines_original = InventoryCountService.list_lines
    InventoryCountService.list_lines = MagicMock(return_value=[line])
    try:
        response = service.export_count_csv_response(str(count_id), auth, db)
    finally:
        InventoryCountService.list_lines = list_lines_original

    assert response.media_type == "text/csv"
    assert (
        response.headers["Content-Disposition"]
        == 'attachment; filename="stock_count_CNT-00042.csv"'
    )
    body = response.body.decode()
    assert "Count Number,Count Date,Status,Item Code,Item Name,Warehouse" in body
    assert (
        "CNT-00042,2026-05-04,POSTED,ITEM-001,Test Item,Main Warehouse,10,8,8,-2,-50,DAMAGE,Broken cartons"
        in body
    )


def test_export_count_pdf_response_returns_pdf_for_posted_count(monkeypatch) -> None:
    service = OperationsInventoryWebService()
    count_id = uuid.uuid4()
    item_id = uuid.uuid4()
    warehouse_id = uuid.uuid4()
    org_id = uuid.uuid4()
    auth = MagicMock(organization_id=org_id)
    db = MagicMock()

    count = MagicMock()
    count.count_id = count_id
    count.organization_id = org_id
    count.count_number = "CNT-00043"
    count.count_date = date(2026, 5, 5)

    from app.models.inventory.inventory_count import CountStatus

    count.status = CountStatus.POSTED

    line = MagicMock()
    line.item_id = item_id
    line.warehouse_id = warehouse_id
    line.system_quantity = Decimal("20")
    line.counted_quantity = Decimal("18")
    line.final_quantity = Decimal("18")
    line.variance_quantity = Decimal("-2")
    line.variance_value = Decimal("-75")
    line.reason_code = "SHORT"
    line.notes = "Missing units"

    item = MagicMock()
    item.item_id = item_id
    item.item_code = "ITEM-002"
    item.item_name = "PDF Item"

    warehouse = MagicMock()
    warehouse.warehouse_id = warehouse_id
    warehouse.warehouse_name = "PDF Warehouse"

    db.get.return_value = count

    class _FakeScalarResult:
        def __init__(self, values):
            self._values = values

        def all(self):
            return self._values

    db.scalars.side_effect = [
        _FakeScalarResult([item]),
        _FakeScalarResult([warehouse]),
    ]
    monkeypatch.setattr(
        "app.services.inventory.count.InventoryCountService.list_lines",
        MagicMock(return_value=[line]),
    )

    captured: dict[str, object] = {}

    def fake_render(self, report_name, organization_id, context):
        captured["report_name"] = report_name
        captured["organization_id"] = organization_id
        captured["context"] = context
        return b"%PDF-1.4 stock count"

    monkeypatch.setattr(
        "app.services.finance.rpt.pdf.ReportPDFService.render",
        fake_render,
    )

    response = service.export_count_pdf_response(str(count_id), auth, db)

    assert response.media_type == "application/pdf"
    assert response.body == b"%PDF-1.4 stock count"
    assert (
        response.headers["Content-Disposition"]
        == 'attachment; filename="stock_count_CNT-00043.pdf"'
    )
    assert captured["report_name"] == "stock_count"
    assert captured["organization_id"] == str(org_id)
    context = captured["context"]
    assert context["count"] is count
    assert context["row_count"] == 1
    assert context["summary"]["total_variance_quantity"] == Decimal("-2")
    assert context["rows"][0]["item_code"] == "ITEM-002"


def test_export_yearly_stock_movement_csv_response_returns_csv() -> None:
    service = OperationsInventoryWebService()
    org_id = uuid.uuid4()
    item_id = uuid.uuid4()
    warehouse_id = uuid.uuid4()
    auth = MagicMock(organization_id=org_id)
    db = MagicMock()

    from app.models.inventory.inventory_transaction import TransactionType

    item = MagicMock()
    item.item_id = item_id
    item.item_code = "ITEM-2026"
    item.item_name = "Annual Item"

    warehouse = MagicMock()
    warehouse.warehouse_id = warehouse_id
    warehouse.warehouse_code = "MAIN"
    warehouse.warehouse_name = "Main Warehouse"

    receipt = MagicMock()
    receipt.transaction_date = date(2026, 1, 10)
    receipt.transaction_type = TransactionType.RECEIPT
    receipt.quantity_before = Decimal("0")
    receipt.quantity_after = Decimal("10")
    receipt.source_document_type = "PURCHASE_RECEIPT"
    receipt.reference = "PO-001"

    issue = MagicMock()
    issue.transaction_date = date(2026, 2, 12)
    issue.transaction_type = TransactionType.ISSUE
    issue.quantity_before = Decimal("10")
    issue.quantity_after = Decimal("7")
    issue.source_document_type = "ISSUE"
    issue.reference = "ISS-001"

    class _FakeExecuteResult:
        def all(self):
            return [(receipt, item, warehouse), (issue, item, warehouse)]

    db.execute.return_value = _FakeExecuteResult()

    response = service.export_yearly_stock_movement_csv_response(
        auth=auth,
        db=db,
        year="2026",
    )

    assert response.media_type == "text/csv"
    assert (
        response.headers["Content-Disposition"]
        == 'attachment; filename="yearly_stock_movement_2026.csv"'
    )
    body = response.body.decode()
    assert "Year,Item Code,Item Name,Warehouse Code,Warehouse Name" in body
    assert "2026,ITEM-2026,Annual Item,MAIN,Main Warehouse,0,10,10,3,3,7" in body


def test_export_yearly_stock_movement_pdf_response_returns_pdf(monkeypatch) -> None:
    service = OperationsInventoryWebService()
    org_id = uuid.uuid4()
    item_id = uuid.uuid4()
    warehouse_id = uuid.uuid4()
    auth = MagicMock(organization_id=org_id)
    db = MagicMock()

    from app.models.inventory.inventory_transaction import TransactionType

    item = MagicMock()
    item.item_id = item_id
    item.item_code = "ITEM-PDF"
    item.item_name = "PDF Annual Item"

    warehouse = MagicMock()
    warehouse.warehouse_id = warehouse_id
    warehouse.warehouse_code = "PDF"
    warehouse.warehouse_name = "PDF Warehouse"

    receipt = MagicMock()
    receipt.transaction_date = date(2026, 3, 10)
    receipt.transaction_type = TransactionType.RECEIPT
    receipt.quantity_before = Decimal("4")
    receipt.quantity_after = Decimal("9")
    receipt.source_document_type = "GOODS_RECEIPT"
    receipt.reference = "GRN-001"

    class _FakeExecuteResult:
        def all(self):
            return [(receipt, item, warehouse)]

    db.execute.return_value = _FakeExecuteResult()
    captured: dict[str, object] = {}

    def fake_render(self, report_name, organization_id, context):
        captured["report_name"] = report_name
        captured["organization_id"] = organization_id
        captured["context"] = context
        return b"%PDF-1.4 yearly movement"

    monkeypatch.setattr(
        "app.services.finance.rpt.pdf.ReportPDFService.render",
        fake_render,
    )

    response = service.export_yearly_stock_movement_pdf_response(
        auth=auth,
        db=db,
        year="2026",
        month="3",
    )

    assert response.media_type == "application/pdf"
    assert response.body == b"%PDF-1.4 yearly movement"
    assert (
        response.headers["Content-Disposition"]
        == 'attachment; filename="yearly_stock_movement_2026_03.pdf"'
    )
    assert captured["report_name"] == "yearly_stock_movement"
    assert captured["organization_id"] == str(org_id)
    context = captured["context"]
    assert context["row_count"] == 1
    assert context["scope_label"] == "Year 2026, March"
    assert context["summary"]["quantity_in"] == Decimal("5")
