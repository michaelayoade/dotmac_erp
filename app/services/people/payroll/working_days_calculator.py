"""
Working Days Calculator - Pro-rated Salary Calculation.

Handles automatic calculation of working days and payment days for employees
who join or leave mid-period. Supports multiple proration methods:
- CALENDAR_DAYS: Simple division by calendar days in period
- BUSINESS_DAYS: Only count working days (excludes weekends/holidays)
- FIXED_30_DAY: Always use 30-day month for consistency

Uses the HolidayList model for organization-specific holidays and weekly off days.
Falls back to hardcoded Nigerian holidays if no holiday list is configured.
"""

from __future__ import annotations

import enum
import logging
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.people.leave.holiday_list import HolidayList

logger = logging.getLogger(__name__)


class ProrationMethod(str, enum.Enum):
    """Method for calculating pro-rated salary."""

    CALENDAR_DAYS = "CALENDAR_DAYS"  # Simple division by calendar days
    BUSINESS_DAYS = "BUSINESS_DAYS"  # Exclude weekends and holidays
    FIXED_30_DAY = "FIXED_30_DAY"  # Always use 30-day month


class ProrationReason(str, enum.Enum):
    """Reason for pro-rating an employee's salary."""

    NONE = "NONE"  # Full period worked
    JOINED_MID_PERIOD = "JOINED_MID_PERIOD"  # Employee started mid-period
    LEFT_MID_PERIOD = "LEFT_MID_PERIOD"  # Employee left mid-period
    BOTH = "BOTH"  # Employee joined AND left within the period


@dataclass
class ProrationResult:
    """
    Result of pro-ration calculation.

    Attributes:
        total_working_days: Total days in the period (for full salary)
        payment_days: Actual days to be paid (may be less than total)
        is_prorated: Whether the salary should be pro-rated
        proration_reason: Why salary is prorated (or NONE)
        effective_start: When employee actually started in period
        effective_end: When employee actually ended in period
        method_used: Which proration method was applied
        proration_factor: Ratio of payment_days to total_working_days
    """

    total_working_days: Decimal
    payment_days: Decimal
    is_prorated: bool
    proration_reason: ProrationReason
    effective_start: date
    effective_end: date
    method_used: ProrationMethod
    proration_factor: Decimal

    def __post_init__(self):
        """Ensure proration_factor is calculated correctly."""
        if self.total_working_days > 0:
            self.proration_factor = (
                self.payment_days / self.total_working_days
            ).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        else:
            self.proration_factor = Decimal("0")


@dataclass
class HolidayCalendar:
    """
    Holiday calendar data loaded from database or defaults.

    Attributes:
        holidays: Set of holiday dates
        weekly_off_days: Set of weekday indices (0=Monday, 6=Sunday)
        source: Where the data came from ('database' or 'default')
    """

    holidays: set[date]
    weekly_off_days: set[int]  # 0=Monday, 6=Sunday
    source: str  # 'database' or 'default'


