"""
Tests for enhanced Project Sync — percent_complete, actual_cost, description,
priority, project_type, and cost center fields.

Tests cover:
- New field mappings in ProjectMapping
- Priority and type mapping transformers
- Field population in create_entity
- Field update in update_entity
- Default values for missing fields
- Cost center resolution
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.services.erpnext.mappings.projects import (
    ProjectMapping,
)

# pytest is used by @pytest.mark.parametrize in TestProjectPriorityMapping/TestProjectTypeMapping
_ = pytest

# ============ Mapping Tests ============


class TestProjectMappingEnhanced:
    """Tests for the 3 new field mappings added to ProjectMapping."""

    def setup_method(self) -> None:
        self.mapping = ProjectMapping()

    def test_percent_complete_mapped(self) -> None:
        """percent_complete is mapped from ERPNext."""
        record = {
            "name": "PROJ-001",
            "project_name": "Test Project",
            "status": "Open",
            "percent_complete": 75.5,
            "modified": "2026-01-01 00:00:00",
        }
        result = self.mapping.transform_record(record)
        assert result["percent_complete"] == Decimal("75.5")

    def test_actual_cost_mapped_from_total_costing_amount(self) -> None:
        """actual_cost is mapped from ERPNext total_costing_amount."""
        record = {
            "name": "PROJ-001",
            "project_name": "Test Project",
            "total_costing_amount": 150000.50,
            "modified": "2026-01-01 00:00:00",
        }
        result = self.mapping.transform_record(record)
        assert result["actual_cost"] == Decimal("150000.50")

    def test_description_mapped(self) -> None:
        """description is mapped from ERPNext."""
        record = {
            "name": "PROJ-001",
            "project_name": "Test Project",
            "description": "A fiber optics installation project",
            "modified": "2026-01-01 00:00:00",
        }
        result = self.mapping.transform_record(record)
        assert result["description"] == "A fiber optics installation project"

    def test_percent_complete_none_when_missing(self) -> None:
        """percent_complete defaults to None when not provided."""
        record = {
            "name": "PROJ-001",
            "project_name": "Test Project",
            "modified": "2026-01-01 00:00:00",
        }
        result = self.mapping.transform_record(record)
        assert result["percent_complete"] is None

    def test_actual_cost_none_when_missing(self) -> None:
        """actual_cost defaults to None when not provided."""
        record = {
            "name": "PROJ-001",
            "project_name": "Test Project",
            "modified": "2026-01-01 00:00:00",
        }
        result = self.mapping.transform_record(record)
        assert result["actual_cost"] is None

    def test_description_none_when_missing(self) -> None:
        """description defaults to None when not provided."""
        record = {
            "name": "PROJ-001",
            "project_name": "Test Project",
            "modified": "2026-01-01 00:00:00",
        }
        result = self.mapping.transform_record(record)
        assert result["description"] is None

    def test_percent_complete_zero(self) -> None:
        """percent_complete handles zero value."""
        record = {
            "name": "PROJ-001",
            "project_name": "Test Project",
            "percent_complete": 0,
            "modified": "2026-01-01 00:00:00",
        }
        result = self.mapping.transform_record(record)
        # 0 is falsy but should still be parsed
        assert result["percent_complete"] == Decimal("0")

    def test_all_three_fields_together(self) -> None:
        """All 3 new fields are mapped correctly in a single record."""
        record = {
            "name": "PROJ-001",
            "project_name": "Full Project",
            "percent_complete": 42.5,
            "total_costing_amount": 250000,
            "description": "Full description here",
            "status": "Working",
            "estimated_costing": 500000,
            "modified": "2026-01-01 00:00:00",
        }
        result = self.mapping.transform_record(record)
        assert result["percent_complete"] == Decimal("42.5")
        assert result["actual_cost"] == Decimal("250000")
        assert result["description"] == "Full description here"
        assert result["budget_amount"] == Decimal("500000")


# ============ Sync Service Tests ============


class TestProjectSyncServiceEnhanced:
    """Tests for new fields in create_entity and update_entity."""

    def setup_method(self) -> None:
        self.db = MagicMock()
        self.org_id = uuid.uuid4()
        self.user_id = uuid.uuid4()

    def _make_service(self):
        from app.services.erpnext.sync.projects import ProjectSyncService

        return ProjectSyncService(self.db, self.org_id, self.user_id)

    def test_create_entity_sets_new_fields(self) -> None:
        """create_entity populates percent_complete, actual_cost, description."""
        service = self._make_service()

        data = {
            "project_code": "PROJ-001",
            "project_name": "Test Project",
            "description": "Fiber installation",
            "status": "ACTIVE",
            "start_date": date(2026, 1, 1),
            "end_date": date(2026, 6, 30),
            "budget_amount": Decimal("500000"),
            "budget_currency_code": "NGN",
            "is_capitalizable": False,
            "percent_complete": Decimal("25.00"),
            "actual_cost": Decimal("125000"),
            "_source_modified": None,
            "_source_name": "PROJ-001",
            "_company": "Test Co",
            "_cost_center_source_name": None,
            "_customer_source_name": None,
        }

        project = service.create_entity(data)
        assert project.description == "Fiber installation"
        assert project.percent_complete == Decimal("25.00")
        assert project.actual_cost == Decimal("125000")

    def test_create_entity_defaults_percent_complete(self) -> None:
        """create_entity defaults percent_complete to 0 when None."""
        service = self._make_service()

        data = {
            "project_code": "PROJ-002",
            "project_name": "Test Project 2",
            "description": None,
            "status": "ACTIVE",
            "start_date": None,
            "end_date": None,
            "budget_amount": None,
            "budget_currency_code": "NGN",
            "is_capitalizable": False,
            "percent_complete": None,
            "actual_cost": None,
            "_source_modified": None,
            "_source_name": "PROJ-002",
            "_company": None,
            "_cost_center_source_name": None,
            "_customer_source_name": None,
        }

        project = service.create_entity(data)
        assert project.percent_complete == Decimal("0.00")
        assert project.actual_cost is None

    def test_update_entity_sets_new_fields(self) -> None:
        """update_entity updates percent_complete and actual_cost."""
        service = self._make_service()

        # Create a mock existing project
        existing = MagicMock()
        existing.project_code = "PROJ-001"
        existing.budget_currency_code = "NGN"
        existing.customer_id = None

        data = {
            "project_name": "Updated Project",
            "description": "Updated description",
            "status": "ACTIVE",
            "start_date": date(2026, 1, 1),
            "end_date": date(2026, 12, 31),
            "budget_amount": Decimal("600000"),
            "budget_currency_code": "NGN",
            "is_capitalizable": False,
            "percent_complete": Decimal("60.50"),
            "actual_cost": Decimal("300000"),
            "_source_modified": None,
            "_source_name": "PROJ-001",
            "_company": None,
            "_cost_center_source_name": None,
            "_customer_source_name": None,
        }

        service.update_entity(existing, data)
        assert existing.description == "Updated description"
        assert existing.percent_complete == Decimal("60.50")
        assert existing.actual_cost == Decimal("300000")

    def test_update_entity_skips_none_percent_complete(self) -> None:
        """update_entity doesn't overwrite percent_complete with None."""
        service = self._make_service()

        existing = MagicMock()
        existing.project_code = "PROJ-001"
        existing.budget_currency_code = "NGN"
        existing.customer_id = None
        existing.percent_complete = Decimal("50.00")

        data = {
            "project_name": "Project",
            "description": None,
            "status": "ACTIVE",
            "start_date": None,
            "end_date": None,
            "budget_amount": None,
            "budget_currency_code": "NGN",
            "is_capitalizable": False,
            "percent_complete": None,
            "actual_cost": None,
            "_source_modified": None,
            "_source_name": "PROJ-001",
            "_company": None,
            "_cost_center_source_name": None,
            "_customer_source_name": None,
        }

        service.update_entity(existing, data)
        # percent_complete should NOT have been overwritten with None
        assert existing.percent_complete == Decimal("50.00")


