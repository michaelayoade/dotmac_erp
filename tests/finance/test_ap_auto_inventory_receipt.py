from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.models.finance.ap.invoice_inventory_receipt_approval import (
    InvoiceInventoryReceiptApproval,
    InvoiceInventoryReceiptApprovalStatus,
)
from app.models.finance.ap.supplier_invoice import (
    InventoryReceiptMode,
    SupplierInvoiceStatus,
)
from app.services.common import ValidationError
from app.services.finance.ap.auto_inventory_receipt import (
    APInvoiceAutoReceiptService,
)
from app.services.finance.ap.inventory_receipt_approval import (
    APInventoryReceiptApprovalService,
)
from app.services.finance.ap.supplier_invoice import SupplierInvoiceService


class _ScalarResult:
    def __init__(self, values):
        self.values = values

    def all(self):
        return list(self.values)

    def first(self):
        return self.values[0] if self.values else None


def _invoice(
    org_id,
    *,
    status=SupplierInvoiceStatus.SUBMITTED,
    auto=True,
    receipt_mode=InventoryReceiptMode.AUTO_RECEIVE,
):
    return SimpleNamespace(
        invoice_id=uuid4(),
        organization_id=org_id,
        invoice_number="SINV202605-0003",
        supplier_invoice_number="SUP-INV-001",
        currency_code="NGN",
        status=status,
        auto_create_inventory_receipt=auto,
        inventory_receipt_mode=receipt_mode,
    )


def _line(invoice_id, item_id, warehouse_id=None, *, quantity="2"):
    return SimpleNamespace(
        line_id=uuid4(),
        invoice_id=invoice_id,
        line_number=1,
        item_id=item_id,
        quantity=Decimal(quantity),
        unit_price=Decimal("10.00"),
        receipt_warehouse_id=warehouse_id,
        receipt_reference="DEL-001",
        receipt_serial_numbers=None,
        receipt_auto_generate_serials=False,
        auto_receipt_transaction_id=None,
    )


def _item(org_id, item_id, *, stock=True, serial=False):
    return SimpleNamespace(
        item_id=item_id,
        organization_id=org_id,
        track_inventory=stock,
        track_serial_numbers=serial,
        base_uom="EA",
    )


def _warehouse(org_id, warehouse_id):
    return SimpleNamespace(warehouse_id=warehouse_id, organization_id=org_id)


def _period():
    return SimpleNamespace(fiscal_period_id=uuid4())


def _approval(org_id, invoice_id, line_id, item_id, warehouse_id):
    return SimpleNamespace(
        approval_id=uuid4(),
        organization_id=org_id,
        supplier_invoice_id=invoice_id,
        supplier_invoice_line_id=line_id,
        item_id=item_id,
        warehouse_id=warehouse_id,
        requested_quantity=Decimal("2"),
        approved_quantity=None,
        receipt_reference="DEL-001",
        receipt_serial_numbers=None,
        status=InvoiceInventoryReceiptApprovalStatus.PENDING,
        approved_by_user_id=None,
        approved_at=None,
        rejected_by_user_id=None,
        rejected_at=None,
        rejection_reason=None,
        inventory_transaction_id=None,
    )


def test_build_invoice_input_preserves_auto_receipt_fields():
    db = MagicMock()
    org_id = uuid4()
    warehouse_id = uuid4()
    item_id = uuid4()

    payload = {
        "supplier_id": str(uuid4()),
        "invoice_date": "2026-05-25",
        "received_date": "2026-05-25",
        "due_date": "2026-05-30",
        "currency_code": "NGN",
        "auto_create_inventory_receipt": True,
        "lines": [
            {
                "description": "Router",
                "quantity": "2",
                "unit_price": "10",
                "expense_account_id": str(uuid4()),
                "item_id": str(item_id),
                "receipt_warehouse_id": str(warehouse_id),
                "receipt_reference": "DEL-001",
                "receipt_serial_numbers": "SN-1\nSN-2",
                "receipt_auto_generate_serials": False,
            }
        ],
    }

    with patch(
        "app.services.finance.ap.supplier_invoice.resolve_currency_code",
        return_value="NGN",
    ):
        result = SupplierInvoiceService.build_input_from_payload(db, org_id, payload)

    assert result.auto_create_inventory_receipt is True
    assert result.inventory_receipt_mode == InventoryReceiptMode.AUTO_RECEIVE
    assert result.lines[0].receipt_warehouse_id == warehouse_id
    assert result.lines[0].receipt_reference == "DEL-001"
    assert result.lines[0].receipt_serial_numbers == ["SN-1", "SN-2"]
    assert result.lines[0].receipt_auto_generate_serials is False


