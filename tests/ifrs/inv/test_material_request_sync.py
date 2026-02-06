"""
Tests for Material Request Sync Service.

Tests cover:
- MaterialRequestMapping and MaterialRequestItemMapping transformations
- Status and type mapping functions
- FK resolution in sync service
- Entity creation and update
"""

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.services.erpnext.mappings.material_request import (
    MaterialRequestMapping,
    MaterialRequestItemMapping,
    map_material_request_status,
    map_material_request_type,
)


# ============ Status Mapping Tests ============


class TestMaterialRequestStatusMapping:
    """Tests for Material Request status mapping."""

    def test_map_status_draft(self):
        """Draft status maps correctly."""
        assert map_material_request_status("Draft") == "DRAFT"

    def test_map_status_submitted(self):
        """Submitted status maps correctly."""
        assert map_material_request_status("Submitted") == "SUBMITTED"

    def test_map_status_partially_ordered(self):
        """Partially Ordered status maps correctly."""
        assert map_material_request_status("Partially Ordered") == "PARTIALLY_ORDERED"

    def test_map_status_ordered(self):
        """Ordered status maps correctly."""
        assert map_material_request_status("Ordered") == "ORDERED"

    def test_map_status_issued(self):
        """Issued status maps correctly."""
        assert map_material_request_status("Issued") == "ISSUED"

    def test_map_status_transferred(self):
        """Transferred status maps correctly."""
        assert map_material_request_status("Transferred") == "TRANSFERRED"

    def test_map_status_cancelled(self):
        """Cancelled status maps correctly."""
        assert map_material_request_status("Cancelled") == "CANCELLED"

    def test_map_status_stopped(self):
        """Stopped status maps to CANCELLED."""
        assert map_material_request_status("Stopped") == "CANCELLED"

    def test_map_status_none(self):
        """None status defaults to DRAFT."""
        assert map_material_request_status(None) == "DRAFT"

    def test_map_status_unknown(self):
        """Unknown status defaults to DRAFT."""
        assert map_material_request_status("UnknownStatus") == "DRAFT"

    def test_all_erpnext_statuses_are_mapped(self):
        """All expected ERPNext statuses have mappings."""
        expected_statuses = [
            "Draft",
            "Submitted",
            "Pending",
            "Partially Ordered",
            "Ordered",
            "Issued",
            "Transferred",
            "Received",
            "Cancelled",
            "Stopped",
        ]
        for status in expected_statuses:
            result = map_material_request_status(status)
            assert result in [
                "DRAFT",
                "SUBMITTED",
                "PARTIALLY_ORDERED",
                "ORDERED",
                "ISSUED",
                "TRANSFERRED",
                "CANCELLED",
            ]


class TestMaterialRequestTypeMapping:
    """Tests for Material Request type mapping."""

    def test_map_type_purchase(self):
        """Purchase type maps correctly."""
        assert map_material_request_type("Purchase") == "PURCHASE"

    def test_map_type_transfer(self):
        """Material Transfer type maps correctly."""
        assert map_material_request_type("Material Transfer") == "TRANSFER"

    def test_map_type_issue(self):
        """Material Issue type maps correctly."""
        assert map_material_request_type("Material Issue") == "ISSUE"

    def test_map_type_manufacture(self):
        """Manufacture type maps correctly."""
        assert map_material_request_type("Manufacture") == "MANUFACTURE"

    def test_map_type_customer_provided(self):
        """Customer Provided type maps to ISSUE."""
        assert map_material_request_type("Customer Provided") == "ISSUE"

    def test_map_type_none(self):
        """None type defaults to PURCHASE."""
        assert map_material_request_type(None) == "PURCHASE"

    def test_map_type_unknown(self):
        """Unknown type defaults to PURCHASE."""
        assert map_material_request_type("UnknownType") == "PURCHASE"


# ============ Mapping Tests ============


