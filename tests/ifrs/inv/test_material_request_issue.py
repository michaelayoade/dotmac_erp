"""Line-level material issue regression tests."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.models.inventory.material_request import (
    MaterialRequestStatus,
    MaterialRequestType,
)
from app.services.inventory.material_request_issue import MaterialRequestIssueService


def _request_with_lines(count: int = 5):
    org_id, user_id, request_id = uuid4(), uuid4(), uuid4()
    warehouse_id = uuid4()
    request = SimpleNamespace(
        request_id=request_id,
        request_number="MR-001",
        request_type=MaterialRequestType.ISSUE,
        source_system="erp",
        status=MaterialRequestStatus.SUBMITTED,
        default_warehouse_id=warehouse_id,
        updated_by_id=None,
    )
    lines = [
        SimpleNamespace(
            item_id=uuid4(),
            sequence=index + 1,
            inventory_item_id=uuid4(),
            warehouse_id=None,
            requested_qty=Decimal("5"),
            ordered_qty=Decimal("0"),
            out_of_stock=False,
            uom="EA",
        )
        for index in range(count)
    ]
    db = MagicMock()
    request_result, lines_result = MagicMock(), MagicMock()
    request_result.first.return_value = request
    lines_result.all.return_value = lines
    db.scalars.side_effect = [request_result, lines_result]
    db.get.return_value = SimpleNamespace(
        organization_id=org_id,
        average_cost=Decimal("1"),
        base_uom="EA",
        currency_code="NGN",
    )
    return db, org_id, user_id, request, lines


def test_issues_four_lines_and_marks_fifth_out_of_stock() -> None:
    db, org_id, user_id, request, lines = _request_with_lines()
    quantities = {line.item_id: Decimal("5") for line in lines}
    quantities[lines[-1].item_id] = Decimal("0")
    expected = {line.item_id: Decimal("0") for line in lines}

    with (
        patch(
            "app.services.inventory.material_request_issue.InventoryTransactionService.get_current_balance"
        ) as balance,
        patch(
            "app.services.inventory.material_request_issue.InventoryTransactionService.create_issue"
        ) as create_issue,
        patch(
            "app.services.inventory.material_request_issue.PeriodGuardService.get_period_for_date"
        ) as period,
    ):
        balance.side_effect = [Decimal("5")] * 4 + [Decimal("0")]
        period.return_value = SimpleNamespace(fiscal_period_id=uuid4())
        result = MaterialRequestIssueService.issue_available(
            db,
            org_id,
            user_id,
            request.request_id,
            quantities,
            expected,
            {lines[-1].item_id},
        )

    assert result.status == MaterialRequestStatus.PARTIALLY_ISSUED
    assert result.updated_by_id == user_id
    assert [line.requested_qty for line in lines] == [Decimal("5")] * 5
    assert [line.ordered_qty for line in lines] == [Decimal("5")] * 4 + [Decimal("0")]
    assert lines[-1].out_of_stock is True
    assert create_issue.call_count == 4
    assert all(
        call.kwargs["auto_commit"] is False for call in create_issue.call_args_list
    )
    assert (
        create_issue.call_args_list[0].args[2].source_document_line_id
        == lines[0].item_id
    )
    db.commit.assert_not_called()


def test_later_issue_only_posts_outstanding_quantity() -> None:
    db, org_id, user_id, request, lines = _request_with_lines(2)
    request.status = MaterialRequestStatus.PARTIALLY_ISSUED
    lines[0].ordered_qty = Decimal("3")
    lines[1].ordered_qty = Decimal("5")
    lines[0].out_of_stock = True
    quantities = {lines[0].item_id: Decimal("2")}
    expected = {lines[0].item_id: Decimal("3")}

    with (
        patch(
            "app.services.inventory.material_request_issue.InventoryTransactionService.get_current_balance",
            return_value=Decimal("2"),
        ),
        patch(
            "app.services.inventory.material_request_issue.InventoryTransactionService.create_issue"
        ) as create_issue,
        patch(
            "app.services.inventory.material_request_issue.PeriodGuardService.get_period_for_date"
        ) as period,
    ):
        period.return_value = SimpleNamespace(fiscal_period_id=uuid4())
        MaterialRequestIssueService.issue_available(
            db,
            org_id,
            user_id,
            request.request_id,
            quantities,
            expected,
            set(),
        )

    assert request.status == MaterialRequestStatus.ISSUED
    assert lines[0].ordered_qty == Decimal("5")
    assert lines[0].out_of_stock is False
    assert create_issue.call_count == 1
    assert create_issue.call_args.args[2].quantity == Decimal("2")


def test_edits_one_line_to_available_quantity() -> None:
    db, org_id, user_id, request, lines = _request_with_lines(1)
    line = lines[0]
    with (
        patch(
            "app.services.inventory.material_request_issue.InventoryTransactionService.get_current_balance",
            return_value=Decimal("3"),
        ),
        patch(
            "app.services.inventory.material_request_issue.InventoryTransactionService.create_issue"
        ) as create_issue,
        patch(
            "app.services.inventory.material_request_issue.PeriodGuardService.get_period_for_date"
        ) as period,
    ):
        period.return_value = SimpleNamespace(fiscal_period_id=uuid4())
        MaterialRequestIssueService.issue_available(
            db,
            org_id,
            user_id,
            request.request_id,
            {line.item_id: Decimal("3")},
            {line.item_id: Decimal("0")},
            set(),
        )

    assert line.requested_qty == Decimal("5")
    assert line.ordered_qty == Decimal("3")
    assert request.status == MaterialRequestStatus.PARTIALLY_ISSUED
    assert create_issue.call_args.args[2].quantity == Decimal("3")


def test_duplicate_item_lines_share_one_stock_budget() -> None:
    db, org_id, user_id, request, lines = _request_with_lines(2)
    lines[1].inventory_item_id = lines[0].inventory_item_id
    quantities = {lines[0].item_id: Decimal("5"), lines[1].item_id: Decimal("0")}
    expected = {line.item_id: Decimal("0") for line in lines}
    with (
        patch(
            "app.services.inventory.material_request_issue.InventoryTransactionService.get_current_balance",
            return_value=Decimal("5"),
        ) as balance,
        patch(
            "app.services.inventory.material_request_issue.InventoryTransactionService.create_issue"
        ) as create_issue,
        patch(
            "app.services.inventory.material_request_issue.PeriodGuardService.get_period_for_date"
        ) as period,
    ):
        period.return_value = SimpleNamespace(fiscal_period_id=uuid4())
        MaterialRequestIssueService.issue_available(
            db,
            org_id,
            user_id,
            request.request_id,
            quantities,
            expected,
            {lines[1].item_id},
        )

    balance.assert_called_once()
    create_issue.assert_called_once()
    assert lines[1].out_of_stock is True


@pytest.mark.parametrize("problem", ["missing_badge", "too_many", "stale", "sub"])
def test_rejects_unsafe_issue_without_posting(problem: str) -> None:
    db, org_id, user_id, request, lines = _request_with_lines(1)
    line = lines[0]
    quantities = {line.item_id: Decimal("0")}
    expected = {line.item_id: Decimal("0")}
    out_ids = set()
    available = Decimal("0")
    if problem == "too_many":
        quantities[line.item_id] = Decimal("6")
        available = Decimal("10")
    elif problem == "stale":
        expected[line.item_id] = Decimal("1")
    elif problem == "sub":
        request.source_system = "sub"

    with (
        patch(
            "app.services.inventory.material_request_issue.InventoryTransactionService.get_current_balance",
            return_value=available,
        ),
        patch(
            "app.services.inventory.material_request_issue.InventoryTransactionService.create_issue"
        ) as create_issue,
    ):
        with pytest.raises(ValueError):
            MaterialRequestIssueService.issue_available(
                db,
                org_id,
                user_id,
                request.request_id,
                quantities,
                expected,
                out_ids,
            )
    create_issue.assert_not_called()


def test_zero_stock_badge_does_not_allow_empty_issue() -> None:
    db, org_id, user_id, request, lines = _request_with_lines(1)
    line_id = lines[0].item_id
    with patch(
        "app.services.inventory.material_request_issue.InventoryTransactionService.get_current_balance",
        return_value=Decimal("0"),
    ):
        with pytest.raises(ValueError, match="at least one line"):
            MaterialRequestIssueService.issue_available(
                db,
                org_id,
                user_id,
                request.request_id,
                {line_id: Decimal("0")},
                {line_id: Decimal("0")},
                {line_id},
            )
    assert request.status == MaterialRequestStatus.SUBMITTED