def test_build_invoice_input_preserves_store_approval_mode():
    db = MagicMock()
    org_id = uuid4()

    payload = {
        "supplier_id": str(uuid4()),
        "invoice_date": "2026-05-25",
        "received_date": "2026-05-25",
        "due_date": "2026-05-30",
        "currency_code": "NGN",
        "inventory_receipt_mode": "STORE_APPROVAL",
        "lines": [
            {
                "description": "Router",
                "quantity": "2",
                "unit_price": "10",
                "expense_account_id": str(uuid4()),
            }
        ],
    }

    with patch(
        "app.services.finance.ap.supplier_invoice.resolve_currency_code",
        return_value="NGN",
    ):
        result = SupplierInvoiceService.build_input_from_payload(db, org_id, payload)

    assert result.auto_create_inventory_receipt is False
    assert result.inventory_receipt_mode == InventoryReceiptMode.STORE_APPROVAL


def test_no_receipt_before_submission():
    db = MagicMock()
    org_id = uuid4()
    invoice = _invoice(org_id, status=SupplierInvoiceStatus.DRAFT)
    db.get.return_value = invoice

    with patch(
        "app.services.finance.ap.auto_inventory_receipt.InventoryTransactionService.create_receipt"
    ) as create_receipt:
        result = APInvoiceAutoReceiptService.create_for_invoice(
            db, org_id, invoice.invoice_id, uuid4()
        )

    assert result.created_count == 0
    create_receipt.assert_not_called()


def test_receipt_created_after_submission():
    db = MagicMock()
    org_id = uuid4()
    invoice = _invoice(org_id)
    item_id = uuid4()
    warehouse_id = uuid4()
    line = _line(invoice.invoice_id, item_id, warehouse_id)
    item = _item(org_id, item_id)
    warehouse = _warehouse(org_id, warehouse_id)
    transaction = SimpleNamespace(transaction_id=uuid4())

    def _get(model, _id):
        if model.__name__ == "SupplierInvoice":
            return invoice
        if model.__name__ == "Item":
            return item
        if model.__name__ == "Warehouse":
            return warehouse
        return None

    db.get.side_effect = _get
    db.scalars.side_effect = [
        _ScalarResult([line]),
        _ScalarResult([]),
        _ScalarResult([_period()]),
    ]

    with patch(
        "app.services.finance.ap.auto_inventory_receipt.InventoryTransactionService.create_receipt",
        return_value=transaction,
    ) as create_receipt:
        result = APInvoiceAutoReceiptService.create_for_invoice(
            db, org_id, invoice.invoice_id, uuid4()
        )

    assert result.created_count == 1
    assert result.transaction_ids == [transaction.transaction_id]
    assert line.auto_receipt_transaction_id == transaction.transaction_id
    create_receipt.assert_called_once()


def test_non_stock_lines_skipped():
    db = MagicMock()
    org_id = uuid4()
    invoice = _invoice(org_id)
    item_id = uuid4()
    line = _line(invoice.invoice_id, item_id, uuid4())
    item = _item(org_id, item_id, stock=False)

    def _get(model, _id):
        if model.__name__ == "SupplierInvoice":
            return invoice
        if model.__name__ == "Item":
            return item
        return None

    db.get.side_effect = _get
    db.scalars.side_effect = [_ScalarResult([line])]

    with patch(
        "app.services.finance.ap.auto_inventory_receipt.InventoryTransactionService.create_receipt"
    ) as create_receipt:
        result = APInvoiceAutoReceiptService.create_for_invoice(
            db, org_id, invoice.invoice_id, uuid4()
        )

    assert result.created_count == 0
    assert result.skipped_count == 1
    create_receipt.assert_not_called()