# ============ Priority Mapping Tests ============


class TestProjectPriorityMapping:
    """Tests for map_project_priority transformer."""

    def setup_method(self) -> None:
        self.mapping = ProjectMapping()

    @pytest.mark.parametrize(
        "erpnext_val,expected",
        [
            ("Low", "LOW"),
            ("Medium", "MEDIUM"),
            ("High", "HIGH"),
            ("Urgent", "CRITICAL"),
        ],
    )
    def test_known_priorities(self, erpnext_val: str, expected: str) -> None:
        """Known ERPNext priority values map correctly."""
        from app.services.erpnext.mappings.projects import map_project_priority

        assert map_project_priority(erpnext_val) == expected

    def test_unknown_priority_defaults_to_medium(self) -> None:
        """Unknown priority values default to MEDIUM."""
        from app.services.erpnext.mappings.projects import map_project_priority

        assert map_project_priority("Extreme") == "MEDIUM"

    def test_none_priority_defaults_to_medium(self) -> None:
        """None priority defaults to MEDIUM."""
        from app.services.erpnext.mappings.projects import map_project_priority

        assert map_project_priority(None) == "MEDIUM"

    def test_empty_priority_defaults_to_medium(self) -> None:
        """Empty string priority defaults to MEDIUM."""
        from app.services.erpnext.mappings.projects import map_project_priority

        assert map_project_priority("") == "MEDIUM"

    def test_priority_in_mapping_transform(self) -> None:
        """priority field flows through ProjectMapping.transform_record."""
        record = {
            "name": "PROJ-001",
            "project_name": "Test",
            "priority": "High",
            "modified": "2026-01-01 00:00:00",
        }
        result = self.mapping.transform_record(record)
        assert result["project_priority"] == "HIGH"

    def test_missing_priority_defaults_in_mapping(self) -> None:
        """Missing priority gets default MEDIUM from FieldMapping."""
        record = {
            "name": "PROJ-001",
            "project_name": "Test",
            "modified": "2026-01-01 00:00:00",
        }
        result = self.mapping.transform_record(record)
        assert result["project_priority"] == "MEDIUM"


