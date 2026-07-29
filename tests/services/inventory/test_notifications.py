"""Tests for inventory notification orchestration."""

from types import SimpleNamespace
from uuid import uuid4

from app.services.inventory.notifications import InventoryNotificationService


def _low_stock_item(name: str) -> SimpleNamespace:
    return SimpleNamespace(item_name=name)


def test_notify_low_stock_sends_one_daily_summary_per_unique_recipient(
    monkeypatch,
) -> None:
    db = SimpleNamespace()
    org_id = uuid4()
    recipient_id = uuid4()
    created: list[dict] = []

    monkeypatch.setattr(
        "app.services.inventory.notifications."
        "InventoryBalanceService.get_low_stock_items",
        lambda db_arg, org_arg: [
            _low_stock_item("Router"),
            _low_stock_item("Cable"),
            _low_stock_item("Radio"),
            _low_stock_item("Battery"),
        ],
    )
    monkeypatch.setattr(
        "app.services.inventory.notifications.get_users_with_permission",
        lambda db_arg, org_arg, permission: [
            SimpleNamespace(person_id=recipient_id),
            SimpleNamespace(person_id=recipient_id),
        ],
    )

    def fake_create(*args, **kwargs):
        created.append(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr(
        "app.services.inventory.notifications."
        "NotificationService.create_if_not_sent_since",
        fake_create,
    )

    result = InventoryNotificationService(db).notify_low_stock(org_id)

    assert result == {"low_stock_items": 4, "notifications_sent": 1}
    assert len(created) == 1
    assert created[0]["recipient_id"] == recipient_id
    assert created[0]["entity_id"] == org_id
    assert created[0]["action_url"] == "/inventory/reports/low-stock"
    assert created[0]["title"] == "Inventory stock requires attention"
    assert created[0]["message"] == (
        "4 items require attention: Router, Cable, Radio, and 1 more."
    )


def test_notify_low_stock_does_nothing_when_stock_is_healthy(monkeypatch) -> None:
    db = SimpleNamespace()
    org_id = uuid4()

    monkeypatch.setattr(
        "app.services.inventory.notifications."
        "InventoryBalanceService.get_low_stock_items",
        lambda db_arg, org_arg: [],
    )

    result = InventoryNotificationService(db).notify_low_stock(org_id)

    assert result == {"low_stock_items": 0, "notifications_sent": 0}