def test_duplicate_prevention_skips_existing_line_receipt():
    db = MagicMock()
    org_id = uuid4()
    invoice = _invoice(org_id)
    item_id = uuid4()
    line = _line(invoice.invoice_id, item_id, uuid4())
    line.auto_receipt_transaction_id = uuid4()
    item = _item(org_id, item_id)

    def _get(model, _id):
        if model.__name__ == "SupplierInvoice":
            return invoice
        if model.__name__ == "Item":
            return item
        return None

    db.get.side_effect = _get
    db.scalars.side_effect = [_ScalarResult([line])]

    with patch(
        "app.services.finance.ap.auto_inventory_receipt.InventoryTransactionService.create_receipt"
    ) as create_receipt:
        result = APInvoiceAutoReceiptService.create_for_invoice(
            db, org_id, invoice.invoice_id, uuid4()
        )

    assert result.created_count == 0
    assert result.skipped_count == 1
    create_receipt.assert_not_called()


def test_missing_warehouse_blocks_receipt_creation_clearly():
    db = MagicMock()
    org_id = uuid4()
    invoice = _invoice(org_id)
    item_id = uuid4()
    line = _line(invoice.invoice_id, item_id, warehouse_id=None)
    item = _item(org_id, item_id)

    def _get(model, _id):
        if model.__name__ == "SupplierInvoice":
            return invoice
        if model.__name__ == "Item":
            return item
        return None

    db.get.side_effect = _get
    db.scalars.side_effect = [
        _ScalarResult([line]),
        _ScalarResult([]),
    ]

    with pytest.raises(ValidationError, match="warehouse is required"):
        APInvoiceAutoReceiptService.create_for_invoice(
            db, org_id, invoice.invoice_id, uuid4()
        )


def test_serial_tracked_auto_receipt_without_serials_does_not_block_submission():
    db = MagicMock()
    org_id = uuid4()
    invoice = _invoice(org_id)
    item_id = uuid4()
    warehouse_id = uuid4()
    line = _line(invoice.invoice_id, item_id, warehouse_id)
    item = _item(org_id, item_id, serial=True)
    warehouse = _warehouse(org_id, warehouse_id)
    transaction = SimpleNamespace(transaction_id=uuid4())

    def _get(model, _id):
        if model.__name__ == "SupplierInvoice":
            return invoice
        if model.__name__ == "Item":
            return item
        if model.__name__ == "Warehouse":
            return warehouse
        return None

    db.get.side_effect = _get
    db.scalars.side_effect = [
        _ScalarResult([line]),
        _ScalarResult([]),
        _ScalarResult([_period()]),
    ]

    with patch(
        "app.services.finance.ap.auto_inventory_receipt.InventoryTransactionService.create_receipt",
        return_value=transaction,
    ) as create_receipt:
        result = APInvoiceAutoReceiptService.create_for_invoice(
            db, org_id, invoice.invoice_id, uuid4()
        )

    assert result.created_count == 1
    txn_input = create_receipt.call_args.args[2]
    assert txn_input.serial_numbers is None
    assert txn_input.allow_missing_serial_numbers is True


