from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from app.models.inventory.item import CostingMethod, ItemType
from app.services.finance.import_export.items import ItemImporter


def _make_importer(mock_db, import_config) -> ItemImporter:
    return ItemImporter(
        mock_db,
        import_config,
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )


def test_item_importer_requires_item_name(import_config, mock_db, monkeypatch):
    import_config.dry_run = True
    importer = _make_importer(mock_db, import_config)
    monkeypatch.setattr(
        importer._category_importer, "ensure_categories", lambda rows: None
    )

    result = importer.import_rows([{"Item Code": "ITEM-001"}])

    assert result.imported_count == 0
    assert result.error_count == 1
    assert "Item name is required" in str(result.errors[0])


def test_item_importer_generates_sequence_code_when_missing(
    import_config, mock_db, monkeypatch
):
    importer = _make_importer(mock_db, import_config)
    category_id = uuid4()

    monkeypatch.setattr(
        importer._category_importer, "ensure_categories", lambda rows: None
    )
    monkeypatch.setattr(
        importer._category_importer,
        "get_category_id",
        lambda category_name: category_id,
    )
    monkeypatch.setattr(
        "app.services.finance.import_export.items.SequenceService.get_next_number",
        lambda db, organization_id, sequence_type: "ITM-202606-0001",
    )
    mock_db.execute.return_value.scalar_one_or_none.return_value = None

    result = importer.import_rows([{"Item Name": "Fiber Cable", "Category": "Cables"}])

    assert result.imported_count == 1
    item = mock_db.add.call_args.args[0]
    assert item.item_code == "ITM-202606-0001"
    assert item.item_name == "Fiber Cable"


def test_item_importer_service_items_disable_stock_tracking(import_config, mock_db):
    importer = _make_importer(mock_db, import_config)
    importer._category_importer._category_cache["SERVICES"] = uuid4()

    item = importer.create_entity(
        {
            "item_code": "SVC-001",
            "item_name": "Installation",
            "item_type_str": "Service",
            "category_name": "Services",
            "track_inventory": True,
            "track_lots": True,
            "track_serial_numbers": True,
        }
    )

    assert item.item_type == ItemType.SERVICE
    assert item.track_inventory is False
    assert item.track_lots is False
    assert item.track_serial_numbers is False


def test_item_importer_defaults_purchase_and_sales_uom_to_base(import_config, mock_db):
    importer = _make_importer(mock_db, import_config)
    importer._category_importer._category_cache["GENERAL"] = uuid4()

    item = importer.create_entity(
        {
            "item_name": "Widget",
            "item_code": "WID-001",
            "category_name": "General",
            "base_uom": "BOX",
        }
    )

    assert item.base_uom == "BOX"
    assert item.purchase_uom == "BOX"
    assert item.sales_uom == "BOX"


def test_item_importer_maps_standard_cost_and_costing_method(import_config, mock_db):
    importer = _make_importer(mock_db, import_config)
    importer._category_importer._category_cache["GENERAL"] = uuid4()

    item = importer.create_entity(
        {
            "item_name": "Widget",
            "item_code": "WID-001",
            "category_name": "General",
            "costing_method_str": "standard cost",
            "standard_cost": Decimal("125.50"),
        }
    )

    assert item.costing_method == CostingMethod.STANDARD_COST
    assert str(item.standard_cost) == "125.50"
