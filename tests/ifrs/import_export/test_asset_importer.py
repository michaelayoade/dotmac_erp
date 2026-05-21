from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.models.fixed_assets.asset import AssetStatus
from app.services.finance.import_export.assets import AssetImporter


@pytest.fixture(autouse=True)
def _stub_asset_sequence(monkeypatch):
    monkeypatch.setattr(
        "app.services.finance.import_export.assets.SequenceService.get_next_number",
        lambda db, organization_id, sequence_type: "FA-TEST-0001",
    )


def _make_importer(mock_db, import_config) -> AssetImporter:
    return AssetImporter(
        mock_db,
        import_config,
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )


def test_asset_importer_generates_sequence_number_ignoring_file_value(
    import_config, mock_db, monkeypatch
):
    importer = _make_importer(mock_db, import_config)

    monkeypatch.setattr(
        "app.services.finance.import_export.assets.SequenceService.get_next_number",
        lambda db, organization_id, sequence_type: "DT-AST-0007",
    )
    monkeypatch.setattr(
        importer._category_importer,
        "get_category_id",
        lambda category_name: uuid4(),
    )
    monkeypatch.setattr(importer, "_resolve_location_id", lambda raw_location: None)

    asset = importer.create_entity(
        {
            "asset_name": "Workstation",
            "asset_number": "Dotmac/OE/BM001",
            "category_name": "Computers",
        }
    )

    assert asset.asset_number == "DT-AST-0007"
    assert asset.asset_name == "Workstation"


def test_asset_importer_duplicate_check_uses_serial_number(import_config, mock_db):
    existing = MagicMock()
    mock_db.execute.return_value.scalar_one_or_none.return_value = existing

    importer = _make_importer(mock_db, import_config)

    result = importer.check_duplicate({"Serial Number": "8CC9491MB2"})

    assert result == existing


def test_asset_importer_duplicate_check_is_case_insensitive(import_config, mock_db):
    importer = _make_importer(mock_db, import_config)

    importer.check_duplicate({"Serial Number": "8CC9491MB2"})
    result = importer.check_duplicate({"Serial": " 8cc9491mb2 "})

    assert result is not None
    assert mock_db.execute.call_count == 1


def test_asset_importer_duplicate_check_ignores_placeholder_serials(
    import_config, mock_db
):
    importer = _make_importer(mock_db, import_config)

    result = importer.check_duplicate({"Serial Number": " NIL "})

    assert result is None
    mock_db.execute.assert_not_called()


def test_asset_importer_create_entity_normalizes_placeholder_serial_to_none(
    import_config, mock_db
):
    importer = _make_importer(mock_db, import_config)
    importer._category_importer._category_cache["ICT_EQUIPMENT"] = uuid4()

    asset = importer.create_entity(
        {
            "asset_name": "Mouse",
            "category_name": "ICT Equipment",
            "serial_number": "N/A",
        }
    )

    assert asset.serial_number is None


def test_asset_importer_import_rows_handles_uppercase_category_header(
    import_config, mock_db, monkeypatch
):
    importer = _make_importer(mock_db, import_config)

    monkeypatch.setattr(
        "app.services.finance.import_export.assets.SequenceService.get_next_number",
        lambda db, organization_id, sequence_type: "DT-AST-0008",
    )
    monkeypatch.setattr(
        importer._category_importer,
        "ensure_categories",
        lambda rows: None,
    )
    get_category_id = MagicMock(return_value=uuid4())
    monkeypatch.setattr(importer._category_importer, "get_category_id", get_category_id)
    monkeypatch.setattr(importer, "_resolve_location_id", lambda raw_location: None)

    result = importer.import_rows(
        [
            {
                "ASSET NAME": "All in one Desktop",
                "ASSET CATEGORY": "Computers & Laptops",
                "SERIAL NUMBER": "8CC9491MB2",
                "STATUS": "In use",
            }
        ]
    )

    assert result.imported_count == 1
    get_category_id.assert_called_once_with("Computers & Laptops")


