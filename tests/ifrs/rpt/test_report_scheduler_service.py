"""
Tests for ReportSchedulerService.
"""

import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from tests.ifrs.rpt.conftest import (
    MockReportDefinition,
    MockReportSchedule,
)


class TestReportSchedulerServiceCreate:
    """Tests for create_schedule method."""

    def test_create_schedule_success(self, mock_db, org_id, user_id, mock_report_definition):
        """Test successful schedule creation."""
        from app.services.ifrs.rpt.report_scheduler import (
            ReportSchedulerService,
            ScheduleInput,
        )
        from app.models.ifrs.rpt.report_schedule import ScheduleFrequency

        mock_db.get.return_value = mock_report_definition

        input_data = ScheduleInput(
            report_def_id=mock_report_definition.report_def_id,
            schedule_name="Monthly Balance Sheet",
            frequency=ScheduleFrequency.MONTHLY,
            output_format="PDF",
            day_of_month=1,
            time_of_day="08:00",
        )

        result = ReportSchedulerService.create_schedule(
            mock_db, org_id, input_data, user_id
        )

        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

    def test_create_schedule_definition_not_found(self, mock_db, org_id, user_id):
        """Test creation with missing report definition."""
        from app.services.ifrs.rpt.report_scheduler import (
            ReportSchedulerService,
            ScheduleInput,
        )
        from app.models.ifrs.rpt.report_schedule import ScheduleFrequency

        mock_db.get.return_value = None

        input_data = ScheduleInput(
            report_def_id=uuid.uuid4(),
            schedule_name="Monthly Report",
            frequency=ScheduleFrequency.MONTHLY,
        )

        with pytest.raises(HTTPException) as exc:
            ReportSchedulerService.create_schedule(
                mock_db, org_id, input_data, user_id
            )

        assert exc.value.status_code == 404
        assert "not found" in exc.value.detail.lower()

    def test_create_schedule_inactive_definition(self, mock_db, org_id, user_id):
        """Test creation with inactive report definition."""
        from app.services.ifrs.rpt.report_scheduler import (
            ReportSchedulerService,
            ScheduleInput,
        )
        from app.models.ifrs.rpt.report_schedule import ScheduleFrequency

        inactive_def = MockReportDefinition(
            organization_id=org_id,
            is_active=False,
        )
        mock_db.get.return_value = inactive_def

        input_data = ScheduleInput(
            report_def_id=inactive_def.report_def_id,
            schedule_name="Monthly Report",
            frequency=ScheduleFrequency.MONTHLY,
        )

        with pytest.raises(HTTPException) as exc:
            ReportSchedulerService.create_schedule(
                mock_db, org_id, input_data, user_id
            )

        assert exc.value.status_code == 400
        assert "inactive" in exc.value.detail.lower()

    def test_create_schedule_unsupported_format(self, mock_db, org_id, user_id):
        """Test creation with unsupported output format."""
        from app.services.ifrs.rpt.report_scheduler import (
            ReportSchedulerService,
            ScheduleInput,
        )
        from app.models.ifrs.rpt.report_schedule import ScheduleFrequency

        definition = MockReportDefinition(
            organization_id=org_id,
            supported_formats=["PDF", "XLSX"],
        )
        mock_db.get.return_value = definition

        input_data = ScheduleInput(
            report_def_id=definition.report_def_id,
            schedule_name="Monthly Report",
            frequency=ScheduleFrequency.MONTHLY,
            output_format="DOCX",
        )

        with pytest.raises(HTTPException) as exc:
            ReportSchedulerService.create_schedule(
                mock_db, org_id, input_data, user_id
            )

        assert exc.value.status_code == 400
        assert "not supported" in exc.value.detail.lower()