# ============ Project Type Mapping Tests ============


class TestProjectTypeMapping:
    """Tests for map_project_type transformer."""

    def setup_method(self) -> None:
        self.mapping = ProjectMapping()

    @pytest.mark.parametrize(
        "erpnext_val,expected",
        [
            ("Fiber Optics Installation", "FIBER_OPTICS_INSTALLATION"),
            ("Radio Installation", "AIR_FIBER_INSTALLATION"),
            ("Cable Rerun", "CABLE_RERUN"),
            ("Radio Fiber Relocation", "AIR_FIBER_RELOCATION"),
            ("Fiber Optics Relocation", "FIBER_OPTICS_RELOCATION"),
            ("GOV ITT", "CLIENT"),
            ("GOV EOI", "CLIENT"),
            ("GOV FIN", "CLIENT"),
            ("NGO Temp", "CLIENT"),
            ("Contract Project", "CLIENT"),
            ("cabinet migration", "INTERNAL"),
            ("EQUIPMENT REPAIR", "INTERNAL"),
        ],
    )
    def test_known_types(self, erpnext_val: str, expected: str) -> None:
        """Known ERPNext project_type values map correctly."""
        from app.services.erpnext.mappings.projects import map_project_type

        assert map_project_type(erpnext_val) == expected

    def test_unknown_type_defaults_to_internal(self) -> None:
        """Unknown project_type values default to INTERNAL."""
        from app.services.erpnext.mappings.projects import map_project_type

        assert map_project_type("Some New Type") == "INTERNAL"

    def test_none_type_defaults_to_internal(self) -> None:
        """None project_type defaults to INTERNAL."""
        from app.services.erpnext.mappings.projects import map_project_type

        assert map_project_type(None) == "INTERNAL"

    def test_empty_type_defaults_to_internal(self) -> None:
        """Empty string project_type defaults to INTERNAL."""
        from app.services.erpnext.mappings.projects import map_project_type

        assert map_project_type("") == "INTERNAL"

    def test_type_in_mapping_transform(self) -> None:
        """project_type field flows through ProjectMapping.transform_record."""
        record = {
            "name": "PROJ-001",
            "project_name": "Test",
            "project_type": "Radio Installation",
            "modified": "2026-01-01 00:00:00",
        }
        result = self.mapping.transform_record(record)
        assert result["project_type"] == "AIR_FIBER_INSTALLATION"

    def test_missing_type_defaults_in_mapping(self) -> None:
        """Missing project_type gets default INTERNAL from FieldMapping."""
        record = {
            "name": "PROJ-001",
            "project_name": "Test",
            "modified": "2026-01-01 00:00:00",
        }
        result = self.mapping.transform_record(record)
        assert result["project_type"] == "INTERNAL"


