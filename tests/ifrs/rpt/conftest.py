"""
Fixtures for Reporting (RPT) Module Tests.
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

# ============ Mock Enums ============


class MockReportType:
    """Mock ReportType enum."""

    BALANCE_SHEET = "BALANCE_SHEET"
    INCOME_STATEMENT = "INCOME_STATEMENT"
    CASH_FLOW = "CASH_FLOW"
    TRIAL_BALANCE = "TRIAL_BALANCE"


class MockReportStatus:
    """Mock ReportStatus enum."""

    QUEUED = "QUEUED"
    GENERATING = "GENERATING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class MockScheduleFrequency:
    """Mock ScheduleFrequency enum."""

    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"
    QUARTERLY = "QUARTERLY"
    ANNUALLY = "ANNUALLY"
    ON_DEMAND = "ON_DEMAND"
    PERIOD_END = "PERIOD_END"


# ============ Mock Models ============


class MockReportDefinition:
    """Mock ReportDefinition model."""

    def __init__(
        self,
        report_def_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        report_code: str = "RPT-001",
        report_name: str = "Test Report",
        description: str | None = None,
        report_type: str = "BALANCE_SHEET",
        category: str | None = "Financial",
        subcategory: str | None = "Balance Sheet",
        default_format: str = "PDF",
        supported_formats: list = None,
        report_structure: dict | None = None,
        column_definitions: dict | None = None,
        row_definitions: dict | None = None,
        filter_definitions: dict | None = None,
        data_source_type: str = "SQL",
        data_source_config: dict | None = None,
        template_file_path: str | None = None,
        template_version: int = 1,
        required_permissions: list | None = None,
        is_system_report: bool = False,
        is_active: bool = True,
        created_by_user_id: uuid.UUID = None,
        created_at: datetime = None,
        updated_at: datetime | None = None,
        **kwargs,
    ):
        self.report_def_id = report_def_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.report_code = report_code
        self.report_name = report_name
        self.description = description
        self.report_type = report_type
        self.category = category
        self.subcategory = subcategory
        self.default_format = default_format
        self.supported_formats = supported_formats or ["PDF", "XLSX", "CSV"]
        self.report_structure = report_structure
        self.column_definitions = column_definitions
        self.row_definitions = row_definitions
        self.filter_definitions = filter_definitions
        self.data_source_type = data_source_type
        self.data_source_config = data_source_config
        self.template_file_path = template_file_path
        self.template_version = template_version
        self.required_permissions = required_permissions
        self.is_system_report = is_system_report
        self.is_active = is_active
        self.created_by_user_id = created_by_user_id or uuid.uuid4()
        self.created_at = created_at or datetime.now(UTC)
        self.updated_at = updated_at
        for k, v in kwargs.items():
            setattr(self, k, v)


class MockReportInstance:
    """Mock ReportInstance model."""

    def __init__(
        self,
        instance_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        report_def_id: uuid.UUID = None,
        schedule_id: uuid.UUID = None,
        fiscal_period_id: uuid.UUID = None,
        instance_name: str = "Report Instance",
        parameters_used: dict | None = None,
        output_format: str = "PDF",
        output_file_path: str | None = None,
        output_size_bytes: int | None = None,
        status: str = "QUEUED",
        error_message: str | None = None,
        queued_at: datetime | None = None,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        generation_time_ms: int | None = None,
        generated_at: datetime | None = None,
        generated_by_user_id: uuid.UUID = None,
        **kwargs,
    ):
        self.instance_id = instance_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.report_def_id = report_def_id or uuid.uuid4()
        self.schedule_id = schedule_id
        self.fiscal_period_id = fiscal_period_id
        self.instance_name = instance_name
        self.parameters_used = parameters_used or {}
        self.output_format = output_format
        self.output_file_path = output_file_path
        self.output_size_bytes = output_size_bytes
        self.status = status
        self.error_message = error_message
        self.queued_at = queued_at or datetime.now(UTC)
        self.started_at = started_at
        self.completed_at = completed_at
        self.generation_time_ms = generation_time_ms
        self.generated_at = generated_at or datetime.now(UTC)
        self.generated_by_user_id = generated_by_user_id or uuid.uuid4()
        for k, v in kwargs.items():
            setattr(self, k, v)


class MockReportSchedule:
    """Mock ReportSchedule model."""

    def __init__(
        self,
        schedule_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        report_def_id: uuid.UUID = None,
        schedule_name: str = "Monthly Report",
        description: str | None = None,
        frequency: str = "MONTHLY",
        cron_expression: str | None = "0 8 1 * *",
        day_of_week: int | None = None,
        day_of_month: int | None = 1,
        time_of_day: str | None = "08:00",
        tz: str = "UTC",
        report_parameters: dict | None = None,
        output_format: str = "PDF",
        email_recipients: list | None = None,
        storage_path: str | None = None,
        retention_days: int | None = None,
        is_active: bool = True,
        next_run_at: datetime | None = None,
        last_run_at: datetime | None = None,
        created_by_user_id: uuid.UUID = None,
        created_at: datetime = None,
        **kwargs,
    ):
        self.schedule_id = schedule_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.report_def_id = report_def_id or uuid.uuid4()
        self.schedule_name = schedule_name
        self.description = description
        self.frequency = frequency
        self.cron_expression = cron_expression
        self.day_of_week = day_of_week
        self.day_of_month = day_of_month
        self.time_of_day = time_of_day
        self.timezone = tz
        self.report_parameters = report_parameters or {}
        self.output_format = output_format
        self.email_recipients = email_recipients or []
        self.storage_path = storage_path
        self.retention_days = retention_days
        self.is_active = is_active
        self.next_run_at = next_run_at
        self.last_run_at = last_run_at
        self.created_by_user_id = created_by_user_id or uuid.uuid4()
        self.created_at = created_at or datetime.now(UTC)
        for k, v in kwargs.items():
            setattr(self, k, v)


# ============ Fixtures ============


@pytest.fixture
def mock_db():
    """Create a mock database session."""
    db = MagicMock()
    db.scalars.return_value.first.return_value = None
    db.scalars.return_value.all.return_value = []
    return db


@pytest.fixture
def org_id() -> uuid.UUID:
    """Generate a test organization ID."""
    return uuid.uuid4()


@pytest.fixture
def user_id() -> uuid.UUID:
    """Generate a test user ID."""
    return uuid.uuid4()


@pytest.fixture
def mock_report_definition(org_id, user_id) -> MockReportDefinition:
    """Create a mock report definition."""
    return MockReportDefinition(
        organization_id=org_id,
        created_by_user_id=user_id,
    )


@pytest.fixture
def mock_system_report(org_id, user_id) -> MockReportDefinition:
    """Create a mock system report definition."""
    return MockReportDefinition(
        organization_id=org_id,
        created_by_user_id=user_id,
        is_system_report=True,
    )


@pytest.fixture
def mock_report_instance(org_id, mock_report_definition, user_id) -> MockReportInstance:
    """Create a mock report instance."""
    return MockReportInstance(
        organization_id=org_id,
        report_def_id=mock_report_definition.report_def_id,
        generated_by_user_id=user_id,
    )


@pytest.fixture
def mock_report_schedule(org_id, mock_report_definition, user_id) -> MockReportSchedule:
    """Create a mock report schedule."""
    return MockReportSchedule(
        organization_id=org_id,
        report_def_id=mock_report_definition.report_def_id,
        created_by_user_id=user_id,
    )