def test_asset_importer_create_entity_assigns_employee_by_email(
    import_config, mock_db, monkeypatch
):
    importer = _make_importer(mock_db, import_config)
    employee_id = uuid4()
    department_id = uuid4()

    monkeypatch.setattr(
        "app.services.finance.import_export.assets.SequenceService.get_next_number",
        lambda db, organization_id, sequence_type: "DT-AST-0009",
    )
    monkeypatch.setattr(
        importer._category_importer,
        "get_category_id",
        lambda category_name: uuid4(),
    )
    monkeypatch.setattr(importer, "_resolve_location_id", lambda raw_location: None)
    monkeypatch.setattr(
        importer,
        "_resolve_department_id",
        lambda **kwargs: department_id,
    )
    monkeypatch.setattr(
        importer,
        "_resolve_employee_id",
        lambda **kwargs: employee_id,
    )

    asset = importer.create_entity(
        {
            "asset_name": "Workstation",
            "category_name": "Computers",
            "department_name": "Admin",
            "assign_to": "ada@example.com",
        }
    )

    assert asset.custodian_employee_id == employee_id


def test_asset_importer_resolve_department_name_is_case_insensitive(
    import_config, mock_db
):
    importer = _make_importer(mock_db, import_config)
    department_id = uuid4()
    importer._department_lookup_loaded = True
    importer._department_by_normalized_name = {
        "human resources": [(department_id, "Human Resources")]
    }

    resolved = importer._resolve_department_id(department_name="  HUMAN   resources ")

    assert resolved == department_id


def test_asset_importer_department_name_suggests_closest_match(import_config, mock_db):
    importer = _make_importer(mock_db, import_config)
    importer._department_lookup_loaded = True
    importer._department_by_normalized_name = {
        "admin": [(uuid4(), "Admin")],
        "finance": [(uuid4(), "Finance")],
    }

    try:
        importer._resolve_department_id(department_name="Admins")
    except ValueError as exc:
        message = str(exc)
    else:
        raise AssertionError("Expected ValueError for unknown department")

    assert 'Department "Admins" not found.' in message
    assert "Did you mean: Admin?" in message


def test_asset_importer_department_name_rejects_ambiguous_match(import_config, mock_db):
    importer = _make_importer(mock_db, import_config)
    importer._department_lookup_loaded = True
    importer._department_by_normalized_name = {
        "finance and admin": [
            (uuid4(), "Finance & Admin"),
            (uuid4(), "Finance and Admin"),
        ]
    }

    try:
        importer._resolve_department_id(department_name="Finance and Admin")
    except ValueError as exc:
        message = str(exc)
    else:
        raise AssertionError("Expected ValueError for ambiguous department")

    assert 'Ambiguous department "Finance and Admin"' in message


def test_asset_importer_resolve_employee_by_email_and_department(
    import_config, mock_db
):
    importer = _make_importer(mock_db, import_config)
    employee_id = uuid4()
    department_id = uuid4()
    importer._employee_lookup_loaded = True
    importer._employee_by_email = {
        "ada@example.com": [(employee_id, "Ada Lovelace", str(department_id))]
    }

    resolved = importer._resolve_employee_id(
        employee_email="Ada@example.com",
        department_id=department_id,
    )

    assert resolved == employee_id


def test_asset_importer_import_rows_reports_department_suggestion_error(
    import_config, mock_db, monkeypatch
):
    importer = _make_importer(mock_db, import_config)

    monkeypatch.setattr(
        "app.services.finance.import_export.assets.SequenceService.get_next_number",
        lambda db, organization_id, sequence_type: "DT-AST-0010",
    )
    monkeypatch.setattr(
        importer._category_importer, "ensure_categories", lambda rows: None
    )
    monkeypatch.setattr(
        importer._category_importer,
        "get_category_id",
        lambda category_name: uuid4(),
    )
    monkeypatch.setattr(importer, "_resolve_location_id", lambda raw_location: None)
    monkeypatch.setattr(
        importer,
        "_resolve_department_id",
        lambda **kwargs: (_ for _ in ()).throw(
            ValueError('Department "Admins" not found. Did you mean: Admin?')
        ),
    )

    result = importer.import_rows(
        [
            {
                "Asset Name": "Laptop",
                "Asset Category": "Computers",
                "Department": "Admins",
            }
        ]
    )

    assert result.imported_count == 0
    assert result.error_count == 1
    assert (
        'Department "Admins" not found. Did you mean: Admin?'
        in result.errors[0].message
    )


