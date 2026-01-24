"""
Tests for ReportDefinitionService.
"""

import uuid
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from tests.ifrs.rpt.conftest import (
    MockReportDefinition,
)


class TestReportDefinitionServiceCreate:
    """Tests for create_definition method."""

    def test_create_definition_success(self, mock_db, org_id, user_id):
        """Test successful report definition creation."""
        from app.services.finance.rpt.report_definition import (
            ReportDefinitionService,
            ReportDefinitionInput,
        )
        from app.models.finance.rpt.report_definition import ReportType

        mock_db.query.return_value.filter.return_value.first.return_value = None

        input_data = ReportDefinitionInput(
            report_code="RPT-001",
            report_name="Balance Sheet",
            report_type=ReportType.BALANCE_SHEET,
            data_source_type="SQL",
        )

        result = ReportDefinitionService.create_definition(
            mock_db, org_id, input_data, user_id
        )

        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

    def test_create_definition_duplicate_code(self, mock_db, org_id, user_id):
        """Test creation with duplicate report code."""
        from app.services.finance.rpt.report_definition import (
            ReportDefinitionService,
            ReportDefinitionInput,
        )
        from app.models.finance.rpt.report_definition import ReportType

        existing = MockReportDefinition(report_code="RPT-001")
        mock_db.query.return_value.filter.return_value.first.return_value = existing

        input_data = ReportDefinitionInput(
            report_code="RPT-001",
            report_name="Balance Sheet",
            report_type=ReportType.BALANCE_SHEET,
            data_source_type="SQL",
        )

        with pytest.raises(HTTPException) as exc:
            ReportDefinitionService.create_definition(
                mock_db, org_id, input_data, user_id
            )

        assert exc.value.status_code == 400
        assert "already exists" in exc.value.detail


class TestReportDefinitionServiceUpdate:
    """Tests for update_definition method."""

    def test_update_definition_success(
        self, mock_db, org_id, mock_report_definition
    ):
        """Test successful report definition update."""
        from app.services.finance.rpt.report_definition import ReportDefinitionService

        mock_db.get.return_value = mock_report_definition

        result = ReportDefinitionService.update_definition(
            mock_db,
            org_id,
            mock_report_definition.report_def_id,
            report_name="Updated Report Name",
            description="Updated description",
        )

        assert mock_report_definition.report_name == "Updated Report Name"
        assert mock_report_definition.description == "Updated description"
        mock_db.commit.assert_called_once()

    def test_update_definition_not_found(self, mock_db, org_id):
        """Test update of non-existent definition."""
        from app.services.finance.rpt.report_definition import ReportDefinitionService

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            ReportDefinitionService.update_definition(
                mock_db,
                org_id,
                uuid.uuid4(),
                report_name="Updated Name",
            )

        assert exc.value.status_code == 404

    def test_update_system_report_fails(self, mock_db, org_id, mock_system_report):
        """Test update of system report fails."""
        from app.services.finance.rpt.report_definition import ReportDefinitionService

        mock_db.get.return_value = mock_system_report

        with pytest.raises(HTTPException) as exc:
            ReportDefinitionService.update_definition(
                mock_db,
                org_id,
                mock_system_report.report_def_id,
                report_name="Updated Name",
            )

        assert exc.value.status_code == 400
        assert "system report" in exc.value.detail.lower()


class TestReportDefinitionServiceUpdateStructure:
    """Tests for update_structure method."""

    def test_update_structure_success(self, mock_db, org_id, mock_report_definition):
        """Test successful structure update."""
        from app.services.finance.rpt.report_definition import ReportDefinitionService

        mock_db.get.return_value = mock_report_definition
        original_version = mock_report_definition.template_version

        column_defs = {"col1": {"name": "Account", "type": "string"}}

        result = ReportDefinitionService.update_structure(
            mock_db,
            org_id,
            mock_report_definition.report_def_id,
            column_definitions=column_defs,
        )

        assert mock_report_definition.column_definitions == column_defs
        assert mock_report_definition.template_version == original_version + 1
        mock_db.commit.assert_called_once()

    def test_update_structure_not_found(self, mock_db, org_id):
        """Test structure update of non-existent definition."""
        from app.services.finance.rpt.report_definition import ReportDefinitionService

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            ReportDefinitionService.update_structure(
                mock_db,
                org_id,
                uuid.uuid4(),
                column_definitions={"col1": {}},
            )

        assert exc.value.status_code == 404


class TestReportDefinitionServiceUpdateDataSource:
    """Tests for update_data_source method."""

    def test_update_data_source_success(self, mock_db, org_id, mock_report_definition):
        """Test successful data source update."""
        from app.services.finance.rpt.report_definition import ReportDefinitionService

        mock_db.get.return_value = mock_report_definition

        data_source_config = {"query": "SELECT * FROM accounts"}

        result = ReportDefinitionService.update_data_source(
            mock_db,
            org_id,
            mock_report_definition.report_def_id,
            data_source_type="SQL",
            data_source_config=data_source_config,
        )

        assert mock_report_definition.data_source_type == "SQL"
        assert mock_report_definition.data_source_config == data_source_config
        mock_db.commit.assert_called_once()