def test_store_approval_mode_creates_pending_approval_without_inventory_receipt():
    db = MagicMock()
    org_id = uuid4()
    invoice = _invoice(
        org_id,
        auto=False,
        receipt_mode=InventoryReceiptMode.STORE_APPROVAL,
    )
    item_id = uuid4()
    warehouse_id = uuid4()
    line = _line(invoice.invoice_id, item_id, warehouse_id)
    item = _item(org_id, item_id)
    warehouse = _warehouse(org_id, warehouse_id)

    def _get(model, _id):
        if model.__name__ == "SupplierInvoice":
            return invoice
        if model.__name__ == "Item":
            return item
        if model.__name__ == "Warehouse":
            return warehouse
        return None

    db.get.side_effect = _get
    db.scalars.side_effect = [
        _ScalarResult([line]),
        _ScalarResult([]),
    ]

    with patch(
        "app.services.finance.ap.auto_inventory_receipt.InventoryTransactionService.create_receipt"
    ) as create_receipt:
        result = APInventoryReceiptApprovalService.create_pending_from_invoice(
            db, org_id, invoice.invoice_id, uuid4()
        )

    assert result.created_count == 1
    create_receipt.assert_not_called()
    approval = next(
        call.args[0]
        for call in db.add.call_args_list
        if isinstance(call.args[0], InvoiceInventoryReceiptApproval)
    )
    assert approval.status == InvoiceInventoryReceiptApprovalStatus.PENDING
    assert approval.supplier_invoice_id == invoice.invoice_id
    assert approval.supplier_invoice_line_id == line.line_id
    assert approval.item_id == item_id
    assert approval.warehouse_id == warehouse_id
    assert approval.requested_quantity == line.quantity


def test_store_approval_serial_tracked_line_without_serials_creates_pending_request():
    db = MagicMock()
    org_id = uuid4()
    invoice = _invoice(
        org_id,
        auto=False,
        receipt_mode=InventoryReceiptMode.STORE_APPROVAL,
    )
    item_id = uuid4()
    warehouse_id = uuid4()
    line = _line(invoice.invoice_id, item_id, warehouse_id)
    item = _item(org_id, item_id, serial=True)
    warehouse = _warehouse(org_id, warehouse_id)

    def _get(model, _id):
        if model.__name__ == "SupplierInvoice":
            return invoice
        if model.__name__ == "Item":
            return item
        if model.__name__ == "Warehouse":
            return warehouse
        return None

    db.get.side_effect = _get
    db.scalars.side_effect = [
        _ScalarResult([line]),
        _ScalarResult([]),
    ]

    result = APInventoryReceiptApprovalService.create_pending_from_invoice(
        db, org_id, invoice.invoice_id, uuid4()
    )

    assert result.created_count == 1
    approval = next(
        call.args[0]
        for call in db.add.call_args_list
        if isinstance(call.args[0], InvoiceInventoryReceiptApproval)
    )
    assert approval.receipt_serial_numbers is None


def test_store_approval_notification_targets_inventory_approvers():
    db = MagicMock()
    org_id = uuid4()
    actor_id = uuid4()
    recipient_id = uuid4()
    invoice = _invoice(
        org_id,
        auto=False,
        receipt_mode=InventoryReceiptMode.STORE_APPROVAL,
    )
    approval = _approval(org_id, invoice.invoice_id, uuid4(), uuid4(), uuid4())

    with (
        patch.object(
            APInventoryReceiptApprovalService,
            "_is_mock_like",
            return_value=False,
        ),
        patch(
            "app.services.finance.ap.inventory_receipt_approval."
            "get_users_with_permission",
            return_value=[SimpleNamespace(person_id=recipient_id)],
        ) as get_users,
        patch(
            "app.services.finance.ap.inventory_receipt_approval."
            "notification_service.create"
        ) as create_notification,
    ):
        APInventoryReceiptApprovalService._notify_store_approvers(
            db,
            org_id,
            invoice,
            approval,
            actor_id=actor_id,
        )

    get_users.assert_called_once()
    create_notification.assert_called_once()
    kwargs = create_notification.call_args.kwargs
    assert kwargs["recipient_id"] == recipient_id
    assert kwargs["action_url"] == (
        f"/inventory/receipt-approvals/{approval.approval_id}"
    )


