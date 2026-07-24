from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import uuid

import pytest

from app.services.inventory.reorder import (
    DEFAULT_APPROACH_THRESHOLD_PERCENT,
    InventoryReorderService,
    ReorderAttentionItem,
    ReorderDashboardData,
    ReorderStatus,
)
from app.services.inventory.web import InventoryWebService
from app.templates import templates
from app.web.inventory import inventory_index


@pytest.mark.parametrize(
    ("available", "expected"),
    [
        (Decimal("120.000001"), None),
        (Decimal("120"), ReorderStatus.APPROACHING_REORDER),
        (Decimal("100.000001"), ReorderStatus.APPROACHING_REORDER),
        (Decimal("100"), ReorderStatus.AT_REORDER),
        (Decimal("99.999999"), ReorderStatus.BELOW_REORDER),
        (Decimal("0"), ReorderStatus.BELOW_REORDER),
        (Decimal("-10"), ReorderStatus.BELOW_REORDER),
    ],
)
def test_classify_reorder_status_boundaries(
    available: Decimal,
    expected: ReorderStatus | None,
) -> None:
    assert (
        InventoryReorderService.classify(
            available,
            Decimal("100"),
            Decimal("20"),
        )
        == expected
    )


def test_non_positive_reorder_point_is_not_classified() -> None:
    assert (
        InventoryReorderService.classify(
            Decimal("0"),
            Decimal("0"),
            Decimal("20"),
        )
        is None
    )


def test_dashboard_data_uses_batch_balances_and_category_fallback() -> None:
    org_id = uuid.uuid4()
    db = MagicMock()
    category = SimpleNamespace(
        category_id=uuid.uuid4(),
        organization_id=org_id,
        reorder_point=None,
    )
    fallback_category = SimpleNamespace(
        category_id=uuid.uuid4(),
        organization_id=org_id,
        reorder_point=Decimal("50"),
    )

    def item(
        code: str,
        *,
        reorder_point: Decimal | None,
        category_id: uuid.UUID = category.category_id,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            item_id=uuid.uuid4(),
            organization_id=org_id,
            category_id=category_id,
            item_code=code,
            item_name=code.replace("-", " ").title(),
            base_uom="EACH",
            reorder_point=reorder_point,
        )

    approaching = item("ITEM-APPROACHING", reorder_point=Decimal("100"))
    reached = item("ITEM-REACHED", reorder_point=Decimal("100"))
    below = item("ITEM-BELOW", reorder_point=Decimal("100"))
    healthy = item("ITEM-HEALTHY", reorder_point=Decimal("100"))
    fallback = item(
        "ITEM-FALLBACK",
        reorder_point=None,
        category_id=fallback_category.category_id,
    )
    unconfigured = item("ITEM-UNCONFIGURED", reorder_point=None)
    rows = [
        (approaching, category),
        (reached, category),
        (below, category),
        (healthy, category),
        (fallback, fallback_category),
        (unconfigured, category),
    ]
    db.execute.return_value.all.return_value = rows

    stock_levels = {
        approaching.item_id: (Decimal("120"), Decimal("0")),
        reached.item_id: (Decimal("100"), Decimal("0")),
        below.item_id: (Decimal("110"), Decimal("20")),
        healthy.item_id: (Decimal("121"), Decimal("0")),
        fallback.item_id: (Decimal("45"), Decimal("0")),
        unconfigured.item_id: (Decimal("0"), Decimal("0")),
    }

    with (
        patch(
            "app.services.inventory.reorder.get_setting_value",
            return_value=20,
        ) as setting_mock,
        patch(
            "app.services.inventory.reorder."
            "InventoryBalanceService.get_batch_stock_levels",
            return_value=stock_levels,
        ) as batch_mock,
    ):
        result = InventoryReorderService().get_dashboard_data(db, org_id)

    assert result.approach_threshold_percent == Decimal("20")
    assert result.total_attention_count == 4
    assert result.unconfigured_count == 1
    assert result.counts == {
        ReorderStatus.APPROACHING_REORDER: 1,
        ReorderStatus.AT_REORDER: 1,
        ReorderStatus.BELOW_REORDER: 2,
    }
    assert {row.item_code for row in result.items} == {
        "ITEM-APPROACHING",
        "ITEM-REACHED",
        "ITEM-BELOW",
        "ITEM-FALLBACK",
    }
    below_projection = next(
        row for row in result.items if row.item_code == "ITEM-BELOW"
    )
    assert below_projection.quantity_on_hand == Decimal("110")
    assert below_projection.quantity_reserved == Decimal("20")
    assert below_projection.quantity_available == Decimal("90")
    fallback_projection = next(
        row for row in result.items if row.item_code == "ITEM-FALLBACK"
    )
    assert fallback_projection.reorder_point == Decimal("50")
    assert fallback_projection.status == ReorderStatus.BELOW_REORDER

    setting_mock.assert_called_once()
    batch_mock.assert_called_once_with(
        db,
        org_id,
        [row[0].item_id for row in rows],
    )
    query_text = str(db.execute.call_args.args[0])
    assert "inv.item.organization_id" in query_text
    assert "inv.item_category.organization_id" in query_text


@pytest.mark.parametrize("raw_value", ["invalid", 0, 101, None])
def test_invalid_approach_threshold_falls_back_to_default(raw_value: object) -> None:
    db = MagicMock()
    with patch(
        "app.services.inventory.reorder.get_setting_value",
        return_value=raw_value,
    ):
        assert (
            InventoryReorderService._resolve_approach_threshold(db)
            == DEFAULT_APPROACH_THRESHOLD_PERCENT
        )