class TestReportSchedulerServiceUpdate:
    """Tests for update_schedule method."""

    def test_update_schedule_success(self, mock_db, org_id, mock_report_schedule):
        """Test successful schedule update."""
        from app.services.ifrs.rpt.report_scheduler import ReportSchedulerService

        mock_report_schedule.organization_id = org_id
        mock_db.get.return_value = mock_report_schedule

        result = ReportSchedulerService.update_schedule(
            mock_db,
            org_id,
            mock_report_schedule.schedule_id,
            schedule_name="Updated Schedule Name",
            description="Updated description",
            email_recipients=["user@example.com"],
        )

        assert mock_report_schedule.schedule_name == "Updated Schedule Name"
        assert mock_report_schedule.description == "Updated description"
        assert mock_report_schedule.email_recipients == ["user@example.com"]
        mock_db.commit.assert_called_once()

    def test_update_schedule_not_found(self, mock_db, org_id):
        """Test update of non-existent schedule."""
        from app.services.ifrs.rpt.report_scheduler import ReportSchedulerService

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            ReportSchedulerService.update_schedule(
                mock_db,
                org_id,
                uuid.uuid4(),
                schedule_name="Updated Name",
            )

        assert exc.value.status_code == 404


class TestReportSchedulerServiceUpdateTiming:
    """Tests for update_timing method."""

    def test_update_timing_success(self, mock_db, org_id, mock_report_schedule):
        """Test successful timing update."""
        from app.services.ifrs.rpt.report_scheduler import ReportSchedulerService
        from app.models.ifrs.rpt.report_schedule import ScheduleFrequency

        mock_report_schedule.organization_id = org_id
        mock_db.get.return_value = mock_report_schedule

        result = ReportSchedulerService.update_timing(
            mock_db,
            org_id,
            mock_report_schedule.schedule_id,
            frequency=ScheduleFrequency.WEEKLY,
            day_of_week=1,
            time_of_day="09:00",
        )

        assert mock_report_schedule.frequency == ScheduleFrequency.WEEKLY
        assert mock_report_schedule.day_of_week == 1
        assert mock_report_schedule.time_of_day == "09:00"
        mock_db.commit.assert_called_once()

    def test_update_timing_not_found(self, mock_db, org_id):
        """Test timing update of non-existent schedule."""
        from app.services.ifrs.rpt.report_scheduler import ReportSchedulerService
        from app.models.ifrs.rpt.report_schedule import ScheduleFrequency

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            ReportSchedulerService.update_timing(
                mock_db,
                org_id,
                uuid.uuid4(),
                frequency=ScheduleFrequency.WEEKLY,
            )

        assert exc.value.status_code == 404


class TestReportSchedulerServiceActivation:
    """Tests for activate and deactivate methods."""

    def test_activate_schedule_success(self, mock_db, org_id, mock_report_schedule):
        """Test successful schedule activation."""
        from app.services.ifrs.rpt.report_scheduler import ReportSchedulerService
        from app.models.ifrs.rpt.report_schedule import ScheduleFrequency

        mock_report_schedule.organization_id = org_id
        mock_report_schedule.is_active = False
        mock_report_schedule.frequency = ScheduleFrequency.DAILY
        mock_report_schedule.time_of_day = "08:00"
        mock_db.get.return_value = mock_report_schedule

        result = ReportSchedulerService.activate(
            mock_db, org_id, mock_report_schedule.schedule_id
        )

        assert mock_report_schedule.is_active is True
        assert mock_report_schedule.next_run_at is not None
        mock_db.commit.assert_called_once()

    def test_activate_schedule_not_found(self, mock_db, org_id):
        """Test activation of non-existent schedule."""
        from app.services.ifrs.rpt.report_scheduler import ReportSchedulerService

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            ReportSchedulerService.activate(
                mock_db, org_id, uuid.uuid4()
            )

        assert exc.value.status_code == 404

    def test_deactivate_schedule_success(self, mock_db, org_id, mock_report_schedule):
        """Test successful schedule deactivation."""
        from app.services.ifrs.rpt.report_scheduler import ReportSchedulerService

        mock_report_schedule.organization_id = org_id
        mock_report_schedule.is_active = True
        mock_db.get.return_value = mock_report_schedule

        result = ReportSchedulerService.deactivate(
            mock_db, org_id, mock_report_schedule.schedule_id
        )

        assert mock_report_schedule.is_active is False
        assert mock_report_schedule.next_run_at is None
        mock_db.commit.assert_called_once()

    def test_deactivate_schedule_not_found(self, mock_db, org_id):
        """Test deactivation of non-existent schedule."""
        from app.services.ifrs.rpt.report_scheduler import ReportSchedulerService

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            ReportSchedulerService.deactivate(
                mock_db, org_id, uuid.uuid4()
            )

        assert exc.value.status_code == 404


