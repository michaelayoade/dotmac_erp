"""
Tests for Leave Without Pay (LWP) integration with payroll.

Tests that approved LWP leave applications are automatically
deducted from salary slips during payroll processing.
"""

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.models.people.leave.leave_application import (
    LeaveApplication,
    LeaveApplicationStatus,
)
from app.models.people.leave.leave_type import LeaveType

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_db():
    """Create mock database session."""
    return MagicMock()


@pytest.fixture
def org_id():
    """Test organization ID."""
    return uuid.uuid4()


@pytest.fixture
def employee_id():
    """Test employee ID."""
    return uuid.uuid4()


@pytest.fixture
def lwp_leave_type(org_id):
    """Create a Leave Without Pay leave type."""
    leave_type = MagicMock(spec=LeaveType)
    leave_type.leave_type_id = uuid.uuid4()
    leave_type.organization_id = org_id
    leave_type.name = "Leave Without Pay"
    leave_type.is_lwp = True
    return leave_type


@pytest.fixture
def paid_leave_type(org_id):
    """Create a paid leave type (not LWP)."""
    leave_type = MagicMock(spec=LeaveType)
    leave_type.leave_type_id = uuid.uuid4()
    leave_type.organization_id = org_id
    leave_type.name = "Annual Leave"
    leave_type.is_lwp = False
    return leave_type


def create_leave_application(
    employee_id: uuid.UUID,
    leave_type: LeaveType,
    from_date: date,
    to_date: date,
    status: LeaveApplicationStatus = LeaveApplicationStatus.APPROVED,
    is_posted_to_payroll: bool = False,
    half_day: bool = False,
    half_day_date: date | None = None,
) -> LeaveApplication:
    """Create a mock leave application."""
    leave = MagicMock(spec=LeaveApplication)
    leave.application_id = uuid.uuid4()
    leave.employee_id = employee_id
    leave.leave_type_id = leave_type.leave_type_id
    leave.leave_type = leave_type
    leave.from_date = from_date
    leave.to_date = to_date
    leave.status = status
    leave.is_posted_to_payroll = is_posted_to_payroll
    leave.salary_slip_id = None
    leave.half_day = half_day
    leave.half_day_date = half_day_date
    return leave


# ---------------------------------------------------------------------------
# LeaveService LWP Integration Tests
# ---------------------------------------------------------------------------


class TestCalculateLwpDaysInPeriod:
    """Tests for LeaveService.calculate_lwp_days_in_period()."""

    def test_full_overlap_single_leave(self, mock_db, employee_id, lwp_leave_type):
        """Test LWP leave fully within pay period."""
        from app.services.people.leave.leave_service import LeaveService

        service = LeaveService(mock_db)

        # Leave: Jan 10-15 (6 days) within pay period Jan 1-31
        leave = create_leave_application(
            employee_id,
            lwp_leave_type,
            from_date=date(2025, 1, 10),
            to_date=date(2025, 1, 15),
        )

        result = service.calculate_lwp_days_in_period(
            [leave],
            period_start=date(2025, 1, 1),
            period_end=date(2025, 1, 31),
        )

        assert result == Decimal("6")

    def test_partial_overlap_leave_starts_before_period(
        self, mock_db, employee_id, lwp_leave_type
    ):
        """Test LWP leave starting before pay period."""
        from app.services.people.leave.leave_service import LeaveService

        service = LeaveService(mock_db)

        # Leave: Dec 28 - Jan 5 (3 days in January)
        leave = create_leave_application(
            employee_id,
            lwp_leave_type,
            from_date=date(2024, 12, 28),
            to_date=date(2025, 1, 5),
        )

        result = service.calculate_lwp_days_in_period(
            [leave],
            period_start=date(2025, 1, 1),
            period_end=date(2025, 1, 31),
        )

        # Jan 1-5 = 5 days
        assert result == Decimal("5")

    def test_partial_overlap_leave_ends_after_period(
        self, mock_db, employee_id, lwp_leave_type
    ):
        """Test LWP leave ending after pay period."""
        from app.services.people.leave.leave_service import LeaveService

        service = LeaveService(mock_db)

        # Leave: Jan 28 - Feb 5 (4 days in January)
        leave = create_leave_application(
            employee_id,
            lwp_leave_type,
            from_date=date(2025, 1, 28),
            to_date=date(2025, 2, 5),
        )

        result = service.calculate_lwp_days_in_period(
            [leave],
            period_start=date(2025, 1, 1),
            period_end=date(2025, 1, 31),
        )

        # Jan 28-31 = 4 days
        assert result == Decimal("4")

    def test_half_day_leave(self, mock_db, employee_id, lwp_leave_type):
        """Test half-day LWP leave."""
        from app.services.people.leave.leave_service import LeaveService

        service = LeaveService(mock_db)

        leave = create_leave_application(
            employee_id,
            lwp_leave_type,
            from_date=date(2025, 1, 15),
            to_date=date(2025, 1, 15),
            half_day=True,
            half_day_date=date(2025, 1, 15),
        )

        result = service.calculate_lwp_days_in_period(
            [leave],
            period_start=date(2025, 1, 1),
            period_end=date(2025, 1, 31),
        )

        assert result == Decimal("0.5")

    def test_multiple_leaves(self, mock_db, employee_id, lwp_leave_type):
        """Test multiple LWP leaves in same period."""
        from app.services.people.leave.leave_service import LeaveService

        service = LeaveService(mock_db)

        leave1 = create_leave_application(
            employee_id,
            lwp_leave_type,
            from_date=date(2025, 1, 5),
            to_date=date(2025, 1, 7),
        )
        leave2 = create_leave_application(
            employee_id,
            lwp_leave_type,
            from_date=date(2025, 1, 20),
            to_date=date(2025, 1, 22),
        )

        result = service.calculate_lwp_days_in_period(
            [leave1, leave2],
            period_start=date(2025, 1, 1),
            period_end=date(2025, 1, 31),
        )

        # 3 days + 3 days = 6 days
        assert result == Decimal("6")

    def test_no_overlap_leave_before_period(self, mock_db, employee_id, lwp_leave_type):
        """Test LWP leave entirely before pay period."""
        from app.services.people.leave.leave_service import LeaveService

        service = LeaveService(mock_db)

        leave = create_leave_application(
            employee_id,
            lwp_leave_type,
            from_date=date(2024, 12, 10),
            to_date=date(2024, 12, 15),
        )

        result = service.calculate_lwp_days_in_period(
            [leave],
            period_start=date(2025, 1, 1),
            period_end=date(2025, 1, 31),
        )

        assert result == Decimal("0")

    def test_empty_leave_list(self, mock_db):
        """Test with no leave applications."""
        from app.services.people.leave.leave_service import LeaveService

        service = LeaveService(mock_db)

        result = service.calculate_lwp_days_in_period(
            [],
            period_start=date(2025, 1, 1),
            period_end=date(2025, 1, 31),
        )

        assert result == Decimal("0")


