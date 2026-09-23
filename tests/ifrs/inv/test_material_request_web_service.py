from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace
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
        "app.services.inventory.material_request_web._generate_material_request_number",
        return_value="MAT-MR-2026-00011",
    ):
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
        "app.services.inventory.material_request_web._generate_material_request_number",
        return_value="MAT-MR-2026-00012",
    ):
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
    request.items = []
    request.source_system = "sub"
    db.scalars.return_value.first.return_value = request
    db.execute.return_value.all.return_value = []

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
    request.items = []
    request.source_system = "sub"
    db.scalars.return_value.first.return_value = request
    db.execute.return_value.all.return_value = []

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
    request.items = []
    request.source_system = "sub"
    db.scalars.return_value.first.return_value = request
    db.execute.return_value.all.return_value = []

    with pytest.raises(ValueError, match="Cancellation reason is required"):
        MaterialRequestWebService.cancel_request(
            db=db,
            organization_id=organization_id,
            user_id=uuid.uuid4(),
            request_id=str(uuid.uuid4()),
            cancel_reason="   ",
        )

    statement = db.scalars.call_args.args[0]
    assert organization_id in statement.compile().params.values()
    assert "organization_id" in str(statement)
    assert "FOR UPDATE" in str(statement)
    db.scalars.return_value.first.return_value = None
    with pytest.raises(ValueError, match="Material request not found"):
        MaterialRequestWebService.cancel_request(
            db=db,
            organization_id=organization_id,
            user_id=uuid.uuid4(),
            request_id=str(uuid.uuid4()),
            cancel_reason="Wrong warehouse selected",
        )


def _scalar_first(value):
    result = MagicMock()
    result.first.return_value = value
    return result


def _scalar_all(values):
    result = MagicMock()
    result.all.return_value = values
    return result


@pytest.mark.parametrize("source_system", ["sub", "erp"])
def test_detail_context_explains_pending_stock_shortage(source_system: str) -> None:
    db = MagicMock()
    organization_id = uuid.uuid4()
    request_id = uuid.uuid4()
    item_id = uuid.uuid4()
    warehouse_id = uuid.uuid4()

    request = SimpleNamespace(
        request_id=request_id,
        organization_id=organization_id,
        request_number="MR202609-00013",
        request_type=MaterialRequestType.ISSUE,
        source_system=source_system,
        status=MaterialRequestStatus.PENDING_STOCK,
        schedule_date=None,
        default_warehouse_id=None,
        transfer_to_warehouse_id=None,
        requested_by_id=None,
        project_id=None,
        ticket_id=None,
        remarks=None,
        cancel_reason=None,
        created_at=None,
        updated_at=None,
        last_synced_at=None,
        erpnext_id=None,
    )
    line = SimpleNamespace(
        item_id=uuid.uuid4(),
        inventory_item_id=item_id,
        warehouse_id=warehouse_id,
        requested_qty=Decimal("1"),
        ordered_qty=Decimal("0"),
        uom="Nos",
        schedule_date=None,
        sequence=1,
    )
    item = SimpleNamespace(
        item_id=item_id, item_code="EDGE-16", item_name="Edge 16", base_uom="Nos"
    )
    warehouse = SimpleNamespace(
        warehouse_id=warehouse_id,
        warehouse_code="Stores - DT",
        warehouse_name="Stores Garki",
    )
    db.scalars.side_effect = [
        _scalar_first(request),
        _scalar_all([line]),
        _scalar_all([item]),
        _scalar_all([warehouse]),
    ]
    db.execute.return_value.all.return_value = [(item_id, warehouse_id, Decimal("0"))]

    with patch(
        "app.services.inventory.material_request_web.get_recent_activity_for_record",
        return_value=[],
    ):
        context = MaterialRequestWebService.detail_context(
            db,
            str(organization_id),
            str(request_id),
        )

    material_request = context["material_request"]
    detail_line = context["material_request_items"][0]
    assert material_request["stock_required"] is True
    assert material_request["stock_is_sufficient"] is False
    assert material_request["can_approve"] is False
    assert material_request["approval_blocked_reason"] is None
    assert material_request["can_issue_lines"] is True
    assert detail_line["available_qty_value"] == 0.0
    assert detail_line["shortage_qty_value"] == 1.0
    assert detail_line["has_sufficient_stock"] is False


