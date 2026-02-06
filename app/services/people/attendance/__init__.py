"""Attendance Management Services."""

from .attendance_service import AttendanceService
from .web import AttendanceWebService, attendance_web_service

__all__ = ["AttendanceService", "AttendanceWebService", "attendance_web_service"]
