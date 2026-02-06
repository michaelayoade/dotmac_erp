"""
Attendance Entity Mappings from ERPNext to DotMac ERP.

Maps ERPNext Attendance DocTypes to DotMac attendance schema:
- Shift Type → attendance.shift_type
- Attendance → attendance.attendance
"""

import logging
from datetime import time as datetime_time
from decimal import Decimal
from typing import Any

from .base import (
    DocTypeMapping,
    FieldMapping,
    clean_string,
    parse_date,
    parse_datetime,
    parse_decimal,
)

logger = logging.getLogger(__name__)

# ERPNext Attendance status to DotMac AttendanceStatus
ATTENDANCE_STATUS_MAP = {
    "Present": "PRESENT",
    "Absent": "ABSENT",
    "Half Day": "HALF_DAY",
    "On Leave": "ON_LEAVE",
    "Work From Home": "WORK_FROM_HOME",
}


def map_attendance_status(value: Any) -> str:
    """Map ERPNext attendance status."""
    if not value:
        return "PRESENT"
    return ATTENDANCE_STATUS_MAP.get(str(value), "PRESENT")


def parse_time(value: Any) -> datetime_time | None:
    """Parse time from ERPNext format."""
    if value is None:
        return None
    if isinstance(value, datetime_time):
        return value
    if isinstance(value, str):
        try:
            # ERPNext time format: "HH:MM:SS" or "HH:MM"
            parts = value.split(":")
            if len(parts) >= 2:
                hour = int(parts[0])
                minute = int(parts[1])
                second = int(parts[2]) if len(parts) > 2 else 0
                return datetime_time(hour, minute, second)
        except (ValueError, TypeError):
            pass
    return None


def calculate_working_hours(start_time: str | None, end_time: str | None) -> Decimal:
    """Calculate working hours from start and end times."""
    if not start_time or not end_time:
        return Decimal("8")  # Default

    start = parse_time(start_time)
    end = parse_time(end_time)

    if not start or not end:
        return Decimal("8")

    # Calculate hours difference
    start_minutes = start.hour * 60 + start.minute
    end_minutes = end.hour * 60 + end.minute

    # Handle overnight shifts
    if end_minutes < start_minutes:
        end_minutes += 24 * 60

    hours = (end_minutes - start_minutes) / 60
    return Decimal(str(round(hours, 2)))


class ShiftTypeMapping(DocTypeMapping):
    """Map ERPNext Shift Type to DotMac ERP attendance.shift_type."""

    def __init__(self):
        super().__init__(
            source_doctype="Shift Type",
            target_table="attendance.shift_type",
            fields=[
                FieldMapping(
                    source="name",
                    target="_source_name",
                    required=True,
                ),
                FieldMapping(
                    source="name",  # ERPNext uses name as identifier
                    target="shift_name",
                    required=True,
                    transformer=lambda v: clean_string(v, 100),
                ),
                FieldMapping(
                    source="start_time",
                    target="start_time",
                    required=True,
                    transformer=parse_time,
                ),
                FieldMapping(
                    source="end_time",
                    target="end_time",
                    required=True,
                    transformer=parse_time,
                ),
                # Grace periods
                FieldMapping(
                    source="late_entry_grace_period",
                    target="late_entry_grace_period",
                    required=False,
                    default=0,
                    transformer=lambda v: int(v) if v else 0,
                ),
                FieldMapping(
                    source="early_exit_grace_period",
                    target="early_exit_grace_period",
                    required=False,
                    default=0,
                    transformer=lambda v: int(v) if v else 0,
                ),
                # Thresholds for half-day/absent marking
                FieldMapping(
                    source="working_hours_threshold_for_half_day",
                    target="half_day_threshold_hours",
                    required=False,
                    transformer=parse_decimal,
                ),
                FieldMapping(
                    source="working_hours_threshold_for_absent",
                    target="_absent_threshold",  # Not used directly
                    required=False,
                    transformer=parse_decimal,
                ),
                FieldMapping(
                    source="modified",
                    target="_source_modified",
                    required=False,
                    transformer=parse_datetime,
                ),
            ],
            unique_key="name",
        )

    def transform_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """Transform with shift code and working hours calculation."""
        result = super().transform_record(record)

        # Generate shift code from name
        name = record.get("name", "")
        code = name.upper().replace(" ", "_").replace("-", "_")
        result["shift_code"] = clean_string(code, 30) or "SHIFT"

        # Calculate working hours from start/end times
        result["working_hours"] = calculate_working_hours(
            record.get("start_time"),
            record.get("end_time"),
        )

        # Set default break duration
        result["break_duration_minutes"] = 60

        # Set active status
        result["is_active"] = True

        # Clean up internal fields
        result.pop("_absent_threshold", None)

        return result


class AttendanceMapping(DocTypeMapping):
    """Map ERPNext Attendance to DotMac ERP attendance.attendance."""

    def __init__(self):
        super().__init__(
            source_doctype="Attendance",
            target_table="attendance.attendance",
            fields=[
                FieldMapping(
                    source="name",
                    target="_source_name",
                    required=True,
                ),
                # Employee reference
                FieldMapping(
                    source="employee",
                    target="_employee_source_name",
                    required=True,
                ),
                # Date
                FieldMapping(
                    source="attendance_date",
                    target="attendance_date",
                    required=True,
                    transformer=parse_date,
                ),
                # Status
                FieldMapping(
                    source="status",
                    target="status",
                    required=True,
                    transformer=map_attendance_status,
                ),
                # Shift reference
                FieldMapping(
                    source="shift",
                    target="_shift_source_name",
                    required=False,
                ),
                # Check-in/out times
                FieldMapping(
                    source="in_time",
                    target="check_in",
                    required=False,
                    transformer=parse_datetime,
                ),
                FieldMapping(
                    source="out_time",
                    target="check_out",
                    required=False,
                    transformer=parse_datetime,
                ),
                # Working hours
                FieldMapping(
                    source="working_hours",
                    target="working_hours",
                    required=False,
                    transformer=parse_decimal,
                ),
                # Late/early flags
                FieldMapping(
                    source="late_entry",
                    target="is_late",
                    required=False,
                    default=False,
                    transformer=bool,
                ),
                FieldMapping(
                    source="early_exit",
                    target="is_early_exit",
                    required=False,
                    default=False,
                    transformer=bool,
                ),
                # Leave linkage
                FieldMapping(
                    source="leave_type",
                    target="_leave_type_source_name",
                    required=False,
                ),
                FieldMapping(
                    source="leave_application",
                    target="_leave_application_source_name",
                    required=False,
                ),
                FieldMapping(
                    source="modified",
                    target="_source_modified",
                    required=False,
                    transformer=parse_datetime,
                ),
            ],
            unique_key="name",
        )

    def transform_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """Transform attendance record."""
        result = super().transform_record(record)

        # Default overtime to 0
        result["overtime_hours"] = Decimal("0")

        return result
