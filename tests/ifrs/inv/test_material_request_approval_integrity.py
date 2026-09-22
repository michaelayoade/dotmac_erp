"""Regression coverage for material-request serials and atomic approval."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.models.inventory.material_request import (
    MaterialRequestStatus,
    MaterialRequestType,
)
from app.services.inventory.material_request_web import MaterialRequestWebService
from app.services.inventory.serial import InventorySerialService
from app.services.inventory.transaction import InventoryTransactionService
from app.services.operations.inv_web import OperationsInventoryWebService

STOCK_TYPES = [
    MaterialRequestType.ISSUE,
    MaterialRequestType.TRANSFER,
    MaterialRequestType.MANUFACTURE,
]


@pytest.fixture
def approval(monkeypatch):
    org_id, user_id, warehouse_id = uuid4(), uuid4(), uuid4()
    lines = [
        SimpleNamespace(
            sequence=sequence,
            item_id=uuid4(),
            inventory_item_id=uuid4(),
            warehouse_id=warehouse_id,
            requested_qty=Decimal("1"),
            ordered_qty=Decimal("0"),
            serial_numbers=[f"SERIAL-{sequence}"],
            uom="Nos",
        )
        for sequence in (1, 2)
    ]
    request = SimpleNamespace(
        request_id=uuid4(),
        request_number="MR-REGRESSION",
        organization_id=org_id,
        request_type=MaterialRequestType.ISSUE,
        status=MaterialRequestStatus.SUBMITTED,
        default_warehouse_id=None,
        transfer_to_warehouse_id=uuid4(),
        items=lines,
        source_system="sub",
        updated_by_id=None,
    )
    db = MagicMock()
    db.scalars.return_value.unique.return_value.first.return_value = request
    db.get.return_value = SimpleNamespace(
        average_cost=Decimal("12"),
        base_uom="Nos",
        currency_code="NGN",
    )
    period = SimpleNamespace(fiscal_period_id=uuid4())
    monkeypatch.setattr(
        "app.services.finance.gl.period_guard.PeriodGuardService.get_period_for_date",
        MagicMock(return_value=period),
    )
    issue, transfer, emit = MagicMock(), MagicMock(), MagicMock()
    monkeypatch.setattr(InventoryTransactionService, "create_issue", issue)
    monkeypatch.setattr(InventoryTransactionService, "create_transfer", transfer)
    monkeypatch.setattr(MaterialRequestWebService, "_emit_sub_outcome", emit)
    return SimpleNamespace(
        db=db,
        request=request,
        org_id=org_id,
        user_id=user_id,
        issue=issue,
        transfer=transfer,
        emit=emit,
    )


def _approve(context):
    return MaterialRequestWebService.approve_request(
        context.db,
        context.org_id,
        user_id=context.user_id,
        request_id=str(context.request.request_id),
    )


def _transaction_mock(context):
    if context.request.request_type == MaterialRequestType.TRANSFER:
        return context.transfer
    return context.issue


@pytest.mark.parametrize("request_type", STOCK_TYPES)
@pytest.mark.parametrize(
    "initial_status",
    [MaterialRequestStatus.SUBMITTED, MaterialRequestStatus.PENDING_STOCK],
)
def test_stock_approval_preserves_serials_provenance_and_transaction_owner(
    approval, request_type, initial_status
):
    approval.request.request_type = request_type
    approval.request.status = initial_status
    result = _approve(approval)
    transaction = _transaction_mock(approval)

    assert result is approval.request
    assert transaction.call_count == 2
    for call, line in zip(transaction.call_args_list, result.items, strict=True):
        txn_input = call.args[2]
        assert txn_input.serial_numbers == line.serial_numbers
        assert txn_input.source_document_type == "MATERIAL_REQUEST"
        assert txn_input.source_document_id == result.request_id
        assert txn_input.source_document_line_id == line.item_id
        assert txn_input.quantity == line.requested_qty
        assert txn_input.allow_missing_serial_numbers is False
        assert call.kwargs["auto_commit"] is False
        assert line.ordered_qty == line.requested_qty

    expected = (
        MaterialRequestStatus.TRANSFERRED
        if request_type == MaterialRequestType.TRANSFER
        else MaterialRequestStatus.ISSUED
    )
    assert result.status == expected
    approval.emit.assert_called_once_with(
        approval.db, approval.org_id, result, initial_status, expected, approval.user_id
    )
    approval.db.commit.assert_not_called()
    approval.db.rollback.assert_not_called()


@pytest.mark.parametrize("request_type", STOCK_TYPES)
@pytest.mark.parametrize("failure_position", [0, 1])
def test_partial_stock_failure_never_emits_a_terminal_outcome(
    approval, request_type, failure_position
):
    approval.request.request_type = request_type
    outcomes = [None, None]
    outcomes[failure_position] = ValueError("serial validation failed")
    _transaction_mock(approval).side_effect = outcomes

    with pytest.raises(ValueError, match="Material request approval failed"):
        _approve(approval)

    assert approval.request.status == MaterialRequestStatus.SUBMITTED
    assert approval.request.updated_by_id is None
    assert approval.request.items[failure_position].ordered_qty == Decimal("0")
    approval.emit.assert_not_called()
    approval.db.commit.assert_not_called()
    approval.db.rollback.assert_not_called()


@pytest.mark.parametrize("request_type", STOCK_TYPES)
def test_all_failed_lines_keep_the_existing_failure_contract(approval, request_type):
    approval.request.request_type = request_type
    _transaction_mock(approval).side_effect = ValueError("stock unavailable")

    with pytest.raises(ValueError, match="All items failed to process"):
        _approve(approval)

    assert approval.request.status == MaterialRequestStatus.SUBMITTED
    assert all(line.ordered_qty == Decimal("0") for line in approval.request.items)
    approval.emit.assert_not_called()


@pytest.mark.parametrize("missing_field", ["warehouse", "item"])
def test_missing_line_dependency_cannot_produce_partial_success(
    approval, missing_field
):
    if missing_field == "warehouse":
        approval.request.items[1].warehouse_id = None
    else:
        approval.db.get.side_effect = [approval.db.get.return_value, None]

    with pytest.raises(ValueError, match="Material request approval failed"):
        _approve(approval)

    assert approval.request.status == MaterialRequestStatus.SUBMITTED
    approval.emit.assert_not_called()


def test_absent_serials_still_fail_real_serial_quantity_validation(approval):
    approval.request.items = approval.request.items[:1]
    approval.request.items[0].serial_numbers = None

    def validate_serials(db, org_id, txn_input, user_id, **kwargs):
        InventorySerialService.validate_serial_quantity(
            txn_input.quantity,
            InventorySerialService.normalize_serial_numbers(txn_input.serial_numbers),
        )

    approval.issue.side_effect = validate_serials
    with pytest.raises(ValueError, match="Serial-tracked quantity requires"):
        _approve(approval)

    assert approval.request.status == MaterialRequestStatus.SUBMITTED
    approval.emit.assert_not_called()


@pytest.mark.parametrize("request_type", STOCK_TYPES)
def test_web_caller_rolls_back_partial_failure_instead_of_committing(
    approval, request_type
):
    approval.request.request_type = request_type
    _transaction_mock(approval).side_effect = [None, ValueError("serial unavailable")]
    auth = SimpleNamespace(organization_id=approval.org_id, user_id=approval.user_id)

    response = OperationsInventoryWebService().approve_material_request_response(
        str(approval.request.request_id), auth, approval.db
    )

    assert response.status_code == 303
    approval.db.rollback.assert_called_once_with()
    approval.db.commit.assert_not_called()
    approval.emit.assert_not_called()


def test_purchase_approval_still_has_no_stock_movement(approval):
    approval.request.request_type = MaterialRequestType.PURCHASE

    result = _approve(approval)

    assert result.status == MaterialRequestStatus.ORDERED
    approval.issue.assert_not_called()
    approval.transfer.assert_not_called()
    approval.emit.assert_called_once()
