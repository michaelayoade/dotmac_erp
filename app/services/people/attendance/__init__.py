"""Attendance Management Services."""

from .attendance_service import AttendanceService, CheckInRequirementError
from .web import AttendanceWebService, attendance_web_service

__all__ = [
    "AttendanceService",
    "AttendanceWebService",
    "CheckInRequirementError",
    "attendance_web_service",
]