def test_draft_store_approval_notification_targets_inventory_approvers():
    db = MagicMock()
    org_id = uuid4()
    actor_id = uuid4()
    recipient_id = uuid4()
    invoice = _invoice(
        org_id,
        status=SupplierInvoiceStatus.DRAFT,
        auto=False,
        receipt_mode=InventoryReceiptMode.STORE_APPROVAL,
    )
    item_id = uuid4()
    line = _line(invoice.invoice_id, item_id, uuid4())
    item = _item(org_id, item_id)

    def _get(model, _id):
        if model.__name__ == "SupplierInvoice":
            return invoice
        if model.__name__ == "Item":
            return item
        return None

    db.get.side_effect = _get
    db.scalars.side_effect = [_ScalarResult([line])]

    with (
        patch.object(
            APInventoryReceiptApprovalService,
            "_is_mock_like",
            return_value=False,
        ),
        patch(
            "app.services.finance.ap.inventory_receipt_approval."
            "get_users_with_permission",
            return_value=[SimpleNamespace(person_id=recipient_id)],
        ),
        patch(
            "app.services.finance.ap.inventory_receipt_approval."
            "notification_service.create"
        ) as create_notification,
    ):
        APInventoryReceiptApprovalService.notify_store_approvers_of_draft_invoice(
            db,
            org_id,
            invoice.invoice_id,
            actor_id=actor_id,
        )

    create_notification.assert_called_once()
    kwargs = create_notification.call_args.kwargs
    assert kwargs["recipient_id"] == recipient_id
    assert kwargs["entity_id"] == invoice.invoice_id
    assert kwargs["action_url"] == "/inventory/receipt-approvals"
    assert "Approval will be available after AP submits it" in kwargs["message"]


def test_store_approval_duplicate_prevention_skips_existing_request():
    db = MagicMock()
    org_id = uuid4()
    invoice = _invoice(
        org_id,
        auto=False,
        receipt_mode=InventoryReceiptMode.STORE_APPROVAL,
    )
    item_id = uuid4()
    line = _line(invoice.invoice_id, item_id, uuid4())
    item = _item(org_id, item_id)
    existing = SimpleNamespace(approval_id=uuid4())

    def _get(model, _id):
        if model.__name__ == "SupplierInvoice":
            return invoice
        if model.__name__ == "Item":
            return item
        return None

    db.get.side_effect = _get
    db.scalars.side_effect = [
        _ScalarResult([line]),
        _ScalarResult([existing]),
    ]

    result = APInventoryReceiptApprovalService.create_pending_from_invoice(
        db, org_id, invoice.invoice_id, uuid4()
    )

    assert result.created_count == 0
    assert result.skipped_count == 1


def test_approve_store_receipt_posts_inventory_and_links_line():
    db = MagicMock()
    org_id = uuid4()
    user_id = uuid4()
    invoice = _invoice(
        org_id,
        auto=False,
        receipt_mode=InventoryReceiptMode.STORE_APPROVAL,
    )
    item_id = uuid4()
    warehouse_id = uuid4()
    line = _line(invoice.invoice_id, item_id, warehouse_id)
    item = _item(org_id, item_id)
    item.costing_method = SimpleNamespace(name="FIFO")
    warehouse = _warehouse(org_id, warehouse_id)
    warehouse.is_receiving = True
    approval = _approval(
        org_id, invoice.invoice_id, line.line_id, item_id, warehouse_id
    )
    transaction = SimpleNamespace(transaction_id=uuid4())

    def _get(model, _id):
        if model.__name__ == "InvoiceInventoryReceiptApproval":
            return approval
        if model.__name__ == "SupplierInvoice":
            return invoice
        if model.__name__ == "SupplierInvoiceLine":
            return line
        if model.__name__ == "Item":
            return item
        if model.__name__ == "Warehouse":
            return warehouse
        return None

    db.get.side_effect = _get
    db.scalars.side_effect = [_ScalarResult([_period()])]

    with patch(
        "app.services.finance.ap.inventory_receipt_approval."
        "InventoryTransactionService.create_receipt",
        return_value=transaction,
    ) as create_receipt:
        result = APInventoryReceiptApprovalService.approve(
            db,
            org_id,
            approval.approval_id,
            user_id,
            approved_quantity=Decimal("2"),
        )

    assert result.status == InvoiceInventoryReceiptApprovalStatus.POSTED_TO_INVENTORY
    assert result.approved_quantity == Decimal("2")
    assert result.approved_by_user_id == user_id
    assert result.inventory_transaction_id == transaction.transaction_id
    assert line.auto_receipt_transaction_id == transaction.transaction_id
    create_receipt.assert_called_once()
    txn_input = create_receipt.call_args.args[2]
    assert txn_input.quantity == Decimal("2")
    assert txn_input.source_document_id == invoice.invoice_id
    assert txn_input.source_document_line_id == line.line_id