class TestMarkLeavePostedToPayroll:
    """Tests for LeaveService.mark_leave_posted_to_payroll()."""

    def test_marks_single_leave(self, mock_db, employee_id, lwp_leave_type):
        """Test marking a single leave as posted."""
        from app.services.people.leave.leave_service import LeaveService

        service = LeaveService(mock_db)
        slip_id = uuid.uuid4()

        leave = create_leave_application(
            employee_id,
            lwp_leave_type,
            from_date=date(2025, 1, 10),
            to_date=date(2025, 1, 15),
            is_posted_to_payroll=False,
        )

        service.mark_leave_posted_to_payroll([leave], slip_id)

        assert leave.is_posted_to_payroll is True
        assert leave.salary_slip_id == slip_id
        mock_db.flush.assert_called_once()

    def test_marks_multiple_leaves(self, mock_db, employee_id, lwp_leave_type):
        """Test marking multiple leaves as posted."""
        from app.services.people.leave.leave_service import LeaveService

        service = LeaveService(mock_db)
        slip_id = uuid.uuid4()

        leave1 = create_leave_application(
            employee_id,
            lwp_leave_type,
            from_date=date(2025, 1, 5),
            to_date=date(2025, 1, 7),
        )
        leave2 = create_leave_application(
            employee_id,
            lwp_leave_type,
            from_date=date(2025, 1, 20),
            to_date=date(2025, 1, 22),
        )

        service.mark_leave_posted_to_payroll([leave1, leave2], slip_id)

        assert leave1.is_posted_to_payroll is True
        assert leave1.salary_slip_id == slip_id
        assert leave2.is_posted_to_payroll is True
        assert leave2.salary_slip_id == slip_id


class TestLwpDeductionCalculation:
    """Tests for LWP deduction amount calculation logic."""

    def test_lwp_deduction_amount(self):
        """Test correct deduction calculation based on daily rate."""
        # Monthly salary: 300,000
        # Working days: 22
        # Daily rate: 300,000 / 22 = 13,636.36
        # LWP days: 3
        # Expected deduction: 13,636.36 * 3 = 40,909.09

        gross_pay = Decimal("300000")
        total_working_days = Decimal("22")
        lwp_days = Decimal("3")

        daily_rate = gross_pay / total_working_days
        lwp_deduction = (daily_rate * lwp_days).quantize(Decimal("0.01"))

        assert lwp_deduction == Decimal("40909.09")

    def test_lwp_deduction_half_day(self):
        """Test deduction for half-day LWP leave."""
        gross_pay = Decimal("300000")
        total_working_days = Decimal("22")
        lwp_days = Decimal("0.5")

        daily_rate = gross_pay / total_working_days
        lwp_deduction = (daily_rate * lwp_days).quantize(Decimal("0.01"))

        assert lwp_deduction == Decimal("6818.18")

    def test_lwp_deduction_capped_at_available(self):
        """Test deduction capped when exceeds available amount."""
        gross_pay = Decimal("100000")
        total_deduction = Decimal("90000")  # Already deducted
        lwp_deduction = Decimal("20000")

        max_deductible = gross_pay - total_deduction
        actual_deduction = min(lwp_deduction, max(max_deductible, Decimal("0")))

        # Can only deduct 10,000 (100,000 - 90,000)
        assert actual_deduction == Decimal("10000")
