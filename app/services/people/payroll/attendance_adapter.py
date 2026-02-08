"""
AttendancePayrollAdapter - Bridge between attendance records and payroll processing.

Computes working hours, overtime, and absent days from attendance data for use
in salary slip generation.

Note: This is a single-tenant implementation. organization_id is used for
explicit scoping but not for tenant isolation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.people.attendance import (
    Attendance,
    AttendanceStatus,
    ShiftAssignment,
    ShiftType,
)

logger = logging.getLogger(__name__)


@dataclass
class AttendanceSummary:
    """Summary of attendance metrics for payroll processing."""

    employee_id: UUID
    period_start: date
    period_end: date

    # Day counts
    total_calendar_days: int = 0
    present_days: int = 0
    absent_days: int = 0
    half_days: int = 0
    leave_days: int = 0
    holiday_days: int = 0
    work_from_home_days: int = 0

    # Effective working days (for payroll)
    # present_days + half_days*0.5 + work_from_home_days
    effective_working_days: Decimal = Decimal("0")

    # Hour calculations
    total_working_hours: Decimal = Decimal("0")
    total_overtime_hours: Decimal = Decimal("0")
    expected_working_hours: Decimal = Decimal("0")

    # Punctuality metrics
    late_entries: int = 0
    total_late_minutes: int = 0
    early_exits: int = 0
    total_early_exit_minutes: int = 0

    # Raw data for detailed processing
    daily_records: list[dict] = field(default_factory=list)


@dataclass
class OvertimeBreakdown:
    """Breakdown of overtime hours by type."""

    regular_overtime: Decimal = Decimal("0")  # Weekday overtime
    weekend_overtime: Decimal = Decimal("0")  # Saturday/Sunday overtime
    holiday_overtime: Decimal = Decimal("0")  # Public holiday overtime
    total_overtime: Decimal = Decimal("0")


class AttendancePayrollAdapter:
    """
    Adapter service that bridges attendance records with payroll processing.

    Provides methods to:
    - Calculate working days from attendance records
    - Compute overtime hours (regular, weekend, holiday)
    - Determine absent days for deductions
    - Generate attendance summary for salary slips

    Usage:
        adapter = AttendancePayrollAdapter(db)
        summary = adapter.get_payroll_attendance_summary(
            organization_id=org_id,
            employee_id=emp_id,
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
        )
    """

    # Default hours per day if shift not assigned
    DEFAULT_WORKING_HOURS_PER_DAY = Decimal("8")

    def __init__(self, db: Session):
        self.db = db

    def get_employee_shift(
        self,
        organization_id: UUID,
        employee_id: UUID,
        as_of_date: date,
    ) -> ShiftType | None:
        """Get the employee's assigned shift for a specific date."""
        stmt = (
            select(ShiftType)
            .join(
                ShiftAssignment,
                ShiftAssignment.shift_type_id == ShiftType.shift_type_id,
            )
            .where(
                ShiftAssignment.organization_id == organization_id,
                ShiftAssignment.employee_id == employee_id,
                ShiftAssignment.is_active == True,  # noqa: E712
                ShiftAssignment.start_date <= as_of_date,
                (
                    (ShiftAssignment.end_date.is_(None))
                    | (ShiftAssignment.end_date >= as_of_date)
                ),
            )
            .order_by(ShiftAssignment.start_date.desc())
        )
        return self.db.scalar(stmt)

    def get_expected_hours_per_day(
        self,
        organization_id: UUID,
        employee_id: UUID,
        as_of_date: date,
    ) -> Decimal:
        """Get expected working hours per day based on shift assignment.

        Note: Call this for each day if shift may change mid-period.
        """
        shift = self.get_employee_shift(organization_id, employee_id, as_of_date)
        # Use explicit None check - Decimal(0) is a valid value
        if shift and shift.working_hours is not None:
            return shift.working_hours
        return self.DEFAULT_WORKING_HOURS_PER_DAY

    def get_attendance_records(
        self,
        organization_id: UUID,
        employee_id: UUID,
        period_start: date,
        period_end: date,
    ) -> list[Attendance]:
        """Get all attendance records for an employee in a period."""
        stmt = (
            select(Attendance)
            .where(
                Attendance.organization_id == organization_id,
                Attendance.employee_id == employee_id,
                Attendance.attendance_date >= period_start,
                Attendance.attendance_date <= period_end,
            )
            .order_by(Attendance.attendance_date)
        )
        return list(self.db.scalars(stmt).all())

    def get_payroll_attendance_summary(
        self,
        organization_id: UUID,
        employee_id: UUID,
        period_start: date,
        period_end: date,
        *,
        include_daily_details: bool = False,
    ) -> AttendanceSummary:
        """
        Generate attendance summary for payroll processing.

        This is the main method for integrating attendance with salary slips.
        It calculates working days, overtime, and absences.

        Args:
            organization_id: Organization scope
            employee_id: Employee to summarize
            period_start: Start of pay period
            period_end: End of pay period
            include_daily_details: Whether to include daily records in summary

        Returns:
            AttendanceSummary with computed metrics

        Raises:
            ValueError: If period_start > period_end
        """
        if period_start > period_end:
            raise ValueError(
                f"period_start ({period_start}) must be <= period_end ({period_end})"
            )

        records = self.get_attendance_records(
            organization_id, employee_id, period_start, period_end
        )

        # Create lookup by date
        attendance_by_date = {r.attendance_date: r for r in records}

        summary = AttendanceSummary(
            employee_id=employee_id,
            period_start=period_start,
            period_end=period_end,
        )

        # Iterate through each day in the period
        current_date = period_start
        while current_date <= period_end:
            summary.total_calendar_days += 1
            is_weekend = current_date.weekday() >= 5  # Saturday=5, Sunday=6

            # Get expected hours for THIS day (handles mid-period shift changes)
            expected_hours_per_day = self.get_expected_hours_per_day(
                organization_id, employee_id, current_date
            )

            attendance = attendance_by_date.get(current_date)

            if attendance:
                # Process attendance record
                daily_data = self._process_attendance_record(
                    attendance, expected_hours_per_day, summary
                )
            else:
                # No attendance record - could be weekend or untracked
                if is_weekend:
                    daily_data = {
                        "date": current_date,
                        "status": "WEEKEND",
                        "working_hours": Decimal("0"),
                        "overtime_hours": Decimal("0"),
                    }
                else:
                    # Working day with no record - assume absent
                    summary.absent_days += 1
                    daily_data = {
                        "date": current_date,
                        "status": "ABSENT_NO_RECORD",
                        "working_hours": Decimal("0"),
                        "overtime_hours": Decimal("0"),
                    }

            if include_daily_details:
                summary.daily_records.append(daily_data)

            current_date += timedelta(days=1)

        # Calculate effective working days
        summary.effective_working_days = (
            Decimal(str(summary.present_days))
            + Decimal(str(summary.half_days)) * Decimal("0.5")
            + Decimal(str(summary.work_from_home_days))
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # Calculate expected hours for the period (excluding weekends)
        working_days_in_period = sum(
            1
            for d in self._date_range(period_start, period_end)
            if d.weekday() < 5  # Exclude weekends
        )
        summary.expected_working_hours = (
            expected_hours_per_day * Decimal(str(working_days_in_period))
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        logger.info(
            "Generated attendance summary for employee %s (period: %s to %s): "
            "present=%d, absent=%d, overtime=%s hrs",
            employee_id,
            period_start,
            period_end,
            summary.present_days,
            summary.absent_days,
            summary.total_overtime_hours,
        )

        return summary

    def _process_attendance_record(
        self,
        attendance: Attendance,
        expected_hours: Decimal,
        summary: AttendanceSummary,
    ) -> dict:
        """Process a single attendance record and update summary."""
        daily_data = {
            "date": attendance.attendance_date,
            "status": attendance.status.value,
            "working_hours": attendance.working_hours or Decimal("0"),
            "overtime_hours": attendance.overtime_hours or Decimal("0"),
            "late_entry": attendance.late_entry,
            "late_minutes": attendance.late_entry_minutes,
            "early_exit": attendance.early_exit,
            "early_exit_minutes": attendance.early_exit_minutes,
        }

        # Update summary based on status
        if attendance.status == AttendanceStatus.PRESENT:
            summary.present_days += 1
            summary.total_working_hours += attendance.working_hours or Decimal("0")
            summary.total_overtime_hours += attendance.overtime_hours or Decimal("0")

        elif attendance.status == AttendanceStatus.HALF_DAY:
            summary.half_days += 1
            # Half days contribute half of expected hours
            summary.total_working_hours += (
                attendance.working_hours or expected_hours / Decimal("2")
            )

        elif attendance.status == AttendanceStatus.ABSENT:
            summary.absent_days += 1

        elif attendance.status == AttendanceStatus.ON_LEAVE:
            summary.leave_days += 1

        elif attendance.status == AttendanceStatus.HOLIDAY:
            summary.holiday_days += 1
            # If worked on holiday, count overtime
            if attendance.working_hours and attendance.working_hours > 0:
                summary.total_overtime_hours += attendance.working_hours

        elif attendance.status == AttendanceStatus.WORK_FROM_HOME:
            summary.work_from_home_days += 1
            summary.total_working_hours += attendance.working_hours or expected_hours
            summary.total_overtime_hours += attendance.overtime_hours or Decimal("0")

        # Track punctuality
        if attendance.late_entry:
            summary.late_entries += 1
            summary.total_late_minutes += attendance.late_entry_minutes

        if attendance.early_exit:
            summary.early_exits += 1
            summary.total_early_exit_minutes += attendance.early_exit_minutes

        return daily_data

    def _date_range(self, start: date, end: date):
        """Generate dates from start to end (inclusive)."""
        current = start
        while current <= end:
            yield current
            current += timedelta(days=1)

    def calculate_overtime_breakdown(
        self,
        organization_id: UUID,
        employee_id: UUID,
        period_start: date,
        period_end: date,
    ) -> OvertimeBreakdown:
        """
        Calculate detailed overtime breakdown by type.

        Separates overtime into:
        - Regular (weekday) overtime
        - Weekend overtime
        - Holiday overtime
        """
        records = self.get_attendance_records(
            organization_id, employee_id, period_start, period_end
        )

        breakdown = OvertimeBreakdown()

        for record in records:
            overtime = record.overtime_hours or Decimal("0")
            if overtime <= 0:
                continue

            is_weekend = record.attendance_date.weekday() >= 5
            is_holiday = record.status == AttendanceStatus.HOLIDAY

            if is_holiday:
                # Holiday worked - all hours count as holiday overtime
                breakdown.holiday_overtime += record.working_hours or Decimal("0")
            elif is_weekend:
                breakdown.weekend_overtime += overtime
            else:
                breakdown.regular_overtime += overtime

        breakdown.total_overtime = (
            breakdown.regular_overtime
            + breakdown.weekend_overtime
            + breakdown.holiday_overtime
        )

        return breakdown

    def get_absent_days_for_deduction(
        self,
        organization_id: UUID,
        employee_id: UUID,
        period_start: date,
        period_end: date,
    ) -> Decimal:
        """
        Get the number of absent days for salary deduction.

        This excludes approved leaves and holidays.
        Half days count as 0.5 absent.
        """
        summary = self.get_payroll_attendance_summary(
            organization_id, employee_id, period_start, period_end
        )

        # Absent days (full) + half of half-days missed
        return Decimal(str(summary.absent_days)) + (
            Decimal(str(summary.half_days)) * Decimal("0.5")
        )

    def integrate_with_salary_slip(
        self,
        organization_id: UUID,
        employee_id: UUID,
        period_start: date,
        period_end: date,
    ) -> dict:
        """
        Generate attendance data formatted for salary slip integration.

        Returns a dict that can be used directly in salary slip creation.
        """
        summary = self.get_payroll_attendance_summary(
            organization_id, employee_id, period_start, period_end
        )
        overtime = self.calculate_overtime_breakdown(
            organization_id, employee_id, period_start, period_end
        )

        return {
            "attendance_based_absent_days": Decimal(str(summary.absent_days)),
            "attendance_based_half_days": summary.half_days,
            "effective_working_days": summary.effective_working_days,
            "total_working_hours": summary.total_working_hours.quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            ),
            "overtime_hours": {
                "regular": overtime.regular_overtime.quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                ),
                "weekend": overtime.weekend_overtime.quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                ),
                "holiday": overtime.holiday_overtime.quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                ),
                "total": overtime.total_overtime.quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                ),
            },
            "late_entries": summary.late_entries,
            "total_late_minutes": summary.total_late_minutes,
            "early_exits": summary.early_exits,
            "total_early_exit_minutes": summary.total_early_exit_minutes,
            "leave_days": summary.leave_days,
            "holiday_days": summary.holiday_days,
        }

    def get_bulk_attendance_summary(
        self,
        organization_id: UUID,
        employee_ids: list[UUID],
        period_start: date,
        period_end: date,
    ) -> dict[UUID, AttendanceSummary]:
        """
        Get attendance summaries for multiple employees efficiently.

        Used for batch payroll processing.
        """
        # Query all attendance records in one go
        stmt = (
            select(Attendance)
            .where(
                Attendance.organization_id == organization_id,
                Attendance.employee_id.in_(employee_ids),
                Attendance.attendance_date >= period_start,
                Attendance.attendance_date <= period_end,
            )
            .order_by(Attendance.employee_id, Attendance.attendance_date)
        )
        all_records = list(self.db.scalars(stmt).all())

        # Group by employee
        records_by_employee: dict[UUID, list[Attendance]] = {}
        for record in all_records:
            if record.employee_id not in records_by_employee:
                records_by_employee[record.employee_id] = []
            records_by_employee[record.employee_id].append(record)

        # Generate summaries
        summaries = {}
        for emp_id in employee_ids:
            employee_records = records_by_employee.get(emp_id, [])
            summary = self._compute_summary_from_records(
                emp_id, period_start, period_end, employee_records, organization_id
            )
            summaries[emp_id] = summary

        return summaries

    def _compute_summary_from_records(
        self,
        employee_id: UUID,
        period_start: date,
        period_end: date,
        records: list[Attendance],
        organization_id: UUID,
    ) -> AttendanceSummary:
        """Compute summary from pre-fetched records."""
        attendance_by_date = {r.attendance_date: r for r in records}

        summary = AttendanceSummary(
            employee_id=employee_id,
            period_start=period_start,
            period_end=period_end,
        )

        current_date = period_start
        while current_date <= period_end:
            summary.total_calendar_days += 1
            is_weekend = current_date.weekday() >= 5

            # Get expected hours for THIS day (handles mid-period shift changes)
            expected_hours = self.get_expected_hours_per_day(
                organization_id, employee_id, current_date
            )

            attendance = attendance_by_date.get(current_date)
            if attendance:
                self._process_attendance_record(attendance, expected_hours, summary)
            elif not is_weekend:
                summary.absent_days += 1

            current_date += timedelta(days=1)

        summary.effective_working_days = (
            Decimal(str(summary.present_days))
            + Decimal(str(summary.half_days)) * Decimal("0.5")
            + Decimal(str(summary.work_from_home_days))
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        return summary
