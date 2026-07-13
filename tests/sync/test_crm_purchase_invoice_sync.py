from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch

from app.models.finance.ap.supplier_invoice import SupplierInvoiceStatus
from app.schemas.sync.dotmac_crm import (
    CRMPurchaseInvoiceItemPayload,
    CRMPurchaseInvoicePayload,
)
from app.services.sync.dotmac_crm_sync_service import DotMacCRMSyncService


def _payload() -> CRMPurchaseInvoicePayload:
    return CRMPurchaseInvoicePayload(
        crm_invoice_id=str(uuid.uuid4()),
        crm_invoice_number="VENDOR-2026-014",
        crm_project_id=str(uuid.uuid4()),
        installation_project_id=str(uuid.uuid4()),
        erp_purchase_order_id="PO-2026-0042",
        vendor_erp_id="SUP-0001",
        vendor_name="Fiber Vendor",
        vendor_code="FIBER",
        currency="NGN",
        subtotal=Decimal("1000"),
        tax_total=Decimal("0"),
        total=Decimal("1000"),
        items=[
            CRMPurchaseInvoiceItemPayload(
                item_type="service",
                description="Fiber installation",
                quantity=Decimal("1"),
                unit_price=Decimal("1000"),
                amount=Decimal("1000"),
            )
        ],
    )


def _invoice(source_id: str):
    invoice = MagicMock()
    invoice.invoice_id = uuid.uuid4()
    invoice.invoice_number = "PINV-2026-0001"
    invoice.status = SupplierInvoiceStatus.DRAFT
    invoice.correlation_id = f"sub-invoice:{source_id}"
    invoice.total_amount = Decimal("1000")
    return invoice


def test_purchase_invoice_retry_returns_existing() -> None:
    db = MagicMock()
    payload = _payload()
    existing = _invoice(payload.crm_invoice_id)
    db.scalar.return_value = existing

    result = DotMacCRMSyncService(db).create_purchase_invoice(
        uuid.uuid4(), payload, uuid.uuid4()
    )

    assert result.invoice_id == existing.invoice_id
    assert result.crm_invoice_id == payload.crm_invoice_id
    assert result.status == "draft"
    db.begin_nested.assert_not_called()


def test_purchase_invoice_is_matched_to_po_lines() -> None:
    db = MagicMock()
    org_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    payload = _payload()

    po_line = MagicMock()
    po_line.line_id = uuid.uuid4()
    po_line.line_number = 1
    po_line.quantity_ordered = Decimal("1")
    po_line.line_amount = Decimal("1000")
    po_line.expense_account_id = uuid.uuid4()
    po_line.asset_account_id = None
    po_line.item_id = None
    po_line.tax_code_id = None
    po_line.cost_center_id = None
    po_line.project_id = None
    po_line.segment_id = None

    po = MagicMock()
    po.po_id = uuid.uuid4()
    po.po_number = payload.erp_purchase_order_id
    po.organization_id = org_id
    po.supplier_id = uuid.uuid4()
    po.currency_code = "NGN"
    po.lines = [po_line]

    supplier = MagicMock()
    supplier.supplier_id = po.supplier_id
    supplier.default_expense_account_id = po_line.expense_account_id
    supplier.default_tax_code_id = None
    supplier.payment_terms_days = 30

    created = _invoice(payload.crm_invoice_id)
    db.scalar.side_effect = [None, po, Decimal("0")]
    savepoint = MagicMock()
    db.begin_nested.return_value = savepoint
    service = DotMacCRMSyncService(db)

    with (
        patch.object(service, "_resolve_supplier", return_value=supplier),
        patch.object(service, "_resolve_project_id", return_value=None),
        patch(
            "app.services.finance.ap.supplier_invoice.SupplierInvoiceService.create_invoice",
            return_value=created,
        ) as create_invoice,
    ):
        result = service.create_purchase_invoice(org_id, payload, actor_id)

    invoice_input = create_invoice.call_args.args[2]
    assert invoice_input.correlation_id == f"sub-invoice:{payload.crm_invoice_id}"
    assert invoice_input.supplier_invoice_number == payload.crm_invoice_number
    assert invoice_input.lines[0].po_line_id == po_line.line_id
    assert invoice_input.lines[0].expense_account_id == po_line.expense_account_id
    assert result.invoice_id == created.invoice_id
    savepoint.commit.assert_called_once()


def test_purchase_invoice_rejects_amount_above_po_remaining() -> None:
    db = MagicMock()
    payload = _payload()
    po_line = MagicMock(
        line_id=uuid.uuid4(),
        line_number=1,
        quantity_ordered=Decimal("1"),
        line_amount=Decimal("900"),
    )
    po = MagicMock(
        po_id=uuid.uuid4(),
        po_number=payload.erp_purchase_order_id,
        supplier_id=uuid.uuid4(),
        currency_code="NGN",
        lines=[po_line],
    )
    supplier = MagicMock(supplier_id=po.supplier_id)
    db.scalar.side_effect = [None, po, Decimal("0")]
    service = DotMacCRMSyncService(db)

    with (
        patch.object(service, "_resolve_supplier", return_value=supplier),
        patch.object(service, "_resolve_project_id", return_value=None),
    ):
        try:
            service.create_purchase_invoice(uuid.uuid4(), payload, uuid.uuid4())
        except ValueError as exc:
            assert "uninvoiced PO amount" in str(exc)
        else:
            raise AssertionError("Expected over-invoice validation")