@pytest.mark.parametrize(
    ("source_system", "can_approve", "can_issue_lines"),
    [("sub", False, True), ("erp", False, True)],
)
def test_detail_context_stock_ready_actions(
    source_system: str, can_approve: bool, can_issue_lines: bool
) -> None:
    db = MagicMock()
    organization_id = uuid.uuid4()
    request_id = uuid.uuid4()
    item_id = uuid.uuid4()
    warehouse_id = uuid.uuid4()

    request = SimpleNamespace(
        request_id=request_id,
        organization_id=organization_id,
        request_number="MR202609-00014",
        request_type=MaterialRequestType.ISSUE,
        source_system=source_system,
        status=MaterialRequestStatus.PENDING_STOCK,
        schedule_date=None,
        default_warehouse_id=warehouse_id,
        transfer_to_warehouse_id=None,
        requested_by_id=None,
        project_id=None,
        ticket_id=None,
        remarks=None,
        cancel_reason=None,
        created_at=None,
        updated_at=None,
        last_synced_at=None,
        erpnext_id=None,
    )
    line = SimpleNamespace(
        item_id=uuid.uuid4(),
        inventory_item_id=item_id,
        warehouse_id=None,
        requested_qty=Decimal("1"),
        ordered_qty=Decimal("0"),
        uom="Nos",
        schedule_date=None,
        sequence=1,
    )
    item = SimpleNamespace(
        item_id=item_id, item_code="EDGE-16", item_name="Edge 16", base_uom="Nos"
    )
    warehouse = SimpleNamespace(
        warehouse_id=warehouse_id,
        warehouse_code="Stores - DT",
        warehouse_name="Stores Garki",
    )
    db.scalars.side_effect = [
        _scalar_first(request),
        _scalar_all([line]),
        _scalar_all([item]),
        _scalar_all([warehouse]),
        _scalar_first(warehouse),
    ]
    db.execute.return_value.all.return_value = [(item_id, warehouse_id, Decimal("2"))]

    with patch(
        "app.services.inventory.material_request_web.get_recent_activity_for_record",
        return_value=[],
    ):
        context = MaterialRequestWebService.detail_context(
            db,
            str(organization_id),
            str(request_id),
        )

    material_request = context["material_request"]
    detail_line = context["material_request_items"][0]
    assert material_request["stock_is_sufficient"] is True
    assert material_request["can_approve"] is can_approve
    assert material_request["can_issue_lines"] is can_issue_lines
    assert material_request["approval_blocked_reason"] is None
    assert detail_line["available_qty_value"] == 2.0
    assert detail_line["shortage_qty_value"] == 0.0
    assert detail_line["has_sufficient_stock"] is True


def test_approve_request_allows_stock_ready_pending_issue_request() -> None:
    db = MagicMock()
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()
    request_id = uuid.uuid4()
    warehouse_id = uuid.uuid4()
    item_id = uuid.uuid4()
    line_id = uuid.uuid4()

    line = MagicMock()
    line.sequence = 1
    line.warehouse_id = warehouse_id
    line.inventory_item_id = item_id
    line.requested_qty = Decimal("1")
    line.uom = "Nos"
    line.item_id = line_id
    line.ordered_qty = Decimal("0")

    request = MagicMock()
    request.request_id = request_id
    request.request_number = "MR202609-00014"
    request.source_system = "sub"
    request.source_reference = str(uuid.uuid4())
    request.organization_id = organization_id
    request.status = MaterialRequestStatus.PENDING_STOCK
    request.request_type = MaterialRequestType.ISSUE
    request.default_warehouse_id = None
    request.transfer_to_warehouse_id = None
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
    item.organization_id = organization_id
    item.item_code = "ITEM001"
    line.serial_numbers = []
    line.out_of_stock = False
    db.get.return_value = item

    with patch(
        "app.services.inventory.transaction.InventoryTransactionService.create_issue"
    ) as mock_create_issue:
        result = MaterialRequestWebService.approve_request(
            db=db,
            organization_id=organization_id,
            user_id=user_id,
            request_id=str(request_id),
        )

    assert result is request
    assert request.status == MaterialRequestStatus.ISSUED
    assert request.updated_by_id == user_id
    assert line.ordered_qty == line.requested_qty
    mock_create_issue.assert_called_once()


def test_legacy_approve_rejects_native_issue_request() -> None:
    db = MagicMock()
    request = MagicMock(
        status=MaterialRequestStatus.SUBMITTED,
        request_type=MaterialRequestType.ISSUE,
        source_system="erp",
        items=[MagicMock()],
    )
    db.scalars.return_value.unique.return_value.first.return_value = request

    with pytest.raises(ValueError, match="line-item issue form"):
        MaterialRequestWebService.approve_request(
            db=db,
            organization_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            request_id=str(uuid.uuid4()),
        )

    assert request.status == MaterialRequestStatus.SUBMITTED


def test_sub_outcome_flushes_current_source_time_before_hook() -> None:
    db = MagicMock()
    request = SimpleNamespace(source_system="sub", updated_at=None)

    def inspect_hook(**kwargs):
        assert request.updated_at.tzinfo is not None
        db.flush.assert_called_once()
        assert kwargs["request"] is request

    with patch(
        "app.services.sync.sub.procurement._ProcurementMixin._emit_sub_material_request_status_changed",
        side_effect=inspect_hook,
    ) as emit:
        MaterialRequestWebService._emit_sub_outcome(
            db,
            uuid.uuid4(),
            request,
            MaterialRequestStatus.SUBMITTED,
            MaterialRequestStatus.ISSUED,
            uuid.uuid4(),
        )
    emit.assert_called_once()
    db.commit.assert_not_called()
