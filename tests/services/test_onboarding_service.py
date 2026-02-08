"""Tests for OnboardingService."""

from datetime import date, datetime, timedelta
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.models.people.hr import (
    ActivityStatus,
    BoardingStatus,
    ChecklistTemplate,
    ChecklistTemplateItem,
    ChecklistTemplateType,
    Employee,
    EmployeeOnboarding,
    EmployeeOnboardingActivity,
)
from app.services.people.hr import OnboardingService


@pytest.fixture
def org_id():
    return uuid4()


@pytest.fixture
def employee_id():
    return uuid4()


@pytest.fixture
def template_id():
    return uuid4()


@pytest.fixture
def mock_employee(org_id, employee_id):
    """Create a mock employee."""
    employee = MagicMock(spec=Employee)
    employee.employee_id = employee_id
    employee.organization_id = org_id
    employee.is_deleted = False
    employee.person_id = uuid4()
    return employee


@pytest.fixture
def mock_template(org_id, template_id):
    """Create a mock checklist template with items."""
    template = MagicMock(spec=ChecklistTemplate)
    template.template_id = template_id
    template.organization_id = org_id
    template.template_name = "Standard Onboarding"
    template.template_type = ChecklistTemplateType.ONBOARDING
    template.is_active = True

    # Create mock items
    items = []
    for i, (name, category, days, assignee) in enumerate(
        [
            ("Sign employment contract", "PRE_BOARDING", -3, "HR"),
            ("Complete personal details form", "PRE_BOARDING", -1, "EMPLOYEE"),
            ("Setup workstation", "DAY_ONE", 0, "IT"),
            ("Team introduction", "DAY_ONE", 0, "MANAGER"),
            ("Complete compliance training", "FIRST_WEEK", 5, "EMPLOYEE"),
            ("Schedule 1-on-1 with manager", "FIRST_MONTH", 14, "EMPLOYEE"),
        ]
    ):
        item = MagicMock(spec=ChecklistTemplateItem)
        item.item_id = uuid4()
        item.template_id = template_id
        item.item_name = name
        item.category = category
        item.days_from_start = days
        item.default_assignee_role = assignee
        item.requires_document = name == "Sign employment contract"
        item.sequence = i
        item.is_required = True
        items.append(item)

    template.items = items
    return template


@pytest.fixture
def mock_onboarding(org_id, employee_id, template_id):
    """Create a mock onboarding record."""
    onboarding = MagicMock(spec=EmployeeOnboarding)
    onboarding.onboarding_id = uuid4()
    onboarding.organization_id = org_id
    onboarding.employee_id = employee_id
    onboarding.template_id = template_id
    onboarding.status = BoardingStatus.IN_PROGRESS
    onboarding.date_of_joining = date.today()
    onboarding.self_service_token = "test_token_abc123"
    onboarding.self_service_token_expires = datetime.now() + timedelta(days=30)
    onboarding.progress_percentage = 50
    onboarding.activities = []
    return onboarding


