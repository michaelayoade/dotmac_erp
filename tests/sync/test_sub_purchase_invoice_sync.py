from __future__ import annotations

from contextlib import nullcontext
import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from dotmac_kernel.fingerprints import fingerprint_of
from sqlalchemy.exc import IntegrityError

from app.models.finance.ap.supplier_invoice import SupplierInvoiceStatus
from app.schemas.sync.dotmac_sub import (
    SubPurchaseInvoiceItemPayload,
    SubPurchaseInvoicePayload,
)
from app.services.sync.dotmac_sub_sync_service import DotmacSubSyncService
from app.services.sync.sub.errors import SubReplayConflictError


def _payload(*, source_invoice_id: str | None = None) -> SubPurchaseInvoicePayload:
    return SubPurchaseInvoicePayload(
        source_invoice_id=source_invoice_id or str(uuid.uuid4()),
        source_invoice_number="VENDOR-2026-014",
        source_project_id=str(uuid.uuid4()),
        installation_project_id=str(uuid.uuid4()),
        erp_purchase_order_id="PO-2026-0042",
        vendor_erp_id="SUP-0001",
        vendor_name="Fiber Vendor",
        vendor_code="FIBER",
        currency="NGN",
        subtotal=Decimal("1000.00"),
        tax_total=Decimal("0.00"),
        total=Decimal("1000.00"),
        items=[
            SubPurchaseInvoiceItemPayload(
                item_type="service",
                description="Fiber installation",
                quantity=Decimal("1"),
                unit_price=Decimal("1000"),
                amount=Decimal("1000.00"),
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
    payload = _payload(source_invoice_id="i" * 120)
    existing = _invoice(payload.source_invoice_id)
    existing.source_fingerprint = fingerprint_of(payload)
    db.scalar.return_value = existing

    result = DotmacSubSyncService(db).create_purchase_invoice(
        uuid.uuid4(), payload, uuid.uuid4()
    )

    assert result.invoice_id == existing.invoice_id
    assert result.source_invoice_id == payload.source_invoice_id
    assert result.status == "draft"
    db.begin_nested.assert_not_called()


def test_changed_purchase_invoice_replay_is_rejected() -> None:
    db = MagicMock()
    payload = _payload()
    existing = _invoice(payload.source_invoice_id)
    existing.source_fingerprint = fingerprint_of(payload)
    db.scalar.return_value = existing
    changed = payload.model_copy(
        update={"source_invoice_number": "CHANGED-IMMUTABLE-NUMBER"}
    )

    with pytest.raises(SubReplayConflictError, match="different immutable payload"):
        DotmacSubSyncService(db).create_purchase_invoice(
            uuid.uuid4(), changed, uuid.uuid4()
        )


def test_purchase_invoice_is_matched_to_po_lines() -> None:
    db = MagicMock()
    org_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    payload = _payload(source_invoice_id="i" * 120)

    po_line = MagicMock(
        line_id=uuid.uuid4(),
        line_number=1,
        quantity_ordered=Decimal("1"),
        line_amount=Decimal("1000"),
        expense_account_id=uuid.uuid4(),
        asset_account_id=None,
        item_id=None,
        tax_code_id=None,
        cost_center_id=None,
        project_id=None,
        segment_id=None,
    )
    po = MagicMock(
        po_id=uuid.uuid4(),
        po_number=payload.erp_purchase_order_id,
        organization_id=org_id,
        supplier_id=uuid.uuid4(),
        currency_code="NGN",
        lines=[po_line],
    )
    supplier = MagicMock(
        supplier_id=po.supplier_id,
        default_expense_account_id=po_line.expense_account_id,
        default_tax_code_id=None,
        payment_terms_days=30,
    )
    created = _invoice(payload.source_invoice_id)
    db.scalar.side_effect = [None, po, Decimal("0")]
    service = DotmacSubSyncService(db)

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
    assert invoice_input.correlation_id == f"sub-invoice:{payload.source_invoice_id}"
    assert len(invoice_input.correlation_id) == 132
    assert invoice_input.supplier_invoice_number == payload.source_invoice_number
    assert invoice_input.lines[0].po_line_id == po_line.line_id
    assert invoice_input.lines[0].expense_account_id == po_line.expense_account_id
    assert result.invoice_id == created.invoice_id
    assert created.source_fingerprint == fingerprint_of(payload)
    db.commit.assert_not_called()
    db.rollback.assert_not_called()


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
    service = DotmacSubSyncService(db)

    with (
        patch.object(service, "_resolve_supplier", return_value=supplier),
        patch.object(service, "_resolve_project_id", return_value=None),
        pytest.raises(ValueError, match="uninvoiced PO amount"),
    ):
        service.create_purchase_invoice(uuid.uuid4(), payload, uuid.uuid4())


@pytest.mark.parametrize("winner_matches", [True, False], ids=["matching", "changed"])
def test_concurrent_purchase_invoice_create_validates_the_winner(
    winner_matches: bool,
) -> None:
    db = MagicMock()
    org_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    payload = _payload()
    po_line = MagicMock(
        line_id=uuid.uuid4(),
        line_number=1,
        quantity_ordered=Decimal("1"),
        line_amount=Decimal("1000.00"),
        expense_account_id=uuid.uuid4(),
        asset_account_id=None,
        item_id=None,
        tax_code_id=None,
        cost_center_id=None,
        project_id=None,
        segment_id=None,
    )
    po = MagicMock(
        po_id=uuid.uuid4(),
        po_number=payload.erp_purchase_order_id,
        supplier_id=uuid.uuid4(),
        currency_code="NGN",
        lines=[po_line],
    )
    supplier = MagicMock(
        supplier_id=po.supplier_id,
        default_expense_account_id=po_line.expense_account_id,
        default_tax_code_id=None,
        payment_terms_days=30,
    )
    winner = _invoice(payload.source_invoice_id)
    winner.source_fingerprint = fingerprint_of(payload) if winner_matches else "0" * 64
    db.scalar.side_effect = [None, po, Decimal("0"), winner]
    service = DotmacSubSyncService(db)

    outcome = (
        nullcontext()
        if winner_matches
        else pytest.raises(SubReplayConflictError, match="different immutable payload")
    )
    with (
        patch.object(service, "_resolve_supplier", return_value=supplier),
        patch.object(service, "_resolve_project_id", return_value=None),
        patch(
            "app.services.finance.ap.supplier_invoice.SupplierInvoiceService.create_invoice",
            side_effect=IntegrityError("insert", {}, Exception("duplicate")),
        ),
        outcome,
    ):
        result = service.create_purchase_invoice(org_id, payload, actor_id)

    if winner_matches:
        assert result.invoice_id == winner.invoice_id
        assert result.source_invoice_id == payload.source_invoice_id
    db.begin_nested.assert_called_once()
    db.commit.assert_not_called()
    db.rollback.assert_not_called()