class TestMaterialRequestMapping:
    """Tests for Material Request header mapping."""

    @pytest.fixture
    def mapping(self):
        """Create mapping instance."""
        return MaterialRequestMapping()

    @pytest.fixture
    def sample_erpnext_record(self):
        """Sample ERPNext Material Request record."""
        return {
            "name": "MAT-REQ-00001",
            "material_request_type": "Purchase",
            "status": "Submitted",
            "schedule_date": "2026-02-15",
            "set_warehouse": "Stores - DM",
            "requested_by": "john.doe@example.com",
            "reason": "Urgent requirement for project",
            "modified": "2026-01-24 10:30:00",
        }

    def test_transform_record_basic(self, mapping, sample_erpnext_record):
        """Transform basic record fields correctly."""
        result = mapping.transform_record(sample_erpnext_record)

        assert result["_source_name"] == "MAT-REQ-00001"
        assert result["request_type"] == "PURCHASE"
        assert result["status"] == "SUBMITTED"
        assert result["schedule_date"] == date(2026, 2, 15)
        assert result["_warehouse_source_name"] == "Stores - DM"
        assert result["_requested_by_user"] == "john.doe@example.com"
        assert result["remarks"] == "Urgent requirement for project"

    def test_transform_record_generates_request_number(
        self, mapping, sample_erpnext_record
    ):
        """Request number is generated from ERPNext name."""
        result = mapping.transform_record(sample_erpnext_record)
        assert result["request_number"] == "MAT-REQ-00001"

    def test_transform_record_non_standard_name(self, mapping):
        """Non-standard names are used as request number."""
        record = {
            "name": "Custom-Request-123",
            "material_request_type": "Purchase",
            "status": "Draft",
        }
        result = mapping.transform_record(record)
        assert result["request_number"] == "Custom-Request-123"

    def test_transform_record_missing_optional_fields(self, mapping):
        """Missing optional fields result in None."""
        record = {
            "name": "MAT-REQ-00002",
            "material_request_type": "Material Transfer",
            "status": "Draft",
        }
        result = mapping.transform_record(record)

        assert result["schedule_date"] is None
        assert result["_warehouse_source_name"] is None
        assert result["_requested_by_user"] is None


class TestMaterialRequestItemMapping:
    """Tests for Material Request Item mapping."""

    @pytest.fixture
    def mapping(self):
        """Create mapping instance."""
        return MaterialRequestItemMapping()

    @pytest.fixture
    def sample_item_record(self):
        """Sample ERPNext Material Request Item record."""
        return {
            "name": "MRI-001",
            "item_code": "ITEM-12345",
            "warehouse": "Main Warehouse - DM",
            "qty": 10.5,
            "ordered_qty": 5.0,
            "stock_uom": "Nos",
            "schedule_date": "2026-02-20",
            "project": "PROJ-001",
            "modified": "2026-01-24 10:30:00",
        }

    def test_transform_item_record(self, mapping, sample_item_record):
        """Transform item record fields correctly."""
        result = mapping.transform_record(sample_item_record)

        assert result["_source_name"] == "MRI-001"
        assert result["_item_source_name"] == "ITEM-12345"
        assert result["_warehouse_source_name"] == "Main Warehouse - DM"
        assert result["requested_qty"] == Decimal("10.5")
        assert result["ordered_qty"] == Decimal("5.0")
        assert result["uom"] == "Nos"
        assert result["schedule_date"] == date(2026, 2, 20)
        assert result["_project_source_name"] == "PROJ-001"

    def test_transform_item_default_ordered_qty(self, mapping):
        """Ordered qty defaults to 0 when missing."""
        record = {
            "name": "MRI-002",
            "item_code": "ITEM-001",
            "qty": 5,
        }
        result = mapping.transform_record(record)
        assert result["ordered_qty"] == 0

    def test_transform_item_missing_project(self, mapping):
        """Missing project results in None."""
        record = {
            "name": "MRI-003",
            "item_code": "ITEM-001",
            "qty": 5,
        }
        result = mapping.transform_record(record)
        assert result["_project_source_name"] is None


# ============ Sync Service Tests ============


