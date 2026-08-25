"""Focused behavior tests for canonical Sub material-request intake."""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest

from app.models.inventory.material_request import (
    MaterialRequestStatus,
    MaterialRequestType,
)
from app.schemas.sync.dotmac_sub import (
    SubMaterialRequestItemPayload,
    SubMaterialRequestPayload,
)
from app.services.sync.dotmac_sub_sync_service import DotmacSubSyncService
from app.services.sync.sub.errors import SubReplayConflictError


@pytest.fixture
def org_id() -> UUID:
    return UUID("00000000-0000-0000-0000-000000000001")


def _payload(
    *, quantity: str = "5", status: str = "issued"
) -> SubMaterialRequestPayload:
    return SubMaterialRequestPayload(
        source_request_id="sub-mr-123",
        request_type="ISSUE",
        status=status,
        items=[
            SubMaterialRequestItemPayload(
                item_code="ITEM001",
                quantity=Decimal(quantity),
                from_warehouse_code="Stores - DT",
            )
        ],
    )


def _existing_request(*, quantity: str = "5") -> tuple[MagicMock, MagicMock, UUID]:
    warehouse_id = uuid4()
    item = MagicMock(
        item_id=uuid4(),
        base_uom="Nos",
        track_lots=False,
        track_serial_numbers=False,
    )
    line = MagicMock(
        item_id=uuid4(),
        sequence=1,
        inventory_item_id=item.item_id,
        warehouse_id=warehouse_id,
        requested_qty=Decimal(quantity),
        uom="Nos",
        serial_numbers=None,
    )
    request = MagicMock(
        request_id=uuid4(),
        request_number="MAT-MR-2026-00001",
        status=MaterialRequestStatus.ISSUED,
        request_type=MaterialRequestType.ISSUE,
        schedule_date=None,
        requested_by_id=None,
        project_id=None,
        remarks=None,
        items=[line],
    )
    return request, item, warehouse_id


def test_identical_replay_returns_existing_request(org_id: UUID) -> None:
    request, item, warehouse_id = _existing_request()
    db = MagicMock()
    db.scalar.side_effect = [item, warehouse_id, request]

    result = DotmacSubSyncService(db).create_material_request(org_id, _payload())

    assert result.request_id == request.request_id
    assert result.request_number == request.request_number
    assert result.status == MaterialRequestStatus.ISSUED.value
    assert result.source_request_id == "sub-mr-123"
    db.add.assert_not_called()


def test_changed_replay_is_rejected(org_id: UUID) -> None:
    request, item, warehouse_id = _existing_request(quantity="1")
    db = MagicMock()
    db.scalar.side_effect = [item, warehouse_id, request]

    with pytest.raises(SubReplayConflictError, match="cannot be modified"):
        DotmacSubSyncService(db).create_material_request(org_id, _payload(quantity="3"))


def test_unknown_inventory_item_fails_closed(org_id: UUID) -> None:
    db = MagicMock()
    db.scalar.return_value = None

    with pytest.raises(ValueError, match="Item not found: ITEM001"):
        DotmacSubSyncService(db).create_material_request(org_id, _payload())


def test_insufficient_stock_creates_pending_stock_request(org_id: UUID) -> None:
    db = MagicMock()
    item = MagicMock(
        item_id=uuid4(),
        base_uom="Nos",
        track_lots=False,
        track_serial_numbers=False,
    )
    warehouse_id = uuid4()
    db.scalar.side_effect = [item, warehouse_id, None]
    added: list[object] = []
    db.add.side_effect = added.append

    def populate_request_id() -> None:
        for obj in added:
            if hasattr(obj, "request_id") and obj.request_id is None:
                obj.request_id = uuid4()

    db.flush.side_effect = populate_request_id
    service = DotmacSubSyncService(db)

    with (
        patch(
            "app.services.finance.common.numbering.SyncNumberingService.generate_next_number",
            return_value="MAT-MR-2026-00002",
        ),
        patch(
            "app.services.inventory.transaction.InventoryTransactionService.get_current_balance",
            return_value=Decimal("1"),
        ),
        patch.object(service, "_snapshot_material_request_lines", return_value=[]),
        patch.object(service, "_emit_sub_material_request_status_changed"),
    ):
        result = service.create_material_request(org_id, _payload(quantity="2"))

    assert result.status == MaterialRequestStatus.PENDING_STOCK.value
    created_request = added[0]
    assert created_request.source_system == "sub"
    assert created_request.source_id == "sub-mr-123"
    assert created_request.project_id is None


def test_issue_posting_receives_selected_serial_numbers(org_id: UUID) -> None:
    from app.models.inventory.item import Item
    from app.services.inventory.transaction import TransactionInput

    db = MagicMock()
    item_id = uuid4()
    warehouse_id = uuid4()
    actor_id = uuid4()
    item = MagicMock(
        item_id=item_id,
        average_cost=Decimal("50"),
        base_uom="Nos",
        currency_code="NGN",
    )
    db.get.return_value = item
    request = MagicMock(
        request_id=uuid4(),
        request_number="MAT-MR-2026-00003",
        created_by_id=actor_id,
    )
    service = DotmacSubSyncService(db)

    with patch(
        "app.services.inventory.transaction.InventoryTransactionService.create_issue"
    ) as create_issue:
        service._post_sub_issue_transaction(
            org_id=org_id,
            request=request,
            line={
                "line_id": uuid4(),
                "item_id": item_id,
                "warehouse_id": warehouse_id,
                "requested_qty": Decimal("1"),
                "uom": "Nos",
                "serial_numbers": ["SN-001"],
            },
            fiscal_period_id=uuid4(),
            transaction_date=datetime.now(UTC),
            created_by_user_id=actor_id,
        )

    db.get.assert_called_once_with(Item, item_id)
    transaction = create_issue.call_args.args[2]
    assert isinstance(transaction, TransactionInput)
    assert transaction.serial_numbers == ["SN-001"]
    assert transaction.source_document_type == "Sub_MATERIAL_REQUEST"