class WorkingDaysCalculator:
    """
    Service for calculating working days and pro-ration.

    Handles automatic pro-rating for:
    - Mid-month starters (employee joins after period start)
    - Mid-month leavers (employee leaves before period end)
    - Both scenarios (joins and leaves within same period)

    Uses HolidayList model for organization-specific holidays.
    Falls back to hardcoded Nigerian holidays if no list is configured.
    """

    # Mapping of day names to weekday indices (0=Monday, 6=Sunday)
    DAY_NAME_TO_INDEX: dict[str, int] = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }

    # Fallback Nigerian public holidays for 2025-2026 (major ones)
    # Used only when no HolidayList is configured for the organization
    DEFAULT_HOLIDAYS: set[date] = {
        # 2025
        date(2025, 1, 1),  # New Year
        date(2025, 4, 18),  # Good Friday
        date(2025, 4, 21),  # Easter Monday
        date(2025, 5, 1),  # Workers' Day
        date(2025, 6, 12),  # Democracy Day
        date(2025, 10, 1),  # Independence Day
        date(2025, 12, 25),  # Christmas
        date(2025, 12, 26),  # Boxing Day
        # 2026
        date(2026, 1, 1),  # New Year
        date(2026, 4, 3),  # Good Friday
        date(2026, 4, 6),  # Easter Monday
        date(2026, 5, 1),  # Workers' Day
        date(2026, 6, 12),  # Democracy Day
        date(2026, 10, 1),  # Independence Day
        date(2026, 12, 25),  # Christmas
        date(2026, 12, 26),  # Boxing Day
    }

    # Default weekly off days (Saturday, Sunday)
    DEFAULT_WEEKLY_OFF: set[int] = {5, 6}

    def __init__(self, db: Session):
        self.db = db
        self._calendar_cache: dict[UUID, HolidayCalendar] = {}

    def get_holiday_calendar(
        self,
        organization_id: UUID,
        period_start: date,
        period_end: date,
    ) -> HolidayCalendar:
        """
        Load holiday calendar from database for an organization.

        Finds the default HolidayList for the organization that covers the
        given period. If multiple lists span the period, holidays from all
        are combined.

        Falls back to DEFAULT_HOLIDAYS if no HolidayList is configured.

        Args:
            organization_id: Organization UUID
            period_start: Start of the period
            period_end: End of the period

        Returns:
            HolidayCalendar with holidays and weekly off days
        """
        # Check cache (keyed by org_id - assumes holidays don't change mid-session)
        cache_key = organization_id
        if cache_key in self._calendar_cache:
            return self._calendar_cache[cache_key]

        # Find holiday lists that cover the period
        stmt = (
            select(HolidayList)
            .options(joinedload(HolidayList.holidays))
            .where(
                HolidayList.organization_id == organization_id,
                HolidayList.is_active == True,
                HolidayList.from_date <= period_end,
                HolidayList.to_date >= period_start,
            )
            .order_by(HolidayList.is_default.desc())  # Prefer default list
        )

        holiday_lists = self.db.scalars(stmt).unique().all()

        if not holiday_lists:
            logger.debug(
                "No holiday list found for org %s, using defaults",
                organization_id,
            )
            calendar = HolidayCalendar(
                holidays=self.DEFAULT_HOLIDAYS.copy(),
                weekly_off_days=self.DEFAULT_WEEKLY_OFF.copy(),
                source="default",
            )
            self._calendar_cache[cache_key] = calendar
            return calendar

        # Collect holidays from all matching lists
        holidays: set[date] = set()
        weekly_off_days: set[int] = set()

        for hl in holiday_lists:
            # Add holiday dates that fall within the period
            for h in hl.holidays:
                if period_start <= h.holiday_date <= period_end:
                    holidays.add(h.holiday_date)

            # Parse weekly off days (e.g., "Saturday,Sunday")
            if hl.weekly_off:
                for day_name in hl.weekly_off.split(","):
                    day_name = day_name.strip().lower()
                    if day_name in self.DAY_NAME_TO_INDEX:
                        weekly_off_days.add(self.DAY_NAME_TO_INDEX[day_name])

        # If no weekly off days parsed, use defaults
        if not weekly_off_days:
            weekly_off_days = self.DEFAULT_WEEKLY_OFF.copy()

        logger.debug(
            "Loaded holiday calendar for org %s: %d holidays, weekly off=%s",
            organization_id,
            len(holidays),
            weekly_off_days,
        )

        calendar = HolidayCalendar(
            holidays=holidays,
            weekly_off_days=weekly_off_days,
            source="database",
        )
        self._calendar_cache[cache_key] = calendar
        return calendar

    def _parse_weekly_off(self, weekly_off_str: str) -> set[int]:
        """
        Parse weekly off string to set of weekday indices.

        Args:
            weekly_off_str: Comma-separated day names (e.g., "Saturday,Sunday")

        Returns:
            Set of weekday indices (0=Monday, 6=Sunday)
        """
        result: set[int] = set()
        for day_name in weekly_off_str.split(","):
            day_name = day_name.strip().lower()
            if day_name in self.DAY_NAME_TO_INDEX:
                result.add(self.DAY_NAME_TO_INDEX[day_name])
        return result

    def calculate_payment_days(
        self,
        organization_id: UUID,
        employee_joining_date: date,
        period_start: date,
        period_end: date,
        employee_leaving_date: Optional[date] = None,
        method: Optional[ProrationMethod] = None,
        exclude_weekends: bool = True,
        exclude_holidays: bool = True,
        custom_holidays: Optional[set[date]] = None,
    ) -> ProrationResult:
        """
        Calculate working days and payment days for an employee in a period.

        Uses the organization's HolidayList for holidays and weekly off days.
        Falls back to hardcoded Nigerian holidays if no list is configured.

        Args:
            organization_id: Organization for loading settings and holiday calendar
            employee_joining_date: Date employee joined the company
            period_start: Start of the pay period
            period_end: End of the pay period
            employee_leaving_date: Date employee left (if applicable)
            method: Proration method (defaults to organization setting or CALENDAR_DAYS)
            exclude_weekends: Whether to exclude weekly off days (for BUSINESS_DAYS method)
            exclude_holidays: Whether to exclude public holidays (for BUSINESS_DAYS)
            custom_holidays: Additional holiday dates to exclude (merged with DB holidays)

        Returns:
            ProrationResult with calculated days and proration details

        Raises:
            ValueError: If period_start > period_end (invalid date range)
        """
        # Validate date range
        if period_start > period_end:
            raise ValueError(
                f"Invalid pay period: period_start ({period_start}) cannot be "
                f"after period_end ({period_end})"
            )

        # Determine method to use
        if method is None:
            method = self._get_org_proration_method(organization_id)

        # Load holiday calendar from database (or use defaults)
        calendar = self.get_holiday_calendar(organization_id, period_start, period_end)

        # Merge custom holidays if provided
        if custom_holidays:
            calendar.holidays.update(custom_holidays)

        # Calculate effective period for this employee
        effective_start = max(employee_joining_date, period_start)
        effective_end = period_end

        if employee_leaving_date and employee_leaving_date < period_end:
            effective_end = employee_leaving_date

        # Determine proration reason
        proration_reason = self._determine_proration_reason(
            employee_joining_date,
            employee_leaving_date,
            period_start,
            period_end,
        )

        # Calculate total working days in the full period
        total_working_days = self._calculate_days(
            period_start,
            period_end,
            method,
            calendar,
            exclude_weekends,
            exclude_holidays,
        )

        # Calculate actual payment days for this employee
        if effective_start > effective_end:
            # Employee not active in this period
            payment_days = Decimal("0")
        elif proration_reason == ProrationReason.NONE:
            # Full period worked
            payment_days = total_working_days
        else:
            # Pro-rated period
            payment_days = self._calculate_days(
                effective_start,
                effective_end,
                method,
                calendar,
                exclude_weekends,
                exclude_holidays,
            )

        is_prorated = proration_reason != ProrationReason.NONE

        logger.debug(
            "Pro-ration calc: period=%s to %s, effective=%s to %s, "
            "total_days=%s, payment_days=%s, reason=%s",
            period_start,
            period_end,
            effective_start,
            effective_end,
            total_working_days,
            payment_days,
            proration_reason.value,
        )

        # Calculate proration factor
        if total_working_days > 0:
            proration_factor = (payment_days / total_working_days).quantize(
                Decimal("0.0001"), rounding=ROUND_HALF_UP
            )
        else:
            proration_factor = Decimal("0")

        return ProrationResult(
            total_working_days=total_working_days,
            payment_days=payment_days,
            is_prorated=is_prorated,
            proration_reason=proration_reason,
            effective_start=effective_start,
            effective_end=effective_end,
            method_used=method,
            proration_factor=proration_factor,
        )

    def _get_org_proration_method(self, organization_id: UUID) -> ProrationMethod:
        """
        Get organization's preferred proration method from settings.

        Falls back to CALENDAR_DAYS if not configured.
        """
        from app.models.finance.core_org.organization import Organization

        org = self.db.get(Organization, organization_id)
        if org and hasattr(org, "hr_proration_method") and org.hr_proration_method:
            try:
                return ProrationMethod(org.hr_proration_method)
            except ValueError:
                pass

        # Default to calendar days (most common in Nigerian payroll)
        return ProrationMethod.CALENDAR_DAYS

    def _determine_proration_reason(
        self,
        joining_date: date,
        leaving_date: Optional[date],
        period_start: date,
        period_end: date,
    ) -> ProrationReason:
        """Determine the reason for pro-rating (if any)."""
        joined_mid_period = joining_date > period_start
        left_mid_period = leaving_date is not None and leaving_date < period_end

        if joined_mid_period and left_mid_period:
            return ProrationReason.BOTH
        elif joined_mid_period:
            return ProrationReason.JOINED_MID_PERIOD
        elif left_mid_period:
            return ProrationReason.LEFT_MID_PERIOD
        else:
            return ProrationReason.NONE

    def _calculate_days(
        self,
        start_date: date,
        end_date: date,
        method: ProrationMethod,
        calendar: HolidayCalendar,
        exclude_weekends: bool,
        exclude_holidays: bool,
    ) -> Decimal:
        """
        Calculate number of working days based on method.

        Args:
            start_date: Start of range (inclusive)
            end_date: End of range (inclusive)
            method: Calculation method
            calendar: Holiday calendar with holidays and weekly off days
            exclude_weekends: Whether to exclude weekly off days (BUSINESS_DAYS only)
            exclude_holidays: Whether to exclude holidays (BUSINESS_DAYS only)

        Returns:
            Number of working days as Decimal
        """
        if start_date > end_date:
            return Decimal("0")

        if method == ProrationMethod.FIXED_30_DAY:
            # Always use 30 days per month regardless of actual days
            # Pro-rate based on proportion of 30-day month
            # Use Decimal arithmetic throughout to avoid floating-point precision errors
            _, days_in_month = monthrange(start_date.year, start_date.month)
            calendar_days = (end_date - start_date).days + 1
            return (
                Decimal(calendar_days) * Decimal(30) / Decimal(days_in_month)
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        elif method == ProrationMethod.BUSINESS_DAYS:
            # Count only business days (exclude weekly off days and holidays)
            business_days = Decimal("0")
            current = start_date

            while current <= end_date:
                # Check if day is a weekly off day (using calendar's weekly_off_days)
                is_weekly_off = (
                    current.weekday() in calendar.weekly_off_days
                    if exclude_weekends
                    else False
                )
                # Check if day is a holiday (using calendar's holidays)
                is_holiday = current in calendar.holidays if exclude_holidays else False

                if not is_weekly_off and not is_holiday:
                    business_days += 1

                current += timedelta(days=1)

            return business_days

        else:  # CALENDAR_DAYS (default)
            # Simple calendar day count
            return Decimal(str((end_date - start_date).days + 1))

    def get_business_days_in_month(
        self,
        year: int,
        month: int,
        organization_id: Optional[UUID] = None,
        exclude_weekends: bool = True,
        exclude_holidays: bool = True,
    ) -> int:
        """
        Get the number of business days in a specific month.

        Uses the organization's HolidayList for holidays and weekly off days.
        Falls back to default holidays if no organization_id is provided.

        Args:
            year: Year
            month: Month (1-12)
            organization_id: Organization UUID for loading holiday calendar (optional)
            exclude_weekends: Whether to exclude weekly off days
            exclude_holidays: Whether to exclude holidays

        Returns:
            Number of business days in the month
        """
        first_day = date(year, month, 1)
        _, last_day_num = monthrange(year, month)
        last_day = date(year, month, last_day_num)

        # Load holiday calendar for the month (uses defaults if org_id is None)
        if organization_id:
            calendar = self.get_holiday_calendar(organization_id, first_day, last_day)
        else:
            calendar = HolidayCalendar(
                holidays=self.DEFAULT_HOLIDAYS.copy(),
                weekly_off_days=self.DEFAULT_WEEKLY_OFF.copy(),
                source="default",
            )

        days = self._calculate_days(
            first_day,
            last_day,
            ProrationMethod.BUSINESS_DAYS,
            calendar,
            exclude_weekends,
            exclude_holidays,
        )

        return int(days)


# Module-level convenience function
def calculate_proration(
    db: Session,
    organization_id: UUID,
    employee_joining_date: date,
    period_start: date,
    period_end: date,
    employee_leaving_date: Optional[date] = None,
    method: Optional[ProrationMethod] = None,
) -> ProrationResult:
    """
    Convenience function for calculating pro-ration.

    Args:
        db: Database session
        organization_id: Organization ID
        employee_joining_date: When employee joined
        period_start: Pay period start
        period_end: Pay period end
        employee_leaving_date: When employee left (optional)
        method: Proration method (optional, uses org default)

    Returns:
        ProrationResult with calculated values
    """
    calculator = WorkingDaysCalculator(db)
    return calculator.calculate_payment_days(
        organization_id=organization_id,
        employee_joining_date=employee_joining_date,
        period_start=period_start,
        period_end=period_end,
        employee_leaving_date=employee_leaving_date,
        method=method,
    )