class TestMaterialRequestSyncService:
    """Tests for Material Request Sync Service."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database session."""
        session = MagicMock()
        session.add = MagicMock()
        session.flush = MagicMock()
        session.execute = MagicMock()
        session.get = MagicMock(return_value=None)
        session.delete = MagicMock()
        return session

    @pytest.fixture
    def organization_id(self):
        """Test organization ID."""
        return uuid.uuid4()

    @pytest.fixture
    def user_id(self):
        """Test user ID."""
        return uuid.uuid4()

    @pytest.fixture
    def service(self, mock_db, organization_id, user_id):
        """Create sync service instance."""
        from app.services.erpnext.sync.material_request import (
            MaterialRequestSyncService,
        )

        return MaterialRequestSyncService(mock_db, organization_id, user_id)

    def test_transform_record_with_items(self, service):
        """Transform record includes items transformation."""
        record = {
            "name": "MAT-REQ-00001",
            "material_request_type": "Purchase",
            "status": "Submitted",
            "items": [
                {
                    "name": "MRI-001",
                    "item_code": "ITEM-001",
                    "qty": 10,
                },
                {
                    "name": "MRI-002",
                    "item_code": "ITEM-002",
                    "qty": 5,
                },
            ],
        }
        result = service.transform_record(record)

        assert "_items" in result
        assert len(result["_items"]) == 2
        assert result["_items"][0]["_item_source_name"] == "ITEM-001"
        assert result["_items"][1]["_item_source_name"] == "ITEM-002"

    def test_transform_record_no_items(self, service):
        """Transform record without items returns empty list."""
        record = {
            "name": "MAT-REQ-00002",
            "material_request_type": "Purchase",
            "status": "Draft",
        }
        result = service.transform_record(record)

        assert "_items" in result
        assert result["_items"] == []

    def test_resolve_entity_id_found(self, service, organization_id):
        """Entity ID is resolved from SyncEntity."""
        target_id = uuid.uuid4()

        mock_sync_entity = MagicMock()
        mock_sync_entity.target_id = target_id

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_sync_entity
        service.db.execute.return_value = mock_result

        result = service._resolve_entity_id("ITEM-001", "Item")

        assert result == target_id

    def test_resolve_entity_id_not_found(self, service):
        """Entity ID returns None when not found."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        service.db.execute.return_value = mock_result

        result = service._resolve_entity_id("UNKNOWN-ITEM", "Item")

        assert result is None

    def test_resolve_entity_id_none_input(self, service):
        """Entity ID returns None for None input."""
        result = service._resolve_entity_id(None, "Item")
        assert result is None

    def test_resolve_item_id_uses_cache(self, service):
        """Item ID resolution uses cache on second call."""
        target_id = uuid.uuid4()
        service._item_cache["ITEM-001"] = target_id

        # Should not hit database
        result = service._resolve_item_id("ITEM-001")

        assert result == target_id
        service.db.execute.assert_not_called()

    def test_resolve_warehouse_id_uses_cache(self, service):
        """Warehouse ID resolution uses cache on second call."""
        target_id = uuid.uuid4()
        service._warehouse_cache["Stores - DM"] = target_id

        result = service._resolve_warehouse_id("Stores - DM")

        assert result == target_id
        service.db.execute.assert_not_called()

    def test_resolve_project_id_uses_cache(self, service):
        """Project ID resolution uses cache on second call."""
        target_id = uuid.uuid4()
        service._project_cache["PROJ-001"] = target_id

        result = service._resolve_project_id("PROJ-001")

        assert result == target_id
        service.db.execute.assert_not_called()

    def test_get_entity_id(self, service):
        """Get entity ID returns request_id."""
        mock_request = MagicMock()
        mock_request.request_id = uuid.uuid4()

        result = service.get_entity_id(mock_request)

        assert result == mock_request.request_id


# ============ Integration-style Tests ============


class TestMaterialRequestMappingIntegration:
    """Integration tests for mapping and transformation flow."""

    def test_full_transform_flow(self):
        """Test complete transform of ERPNext record to DotMac format."""
        mapping = MaterialRequestMapping()
        item_mapping = MaterialRequestItemMapping()

        erpnext_record = {
            "name": "MAT-REQ-2026-00042",
            "material_request_type": "Material Transfer",
            "status": "Partially Ordered",
            "schedule_date": "2026-03-01",
            "set_warehouse": "Finished Goods - DM",
            "requested_by": "warehouse.manager@company.com",
            "reason": "Transfer stock to retail location",
            "modified": "2026-01-24 15:45:30",
            "items": [
                {
                    "name": "row-001",
                    "item_code": "SKU-A1001",
                    "warehouse": "Main Store - DM",
                    "qty": 100,
                    "ordered_qty": 50,
                    "stock_uom": "Nos",
                    "schedule_date": "2026-03-05",
                    "project": "RETAIL-EXPANSION",
                },
                {
                    "name": "row-002",
                    "item_code": "SKU-B2002",
                    "warehouse": "Main Store - DM",
                    "qty": 25,
                    "ordered_qty": 0,
                    "stock_uom": "Box",
                },
            ],
        }

        # Transform header
        result = mapping.transform_record(erpnext_record)

        # Transform items
        result["_items"] = []
        for item in erpnext_record.get("items", []):
            item_data = item_mapping.transform_record(item)
            result["_items"].append(item_data)

        # Verify header
        assert result["request_number"] == "MAT-REQ-2026-00042"
        assert result["request_type"] == "TRANSFER"
        assert result["status"] == "PARTIALLY_ORDERED"
        assert result["schedule_date"] == date(2026, 3, 1)
        assert result["_warehouse_source_name"] == "Finished Goods - DM"
        assert result["_requested_by_user"] == "warehouse.manager@company.com"
        assert result["remarks"] == "Transfer stock to retail location"

        # Verify items
        assert len(result["_items"]) == 2

        item1 = result["_items"][0]
        assert item1["_item_source_name"] == "SKU-A1001"
        assert item1["requested_qty"] == Decimal("100")
        assert item1["ordered_qty"] == Decimal("50")
        assert item1["_project_source_name"] == "RETAIL-EXPANSION"

        item2 = result["_items"][1]
        assert item2["_item_source_name"] == "SKU-B2002"
        assert item2["requested_qty"] == Decimal("25")
        assert item2["ordered_qty"] == Decimal("0")
        assert item2["_project_source_name"] is None