# ============ Priority + Type in Sync Service Tests ============


class TestProjectSyncPriorityType:
    """Tests for priority and project_type in create_entity and update_entity."""

    def setup_method(self) -> None:
        self.db = MagicMock()
        self.org_id = uuid.uuid4()
        self.user_id = uuid.uuid4()

    def _make_service(self):
        from app.services.erpnext.sync.projects import ProjectSyncService

        return ProjectSyncService(self.db, self.org_id, self.user_id)

    def _base_data(self, **overrides: object) -> dict:
        """Build a base data dict for create_entity."""
        data: dict = {
            "project_code": "PROJ-001",
            "project_name": "Test Project",
            "description": None,
            "status": "ACTIVE",
            "project_priority": "MEDIUM",
            "project_type": "INTERNAL",
            "start_date": None,
            "end_date": None,
            "budget_amount": None,
            "budget_currency_code": "NGN",
            "is_capitalizable": False,
            "percent_complete": None,
            "actual_cost": None,
            "_source_modified": None,
            "_source_name": "PROJ-001",
            "_company": None,
            "_cost_center_source_name": None,
            "_customer_source_name": None,
        }
        data.update(overrides)
        return data

    def test_create_entity_sets_priority_high(self) -> None:
        """create_entity sets project_priority from mapped value."""
        from app.models.finance.core_org.project import ProjectPriority

        service = self._make_service()
        project = service.create_entity(self._base_data(project_priority="HIGH"))
        assert project.project_priority == ProjectPriority.HIGH

    def test_create_entity_sets_priority_critical(self) -> None:
        """create_entity handles CRITICAL priority (mapped from Urgent)."""
        from app.models.finance.core_org.project import ProjectPriority

        service = self._make_service()
        project = service.create_entity(self._base_data(project_priority="CRITICAL"))
        assert project.project_priority == ProjectPriority.CRITICAL

    def test_create_entity_defaults_priority_on_invalid(self) -> None:
        """create_entity defaults to MEDIUM for invalid priority value."""
        from app.models.finance.core_org.project import ProjectPriority

        service = self._make_service()
        project = service.create_entity(self._base_data(project_priority="INVALID"))
        assert project.project_priority == ProjectPriority.MEDIUM

    def test_create_entity_sets_type_fiber(self) -> None:
        """create_entity sets project_type from mapped value."""
        from app.models.finance.core_org.project import ProjectType

        service = self._make_service()
        project = service.create_entity(
            self._base_data(project_type="FIBER_OPTICS_INSTALLATION")
        )
        assert project.project_type == ProjectType.FIBER_OPTICS_INSTALLATION

    def test_create_entity_sets_type_client(self) -> None:
        """create_entity handles CLIENT type (mapped from GOV/contract types)."""
        from app.models.finance.core_org.project import ProjectType

        service = self._make_service()
        project = service.create_entity(self._base_data(project_type="CLIENT"))
        assert project.project_type == ProjectType.CLIENT

    def test_create_entity_defaults_type_on_invalid(self) -> None:
        """create_entity defaults to INTERNAL for invalid type value."""
        from app.models.finance.core_org.project import ProjectType

        service = self._make_service()
        project = service.create_entity(self._base_data(project_type="UNKNOWN_TYPE"))
        assert project.project_type == ProjectType.INTERNAL

    def test_update_entity_sets_priority(self) -> None:
        """update_entity updates project_priority."""
        from app.models.finance.core_org.project import ProjectPriority

        service = self._make_service()
        existing = MagicMock()
        existing.project_code = "PROJ-001"
        existing.budget_currency_code = "NGN"
        existing.customer_id = None

        service.update_entity(existing, self._base_data(project_priority="HIGH"))
        assert existing.project_priority == ProjectPriority.HIGH

    def test_update_entity_sets_type(self) -> None:
        """update_entity updates project_type."""
        from app.models.finance.core_org.project import ProjectType

        service = self._make_service()
        existing = MagicMock()
        existing.project_code = "PROJ-001"
        existing.budget_currency_code = "NGN"
        existing.customer_id = None

        service.update_entity(
            existing,
            self._base_data(project_type="AIR_FIBER_INSTALLATION"),
        )
        assert existing.project_type == ProjectType.AIR_FIBER_INSTALLATION

    def test_update_entity_skips_none_priority(self) -> None:
        """update_entity doesn't overwrite priority when not provided."""
        from app.models.finance.core_org.project import ProjectPriority

        service = self._make_service()
        existing = MagicMock()
        existing.project_code = "PROJ-001"
        existing.budget_currency_code = "NGN"
        existing.customer_id = None
        existing.project_priority = ProjectPriority.HIGH

        # No project_priority key → popped as None → skip
        data = self._base_data()
        del data["project_priority"]
        service.update_entity(existing, data)
        # Should NOT have been overwritten
        assert existing.project_priority == ProjectPriority.HIGH

    def test_update_entity_skips_none_type(self) -> None:
        """update_entity doesn't overwrite type when not provided."""
        from app.models.finance.core_org.project import ProjectType

        service = self._make_service()
        existing = MagicMock()
        existing.project_code = "PROJ-001"
        existing.budget_currency_code = "NGN"
        existing.customer_id = None
        existing.project_type = ProjectType.CLIENT

        data = self._base_data()
        del data["project_type"]
        service.update_entity(existing, data)
        assert existing.project_type == ProjectType.CLIENT


