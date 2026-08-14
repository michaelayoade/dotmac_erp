from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.models.inventory.inventory_transaction import TransactionType
from app.models.inventory.material_request import (
    MaterialRequestStatus,
    MaterialRequestType,
)
from app.services.inventory.material_request_web import MaterialRequestWebService


def test_create_from_form_requires_destination_for_transfer() -> None:
    db = MagicMock()

    with patch(
        "app.services.finance.common.numbering.SyncNumberingService"
    ) as mock_numbering_cls:
        mock_numbering = MagicMock()
        mock_numbering.generate_next_number.return_value = "MAT-MR-2026-00011"
        mock_numbering_cls.return_value = mock_numbering

        with pytest.raises(ValueError, match="Destination warehouse is required"):
            MaterialRequestWebService.create_from_form(
                db=db,
                organization_id=uuid.uuid4(),
                user_id=uuid.uuid4(),
                request_type="TRANSFER",
                schedule_date="2026-04-09",
                default_warehouse_id=str(uuid.uuid4()),
                items=[
                    {
                        "item_id": str(uuid.uuid4()),
                        "qty": "2",
                    }
                ],
            )


def test_create_from_form_sets_transfer_destination() -> None:
    db = MagicMock()
    added_objects: list[object] = []
    db.add.side_effect = added_objects.append

    source_warehouse_id = uuid.uuid4()
    destination_warehouse_id = uuid.uuid4()

    with patch(
        "app.services.finance.common.numbering.SyncNumberingService"
    ) as mock_numbering_cls:
        mock_numbering = MagicMock()
        mock_numbering.generate_next_number.return_value = "MAT-MR-2026-00012"
        mock_numbering_cls.return_value = mock_numbering

        request = MaterialRequestWebService.create_from_form(
            db=db,
            organization_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            request_type="TRANSFER",
            schedule_date="2026-04-09",
            default_warehouse_id=str(source_warehouse_id),
            transfer_to_warehouse_id=str(destination_warehouse_id),
            items=[
                {
                    "item_id": str(uuid.uuid4()),
                    "warehouse_id": str(source_warehouse_id),
                    "qty": "2",
                }
            ],
        )

    assert request.request_type == MaterialRequestType.TRANSFER
    assert request.default_warehouse_id == source_warehouse_id
    assert request.transfer_to_warehouse_id == destination_warehouse_id
    assert len(added_objects) == 2


def test_approve_request_posts_real_transfer() -> None:
    db = MagicMock()
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()
    request_id = uuid.uuid4()
    source_warehouse_id = uuid.uuid4()
    destination_warehouse_id = uuid.uuid4()
    item_id = uuid.uuid4()
    line_id = uuid.uuid4()

    line = MagicMock()
    line.sequence = 1
    line.warehouse_id = None
    line.inventory_item_id = item_id
    line.requested_qty = Decimal("3")
    line.uom = "Nos"
    line.item_id = line_id
    line.ordered_qty = Decimal("0")

    request = MagicMock()
    request.request_id = request_id
    request.request_number = "MAT-MR-2026-00013"
    request.organization_id = organization_id
    request.status = MaterialRequestStatus.SUBMITTED
    request.request_type = MaterialRequestType.TRANSFER
    request.default_warehouse_id = source_warehouse_id
    request.transfer_to_warehouse_id = destination_warehouse_id
    request.items = [line]
    request.updated_by_id = None

    request_result = MagicMock()
    request_result.unique.return_value.first.return_value = request

    fiscal_period = MagicMock()
    fiscal_period.fiscal_period_id = uuid.uuid4()
    fiscal_result = MagicMock()
    fiscal_result.first.return_value = fiscal_period

    db.scalars.side_effect = [request_result, fiscal_result]

    item = MagicMock()
    item.average_cost = Decimal("12")
    item.base_uom = "Nos"
    item.currency_code = "NGN"
    db.get.return_value = item

    with patch(
        "app.services.inventory.transaction.InventoryTransactionService.create_transfer"
    ) as mock_create_transfer:
        result = MaterialRequestWebService.approve_request(
            db=db,
            organization_id=organization_id,
            user_id=user_id,
            request_id=str(request_id),
        )

    assert result is request
    assert request.status == MaterialRequestStatus.TRANSFERRED
    assert request.updated_by_id == user_id
    assert line.ordered_qty == line.requested_qty

    call_args, call_kwargs = mock_create_transfer.call_args
    txn_input = call_args[2]
    assert txn_input.transaction_type == TransactionType.TRANSFER
    assert txn_input.warehouse_id == source_warehouse_id
    assert txn_input.to_warehouse_id == destination_warehouse_id
    assert txn_input.source_document_type == "MATERIAL_REQUEST"
    assert txn_input.source_document_id == request_id
    assert txn_input.source_document_line_id == line_id
    assert call_kwargs["auto_commit"] is False


@pytest.mark.parametrize(
    "status",
    [
        MaterialRequestStatus.DRAFT,
        MaterialRequestStatus.SUBMITTED,
        MaterialRequestStatus.PENDING_STOCK,
    ],
)
def test_cancel_request_allows_unprocessed_states(status) -> None:
    db = MagicMock()
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()
    request_id = uuid.uuid4()
    request = MagicMock(
        organization_id=organization_id,
        status=status,
        source_system="sub",
    )
    db.get.return_value = request

    with patch.object(MaterialRequestWebService, "_emit_sub_outcome") as emit:
        result = MaterialRequestWebService.cancel_request(
            db=db,
            organization_id=organization_id,
            user_id=user_id,
            request_id=str(request_id),
            cancel_reason="Wrong warehouse selected",
        )

    assert result is request
    assert request.status == MaterialRequestStatus.CANCELLED
    assert request.cancel_reason == "Wrong warehouse selected"
    assert request.updated_by_id == user_id
    emit.assert_called_once_with(
        db,
        organization_id,
        request,
        status,
        MaterialRequestStatus.CANCELLED,
        user_id,
    )


def test_cancel_request_rejects_processed_state() -> None:
    db = MagicMock()
    organization_id = uuid.uuid4()
    request = MagicMock(
        organization_id=organization_id,
        status=MaterialRequestStatus.ISSUED,
    )
    db.get.return_value = request

    with pytest.raises(ValueError, match="pending-stock"):
        MaterialRequestWebService.cancel_request(
            db=db,
            organization_id=organization_id,
            user_id=uuid.uuid4(),
            request_id=str(uuid.uuid4()),
            cancel_reason="Wrong warehouse selected",
        )

    assert request.status == MaterialRequestStatus.ISSUED


def test_cancel_request_requires_reason_and_enforces_tenant() -> None:
    db = MagicMock()
    organization_id = uuid.uuid4()
    request = MagicMock(
        organization_id=organization_id,
        status=MaterialRequestStatus.PENDING_STOCK,
    )
    db.get.return_value = request

    with pytest.raises(ValueError, match="Cancellation reason is required"):
        MaterialRequestWebService.cancel_request(
            db=db,
            organization_id=organization_id,
            user_id=uuid.uuid4(),
            request_id=str(uuid.uuid4()),
            cancel_reason="   ",
        )

    request.organization_id = uuid.uuid4()
    with pytest.raises(ValueError, match="Material request not found"):
        MaterialRequestWebService.cancel_request(
            db=db,
            organization_id=organization_id,
            user_id=uuid.uuid4(),
            request_id=str(uuid.uuid4()),
            cancel_reason="Wrong warehouse selected",
        )
