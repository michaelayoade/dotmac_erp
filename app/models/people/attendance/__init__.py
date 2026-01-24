"""
Attendance Management Models.

This module contains models for shifts and attendance tracking.
"""

from app.models.people.attendance.shift_type import ShiftType
from app.models.people.attendance.attendance import Attendance, AttendanceStatus
from app.models.people.attendance.shift_assignment import ShiftAssignment
from app.models.people.attendance.attendance_request import (
    AttendanceRequest,
    AttendanceRequestStatus,
)

__all__ = [
    "ShiftType",
    "Attendance",
    "AttendanceStatus",
    "ShiftAssignment",
    "AttendanceRequest",
    "AttendanceRequestStatus",
]