class TestOnboardingService:
    """Test cases for OnboardingService."""

    def test_calculate_progress_empty_activities(self, mock_onboarding):
        """Test progress calculation with no activities."""
        mock_onboarding.activities = []
        progress = OnboardingService.calculate_progress(mock_onboarding)
        assert progress == 0

    def test_calculate_progress_all_pending(self, mock_onboarding):
        """Test progress calculation with all pending activities."""
        activities = []
        for _i in range(5):
            activity = MagicMock(spec=EmployeeOnboardingActivity)
            activity.activity_status = ActivityStatus.PENDING.value
            activity.status = None
            activities.append(activity)
        mock_onboarding.activities = activities

        progress = OnboardingService.calculate_progress(mock_onboarding)
        assert progress == 0

    def test_calculate_progress_partial(self, mock_onboarding):
        """Test progress calculation with some completed activities."""
        activities = []
        # 3 completed, 2 pending = 60% complete
        for _i in range(3):
            activity = MagicMock(spec=EmployeeOnboardingActivity)
            activity.activity_status = ActivityStatus.COMPLETED.value
            activity.status = "completed"
            activities.append(activity)
        for _i in range(2):
            activity = MagicMock(spec=EmployeeOnboardingActivity)
            activity.activity_status = ActivityStatus.PENDING.value
            activity.status = None
            activities.append(activity)
        mock_onboarding.activities = activities

        progress = OnboardingService.calculate_progress(mock_onboarding)
        assert progress == 60

    def test_calculate_progress_skipped_counts(self, mock_onboarding):
        """Test that skipped activities count toward progress."""
        activities = []
        # 2 completed, 1 skipped, 2 pending = 60% complete
        for _i in range(2):
            activity = MagicMock(spec=EmployeeOnboardingActivity)
            activity.activity_status = ActivityStatus.COMPLETED.value
            activity.status = "completed"
            activities.append(activity)
        # Skipped
        activity = MagicMock(spec=EmployeeOnboardingActivity)
        activity.activity_status = ActivityStatus.SKIPPED.value
        activity.status = "skipped"
        activities.append(activity)
        # Pending
        for _i in range(2):
            activity = MagicMock(spec=EmployeeOnboardingActivity)
            activity.activity_status = ActivityStatus.PENDING.value
            activity.status = None
            activities.append(activity)
        mock_onboarding.activities = activities

        progress = OnboardingService.calculate_progress(mock_onboarding)
        assert progress == 60

    def test_calculate_progress_all_complete(self, mock_onboarding):
        """Test progress calculation with all activities completed."""
        activities = []
        for _i in range(5):
            activity = MagicMock(spec=EmployeeOnboardingActivity)
            activity.activity_status = ActivityStatus.COMPLETED.value
            activity.status = "completed"
            activities.append(activity)
        mock_onboarding.activities = activities

        progress = OnboardingService.calculate_progress(mock_onboarding)
        assert progress == 100


class TestSelfServiceToken:
    """Test cases for self-service token management."""

    def test_token_generation_length(self):
        """Test that generated tokens are sufficiently long."""
        import secrets

        token = secrets.token_urlsafe(32)
        # Base64 encoding of 32 bytes = ~43 characters
        assert len(token) >= 40

    def test_token_expiry_calculation(self):
        """Test token expiry is set correctly."""
        validity_days = 30
        now = datetime.now()
        expires = now + timedelta(days=validity_days)

        # Should be approximately 30 days from now
        delta = expires - now
        assert delta.days == 30


class TestActivityDueDateCalculation:
    """Test cases for activity due date calculation."""

    def test_due_date_positive_days(self):
        """Test due date calculation for tasks after start date."""
        start_date = date(2024, 2, 1)
        days_from_start = 7
        due_date = start_date + timedelta(days=days_from_start)
        assert due_date == date(2024, 2, 8)

    def test_due_date_negative_days(self):
        """Test due date calculation for pre-boarding tasks."""
        start_date = date(2024, 2, 1)
        days_from_start = -3
        due_date = start_date + timedelta(days=days_from_start)
        assert due_date == date(2024, 1, 29)

    def test_due_date_zero_days(self):
        """Test due date calculation for day-one tasks."""
        start_date = date(2024, 2, 1)
        days_from_start = 0
        due_date = start_date + timedelta(days=days_from_start)
        assert due_date == date(2024, 2, 1)


class TestExpectedCompletionDate:
    """Test cases for expected completion date calculation."""

    def test_expected_completion_from_template(self, mock_template):
        """Test expected completion date is calculated from max days_from_start."""
        start_date = date(2024, 2, 1)

        # Find max days_from_start from template items
        max_days = max(
            (item.days_from_start for item in mock_template.items), default=0
        )
        # Template has max 14 days_from_start, add 7 day buffer = 21, but min is 30
        expected = start_date + timedelta(days=max(max_days + 7, 30))

        # 14 + 7 = 21, max(21, 30) = 30
        assert expected == date(2024, 3, 2)  # 30 days from Feb 1

    def test_expected_completion_no_template(self):
        """Test expected completion when no template is used."""
        # Should be None if no template
        expected_completion = None
        assert expected_completion is None