class TestReportDefinitionServiceDeactivate:
    """Tests for deactivate method."""

    def test_deactivate_success(self, mock_db, org_id, mock_report_definition):
        """Test successful deactivation."""
        from app.services.finance.rpt.report_definition import ReportDefinitionService

        mock_db.get.return_value = mock_report_definition

        result = ReportDefinitionService.deactivate(
            mock_db, org_id, mock_report_definition.report_def_id
        )

        assert mock_report_definition.is_active is False
        mock_db.commit.assert_called_once()

    def test_deactivate_system_report_fails(self, mock_db, org_id, mock_system_report):
        """Test deactivation of system report fails."""
        from app.services.finance.rpt.report_definition import ReportDefinitionService

        mock_db.get.return_value = mock_system_report

        with pytest.raises(HTTPException) as exc:
            ReportDefinitionService.deactivate(
                mock_db, org_id, mock_system_report.report_def_id
            )

        assert exc.value.status_code == 400
        assert "system report" in exc.value.detail.lower()


class TestReportDefinitionServiceClone:
    """Tests for clone_definition method."""

    def test_clone_definition_success(
        self, mock_db, org_id, user_id, mock_report_definition
    ):
        """Test successful definition cloning."""
        from app.services.finance.rpt.report_definition import ReportDefinitionService

        mock_db.get.return_value = mock_report_definition
        mock_db.query.return_value.filter.return_value.first.return_value = None

        result = ReportDefinitionService.clone_definition(
            mock_db,
            org_id,
            mock_report_definition.report_def_id,
            new_report_code="RPT-CLONE",
            new_report_name="Cloned Report",
            created_by_user_id=user_id,
        )

        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

    def test_clone_definition_source_not_found(self, mock_db, org_id, user_id):
        """Test cloning non-existent source definition."""
        from app.services.finance.rpt.report_definition import ReportDefinitionService

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            ReportDefinitionService.clone_definition(
                mock_db,
                org_id,
                uuid.uuid4(),
                new_report_code="RPT-CLONE",
                new_report_name="Cloned Report",
                created_by_user_id=user_id,
            )

        assert exc.value.status_code == 404

    def test_clone_definition_duplicate_code(
        self, mock_db, org_id, user_id, mock_report_definition
    ):
        """Test cloning with duplicate code."""
        from app.services.finance.rpt.report_definition import ReportDefinitionService

        mock_db.get.return_value = mock_report_definition
        existing = MockReportDefinition(report_code="RPT-CLONE")
        mock_db.query.return_value.filter.return_value.first.return_value = existing

        with pytest.raises(HTTPException) as exc:
            ReportDefinitionService.clone_definition(
                mock_db,
                org_id,
                mock_report_definition.report_def_id,
                new_report_code="RPT-CLONE",
                new_report_name="Cloned Report",
                created_by_user_id=user_id,
            )

        assert exc.value.status_code == 400
        assert "already exists" in exc.value.detail


class TestReportDefinitionServiceQueries:
    """Tests for query methods."""

    def test_get_by_code(self, mock_db, org_id, mock_report_definition):
        """Test getting definition by code."""
        from app.services.finance.rpt.report_definition import ReportDefinitionService

        mock_db.query.return_value.filter.return_value.first.return_value = (
            mock_report_definition
        )

        result = ReportDefinitionService.get_by_code(
            mock_db, str(org_id), "RPT-001"
        )

        assert result is not None

    def test_get_by_code_not_found(self, mock_db, org_id):
        """Test getting non-existent code."""
        from app.services.finance.rpt.report_definition import ReportDefinitionService

        mock_db.query.return_value.filter.return_value.first.return_value = None

        result = ReportDefinitionService.get_by_code(
            mock_db, str(org_id), "NONEXISTENT"
        )

        assert result is None

    def test_get_by_type(self, mock_db, org_id, mock_report_definition):
        """Test getting definitions by type."""
        from app.services.finance.rpt.report_definition import ReportDefinitionService
        from app.models.finance.rpt.report_definition import ReportType

        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            mock_report_definition
        ]

        result = ReportDefinitionService.get_by_type(
            mock_db, str(org_id), ReportType.BALANCE_SHEET
        )

        assert len(result) == 1

    def test_get_definition_by_id(self, mock_db, mock_report_definition):
        """Test getting definition by ID."""
        from app.services.finance.rpt.report_definition import ReportDefinitionService

        mock_db.get.return_value = mock_report_definition

        result = ReportDefinitionService.get(
            mock_db, str(mock_report_definition.report_def_id)
        )

        assert result is not None

    def test_get_definition_not_found(self, mock_db):
        """Test getting non-existent definition."""
        from app.services.finance.rpt.report_definition import ReportDefinitionService

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            ReportDefinitionService.get(mock_db, str(uuid.uuid4()))

        assert exc.value.status_code == 404

    def test_list_definitions(self, mock_db, org_id, mock_report_definition):
        """Test listing definitions."""
        from app.services.finance.rpt.report_definition import ReportDefinitionService
        from app.models.finance.rpt.report_definition import ReportType

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.all.return_value = [mock_report_definition]
        mock_db.query.return_value = mock_query

        result = ReportDefinitionService.list(
            mock_db,
            organization_id=str(org_id),
            report_type=ReportType.BALANCE_SHEET,
            category="Financial",
            is_active=True,
        )

        assert len(result) == 1

    def test_list_definitions_no_filters(self, mock_db, mock_report_definition):
        """Test listing definitions without filters."""
        from app.services.finance.rpt.report_definition import ReportDefinitionService

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.all.return_value = [mock_report_definition]
        mock_db.query.return_value = mock_query

        result = ReportDefinitionService.list(mock_db)

        assert len(result) == 1