class TestReportSchedulerServiceExecution:
    """Tests for record_execution method."""

    def test_record_execution_success(self, mock_db, mock_report_schedule):
        """Test successful execution recording."""
        from app.services.ifrs.rpt.report_scheduler import ReportSchedulerService
        from app.models.ifrs.rpt.report_schedule import ScheduleFrequency

        mock_report_schedule.is_active = True
        mock_report_schedule.frequency = ScheduleFrequency.DAILY
        mock_report_schedule.time_of_day = "08:00"
        mock_db.get.return_value = mock_report_schedule

        result = ReportSchedulerService.record_execution(
            mock_db, mock_report_schedule.schedule_id
        )

        assert mock_report_schedule.last_run_at is not None
        assert mock_report_schedule.next_run_at is not None
        mock_db.commit.assert_called_once()

    def test_record_execution_not_found(self, mock_db):
        """Test execution recording for non-existent schedule."""
        from app.services.ifrs.rpt.report_scheduler import ReportSchedulerService

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            ReportSchedulerService.record_execution(mock_db, uuid.uuid4())

        assert exc.value.status_code == 404


class TestReportSchedulerServiceQueries:
    """Tests for query methods."""

    def test_get_due_schedules(self, mock_db, mock_report_schedule):
        """Test getting due schedules."""
        from app.services.ifrs.rpt.report_scheduler import ReportSchedulerService

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.all.return_value = [mock_report_schedule]
        mock_db.query.return_value = mock_query

        result = ReportSchedulerService.get_due_schedules(mock_db)

        assert len(result) == 1

    def test_get_due_schedules_with_org_filter(self, mock_db, org_id, mock_report_schedule):
        """Test getting due schedules with organization filter."""
        from app.services.ifrs.rpt.report_scheduler import ReportSchedulerService

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.all.return_value = [mock_report_schedule]
        mock_db.query.return_value = mock_query

        result = ReportSchedulerService.get_due_schedules(
            mock_db, organization_id=str(org_id)
        )

        assert len(result) == 1

    def test_get_upcoming_schedules(self, mock_db, org_id, mock_report_schedule, mock_report_definition):
        """Test getting upcoming schedules."""
        from app.services.ifrs.rpt.report_scheduler import ReportSchedulerService

        mock_report_schedule.report_def_id = mock_report_definition.report_def_id

        mock_query = MagicMock()
        mock_query.join.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.all.return_value = [mock_report_schedule]
        mock_db.query.return_value = mock_query
        mock_db.get.return_value = mock_report_definition

        result = ReportSchedulerService.get_upcoming_schedules(
            mock_db, str(org_id), hours_ahead=24
        )

        assert len(result) == 1
        assert result[0].schedule_name == mock_report_schedule.schedule_name

    def test_get_schedule_by_id(self, mock_db, mock_report_schedule):
        """Test getting schedule by ID."""
        from app.services.ifrs.rpt.report_scheduler import ReportSchedulerService

        mock_db.get.return_value = mock_report_schedule

        result = ReportSchedulerService.get(
            mock_db, str(mock_report_schedule.schedule_id)
        )

        assert result is not None

    def test_get_schedule_not_found(self, mock_db):
        """Test getting non-existent schedule."""
        from app.services.ifrs.rpt.report_scheduler import ReportSchedulerService

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            ReportSchedulerService.get(mock_db, str(uuid.uuid4()))

        assert exc.value.status_code == 404

    def test_list_schedules(self, mock_db, org_id, mock_report_schedule):
        """Test listing schedules."""
        from app.services.ifrs.rpt.report_scheduler import ReportSchedulerService

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.all.return_value = [mock_report_schedule]
        mock_db.query.return_value = mock_query

        result = ReportSchedulerService.list(
            mock_db,
            organization_id=str(org_id),
        )

        assert len(result) == 1

    def test_list_schedules_with_filters(self, mock_db, org_id, mock_report_schedule):
        """Test listing schedules with filters."""
        from app.services.ifrs.rpt.report_scheduler import ReportSchedulerService
        from app.models.ifrs.rpt.report_schedule import ScheduleFrequency

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.all.return_value = [mock_report_schedule]
        mock_db.query.return_value = mock_query

        result = ReportSchedulerService.list(
            mock_db,
            organization_id=str(org_id),
            frequency=ScheduleFrequency.MONTHLY,
            is_active=True,
        )

        assert len(result) == 1