def test_approve_store_receipt_allows_serial_tracked_item_without_serials():
    db = MagicMock()
    org_id = uuid4()
    user_id = uuid4()
    invoice = _invoice(
        org_id,
        auto=False,
        receipt_mode=InventoryReceiptMode.STORE_APPROVAL,
    )
    item_id = uuid4()
    warehouse_id = uuid4()
    line = _line(invoice.invoice_id, item_id, warehouse_id)
    item = _item(org_id, item_id, serial=True)
    item.costing_method = SimpleNamespace(name="FIFO")
    warehouse = _warehouse(org_id, warehouse_id)
    warehouse.is_receiving = True
    approval = _approval(
        org_id, invoice.invoice_id, line.line_id, item_id, warehouse_id
    )
    transaction = SimpleNamespace(transaction_id=uuid4())

    def _get(model, _id):
        if model.__name__ == "InvoiceInventoryReceiptApproval":
            return approval
        if model.__name__ == "SupplierInvoice":
            return invoice
        if model.__name__ == "SupplierInvoiceLine":
            return line
        if model.__name__ == "Item":
            return item
        if model.__name__ == "Warehouse":
            return warehouse
        return None

    db.get.side_effect = _get
    db.scalars.side_effect = [_ScalarResult([_period()])]

    with patch(
        "app.services.finance.ap.inventory_receipt_approval."
        "InventoryTransactionService.create_receipt",
        return_value=transaction,
    ) as create_receipt:
        APInventoryReceiptApprovalService.approve(
            db,
            org_id,
            approval.approval_id,
            user_id,
            approved_quantity=Decimal("2"),
        )

    txn_input = create_receipt.call_args.args[2]
    assert txn_input.serial_numbers is None
    assert txn_input.allow_missing_serial_numbers is True


def test_approve_store_receipt_posts_inventory_before_invoice_posting_or_payment():
    db = MagicMock()
    org_id = uuid4()
    user_id = uuid4()
    invoice = _invoice(
        org_id,
        status=SupplierInvoiceStatus.SUBMITTED,
        auto=False,
        receipt_mode=InventoryReceiptMode.STORE_APPROVAL,
    )
    item_id = uuid4()
    warehouse_id = uuid4()
    line = _line(invoice.invoice_id, item_id, warehouse_id)
    item = _item(org_id, item_id)
    item.costing_method = SimpleNamespace(name="FIFO")
    warehouse = _warehouse(org_id, warehouse_id)
    warehouse.is_receiving = True
    approval = _approval(
        org_id, invoice.invoice_id, line.line_id, item_id, warehouse_id
    )
    transaction = SimpleNamespace(transaction_id=uuid4())

    def _get(model, _id):
        if model.__name__ == "InvoiceInventoryReceiptApproval":
            return approval
        if model.__name__ == "SupplierInvoice":
            return invoice
        if model.__name__ == "SupplierInvoiceLine":
            return line
        if model.__name__ == "Item":
            return item
        if model.__name__ == "Warehouse":
            return warehouse
        return None

    db.get.side_effect = _get
    db.scalars.side_effect = [_ScalarResult([_period()])]

    with patch(
        "app.services.finance.ap.inventory_receipt_approval."
        "InventoryTransactionService.create_receipt",
        return_value=transaction,
    ) as create_receipt:
        result = APInventoryReceiptApprovalService.approve(
            db,
            org_id,
            approval.approval_id,
            user_id,
            approved_quantity=Decimal("2"),
        )

    assert invoice.status == SupplierInvoiceStatus.SUBMITTED
    assert result.status == InvoiceInventoryReceiptApprovalStatus.POSTED_TO_INVENTORY
    assert result.inventory_transaction_id == transaction.transaction_id
    assert line.auto_receipt_transaction_id == transaction.transaction_id
    create_receipt.assert_called_once()


