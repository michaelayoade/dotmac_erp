"""
Tests for WorkingDaysCalculator - Pro-rated Salary Calculation.
"""

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.services.people.payroll.working_days_calculator import (
    WorkingDaysCalculator,
    ProrationMethod,
    ProrationReason,
    ProrationResult,
    calculate_proration,
)


class TestWorkingDaysCalculator:
    """Tests for WorkingDaysCalculator service."""

    @pytest.fixture
    def org_id(self):
        return uuid.uuid4()

    @pytest.fixture
    def mock_db(self):
        """Create a mock database session for unit tests."""
        db = MagicMock()
        # Mock scalars().unique().all() to return empty list (triggers default holidays)
        db.scalars.return_value.unique.return_value.all.return_value = []
        return db

    @pytest.fixture
    def calculator(self, mock_db):
        """Create a calculator with mocked org proration method."""
        calc = WorkingDaysCalculator(mock_db)
        # Mock the org lookup to avoid DB queries - default to CALENDAR_DAYS
        calc._get_org_proration_method = MagicMock(
            return_value=ProrationMethod.CALENDAR_DAYS
        )
        return calc

    def test_full_period_no_proration(self, calculator, org_id):
        """Employee who worked full period should not be prorated."""
        result = calculator.calculate_payment_days(
            organization_id=org_id,
            employee_joining_date=date(2025, 1, 1),  # Joined before period
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
            employee_leaving_date=None,  # Still employed
        )

        assert result.is_prorated is False
        assert result.proration_reason == ProrationReason.NONE
        assert result.total_working_days == Decimal("31")  # January has 31 days
        assert result.payment_days == Decimal("31")
        assert result.effective_start == date(2026, 1, 1)
        assert result.effective_end == date(2026, 1, 31)

    def test_mid_month_joiner_prorated(self, calculator, org_id):
        """Employee joining mid-month should have prorated salary."""
        result = calculator.calculate_payment_days(
            organization_id=org_id,
            employee_joining_date=date(2026, 1, 15),  # Joined mid-month
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
            employee_leaving_date=None,
        )

        assert result.is_prorated is True
        assert result.proration_reason == ProrationReason.JOINED_MID_PERIOD
        assert result.total_working_days == Decimal("31")
        assert result.payment_days == Decimal("17")  # Jan 15-31 = 17 days
        assert result.effective_start == date(2026, 1, 15)
        assert result.effective_end == date(2026, 1, 31)

    def test_mid_month_leaver_prorated(self, calculator, org_id):
        """Employee leaving mid-month should have prorated salary."""
        result = calculator.calculate_payment_days(
            organization_id=org_id,
            employee_joining_date=date(2025, 1, 1),  # Joined before
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
            employee_leaving_date=date(2026, 1, 20),  # Left mid-month
        )

        assert result.is_prorated is True
        assert result.proration_reason == ProrationReason.LEFT_MID_PERIOD
        assert result.total_working_days == Decimal("31")
        assert result.payment_days == Decimal("20")  # Jan 1-20 = 20 days
        assert result.effective_start == date(2026, 1, 1)
        assert result.effective_end == date(2026, 1, 20)

    def test_both_join_and_leave_mid_period(self, calculator, org_id):
        """Employee who joined AND left in same period."""
        result = calculator.calculate_payment_days(
            organization_id=org_id,
            employee_joining_date=date(2026, 1, 10),  # Joined mid-month
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
            employee_leaving_date=date(2026, 1, 25),  # Left before end
        )

        assert result.is_prorated is True
        assert result.proration_reason == ProrationReason.BOTH
        assert result.total_working_days == Decimal("31")
        assert result.payment_days == Decimal("16")  # Jan 10-25 = 16 days
        assert result.effective_start == date(2026, 1, 10)
        assert result.effective_end == date(2026, 1, 25)

    def test_calendar_days_method(self, calculator, org_id):
        """Test CALENDAR_DAYS proration method (simplest)."""
        result = calculator.calculate_payment_days(
            organization_id=org_id,
            employee_joining_date=date(2026, 2, 15),
            period_start=date(2026, 2, 1),
            period_end=date(2026, 2, 28),  # Feb 2026 has 28 days
            method=ProrationMethod.CALENDAR_DAYS,
        )

        assert result.method_used == ProrationMethod.CALENDAR_DAYS
        assert result.total_working_days == Decimal("28")
        assert result.payment_days == Decimal("14")  # Feb 15-28

    def test_fixed_30_day_method(self, calculator, org_id):
        """Test FIXED_30_DAY proration method."""
        # January has 31 days but we use 30
        result = calculator.calculate_payment_days(
            organization_id=org_id,
            employee_joining_date=date(2026, 1, 16),
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
            method=ProrationMethod.FIXED_30_DAY,
        )

        assert result.method_used == ProrationMethod.FIXED_30_DAY
        # With 30-day method, total is 30 and payment is proportionally adjusted
        # 16 days worked out of 31 calendar = (16/31)*30 ≈ 15.48
        assert result.payment_days >= Decimal("15")
        assert result.payment_days <= Decimal("16")

    def test_business_days_method_excludes_weekends(self, calculator, org_id):
        """Test BUSINESS_DAYS proration excludes weekends."""
        # First week of Jan 2026: Wed(1), Thu(2), Fri(3), Sat(4), Sun(5)
        result = calculator.calculate_payment_days(
            organization_id=org_id,
            employee_joining_date=date(2026, 1, 1),
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 7),  # Wed-Tue
            method=ProrationMethod.BUSINESS_DAYS,
            exclude_weekends=True,
            exclude_holidays=False,
        )

        assert result.method_used == ProrationMethod.BUSINESS_DAYS
        # Wed(1), Thu(2), Fri(3), Mon(6), Tue(7) = 5 business days
        assert result.total_working_days == Decimal("5")

    def test_business_days_excludes_holidays(self, calculator, org_id):
        """Test BUSINESS_DAYS proration excludes holidays."""
        # Jan 1 is New Year holiday
        result = calculator.calculate_payment_days(
            organization_id=org_id,
            employee_joining_date=date(2026, 1, 1),
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 3),  # Wed-Fri
            method=ProrationMethod.BUSINESS_DAYS,
            exclude_weekends=False,
            exclude_holidays=True,
        )

        # Should be 2 days (excluding Jan 1 holiday)
        assert result.total_working_days == Decimal("2")

    def test_proration_factor_calculated(self, calculator, org_id):
        """Test that proration factor is correctly calculated."""
        result = calculator.calculate_payment_days(
            organization_id=org_id,
            employee_joining_date=date(2026, 1, 16),
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
        )

        # 16/31 ≈ 0.5161
        expected_factor = Decimal("16") / Decimal("31")
        assert abs(result.proration_factor - expected_factor) < Decimal("0.001")

    def test_employee_not_active_in_period(self, calculator, org_id):
        """Employee who hasn't joined yet should have 0 payment days."""
        result = calculator.calculate_payment_days(
            organization_id=org_id,
            employee_joining_date=date(2026, 2, 1),  # Joins next month
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
        )

        assert result.payment_days == Decimal("0")
        assert result.is_prorated is True

    def test_employee_left_before_period(self, calculator, org_id):
        """Employee who left before period should have 0 payment days."""
        result = calculator.calculate_payment_days(
            organization_id=org_id,
            employee_joining_date=date(2025, 1, 1),
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
            employee_leaving_date=date(2025, 12, 15),  # Left last year
        )

        assert result.payment_days == Decimal("0")

    def test_get_business_days_in_month(self, mock_db):
        """Test helper method for business days in month."""
        calculator = WorkingDaysCalculator(mock_db)

        # January 2026 has 22 business days (assuming 9 weekend days)
        business_days = calculator.get_business_days_in_month(2026, 1)
        assert 20 <= business_days <= 23  # Reasonable range

    def test_convenience_function(self, mock_db, org_id):
        """Test module-level calculate_proration function."""
        with patch.object(
            WorkingDaysCalculator,
            "_get_org_proration_method",
            return_value=ProrationMethod.CALENDAR_DAYS,
        ):
            result = calculate_proration(
                db=mock_db,
                organization_id=org_id,
                employee_joining_date=date(2026, 1, 15),
                period_start=date(2026, 1, 1),
                period_end=date(2026, 1, 31),
            )

            assert isinstance(result, ProrationResult)
            assert result.is_prorated is True
            assert result.proration_reason == ProrationReason.JOINED_MID_PERIOD


class TestProrationResult:
    """Tests for ProrationResult dataclass."""

    def test_proration_factor_auto_calculated(self):
        """Test that proration_factor is auto-calculated in post_init."""
        result = ProrationResult(
            total_working_days=Decimal("30"),
            payment_days=Decimal("15"),
            is_prorated=True,
            proration_reason=ProrationReason.JOINED_MID_PERIOD,
            effective_start=date(2026, 1, 16),
            effective_end=date(2026, 1, 31),
            method_used=ProrationMethod.CALENDAR_DAYS,
            proration_factor=Decimal("0"),  # Will be recalculated
        )

        assert result.proration_factor == Decimal("0.5000")

    def test_zero_total_days_factor(self):
        """Test proration_factor is 0 when total_working_days is 0."""
        result = ProrationResult(
            total_working_days=Decimal("0"),
            payment_days=Decimal("0"),
            is_prorated=True,
            proration_reason=ProrationReason.JOINED_MID_PERIOD,
            effective_start=date(2026, 1, 16),
            effective_end=date(2026, 1, 15),  # Invalid range
            method_used=ProrationMethod.CALENDAR_DAYS,
            proration_factor=Decimal("0"),
        )

        assert result.proration_factor == Decimal("0")