def test_asset_importer_parses_in_use_status_label(import_config, mock_db):
    importer = _make_importer(mock_db, import_config)

    assert importer._parse_status("In use") == AssetStatus.IN_USE


def test_asset_importer_defaults_missing_status_to_in_use(import_config, mock_db):
    importer = _make_importer(mock_db, import_config)

    asset = importer.create_entity(
        {
            "asset_name": "Core Router",
            "asset_number": "FA-001",
            "category_name": "Network Equipment",
            "acquisition_cost": Decimal("1500.00"),
        }
    )

    assert asset.status == AssetStatus.IN_USE


def test_asset_importer_rounds_acquisition_cost_to_two_decimal_places(
    import_config, mock_db
):
    importer = _make_importer(mock_db, import_config)

    asset = importer.create_entity(
        {
            "asset_name": "Core Router",
            "asset_number": "FA-002",
            "category_name": "Network Equipment",
            "acquisition_cost": Decimal("1500.125"),
        }
    )

    assert asset.acquisition_cost == Decimal("1500.13")
    assert asset.functional_currency_cost == Decimal("1500.13")


def test_asset_importer_accepts_migration_snake_case_headers(import_config, mock_db):
    category_id = uuid4()
    location_id = uuid4()

    importer = _make_importer(mock_db, import_config)
    importer._category_importer._category_cache["ICT_EQUIPMENT"] = category_id
    importer._location_cache["head office."] = location_id

    asset = importer.create_entity(
        {
            "asset_name": "All in One Desktop",
            "asset_number": "Dotmac/OE/Dt001",
            "category_name": "ICT Equipment",
            "location": "Head Office.",
            "department_alt": "Facility",
            "serial_number": "8CC841051K",
            "model": "HP 24-XA0057C",
            "manufacturer": "HP",
            "acquisition_date": "2021-04-28",
            "acquisition_cost": Decimal("853000"),
            "currency_code": "NGN",
            "depreciation_method_str": "STRAIGHT_LINE",
            "useful_life_months": 60,
            "residual_value": Decimal("0"),
            "status_str": "In use",
        }
    )

    assert asset.category_id == category_id
    assert asset.location_id == location_id
    assert asset.status == AssetStatus.IN_USE


def test_asset_importer_preserves_asset_name_from_title_case_header(
    import_config, mock_db
):
    importer = _make_importer(mock_db, import_config)
    importer._category_importer._category_cache["ICT_EQUIPMENT"] = uuid4()

    transformed = importer.transform_row(
        {
            "Asset Name": "All in One Desktop",
            "Asset Category": "ICT Equipment",
            "Acquisition Cost": "853000",
        },
        row_num=1,
    )

    assert transformed["asset_name"] == "All in One Desktop"
    assert transformed["category_name"] == "ICT Equipment"


def test_asset_category_importer_ensure_categories_accepts_snake_case_category_name(
    import_config, mock_db
):
    importer = _make_importer(mock_db, import_config)

    importer._category_importer.ensure_categories(
        [
            {"category_name": "ICT Equipment"},
            {"asset_class_alt": "Motor Vehicles"},
        ]
    )

    assert "ICT_EQUIPMENT" in importer._category_importer._category_cache
    assert "MOTOR_VEHICLES" in importer._category_importer._category_cache


def test_asset_importer_creates_missing_category_from_mapped_category_name(
    import_config, mock_db
):
    importer = _make_importer(mock_db, import_config)
    added_entities: list[object] = []
    mock_db.add.side_effect = added_entities.append

    asset = importer.create_entity(
        {
            "asset_name": "Core Router",
            "asset_number": "FA-003",
            "category_name": "Network Equipment",
            "acquisition_cost": Decimal("1500.00"),
        }
    )

    assert asset.category_id is not None
    assert "NETWORK_EQUIPMENT" in importer._category_importer._category_cache
    assert len(added_entities) == 1
