"""
Tests for ReportInstanceService.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

from tests.ifrs.rpt.conftest import (
    MockReportDefinition,
    MockReportInstance,
)


class TestReportInstanceServiceQueue:
    """Tests for queue_report method."""

    def test_queue_report_success(
        self, mock_db, org_id, user_id, mock_report_definition
    ):
        """Test successful report queue."""
        from app.services.finance.rpt.report_instance import (
            ReportGenerationRequest,
            ReportInstanceService,
        )

        mock_db.get.return_value = mock_report_definition

        request = ReportGenerationRequest(
            report_def_id=mock_report_definition.report_def_id,
            output_format="PDF",
            parameters={"date": "2024-01-01"},
        )

        ReportInstanceService.queue_report(mock_db, org_id, request, user_id)

        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

    def test_queue_report_definition_not_found(self, mock_db, org_id, user_id):
        """Test queue with missing report definition."""
        from app.services.finance.rpt.report_instance import (
            ReportGenerationRequest,
            ReportInstanceService,
        )

        mock_db.get.return_value = None

        request = ReportGenerationRequest(
            report_def_id=uuid.uuid4(),
            output_format="PDF",
        )

        with pytest.raises(HTTPException) as exc:
            ReportInstanceService.queue_report(mock_db, org_id, request, user_id)

        assert exc.value.status_code == 404
        assert "not found" in exc.value.detail.lower()

    def test_queue_report_inactive_definition(self, mock_db, org_id, user_id):
        """Test queue with inactive report definition."""
        from app.services.finance.rpt.report_instance import (
            ReportGenerationRequest,
            ReportInstanceService,
        )

        inactive_def = MockReportDefinition(
            organization_id=org_id,
            is_active=False,
        )
        mock_db.get.return_value = inactive_def

        request = ReportGenerationRequest(
            report_def_id=inactive_def.report_def_id,
            output_format="PDF",
        )

        with pytest.raises(HTTPException) as exc:
            ReportInstanceService.queue_report(mock_db, org_id, request, user_id)

        assert exc.value.status_code == 400
        assert "not active" in exc.value.detail.lower()

    def test_queue_report_unsupported_format(self, mock_db, org_id, user_id):
        """Test queue with unsupported output format."""
        from app.services.finance.rpt.report_instance import (
            ReportGenerationRequest,
            ReportInstanceService,
        )

        definition = MockReportDefinition(
            organization_id=org_id,
            supported_formats=["PDF", "XLSX"],
        )
        mock_db.get.return_value = definition

        request = ReportGenerationRequest(
            report_def_id=definition.report_def_id,
            output_format="DOCX",
        )

        with pytest.raises(HTTPException) as exc:
            ReportInstanceService.queue_report(mock_db, org_id, request, user_id)

        assert exc.value.status_code == 400
        assert "not supported" in exc.value.detail.lower()


class TestReportInstanceServiceGeneration:
    """Tests for generation lifecycle methods."""

    def test_start_generation_success(self, mock_db, mock_report_instance):
        """Test successful generation start."""
        from app.models.finance.rpt.report_instance import ReportStatus
        from app.services.finance.rpt.report_instance import ReportInstanceService

        mock_report_instance.status = ReportStatus.QUEUED
        mock_db.get.return_value = mock_report_instance

        ReportInstanceService.start_generation(
            mock_db, mock_report_instance.instance_id
        )

        assert mock_report_instance.status == ReportStatus.GENERATING
        assert mock_report_instance.started_at is not None
        mock_db.commit.assert_called_once()

    def test_start_generation_not_found(self, mock_db):
        """Test start generation with missing instance."""
        from app.services.finance.rpt.report_instance import ReportInstanceService

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            ReportInstanceService.start_generation(mock_db, uuid.uuid4())

        assert exc.value.status_code == 404

    def test_start_generation_wrong_status(self, mock_db, mock_report_instance):
        """Test start generation with wrong status."""
        from app.models.finance.rpt.report_instance import ReportStatus
        from app.services.finance.rpt.report_instance import ReportInstanceService

        mock_report_instance.status = ReportStatus.COMPLETED
        mock_db.get.return_value = mock_report_instance

        with pytest.raises(HTTPException) as exc:
            ReportInstanceService.start_generation(
                mock_db, mock_report_instance.instance_id
            )

        assert exc.value.status_code == 400

    def test_complete_generation_success(self, mock_db, mock_report_instance):
        """Test successful generation completion."""
        from app.models.finance.rpt.report_instance import ReportStatus
        from app.services.finance.rpt.report_instance import ReportInstanceService

        mock_report_instance.status = ReportStatus.GENERATING
        mock_report_instance.started_at = datetime.now(UTC) - timedelta(seconds=5)
        mock_db.get.return_value = mock_report_instance

        ReportInstanceService.complete_generation(
            mock_db,
            mock_report_instance.instance_id,
            output_file_path="/reports/output.pdf",
            output_size_bytes=1024,
        )

        assert mock_report_instance.status == ReportStatus.COMPLETED
        assert mock_report_instance.output_file_path == "/reports/output.pdf"
        assert mock_report_instance.output_size_bytes == 1024
        assert mock_report_instance.generation_time_ms is not None
        mock_db.commit.assert_called_once()

    def test_complete_generation_not_found(self, mock_db):
        """Test complete with missing instance."""
        from app.services.finance.rpt.report_instance import ReportInstanceService

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            ReportInstanceService.complete_generation(
                mock_db,
                uuid.uuid4(),
                output_file_path="/reports/output.pdf",
                output_size_bytes=1024,
            )

        assert exc.value.status_code == 404

    def test_complete_generation_wrong_status(self, mock_db, mock_report_instance):
        """Test complete with wrong status."""
        from app.models.finance.rpt.report_instance import ReportStatus
        from app.services.finance.rpt.report_instance import ReportInstanceService

        mock_report_instance.status = ReportStatus.QUEUED
        mock_db.get.return_value = mock_report_instance

        with pytest.raises(HTTPException) as exc:
            ReportInstanceService.complete_generation(
                mock_db,
                mock_report_instance.instance_id,
                output_file_path="/reports/output.pdf",
                output_size_bytes=1024,
            )

        assert exc.value.status_code == 400

    def test_fail_generation_success(self, mock_db, mock_report_instance):
        """Test successful generation failure recording."""
        from app.models.finance.rpt.report_instance import ReportStatus
        from app.services.finance.rpt.report_instance import ReportInstanceService

        mock_report_instance.status = ReportStatus.GENERATING
        mock_report_instance.started_at = datetime.now(UTC)
        mock_db.get.return_value = mock_report_instance

        ReportInstanceService.fail_generation(
            mock_db,
            mock_report_instance.instance_id,
            error_message="Database connection failed",
        )

        assert mock_report_instance.status == ReportStatus.FAILED
        assert mock_report_instance.error_message == "Database connection failed"
        mock_db.commit.assert_called_once()

    def test_fail_generation_not_found(self, mock_db):
        """Test fail with missing instance."""
        from app.services.finance.rpt.report_instance import ReportInstanceService

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            ReportInstanceService.fail_generation(
                mock_db, uuid.uuid4(), error_message="Error"
            )

        assert exc.value.status_code == 404


class TestReportInstanceServiceCancel:
    """Tests for cancel_report method."""

    def test_cancel_report_success(self, mock_db, org_id, mock_report_instance):
        """Test successful report cancellation."""
        from app.models.finance.rpt.report_instance import ReportStatus
        from app.services.finance.rpt.report_instance import ReportInstanceService

        mock_report_instance.status = ReportStatus.QUEUED
        mock_report_instance.organization_id = org_id
        mock_db.get.return_value = mock_report_instance

        ReportInstanceService.cancel_report(
            mock_db, org_id, mock_report_instance.instance_id
        )

        assert mock_report_instance.status == ReportStatus.CANCELLED
        mock_db.commit.assert_called_once()

    def test_cancel_report_not_found(self, mock_db, org_id):
        """Test cancel with missing instance."""
        from app.services.finance.rpt.report_instance import ReportInstanceService

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            ReportInstanceService.cancel_report(mock_db, org_id, uuid.uuid4())

        assert exc.value.status_code == 404

    def test_cancel_report_wrong_status(self, mock_db, org_id, mock_report_instance):
        """Test cancel with wrong status (not QUEUED)."""
        from app.models.finance.rpt.report_instance import ReportStatus
        from app.services.finance.rpt.report_instance import ReportInstanceService

        mock_report_instance.status = ReportStatus.GENERATING
        mock_report_instance.organization_id = org_id
        mock_db.get.return_value = mock_report_instance

        with pytest.raises(HTTPException) as exc:
            ReportInstanceService.cancel_report(
                mock_db, org_id, mock_report_instance.instance_id
            )

        assert exc.value.status_code == 400


class TestReportInstanceServiceQueries:
    """Tests for query methods."""

    def test_get_queued_reports(self, mock_db, mock_report_instance):
        """Test getting queued reports."""
        from app.services.finance.rpt.report_instance import ReportInstanceService

        mock_db.scalars.return_value.all.return_value = [mock_report_instance]

        result = ReportInstanceService.get_queued_reports(mock_db)

        assert len(result) == 1

    def test_get_queued_reports_with_org_filter(
        self, mock_db, org_id, mock_report_instance
    ):
        """Test getting queued reports with organization filter."""
        from app.services.finance.rpt.report_instance import ReportInstanceService

        mock_db.scalars.return_value.all.return_value = [mock_report_instance]

        result = ReportInstanceService.get_queued_reports(
            mock_db, organization_id=str(org_id)
        )

        assert len(result) == 1

    def test_get_generation_statistics(self, mock_db, org_id, mock_report_instance):
        """Test getting generation statistics."""
        from app.models.finance.rpt.report_instance import ReportStatus
        from app.services.finance.rpt.report_instance import ReportInstanceService

        completed_instance = MockReportInstance(
            organization_id=org_id,
            status=ReportStatus.COMPLETED,
            generation_time_ms=5000,
        )
        failed_instance = MockReportInstance(
            organization_id=org_id,
            status=ReportStatus.FAILED,
        )

        mock_db.scalars.return_value.all.return_value = [
            completed_instance,
            failed_instance,
        ]

        result = ReportInstanceService.get_generation_statistics(mock_db, str(org_id))

        assert result["total"] == 2
        assert result["completed"] == 1
        assert result["failed"] == 1

    def test_get_instance_by_id(self, mock_db, mock_report_instance):
        """Test getting instance by ID."""
        from app.services.finance.rpt.report_instance import ReportInstanceService

        mock_db.get.return_value = mock_report_instance

        result = ReportInstanceService.get(
            mock_db, str(mock_report_instance.instance_id)
        )

        assert result is not None

    def test_get_instance_not_found(self, mock_db):
        """Test getting non-existent instance."""
        from app.services.finance.rpt.report_instance import ReportInstanceService

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            ReportInstanceService.get(mock_db, str(uuid.uuid4()))

        assert exc.value.status_code == 404

    def test_list_instances(self, mock_db, org_id, mock_report_instance):
        """Test listing instances."""
        from app.services.finance.rpt.report_instance import ReportInstanceService

        mock_db.scalars.return_value.all.return_value = [mock_report_instance]

        result = ReportInstanceService.list(
            mock_db,
            organization_id=str(org_id),
        )

        assert len(result) == 1


class TestReportInstanceServiceRegenerate:
    """Tests for regenerate_report method."""

    def test_regenerate_report_success(
        self, mock_db, org_id, user_id, mock_report_instance, mock_report_definition
    ):
        """Test successful report regeneration."""
        from app.services.finance.rpt.report_instance import ReportInstanceService

        mock_report_instance.organization_id = org_id
        mock_report_instance.report_def_id = mock_report_definition.report_def_id
        mock_report_instance.output_format = "PDF"
        mock_report_instance.fiscal_period_id = None
        mock_report_instance.parameters_used = {}
        mock_report_instance.schedule_id = None

        def mock_get_side_effect(model, id):
            if (
                hasattr(model, "__tablename__")
                and model.__tablename__ == "report_definition"
            ):
                return mock_report_definition
            return mock_report_instance

        mock_db.get.side_effect = [mock_report_instance, mock_report_definition]

        ReportInstanceService.regenerate_report(
            mock_db, org_id, mock_report_instance.instance_id, user_id
        )

        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

    def test_regenerate_report_not_found(self, mock_db, org_id, user_id):
        """Test regenerate with missing original instance."""
        from app.services.finance.rpt.report_instance import ReportInstanceService

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            ReportInstanceService.regenerate_report(
                mock_db, org_id, uuid.uuid4(), user_id
            )

        assert exc.value.status_code == 404


class TestReportInstanceServiceCleanup:
    """Tests for cleanup_old_instances method."""

    def test_cleanup_old_instances(self, mock_db, org_id, mock_report_instance):
        """Test cleanup of old instances."""
        from app.services.finance.rpt.report_instance import ReportInstanceService

        mock_db.scalars.return_value.all.return_value = [mock_report_instance]

        result = ReportInstanceService.cleanup_old_instances(
            mock_db, org_id, retention_days=30
        )

        assert result == 1
        mock_db.delete.assert_called_once_with(mock_report_instance)
        mock_db.commit.assert_called_once()