# ============ Cost Center Resolution Tests ============


class TestCostCenterResolution:
    """Tests for _resolve_cost_center_id."""

    def setup_method(self) -> None:
        self.db = MagicMock()
        self.org_id = uuid.uuid4()
        self.user_id = uuid.uuid4()

    def _make_service(self):
        from app.services.erpnext.sync.projects import ProjectSyncService

        return ProjectSyncService(self.db, self.org_id, self.user_id)

    def test_returns_none_for_none_source(self) -> None:
        """Returns None when source_name is None."""
        service = self._make_service()
        assert service._resolve_cost_center_id(None) is None

    def test_returns_none_for_empty_source(self) -> None:
        """Returns None when source_name is empty."""
        service = self._make_service()
        assert service._resolve_cost_center_id("") is None

    def test_returns_target_id_when_found(self) -> None:
        """Returns target_id from SyncEntity when found."""
        service = self._make_service()
        target_id = uuid.uuid4()
        mock_sync = MagicMock()
        mock_sync.target_id = target_id
        self.db.execute.return_value.scalar_one_or_none.return_value = mock_sync

        result = service._resolve_cost_center_id("Main - DMN")
        assert result == target_id

    def test_returns_none_when_not_found(self) -> None:
        """Returns None when SyncEntity not found."""
        service = self._make_service()
        self.db.execute.return_value.scalar_one_or_none.return_value = None

        result = service._resolve_cost_center_id("Unknown CC")
        assert result is None

    def test_create_entity_sets_cost_center(self) -> None:
        """create_entity populates cost_center_id when resolved."""
        service = self._make_service()
        cc_id = uuid.uuid4()

        # Mock the cost center resolution
        self.db.execute.return_value.scalar_one_or_none.return_value = MagicMock(
            target_id=cc_id
        )

        data = {
            "project_code": "PROJ-CC",
            "project_name": "CC Project",
            "description": None,
            "status": "ACTIVE",
            "project_priority": "MEDIUM",
            "project_type": "INTERNAL",
            "start_date": None,
            "end_date": None,
            "budget_amount": None,
            "budget_currency_code": "NGN",
            "is_capitalizable": False,
            "percent_complete": None,
            "actual_cost": None,
            "_source_modified": None,
            "_source_name": "PROJ-CC",
            "_company": None,
            "_cost_center_source_name": "Main - DMN",
            "_customer_source_name": None,
        }

        project = service.create_entity(data)
        assert project.cost_center_id == cc_id
