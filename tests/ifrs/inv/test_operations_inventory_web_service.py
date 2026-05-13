from __future__ import annotations

from decimal import Decimal
from datetime import date
from io import BytesIO
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.datastructures import FormData
from starlette.datastructures import UploadFile

from app.services.operations.inv_web import OperationsInventoryWebService


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