def _attention_item(
    *,
    code: str,
    status: ReorderStatus,
    available: str,
    reorder_point: str = "100",
) -> ReorderAttentionItem:
    available_decimal = Decimal(available)
    reorder_decimal = Decimal(reorder_point)
    return ReorderAttentionItem(
        item_id=uuid.uuid4(),
        item_code=code,
        item_name=code.replace("-", " ").title(),
        base_uom="EACH",
        quantity_on_hand=available_decimal,
        quantity_reserved=Decimal("0"),
        quantity_available=available_decimal,
        reorder_point=reorder_decimal,
        stock_to_reorder_percent=available_decimal / reorder_decimal * 100,
        status=status,
    )


def test_dashboard_context_filters_chart_and_table_from_same_projection() -> None:
    approaching = _attention_item(
        code="ITEM-APPROACHING",
        status=ReorderStatus.APPROACHING_REORDER,
        available="110",
    )
    reached = _attention_item(
        code="ITEM-REACHED",
        status=ReorderStatus.AT_REORDER,
        available="100",
    )
    below = _attention_item(
        code="ITEM-BELOW",
        status=ReorderStatus.BELOW_REORDER,
        available="80",
    )
    dashboard = ReorderDashboardData(
        items=(below, reached, approaching),
        counts={
            ReorderStatus.APPROACHING_REORDER: 1,
            ReorderStatus.AT_REORDER: 1,
            ReorderStatus.BELOW_REORDER: 1,
        },
        unconfigured_count=2,
        approach_threshold_percent=Decimal("20"),
    )

    with patch(
        "app.services.inventory.web.inventory_reorder_service.get_dashboard_data",
        return_value=dashboard,
    ):
        context = InventoryWebService.dashboard_context(
            MagicMock(),
            str(uuid.uuid4()),
            reorder_status="below_reorder",
        )

    assert context["reorder_selected_status"] == "below_reorder"
    assert context["reorder_selected_label"] == "Below reorder"
    assert [item["item_code"] for item in context["reorder_items"]] == ["ITEM-BELOW"]
    assert context["reorder_chart_config"]["labels"] == ["ITEM-BELOW — Item Below"]
    assert context["reorder_chart_config"]["datasets"][0]["data"] == [80.0]
    assert context["reorder_chart_config"]["tooltipDetails"] == [
        [
            "On hand: 80.00 EACH",
            "Reserved: 0.00 EACH",
            "Available: 80.00 EACH",
            "Reorder level: 100.00 EACH",
            "Status: Below reorder",
        ]
    ]
    assert context["reorder_total_attention_count"] == 3
    assert context["reorder_unconfigured_count"] == 2
    assert next(
        card for card in context["reorder_cards"] if card["status"] == "below_reorder"
    )["active"]


def test_invalid_dashboard_filter_safely_shows_all_items() -> None:
    item = _attention_item(
        code="ITEM-BELOW",
        status=ReorderStatus.BELOW_REORDER,
        available="80",
    )
    dashboard = ReorderDashboardData(
        items=(item,),
        counts={
            ReorderStatus.APPROACHING_REORDER: 0,
            ReorderStatus.AT_REORDER: 0,
            ReorderStatus.BELOW_REORDER: 1,
        },
        unconfigured_count=0,
        approach_threshold_percent=Decimal("20"),
    )

    with patch(
        "app.services.inventory.web.inventory_reorder_service.get_dashboard_data",
        return_value=dashboard,
    ):
        context = InventoryWebService.dashboard_context(
            MagicMock(),
            str(uuid.uuid4()),
            reorder_status="not-a-status",
        )

    assert context["reorder_selected_status"] is None
    assert len(context["reorder_items"]) == 1


def test_inventory_index_loads_dashboard_for_stock_readers() -> None:
    request = MagicMock()
    auth = MagicMock(organization_id=uuid.uuid4())
    auth.has_permission.side_effect = lambda permission: (
        permission
        in {
            "inventory:dashboard",
            "inventory:stock:read",
        }
    )
    db = MagicMock()

    with (
        patch(
            "app.web.inventory.base_context",
            return_value={"existing": "context"},
        ),
        patch(
            "app.web.inventory.inv_web_service.dashboard_context",
            return_value={"show_reorder_dashboard": True, "reorder_items": []},
        ) as dashboard_context_mock,
        patch(
            "app.web.inventory.templates.TemplateResponse",
            return_value="response",
        ) as template_response_mock,
    ):
        response = inventory_index(
            request=request,
            reorder_status="below_reorder",
            auth=auth,
            db=db,
        )

    assert response == "response"
    dashboard_context_mock.assert_called_once_with(
        db,
        str(auth.organization_id),
        reorder_status="below_reorder",
    )
    assert template_response_mock.call_args.args[1] == "inventory/index.html"
    assert template_response_mock.call_args.args[2]["show_reorder_dashboard"] is True


def test_inventory_index_hides_stock_dashboard_without_stock_read_permission() -> None:
    request = MagicMock()
    auth = MagicMock(organization_id=uuid.uuid4())
    auth.has_permission.return_value = False
    db = MagicMock()

    with (
        patch("app.web.inventory.base_context", return_value={}),
        patch(
            "app.web.inventory.inv_web_service.dashboard_context"
        ) as dashboard_context_mock,
        patch(
            "app.web.inventory.templates.TemplateResponse",
            return_value="response",
        ) as template_response_mock,
    ):
        response = inventory_index(
            request=request,
            reorder_status=None,
            auth=auth,
            db=db,
        )

    assert response == "response"
    dashboard_context_mock.assert_not_called()
    assert template_response_mock.call_args.args[2]["show_reorder_dashboard"] is False


def test_inventory_dashboard_template_compiles() -> None:
    templates.env.get_template("inventory/index.html")