class TestReportSchedulerServiceNextRunCalculation:
    """Tests for _calculate_next_run method."""

    def test_calculate_next_run_daily(self):
        """Test next run calculation for daily frequency."""
        from app.services.ifrs.rpt.report_scheduler import ReportSchedulerService
        from app.models.ifrs.rpt.report_schedule import ScheduleFrequency

        result = ReportSchedulerService._calculate_next_run(
            frequency=ScheduleFrequency.DAILY,
            cron_expression=None,
            day_of_week=None,
            day_of_month=None,
            time_of_day="08:00",
            tz="UTC",
        )

        assert result is not None
        assert result.hour == 8
        assert result.minute == 0

    def test_calculate_next_run_weekly(self):
        """Test next run calculation for weekly frequency."""
        from app.services.ifrs.rpt.report_scheduler import ReportSchedulerService
        from app.models.ifrs.rpt.report_schedule import ScheduleFrequency

        result = ReportSchedulerService._calculate_next_run(
            frequency=ScheduleFrequency.WEEKLY,
            cron_expression=None,
            day_of_week=1,  # Tuesday
            day_of_month=None,
            time_of_day="09:00",
            tz="UTC",
        )

        assert result is not None
        assert result.hour == 9
        assert result.weekday() == 1  # Tuesday

    def test_calculate_next_run_monthly(self):
        """Test next run calculation for monthly frequency."""
        from app.services.ifrs.rpt.report_scheduler import ReportSchedulerService
        from app.models.ifrs.rpt.report_schedule import ScheduleFrequency

        result = ReportSchedulerService._calculate_next_run(
            frequency=ScheduleFrequency.MONTHLY,
            cron_expression=None,
            day_of_week=None,
            day_of_month=15,
            time_of_day="10:00",
            tz="UTC",
        )

        assert result is not None
        assert result.hour == 10
        assert result.day == 15

    def test_calculate_next_run_on_demand(self):
        """Test next run calculation for on-demand frequency."""
        from app.services.ifrs.rpt.report_scheduler import ReportSchedulerService
        from app.models.ifrs.rpt.report_schedule import ScheduleFrequency

        result = ReportSchedulerService._calculate_next_run(
            frequency=ScheduleFrequency.ON_DEMAND,
            cron_expression=None,
            day_of_week=None,
            day_of_month=None,
            time_of_day=None,
            tz="UTC",
        )

        assert result is None

    def test_calculate_next_run_quarterly(self):
        """Test next run calculation for quarterly frequency."""
        from app.services.ifrs.rpt.report_scheduler import ReportSchedulerService
        from app.models.ifrs.rpt.report_schedule import ScheduleFrequency

        result = ReportSchedulerService._calculate_next_run(
            frequency=ScheduleFrequency.QUARTERLY,
            cron_expression=None,
            day_of_week=None,
            day_of_month=1,
            time_of_day="08:00",
            tz="UTC",
        )

        assert result is not None
        assert result.month in [1, 4, 7, 10]

    def test_calculate_next_run_annually(self):
        """Test next run calculation for annually frequency."""
        from app.services.ifrs.rpt.report_scheduler import ReportSchedulerService
        from app.models.ifrs.rpt.report_schedule import ScheduleFrequency

        now = datetime.now(timezone.utc)

        result = ReportSchedulerService._calculate_next_run(
            frequency=ScheduleFrequency.ANNUALLY,
            cron_expression=None,
            day_of_week=None,
            day_of_month=1,
            time_of_day="08:00",
            tz="UTC",
        )

        assert result is not None
        assert result.year == now.year + 1
        assert result.month == 1

    def test_calculate_next_run_period_end(self):
        """Test next run calculation for period-end frequency."""
        from app.services.ifrs.rpt.report_scheduler import ReportSchedulerService
        from app.models.ifrs.rpt.report_schedule import ScheduleFrequency

        result = ReportSchedulerService._calculate_next_run(
            frequency=ScheduleFrequency.PERIOD_END,
            cron_expression=None,
            day_of_week=None,
            day_of_month=None,
            time_of_day=None,
            tz="UTC",
        )

        assert result is None  # Requires external trigger
