"""Self-Care requests use the same explicit, cumulative ERP issue command."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.models.inventory.material_request import (
    MaterialRequestStatus,
    MaterialRequestType,
)
from app.services.inventory.material_request_issue import (
    MaterialRequestIssueService,
    _serials_for_issue,
    validate_sub_issue_history,
)
from app.services.sync.sub.procurement import _ProcurementMixin


def _fixture(issued: str = "0"):
    org, user, warehouse = uuid4(), uuid4(), uuid4()
    line = SimpleNamespace(
        item_id=uuid4(),
        inventory_item_id=uuid4(),
        sequence=1,
        warehouse_id=warehouse,
        requested_qty=Decimal("5"),
        ordered_qty=Decimal(issued),
        serial_numbers=None,
        out_of_stock=False,
        uom="EA",
    )
    request = SimpleNamespace(
        request_id=uuid4(),
        organization_id=org,
        request_number="MR-OLD-001",
        request_type=MaterialRequestType.ISSUE,
        source_system="sub",
        source_reference=str(uuid4()),
        default_warehouse_id=warehouse,
        status=MaterialRequestStatus.SUBMITTED
        if issued == "0"
        else MaterialRequestStatus.PARTIALLY_ISSUED,
        created_at=datetime.now(UTC) - timedelta(days=40),
        updated_at=None,
        updated_by_id=None,
        items=[line],
    )
    db = MagicMock()
    db.scalars.side_effect = [
        MagicMock(first=MagicMock(return_value=request)),
        MagicMock(all=MagicMock(return_value=[line])),
    ]
    db.execute.return_value.all.return_value = (
        [] if issued == "0" else [(line.item_id, Decimal(issued))]
    )
    db.get.return_value = SimpleNamespace(
        organization_id=org,
        item_code="A",
        average_cost=Decimal("1"),
        base_uom="EA",
        currency_code="NGN",
    )
    return db, org, user, request, line


@pytest.mark.parametrize(
    ("issued", "quantity", "status"),
    [
        ("0", "3", MaterialRequestStatus.PARTIALLY_ISSUED),
        ("3", "1", MaterialRequestStatus.PARTIALLY_ISSUED),
        ("3", "2", MaterialRequestStatus.ISSUED),
    ],
)
def test_existing_selfcare_request_issues_only_selected_balance(
    issued, quantity, status
):
    db, org, user, request, line = _fixture(issued)
    with (
        patch(
            "app.services.inventory.material_request_issue.InventoryTransactionService.get_current_balance",
            return_value=Decimal("5"),
        ),
        patch(
            "app.services.inventory.material_request_issue.InventoryTransactionService.create_issue"
        ) as post,
        patch(
            "app.services.inventory.material_request_issue.PeriodGuardService.get_period_for_date",
            return_value=SimpleNamespace(fiscal_period_id=uuid4()),
        ),
        patch.object(
            _ProcurementMixin, "_emit_sub_material_request_status_changed"
        ) as emit,
    ):
        result = MaterialRequestIssueService.issue_available(
            db,
            org,
            user,
            request.request_id,
            {line.item_id: Decimal(quantity)},
            {line.item_id: Decimal(issued)},
            set(),
        )
    assert result.status == status
    assert line.requested_qty == Decimal("5")
    assert line.ordered_qty == Decimal(issued) + Decimal(quantity)
    assert post.call_args.args[2].quantity == Decimal(quantity)
    assert post.call_args.kwargs["auto_commit"] is False
    assert result.updated_at.tzinfo is not None
    emit.assert_called_once()
    assert emit.call_args.kwargs["new_status"] == status
    db.commit.assert_not_called()


def test_serialized_remainder_never_reuses_previously_issued_units():
    _, _, _, _, line = _fixture("3")
    line.serial_numbers = ["S1", "S2", "S3", "S4", "S5"]
    assert _serials_for_issue(line, Decimal("2")) == ["S4", "S5"]
    assert line.serial_numbers == ["S1", "S2", "S3", "S4", "S5"]
    with pytest.raises(ValueError, match="whole units"):
        _serials_for_issue(line, Decimal("1.5"))


@pytest.mark.parametrize("problem", ["counter", "unmapped", "too_large", "negative"])
def test_ambiguous_historical_issues_require_reconciliation(problem):
    db, org, _, request, line = _fixture("0")
    if problem == "counter":
        db.execute.return_value.all.return_value = [(line.item_id, Decimal("1"))]
    elif problem == "unmapped":
        db.execute.return_value.all.return_value = [(None, Decimal("1"))]
    elif problem == "too_large":
        line.ordered_qty = Decimal("6")
        db.execute.return_value.all.return_value = [(line.item_id, Decimal("6"))]
    elif problem == "negative":
        line.ordered_qty = Decimal("-1")
    with pytest.raises(ValueError, match="reconcile"):
        validate_sub_issue_history(db, org, request.request_id, [line])
    db.add.assert_not_called()
    db.commit.assert_not_called()


def test_failed_inventory_post_does_not_emit_fulfillment():
    db, org, user, request, line = _fixture()
    with (
        patch(
            "app.services.inventory.material_request_issue.InventoryTransactionService.get_current_balance",
            return_value=Decimal("5"),
        ),
        patch(
            "app.services.inventory.material_request_issue.InventoryTransactionService.create_issue",
            side_effect=ValueError("serial validation failed"),
        ),
        patch(
            "app.services.inventory.material_request_issue.PeriodGuardService.get_period_for_date",
            return_value=SimpleNamespace(fiscal_period_id=uuid4()),
        ),
        patch.object(
            _ProcurementMixin, "_emit_sub_material_request_status_changed"
        ) as emit,
    ):
        with pytest.raises(ValueError, match="serial validation"):
            MaterialRequestIssueService.issue_available(
                db,
                org,
                user,
                request.request_id,
                {line.item_id: Decimal("1")},
                {line.item_id: Decimal("0")},
                set(),
            )
    emit.assert_not_called()
    assert request.status == MaterialRequestStatus.SUBMITTED
    assert line.ordered_qty == Decimal("0")


@pytest.mark.parametrize(
    "requested_status",
    [
        MaterialRequestStatus.SUBMITTED,
        MaterialRequestStatus.ISSUED,
        MaterialRequestStatus.CANCELLED,
    ],
)
def test_partial_request_is_not_reset_or_reissued_by_legacy_resend(requested_status):
    db, org, user, request, _ = _fixture("3")
    service = _ProcurementMixin(db)
    with (
        patch.object(service, "_post_sub_issue_transaction") as post,
        patch.object(service, "_emit_sub_material_request_status_changed") as emit,
    ):
        result = service._advance_sub_material_request_status(
            org_id=org,
            request=request,
            requested_status=requested_status,
            actor_person_id=user,
        )
    assert result.status == MaterialRequestStatus.PARTIALLY_ISSUED.value
    post.assert_not_called()
    assert emit.call_count == (
        1 if requested_status == MaterialRequestStatus.CANCELLED else 0
    )


def test_versioned_webhook_preserves_original_quantities_and_serial_selections():
    db, _, _, request, line = _fixture("3")
    request.updated_at = datetime.now(UTC)
    line.serial_numbers = ["S1", "S2", "S3", "S4", "S5"]
    payload = _ProcurementMixin(db)._build_sub_material_request_status_event_payload(
        request,
        old_status=MaterialRequestStatus.SUBMITTED,
        new_status=MaterialRequestStatus.PARTIALLY_ISSUED,
    )
    assert payload.fulfillment_version == 1
    assert payload.items[0].item_code == "A"
    assert payload.items[0].requested_qty == Decimal("5")
    assert payload.items[0].issued_qty == Decimal("3")
    assert payload.items[0].serial_numbers == ("S1", "S2", "S3", "S4", "S5")