def test_partial_store_receipt_keeps_remaining_quantity_pending():
    db = MagicMock()
    org_id = uuid4()
    user_id = uuid4()
    invoice = _invoice(
        org_id,
        auto=False,
        receipt_mode=InventoryReceiptMode.STORE_APPROVAL,
    )
    item_id = uuid4()
    warehouse_id = uuid4()
    line = _line(invoice.invoice_id, item_id, warehouse_id)
    item = _item(org_id, item_id)
    item.costing_method = SimpleNamespace(name="FIFO")
    warehouse = _warehouse(org_id, warehouse_id)
    warehouse.is_receiving = True
    approval = _approval(
        org_id, invoice.invoice_id, line.line_id, item_id, warehouse_id
    )
    transaction = SimpleNamespace(transaction_id=uuid4())

    def _get(model, _id):
        if model.__name__ == "InvoiceInventoryReceiptApproval":
            return approval
        if model.__name__ == "SupplierInvoice":
            return invoice
        if model.__name__ == "SupplierInvoiceLine":
            return line
        if model.__name__ == "Item":
            return item
        if model.__name__ == "Warehouse":
            return warehouse
        return None

    db.get.side_effect = _get
    db.scalars.side_effect = [_ScalarResult([_period()])]

    with patch(
        "app.services.finance.ap.inventory_receipt_approval."
        "InventoryTransactionService.create_receipt",
        return_value=transaction,
    ) as create_receipt:
        result = APInventoryReceiptApprovalService.approve(
            db,
            org_id,
            approval.approval_id,
            user_id,
            approved_quantity=Decimal("1.5"),
        )

    assert result.status == InvoiceInventoryReceiptApprovalStatus.PARTIALLY_RECEIVED
    assert result.approved_quantity == Decimal("1.5")
    assert result.inventory_transaction_id == transaction.transaction_id
    assert line.auto_receipt_transaction_id is None
    create_receipt.assert_called_once()
    residual = next(
        call.args[0]
        for call in db.add.call_args_list
        if isinstance(call.args[0], InvoiceInventoryReceiptApproval)
    )
    assert residual.status == InvoiceInventoryReceiptApprovalStatus.PENDING
    assert residual.requested_quantity == Decimal("0.5")
    assert residual.supplier_invoice_line_id == line.line_id
    assert residual.warehouse_id == warehouse_id


def test_reject_store_receipt_does_not_post_inventory():
    db = MagicMock()
    org_id = uuid4()
    user_id = uuid4()
    approval = _approval(org_id, uuid4(), uuid4(), uuid4(), uuid4())

    def _get(model, _id):
        if model.__name__ == "InvoiceInventoryReceiptApproval":
            return approval
        return None

    db.get.side_effect = _get

    with patch(
        "app.services.finance.ap.inventory_receipt_approval."
        "InventoryTransactionService.create_receipt"
    ) as create_receipt:
        result = APInventoryReceiptApprovalService.reject(
            db,
            org_id,
            approval.approval_id,
            user_id,
            rejection_reason="Items not received",
        )

    assert result.status == InvoiceInventoryReceiptApprovalStatus.REJECTED
    assert result.rejected_by_user_id == user_id
    assert result.rejection_reason == "Items not received"
    create_receipt.assert_not_called()
