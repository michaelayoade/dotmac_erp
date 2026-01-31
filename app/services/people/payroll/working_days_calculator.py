"""
Working Days Calculator - Pro-rated Salary Calculation.

Handles automatic calculation of working days and payment days for employees
who join or leave mid-period. Supports multiple proration methods:
- CALENDAR_DAYS: Simple division by calendar days in period
- BUSINESS_DAYS: Only count working days (excludes weekends/holidays)
- FIXED_30_DAY: Always use 30-day month for consistency
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
from sqlalchemy.orm import Session

from app.models.people.payroll.payroll_entry import PayrollEntry

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


class WorkingDaysCalculator:
    """
    Service for calculating working days and pro-ration.

    Handles automatic pro-rating for:
    - Mid-month starters (employee joins after period start)
    - Mid-month leavers (employee leaves before period end)
    - Both scenarios (joins and leaves within same period)
    """

    # Nigerian public holidays for 2025-2026 (major ones)
    # In production, this should come from a holiday calendar table
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

    def __init__(self, db: Session):
        self.db = db

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

        Args:
            organization_id: Organization for loading settings
            employee_joining_date: Date employee joined the company
            period_start: Start of the pay period
            period_end: End of the pay period
            employee_leaving_date: Date employee left (if applicable)
            method: Proration method (defaults to organization setting or CALENDAR_DAYS)
            exclude_weekends: Whether to exclude Sat/Sun (for BUSINESS_DAYS method)
            exclude_holidays: Whether to exclude public holidays (for BUSINESS_DAYS)
            custom_holidays: Additional holiday dates to exclude

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
            exclude_weekends,
            exclude_holidays,
            custom_holidays,
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
                exclude_weekends,
                exclude_holidays,
                custom_holidays,
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
        exclude_weekends: bool,
        exclude_holidays: bool,
        custom_holidays: Optional[set[date]],
    ) -> Decimal:
        """
        Calculate number of working days based on method.

        Args:
            start_date: Start of range (inclusive)
            end_date: End of range (inclusive)
            method: Calculation method
            exclude_weekends: Whether to exclude weekends (BUSINESS_DAYS only)
            exclude_holidays: Whether to exclude holidays (BUSINESS_DAYS only)
            custom_holidays: Additional holiday dates

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
            # Count only business days (exclude weekends and holidays)
            holidays = self.DEFAULT_HOLIDAYS.copy()
            if custom_holidays:
                holidays.update(custom_holidays)

            business_days = Decimal("0")
            current = start_date

            while current <= end_date:
                is_weekend = current.weekday() >= 5 if exclude_weekends else False
                is_holiday = current in holidays if exclude_holidays else False

                if not is_weekend and not is_holiday:
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
        exclude_weekends: bool = True,
        exclude_holidays: bool = True,
    ) -> int:
        """
        Get the number of business days in a specific month.

        Useful for reporting and validation.
        """
        first_day = date(year, month, 1)
        _, last_day_num = monthrange(year, month)
        last_day = date(year, month, last_day_num)

        days = self._calculate_days(
            first_day,
            last_day,
            ProrationMethod.BUSINESS_DAYS,
            exclude_weekends,
            exclude_holidays,
            None,
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
