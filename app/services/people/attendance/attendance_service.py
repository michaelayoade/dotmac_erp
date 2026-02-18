"""Attendance management service implementation.

Handles shift types, attendance records, and reporting.
Adapted from DotMac People for the unified ERP platform.
"""

from __future__ import annotations

import logging
import math
from datetime import UTC, date, datetime, time, timedelta
from datetime import tzinfo as dt_tzinfo
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, literal_column, or_, select
from sqlalchemy.orm import Session

from app.models.finance.core_org.location import Location
from app.models.finance.core_org.organization import Organization
from app.models.people.attendance import (
    Attendance,
    AttendanceRequest,
    AttendanceRequestStatus,
    AttendanceStatus,
    ShiftAssignment,
    ShiftType,
)
from app.models.people.hr.employee import Employee
from app.services.common import PaginatedResult, PaginationParams, ValidationError

logger = logging.getLogger(__name__)

# Shapely for GeoJSON polygon validation
try:
    from shapely.geometry import Point, shape
    from shapely.validation import make_valid

    SHAPELY_AVAILABLE = True
except ImportError:
    SHAPELY_AVAILABLE = False

if TYPE_CHECKING:
    from app.web.deps import WebAuthContext

__all__ = ["AttendanceService"]


class AttendanceServiceError(Exception):
    """Base error for attendance service."""

    pass


class ShiftTypeNotFoundError(AttendanceServiceError):
    """Shift type not found."""

    def __init__(self, shift_type_id: UUID):
        self.shift_type_id = shift_type_id
        super().__init__(f"Shift type {shift_type_id} not found")


class AttendanceNotFoundError(AttendanceServiceError):
    """Attendance record not found."""

    def __init__(self, attendance_id: UUID):
        self.attendance_id = attendance_id
        super().__init__(f"Attendance record {attendance_id} not found")


class DuplicateAttendanceError(AttendanceServiceError):
    """Duplicate attendance record."""

    def __init__(self, employee_id: UUID, attendance_date: date):
        self.employee_id = employee_id
        self.attendance_date = attendance_date
        super().__init__(
            f"Attendance already exists for employee {employee_id} on {attendance_date}"
        )


class ShiftAssignmentNotFoundError(AttendanceServiceError):
    """Shift assignment not found."""

    def __init__(self, shift_assignment_id: UUID):
        self.shift_assignment_id = shift_assignment_id
        super().__init__(f"Shift assignment {shift_assignment_id} not found")


class AttendanceRequestNotFoundError(AttendanceServiceError):
    """Attendance request not found."""

    def __init__(self, request_id: UUID):
        self.request_id = request_id
        super().__init__(f"Attendance request {request_id} not found")


class AttendanceRequestStatusError(AttendanceServiceError):
    """Attendance request status transition error."""

    def __init__(self, request_id: UUID, current: str, target: str):
        self.request_id = request_id
        super().__init__(
            f"Cannot transition attendance request {request_id} from {current} to {target}"
        )


class AttendanceService:
    """Service for attendance management operations.

    Handles:
    - Shift types configuration
    - Attendance recording (check-in/check-out)
    - Working hours calculation
    - Attendance reports and summaries
    """

    def __init__(
        self,
        db: Session,
        ctx: WebAuthContext | None = None,
    ) -> None:
        self.db = db
        self.ctx = ctx

    @staticmethod
    def _haversine_distance_m(
        lat1: float,
        lon1: float,
        lat2: float,
        lon2: float,
    ) -> float:
        """Calculate distance in meters between two lat/lon points."""
        radius_m = 6371000.0
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        d_phi = math.radians(lat2 - lat1)
        d_lambda = math.radians(lon2 - lon1)
        a = (
            math.sin(d_phi / 2) ** 2
            + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
        )
        return 2 * radius_m * math.asin(math.sqrt(a))

    @staticmethod
    def _point_in_polygon(
        latitude: float,
        longitude: float,
        geojson_polygon: dict[str, Any],
    ) -> bool:
        """Check if a point is inside a GeoJSON polygon.

        Args:
            latitude: Point latitude (decimal degrees)
            longitude: Point longitude (decimal degrees)
            geojson_polygon: GeoJSON Polygon or MultiPolygon geometry

        Returns:
            True if point is inside the polygon
        """
        if not SHAPELY_AVAILABLE:
            raise ValidationError(
                "Polygon geofencing requires shapely library. "
                "Install with: pip install shapely"
            )

        try:
            # GeoJSON uses longitude, latitude order (x, y)
            point = Point(longitude, latitude)

            # Convert GeoJSON to Shapely geometry
            polygon = shape(geojson_polygon)

            # Ensure polygon is valid
            if not polygon.is_valid:
                polygon = make_valid(polygon)

            return bool(polygon.contains(point))
        except Exception as e:
            raise ValidationError(f"Invalid geofence polygon: {e}")

    def _get_employee_location(
        self,
        org_id: UUID,
        employee_id: UUID,
    ) -> Location | None:
        return self.db.scalar(
            select(Location)
            .join(Employee, Employee.assigned_location_id == Location.location_id)
            .where(
                Employee.organization_id == org_id,
                Employee.employee_id == employee_id,
                Location.organization_id == org_id,
            )
        )

    @staticmethod
    def _normalize_dt(value: datetime) -> datetime:
        """Ensure datetime is timezone-aware (default UTC)."""
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value

    def _org_timezone_name(self, org_id: UUID) -> str:
        org = self.db.get(Organization, org_id)
        if org and org.timezone:
            return org.timezone
        return "UTC"

    def _org_tzinfo(self, org_id: UUID) -> dt_tzinfo:
        tz_name = self._org_timezone_name(org_id)
        try:
            return ZoneInfo(tz_name)
        except Exception:
            return UTC

    def get_org_tzinfo(self, org_id: UUID) -> dt_tzinfo:
        """Public accessor for org timezone info."""
        return self._org_tzinfo(org_id)

    def _now_in_org_tz(self, org_id: UUID) -> datetime:
        return datetime.now(tz=self._org_tzinfo(org_id))

    def get_org_today(self, org_id: UUID) -> date:
        return self._now_in_org_tz(org_id).date()

    def _normalize_in_org_tz(self, org_id: UUID, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=self._org_tzinfo(org_id))
        return value

    @staticmethod
    def _now_like(reference: datetime | None = None) -> datetime:
        """Return timezone-aware now, using reference tz when available."""
        if reference and reference.tzinfo is not None:
            return datetime.now(tz=reference.tzinfo)
        return datetime.now(tz=UTC)

    @staticmethod
    def _combine_date_time(
        day: date,
        clock: time,
        tzinfo: dt_tzinfo | None,
    ) -> datetime:
        dt = datetime.combine(day, clock)
        if tzinfo is not None:
            dt = dt.replace(tzinfo=tzinfo)
        return dt

    def _validate_geofence(
        self,
        org_id: UUID,
        employee_id: UUID,
        latitude: float | None,
        longitude: float | None,
        *,
        action_label: str,
    ) -> None:
        """Validate employee location against geofence (circle or polygon).

        Supports two geofence types:
        - CIRCLE: Traditional radius-based validation using Haversine distance
        - POLYGON: GeoJSON polygon boundary using Shapely point-in-polygon

        Args:
            org_id: Organization ID
            employee_id: Employee ID
            latitude: Employee's current latitude
            longitude: Employee's current longitude
            action_label: Action being performed (for error messages)

        Raises:
            ValidationError: If employee is outside geofence
        """
        org = self.db.get(Organization, org_id)
        if not org or (org.hr_attendance_mode or "").upper() != "GEOFENCED":
            return

        location = self._get_employee_location(org_id, employee_id)
        if not location:
            raise ValidationError("Branch location not configured for employee.")
        if not location.geofence_enabled:
            return
        if latitude is None or longitude is None:
            raise ValidationError("You are currently outside the check-in radius.")

        # Use polygon if configured, otherwise fall back to circle
        if location.geofence_polygon:
            # Polygon-based validation using GeoJSON
            is_inside = self._point_in_polygon(
                float(latitude),
                float(longitude),
                location.geofence_polygon,
            )

            if not is_inside:
                raise ValidationError(
                    f"You are outside the allowed work area for {action_label}. "
                    "Please move to the designated location."
                )
        else:
            # Circle-based validation using Haversine distance (default)
            if location.latitude is None or location.longitude is None:
                raise ValidationError("Branch location coordinates not configured.")

            distance = self._haversine_distance_m(
                float(latitude),
                float(longitude),
                float(location.latitude),
                float(location.longitude),
            )
            radius = float(location.geofence_radius_m or 0)
            if distance > radius:
                raise ValidationError(
                    f"You're not within the allowed radius for {action_label}. "
                    f"Distance: {distance:.0f}m, Allowed: {radius:.0f}m"
                )

    # =========================================================================
    # Shift Types
    # =========================================================================

    def list_shift_types(
        self,
        org_id: UUID,
        *,
        search: str | None = None,
        is_active: bool | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[ShiftType]:
        """List shift types for an organization."""
        query = select(ShiftType).where(ShiftType.organization_id == org_id)

        if search:
            search_term = f"%{search}%"
            query = query.where(
                or_(
                    ShiftType.shift_code.ilike(search_term),
                    ShiftType.shift_name.ilike(search_term),
                )
            )

        if is_active is not None:
            query = query.where(ShiftType.is_active == is_active)

        query = query.order_by(ShiftType.shift_name)

        # Count total
        count_query = select(func.count()).select_from(query.subquery())
        total = self.db.scalar(count_query) or 0

        # Apply pagination
        if pagination:
            query = query.offset(pagination.offset).limit(pagination.limit)

        items = list(self.db.scalars(query).all())

        return PaginatedResult(
            items=items,
            total=total,
            offset=pagination.offset if pagination else 0,
            limit=pagination.limit if pagination else len(items),
        )

    def get_shift_type(self, org_id: UUID, shift_type_id: UUID) -> ShiftType:
        """Get a shift type by ID."""
        shift_type = self.db.scalar(
            select(ShiftType).where(
                ShiftType.shift_type_id == shift_type_id,
                ShiftType.organization_id == org_id,
            )
        )
        if not shift_type:
            raise ShiftTypeNotFoundError(shift_type_id)
        return shift_type

    def create_shift_type(
        self,
        org_id: UUID,
        *,
        shift_code: str,
        shift_name: str,
        start_time: time,
        end_time: time,
        working_hours: Decimal | None = None,
        late_entry_grace_period: int = 0,
        early_exit_grace_period: int = 0,
        enable_half_day: bool = True,
        half_day_threshold_hours: Decimal | None = None,
        enable_overtime: bool = False,
        overtime_threshold_hours: Decimal | None = None,
        break_duration_minutes: int = 60,
        is_active: bool = True,
        description: str | None = None,
    ) -> ShiftType:
        """Create a new shift type."""
        # Calculate working hours if not provided
        if working_hours is None:
            start_dt = datetime.combine(date.today(), start_time)
            end_dt = datetime.combine(date.today(), end_time)
            if end_time <= start_time:
                end_dt += timedelta(days=1)
            delta = end_dt - start_dt
            working_hours = Decimal(str(delta.total_seconds() / 3600))

        shift_type = ShiftType(
            organization_id=org_id,
            shift_code=shift_code,
            shift_name=shift_name,
            start_time=start_time,
            end_time=end_time,
            working_hours=working_hours,
            late_entry_grace_period=late_entry_grace_period,
            early_exit_grace_period=early_exit_grace_period,
            enable_half_day=enable_half_day,
            half_day_threshold_hours=half_day_threshold_hours,
            enable_overtime=enable_overtime,
            overtime_threshold_hours=overtime_threshold_hours,
            break_duration_minutes=break_duration_minutes,
            is_active=is_active,
            description=description,
        )

        self.db.add(shift_type)
        self.db.flush()
        return shift_type

    def update_shift_type(
        self,
        org_id: UUID,
        shift_type_id: UUID,
        **kwargs,
    ) -> ShiftType:
        """Update a shift type."""
        shift_type = self.get_shift_type(org_id, shift_type_id)

        for key, value in kwargs.items():
            if value is not None and hasattr(shift_type, key):
                setattr(shift_type, key, value)

        self.db.flush()
        return shift_type

    def delete_shift_type(self, org_id: UUID, shift_type_id: UUID) -> None:
        """Delete a shift type (soft delete by deactivating)."""
        shift_type = self.get_shift_type(org_id, shift_type_id)
        shift_type.is_active = False
        self.db.flush()

    # =========================================================================
    # Shift Assignments
    # =========================================================================

    def list_shift_assignments(
        self,
        org_id: UUID,
        *,
        employee_id: UUID | None = None,
        shift_type_id: UUID | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[ShiftAssignment]:
        """List shift assignments for an organization."""
        query = select(ShiftAssignment).where(ShiftAssignment.organization_id == org_id)

        if employee_id:
            query = query.where(ShiftAssignment.employee_id == employee_id)

        if shift_type_id:
            query = query.where(ShiftAssignment.shift_type_id == shift_type_id)

        if start_date:
            query = query.where(ShiftAssignment.start_date >= start_date)

        if end_date:
            query = query.where(
                or_(
                    ShiftAssignment.end_date.is_(None),
                    ShiftAssignment.end_date <= end_date,
                )
            )

        query = query.order_by(ShiftAssignment.start_date.desc())

        count_query = select(func.count()).select_from(query.subquery())
        total = self.db.scalar(count_query) or 0

        if pagination:
            query = query.offset(pagination.offset).limit(pagination.limit)

        items = list(self.db.scalars(query).all())

        return PaginatedResult(
            items=items,
            total=total,
            offset=pagination.offset if pagination else 0,
            limit=pagination.limit if pagination else len(items),
        )

    def get_shift_assignment(
        self, org_id: UUID, shift_assignment_id: UUID
    ) -> ShiftAssignment:
        """Get a shift assignment by ID."""
        assignment = self.db.scalar(
            select(ShiftAssignment).where(
                ShiftAssignment.shift_assignment_id == shift_assignment_id,
                ShiftAssignment.organization_id == org_id,
            )
        )
        if not assignment:
            raise ShiftAssignmentNotFoundError(shift_assignment_id)
        return assignment

    def create_shift_assignment(
        self,
        org_id: UUID,
        *,
        employee_id: UUID,
        shift_type_id: UUID,
        start_date: date,
        end_date: date | None = None,
        is_active: bool = True,
    ) -> ShiftAssignment:
        """Create a shift assignment."""
        if end_date and end_date < start_date:
            raise AttendanceServiceError("end_date must be on or after start_date")

        assignment = ShiftAssignment(
            organization_id=org_id,
            employee_id=employee_id,
            shift_type_id=shift_type_id,
            start_date=start_date,
            end_date=end_date,
            is_active=is_active,
        )
        self.db.add(assignment)
        self.db.flush()
        return assignment

    def update_shift_assignment(
        self,
        org_id: UUID,
        shift_assignment_id: UUID,
        **kwargs,
    ) -> ShiftAssignment:
        """Update a shift assignment."""
        assignment = self.get_shift_assignment(org_id, shift_assignment_id)

        for key, value in kwargs.items():
            if value is not None and hasattr(assignment, key):
                setattr(assignment, key, value)

        if assignment.end_date and assignment.end_date < assignment.start_date:
            raise AttendanceServiceError("end_date must be on or after start_date")

        self.db.flush()
        return assignment

    def delete_shift_assignment(self, org_id: UUID, shift_assignment_id: UUID) -> None:
        """Deactivate a shift assignment."""
        assignment = self.get_shift_assignment(org_id, shift_assignment_id)
        assignment.is_active = False
        self.db.flush()

    # =========================================================================
    # Attendance Records
    # =========================================================================

    def list_attendance(
        self,
        org_id: UUID,
        *,
        employee_id: UUID | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
        status: AttendanceStatus | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[Attendance]:
        """List attendance records."""
        query = select(Attendance).where(Attendance.organization_id == org_id)

        if employee_id:
            query = query.where(Attendance.employee_id == employee_id)

        if from_date:
            query = query.where(Attendance.attendance_date >= from_date)

        if to_date:
            query = query.where(Attendance.attendance_date <= to_date)

        if status:
            status_value = status
            if isinstance(status, str):
                status_value = AttendanceStatus(status)
            query = query.where(Attendance.status == status_value)

        query = query.order_by(Attendance.attendance_date.desc())

        # Count total
        count_query = select(func.count()).select_from(query.subquery())
        total = self.db.scalar(count_query) or 0

        # Apply pagination
        if pagination:
            query = query.offset(pagination.offset).limit(pagination.limit)

        items = list(self.db.scalars(query).all())

        return PaginatedResult(
            items=items,
            total=total,
            offset=pagination.offset if pagination else 0,
            limit=pagination.limit if pagination else len(items),
        )

    def get_attendance(self, org_id: UUID, attendance_id: UUID) -> Attendance:
        """Get an attendance record by ID."""
        attendance = self.db.scalar(
            select(Attendance).where(
                Attendance.attendance_id == attendance_id,
                Attendance.organization_id == org_id,
            )
        )
        if not attendance:
            raise AttendanceNotFoundError(attendance_id)
        return attendance

    def get_attendance_by_date(
        self,
        org_id: UUID,
        employee_id: UUID,
        attendance_date: date,
    ) -> Attendance | None:
        """Get attendance for an employee on a specific date."""
        return self.db.scalar(
            select(Attendance).where(
                Attendance.organization_id == org_id,
                Attendance.employee_id == employee_id,
                Attendance.attendance_date == attendance_date,
            )
        )

    def create_attendance(
        self,
        org_id: UUID,
        *,
        employee_id: UUID,
        attendance_date: date,
        status: AttendanceStatus = AttendanceStatus.PRESENT,
        shift_type_id: UUID | None = None,
        check_in: datetime | None = None,
        check_out: datetime | None = None,
        working_hours: Decimal | None = None,
        late_entry: bool = False,
        early_exit: bool = False,
        remarks: str | None = None,
        marked_by: str = "MANUAL",
        leave_application_id: UUID | None = None,
    ) -> Attendance:
        """Create an attendance record."""
        # Check for duplicate
        existing = self.get_attendance_by_date(org_id, employee_id, attendance_date)
        if existing:
            raise DuplicateAttendanceError(employee_id, attendance_date)

        # Manual/web inputs often arrive as timezone-naive local values.
        # Normalize them to the organization's timezone so display round-trips
        # do not shift by timezone offset.
        if check_in:
            check_in = self._normalize_in_org_tz(org_id, check_in)
        if check_out:
            check_out = self._normalize_in_org_tz(org_id, check_out)

        # Calculate working hours if check-in and check-out provided
        if working_hours is None and check_in and check_out:
            delta = check_out - check_in
            working_hours = Decimal(str(delta.total_seconds() / 3600))

        attendance = Attendance(
            organization_id=org_id,
            employee_id=employee_id,
            attendance_date=attendance_date,
            status=status,
            shift_type_id=shift_type_id,
            check_in=check_in,
            check_out=check_out,
            working_hours=working_hours or Decimal("0"),
            late_entry=late_entry,
            early_exit=early_exit,
            remarks=remarks,
            marked_by=marked_by,
            leave_application_id=leave_application_id,
        )

        self.db.add(attendance)
        self.db.flush()
        return attendance

    def check_in(
        self,
        org_id: UUID,
        employee_id: UUID,
        *,
        check_in_time: datetime | None = None,
        shift_type_id: UUID | None = None,
        notes: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
    ) -> Attendance:
        """Record employee check-in."""
        now = (
            self._normalize_in_org_tz(org_id, check_in_time)
            if check_in_time
            else self._now_in_org_tz(org_id)
        )
        today = now.date()

        # Check if already checked in
        existing = self.get_attendance_by_date(org_id, employee_id, today)
        if existing and existing.check_in:
            raise AttendanceServiceError(
                f"Employee already checked in at {existing.check_in}"
            )

        self._validate_geofence(
            org_id,
            employee_id,
            latitude,
            longitude,
            action_label="check in",
        )

        # Determine if late entry
        late_entry = False
        if shift_type_id:
            shift = self.get_shift_type(org_id, shift_type_id)
            tzinfo = now.tzinfo
            shift_start = self._combine_date_time(today, shift.start_time, tzinfo)
            grace_end = shift_start + timedelta(minutes=shift.late_entry_grace_period)
            late_entry = now > grace_end

        if existing:
            existing.check_in = now
            existing.late_entry = late_entry
            existing.status = AttendanceStatus.PRESENT
            if notes:
                existing.remarks = notes
            self.db.flush()
            return existing

        return self.create_attendance(
            org_id,
            employee_id=employee_id,
            attendance_date=today,
            status=AttendanceStatus.PRESENT,
            shift_type_id=shift_type_id,
            check_in=now,
            late_entry=late_entry,
            remarks=notes,
        )

    def check_out(
        self,
        org_id: UUID,
        employee_id: UUID,
        *,
        check_out_time: datetime | None = None,
        notes: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
    ) -> Attendance:
        """Record employee check-out."""
        now = (
            self._normalize_in_org_tz(org_id, check_out_time)
            if check_out_time
            else self._now_in_org_tz(org_id)
        )
        today = now.date()

        attendance = self.get_attendance_by_date(org_id, employee_id, today)
        if not attendance:
            raise AttendanceServiceError(f"No check-in found for {today}")

        if not attendance.check_in:
            raise AttendanceServiceError("Cannot check out without checking in first")

        self._validate_geofence(
            org_id,
            employee_id,
            latitude,
            longitude,
            action_label="check out",
        )

        # Determine if early exit
        early_exit = False
        if attendance.shift_type_id:
            shift = self.get_shift_type(org_id, attendance.shift_type_id)
            tzinfo = now.tzinfo
            shift_end = self._combine_date_time(today, shift.end_time, tzinfo)
            if shift.end_time <= shift.start_time:
                shift_end += timedelta(days=1)
            grace_start = shift_end - timedelta(minutes=shift.early_exit_grace_period)
            early_exit = now < grace_start

        attendance.check_out = now
        attendance.early_exit = early_exit
        if notes:
            attendance.remarks = notes

        # Calculate working hours
        if attendance.check_in:
            check_in_dt = self._normalize_dt(attendance.check_in)
            now_dt = self._normalize_dt(now)
            delta = now_dt - check_in_dt
            attendance.working_hours = Decimal(str(delta.total_seconds() / 3600))

        self.db.flush()
        return attendance

    def check_in_by_attendance_id(
        self,
        org_id: UUID,
        attendance_id: UUID,
        *,
        check_in_time: datetime | None = None,
        notes: str | None = None,
    ) -> Attendance:
        """Record check-in against an existing attendance record."""
        attendance = self.get_attendance(org_id, attendance_id)
        if attendance.check_in:
            raise AttendanceServiceError(
                f"Attendance already checked in at {attendance.check_in}"
            )

        if check_in_time:
            tzinfo = attendance.check_in.tzinfo if attendance.check_in else None
            if check_in_time.tzinfo is None:
                check_in_time = check_in_time.replace(
                    tzinfo=tzinfo or self._org_tzinfo(org_id)
                )
            now = check_in_time
        else:
            now = self._now_like(attendance.check_in)
        late_entry = False
        if attendance.shift_type_id:
            shift = self.get_shift_type(org_id, attendance.shift_type_id)
            tzinfo = now.tzinfo
            shift_start = self._combine_date_time(
                attendance.attendance_date, shift.start_time, tzinfo
            )
            grace_end = shift_start + timedelta(minutes=shift.late_entry_grace_period)
            late_entry = now > grace_end

        attendance.check_in = now
        attendance.late_entry = late_entry
        attendance.status = AttendanceStatus.PRESENT
        if notes:
            attendance.remarks = notes

        self.db.flush()
        return attendance

    def check_out_by_attendance_id(
        self,
        org_id: UUID,
        attendance_id: UUID,
        *,
        check_out_time: datetime | None = None,
        notes: str | None = None,
    ) -> Attendance:
        """Record check-out against an existing attendance record."""
        attendance = self.get_attendance(org_id, attendance_id)
        if not attendance.check_in:
            raise AttendanceServiceError("Cannot check out without checking in first")
        if attendance.check_out:
            raise AttendanceServiceError(
                f"Attendance already checked out at {attendance.check_out}"
            )

        if check_out_time:
            tzinfo = attendance.check_in.tzinfo if attendance.check_in else None
            if check_out_time.tzinfo is None:
                check_out_time = check_out_time.replace(
                    tzinfo=tzinfo or self._org_tzinfo(org_id)
                )
            now = check_out_time
        else:
            now = self._now_like(attendance.check_in)
        early_exit = False
        if attendance.shift_type_id:
            shift = self.get_shift_type(org_id, attendance.shift_type_id)
            tzinfo = now.tzinfo
            shift_end = self._combine_date_time(
                attendance.attendance_date, shift.end_time, tzinfo
            )
            if shift.end_time <= shift.start_time:
                shift_end += timedelta(days=1)
            grace_start = shift_end - timedelta(minutes=shift.early_exit_grace_period)
            early_exit = now < grace_start

        attendance.check_out = now
        attendance.early_exit = early_exit
        if notes:
            attendance.remarks = notes

        if attendance.check_in:
            check_in_dt = self._normalize_dt(attendance.check_in)
            now_dt = self._normalize_dt(now)
            delta = now_dt - check_in_dt
            attendance.working_hours = Decimal(str(delta.total_seconds() / 3600))

        self.db.flush()
        return attendance

    def bulk_mark_attendance(
        self,
        org_id: UUID,
        *,
        attendance_date: date,
        records: list[dict] | None = None,
        employee_ids: list[UUID] | None = None,
        status: AttendanceStatus = AttendanceStatus.PRESENT,
        shift_type_id: UUID | None = None,
        remarks: str | None = None,
    ) -> dict:
        """Bulk mark attendance for multiple employees."""
        if records is None:
            records = []
            if employee_ids:
                for emp_id in employee_ids:
                    records.append(
                        {
                            "employee_id": emp_id,
                            "status": status.value
                            if isinstance(status, AttendanceStatus)
                            else status,
                            "shift_type_id": shift_type_id,
                            "notes": remarks,
                        }
                    )

        success_count = 0
        failed_count = 0
        errors: list[dict] = []

        for record in records:
            employee_id = record["employee_id"]
            status_value = record.get("status", "PRESENT")
            record_status = (
                status_value
                if isinstance(status_value, AttendanceStatus)
                else AttendanceStatus(status_value)
            )

            try:
                existing = self.get_attendance_by_date(
                    org_id, employee_id, attendance_date
                )
                if existing:
                    existing.status = record_status
                    if "notes" in record and record["notes"]:
                        existing.remarks = record["notes"]
                    success_count += 1
                else:
                    self.create_attendance(
                        org_id,
                        employee_id=employee_id,
                        attendance_date=attendance_date,
                        status=record_status,
                        shift_type_id=record.get("shift_type_id"),
                        remarks=record.get("notes"),
                    )
                    success_count += 1
            except DuplicateAttendanceError:
                failed_count += 1
                errors.append(
                    {
                        "employee_id": str(employee_id),
                        "reason": "Attendance already exists for this date",
                    }
                )

        self.db.flush()
        return {
            "success_count": success_count,
            "failed_count": failed_count,
            "errors": errors,
        }

    def update_attendance(
        self,
        org_id: UUID,
        attendance_id: UUID,
        **kwargs,
    ) -> Attendance:
        """Update an attendance record."""
        attendance = self.get_attendance(org_id, attendance_id)

        for key, value in kwargs.items():
            if value is not None and hasattr(attendance, key):
                setattr(attendance, key, value)

        # Recalculate working hours if times changed
        if attendance.check_in and attendance.check_out:
            delta = attendance.check_out - attendance.check_in
            attendance.working_hours = Decimal(str(delta.total_seconds() / 3600))

        self.db.flush()
        return attendance

    def delete_attendance(self, org_id: UUID, attendance_id: UUID) -> None:
        """Delete an attendance record.

        Can only delete manually marked attendance, not system-generated records.
        """
        attendance = self.get_attendance(org_id, attendance_id)

        # Prevent deletion of leave-linked attendance
        if attendance.leave_application_id:
            raise AttendanceServiceError(
                "Cannot delete attendance linked to a leave application. "
                "Cancel the leave application instead."
            )

        self.db.delete(attendance)
        self.db.flush()

    # =========================================================================
    # Attendance Requests
    # =========================================================================

    def list_attendance_requests(
        self,
        org_id: UUID,
        *,
        employee_id: UUID | None = None,
        status: AttendanceRequestStatus | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[AttendanceRequest]:
        """List attendance requests with filters."""
        query = select(AttendanceRequest).where(
            AttendanceRequest.organization_id == org_id
        )

        if employee_id:
            query = query.where(AttendanceRequest.employee_id == employee_id)

        if status:
            status_value = status
            if isinstance(status, str):
                status_value = AttendanceRequestStatus(status)
            query = query.where(AttendanceRequest.status == status_value)

        if from_date:
            query = query.where(AttendanceRequest.from_date >= from_date)

        if to_date:
            query = query.where(AttendanceRequest.to_date <= to_date)

        query = query.order_by(AttendanceRequest.created_at.desc())

        count_query = select(func.count()).select_from(query.subquery())
        total = self.db.scalar(count_query) or 0

        if pagination:
            query = query.offset(pagination.offset).limit(pagination.limit)

        items = list(self.db.scalars(query).all())

        return PaginatedResult(
            items=items,
            total=total,
            offset=pagination.offset if pagination else 0,
            limit=pagination.limit if pagination else len(items),
        )

    def get_attendance_request(
        self, org_id: UUID, request_id: UUID
    ) -> AttendanceRequest:
        """Get an attendance request by ID."""
        request = self.db.scalar(
            select(AttendanceRequest).where(
                AttendanceRequest.attendance_request_id == request_id,
                AttendanceRequest.organization_id == org_id,
            )
        )
        if not request:
            raise AttendanceRequestNotFoundError(request_id)
        return request

    def create_attendance_request(
        self,
        org_id: UUID,
        *,
        employee_id: UUID,
        from_date: date,
        to_date: date,
        half_day: bool = False,
        half_day_date: date | None = None,
        reason: str | None = None,
        explanation: str | None = None,
    ) -> AttendanceRequest:
        """Create a new attendance request."""
        if to_date < from_date:
            raise AttendanceServiceError("from_date must be on or before to_date")
        if half_day and not half_day_date:
            raise AttendanceServiceError(
                "half_day_date is required when half_day is true"
            )

        request = AttendanceRequest(
            organization_id=org_id,
            employee_id=employee_id,
            from_date=from_date,
            to_date=to_date,
            half_day=half_day,
            half_day_date=half_day_date,
            reason=reason,
            explanation=explanation,
            status=AttendanceRequestStatus.PENDING,
        )
        self.db.add(request)
        self.db.flush()
        return request

    def update_attendance_request(
        self,
        org_id: UUID,
        request_id: UUID,
        **kwargs,
    ) -> AttendanceRequest:
        """Update an attendance request."""
        request = self.get_attendance_request(org_id, request_id)

        for key, value in kwargs.items():
            if value is not None and hasattr(request, key):
                setattr(request, key, value)

        if request.to_date < request.from_date:
            raise AttendanceServiceError("from_date must be on or before to_date")
        if request.half_day and not request.half_day_date:
            raise AttendanceServiceError(
                "half_day_date is required when half_day is true"
            )

        self.db.flush()
        return request

    def submit_attendance_request(
        self, org_id: UUID, request_id: UUID
    ) -> AttendanceRequest:
        """Submit an attendance request for approval."""
        request = self.get_attendance_request(org_id, request_id)
        if request.status != AttendanceRequestStatus.DRAFT:
            raise AttendanceRequestStatusError(
                request_id,
                request.status.value,
                AttendanceRequestStatus.PENDING.value,
            )
        request.status = AttendanceRequestStatus.PENDING
        request.status_changed_at = datetime.now()
        self.db.flush()
        return request

    def approve_attendance_request(
        self, org_id: UUID, request_id: UUID
    ) -> AttendanceRequest:
        """Approve an attendance request and create attendance records."""
        request = self.get_attendance_request(org_id, request_id)
        if request.status != AttendanceRequestStatus.PENDING:
            raise AttendanceRequestStatusError(
                request_id,
                request.status.value,
                AttendanceRequestStatus.APPROVED.value,
            )

        request.status = AttendanceRequestStatus.APPROVED
        request.status_changed_at = datetime.now()
        self._process_attendance_request(org_id, request)
        self.db.flush()
        return request

    def reject_attendance_request(
        self, org_id: UUID, request_id: UUID
    ) -> AttendanceRequest:
        """Reject an attendance request."""
        request = self.get_attendance_request(org_id, request_id)
        if request.status != AttendanceRequestStatus.PENDING:
            raise AttendanceRequestStatusError(
                request_id,
                request.status.value,
                AttendanceRequestStatus.REJECTED.value,
            )

        request.status = AttendanceRequestStatus.REJECTED
        request.status_changed_at = datetime.now()
        self.db.flush()
        return request

    def delete_attendance_request(self, org_id: UUID, request_id: UUID) -> None:
        """Delete an attendance request."""
        request = self.get_attendance_request(org_id, request_id)
        self.db.delete(request)
        self.db.flush()

    def bulk_approve_attendance_requests(
        self, org_id: UUID, request_ids: list[UUID]
    ) -> dict:
        """Bulk approve attendance requests."""
        updated = 0
        for req_id in request_ids:
            try:
                self.approve_attendance_request(org_id, req_id)
                updated += 1
            except (AttendanceRequestNotFoundError, AttendanceRequestStatusError):
                continue
        return {"updated": updated, "requested": len(request_ids)}

    def bulk_reject_attendance_requests(
        self, org_id: UUID, request_ids: list[UUID]
    ) -> dict:
        """Bulk reject attendance requests."""
        updated = 0
        for req_id in request_ids:
            try:
                self.reject_attendance_request(org_id, req_id)
                updated += 1
            except (AttendanceRequestNotFoundError, AttendanceRequestStatusError):
                continue
        return {"updated": updated, "requested": len(request_ids)}

    def _process_attendance_request(
        self, org_id: UUID, request: AttendanceRequest
    ) -> None:
        """Create or update attendance records for an approved request."""
        current_date = request.from_date
        while current_date <= request.to_date:
            status = AttendanceStatus.PRESENT
            if request.half_day and current_date == request.half_day_date:
                status = AttendanceStatus.HALF_DAY

            existing = self.get_attendance_by_date(
                org_id, request.employee_id, current_date
            )
            if existing:
                existing.status = status
            else:
                attendance = Attendance(
                    organization_id=org_id,
                    employee_id=request.employee_id,
                    attendance_date=current_date,
                    status=status,
                    marked_by="REQUEST",
                )
                self.db.add(attendance)

            current_date += timedelta(days=1)

        self.db.flush()

    # =========================================================================
    # Reporting
    # =========================================================================

    def get_attendance_summary(
        self,
        org_id: UUID,
        *,
        employee_id: UUID | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
    ) -> dict:
        """Get attendance status summary for a date range."""
        query = select(Attendance).where(Attendance.organization_id == org_id)

        if employee_id:
            query = query.where(Attendance.employee_id == employee_id)

        if from_date:
            query = query.where(Attendance.attendance_date >= from_date)

        if to_date:
            query = query.where(Attendance.attendance_date <= to_date)

        records = list(self.db.scalars(query).all())

        status_counts: dict[str, int] = {}
        late_entries = 0
        early_exits = 0
        for record in records:
            status_key = record.status.value if record.status else "UNKNOWN"
            status_counts[status_key] = status_counts.get(status_key, 0) + 1
            if record.late_entry:
                late_entries += 1
            if record.early_exit:
                early_exits += 1

        return {
            "status_counts": status_counts,
            "late_entries": late_entries,
            "early_exits": early_exits,
        }

    def get_employee_monthly_summary(
        self,
        org_id: UUID,
        employee_id: UUID,
        year: int,
        month: int,
    ) -> dict:
        """Get monthly attendance summary for an employee."""
        start_date = date(year, month, 1)
        if month == 12:
            end_date = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            end_date = date(year, month + 1, 1) - timedelta(days=1)

        records = self.db.scalars(
            select(Attendance).where(
                Attendance.organization_id == org_id,
                Attendance.employee_id == employee_id,
                Attendance.attendance_date >= start_date,
                Attendance.attendance_date <= end_date,
            )
        ).all()

        total_days = (end_date - start_date).days + 1
        present_count = sum(1 for r in records if r.status == AttendanceStatus.PRESENT)
        absent_count = sum(1 for r in records if r.status == AttendanceStatus.ABSENT)
        half_day_count = sum(
            1 for r in records if r.status == AttendanceStatus.HALF_DAY
        )
        on_leave_count = sum(
            1 for r in records if r.status == AttendanceStatus.ON_LEAVE
        )
        late_count = sum(1 for r in records if r.late_entry)
        early_exit_count = sum(1 for r in records if r.early_exit)
        total_hours = sum((r.working_hours or Decimal("0")) for r in records)

        return {
            "employee_id": employee_id,
            "year": year,
            "month": month,
            "total_days": total_days,
            "present": present_count,
            "absent": absent_count,
            "half_day": half_day_count,
            "on_leave": on_leave_count,
            "late_entries": late_count,
            "early_exits": early_exit_count,
            "total_working_hours": total_hours,
            "attendance_percentage": round(
                (present_count + half_day_count * Decimal("0.5")) / total_days * 100, 2
            )
            if total_days > 0
            else Decimal("0"),
        }

    def get_daily_summary(
        self,
        org_id: UUID,
        attendance_date: date,
    ) -> dict:
        """Get daily attendance summary for an organization."""
        records = self.db.scalars(
            select(Attendance).where(
                Attendance.organization_id == org_id,
                Attendance.attendance_date == attendance_date,
            )
        ).all()

        present = sum(1 for r in records if r.status == AttendanceStatus.PRESENT)
        absent = sum(1 for r in records if r.status == AttendanceStatus.ABSENT)
        half_day = sum(1 for r in records if r.status == AttendanceStatus.HALF_DAY)
        on_leave = sum(1 for r in records if r.status == AttendanceStatus.ON_LEAVE)
        late = sum(1 for r in records if r.late_entry)

        return {
            "date": attendance_date,
            "total_records": len(records),
            "present": present,
            "absent": absent,
            "half_day": half_day,
            "on_leave": on_leave,
            "late_entries": late,
        }

    def get_attendance_stats(self, org_id: UUID) -> dict:
        """Get attendance statistics for dashboard."""
        today = self.get_org_today(org_id)

        # Today's attendance
        today_present = (
            self.db.scalar(
                select(func.count(Attendance.attendance_id)).where(
                    Attendance.organization_id == org_id,
                    Attendance.attendance_date == today,
                    Attendance.status == AttendanceStatus.PRESENT,
                )
            )
            or 0
        )

        today_absent = (
            self.db.scalar(
                select(func.count(Attendance.attendance_id)).where(
                    Attendance.organization_id == org_id,
                    Attendance.attendance_date == today,
                    Attendance.status == AttendanceStatus.ABSENT,
                )
            )
            or 0
        )

        today_late = (
            self.db.scalar(
                select(func.count(Attendance.attendance_id)).where(
                    Attendance.organization_id == org_id,
                    Attendance.attendance_date == today,
                    Attendance.late_entry == True,
                )
            )
            or 0
        )

        return {
            "today_present": today_present,
            "today_absent": today_absent,
            "today_late": today_late,
            "date": today,
        }

    # =========================================================================
    # Report Methods
    # =========================================================================

    def get_attendance_summary_report(
        self,
        org_id: UUID,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
        department_id: UUID | None = None,
    ) -> dict:
        """
        Get comprehensive attendance summary report.

        Returns status breakdown, late/early stats, and working hours summary.
        """
        from app.models.people.hr import Employee

        today = self.get_org_today(org_id)
        if not start_date:
            start_date = today.replace(day=1)
        if not end_date:
            end_date = today

        # Base query
        query = (
            select(
                func.count(Attendance.attendance_id).label("total_records"),
                func.count(
                    case((Attendance.status == AttendanceStatus.PRESENT, 1))
                ).label("present"),
                func.count(
                    case((Attendance.status == AttendanceStatus.ABSENT, 1))
                ).label("absent"),
                func.count(
                    case((Attendance.status == AttendanceStatus.HALF_DAY, 1))
                ).label("half_day"),
                func.count(
                    case((Attendance.status == AttendanceStatus.ON_LEAVE, 1))
                ).label("on_leave"),
                func.count(case((Attendance.late_entry == True, 1))).label(
                    "late_entries"
                ),
                func.count(case((Attendance.early_exit == True, 1))).label(
                    "early_exits"
                ),
                func.sum(Attendance.working_hours).label("total_working_hours"),
                func.sum(Attendance.overtime_hours).label("total_overtime_hours"),
            )
            .join(Employee, Employee.employee_id == Attendance.employee_id)
            .where(
                Attendance.organization_id == org_id,
                Attendance.attendance_date >= start_date,
                Attendance.attendance_date <= end_date,
            )
        )

        if department_id:
            query = query.where(Employee.department_id == department_id)

        result = self.db.execute(query).one()

        # Calculate attendance percentage
        total = result.total_records or 0
        present = result.present or 0
        half_day = result.half_day or 0
        attendance_pct = ((present + half_day * 0.5) / total * 100) if total > 0 else 0

        return {
            "start_date": start_date,
            "end_date": end_date,
            "total_records": total,
            "present": present,
            "absent": result.absent or 0,
            "half_day": half_day,
            "on_leave": result.on_leave or 0,
            "late_entries": result.late_entries or 0,
            "early_exits": result.early_exits or 0,
            "total_working_hours": result.total_working_hours or Decimal("0"),
            "total_overtime_hours": result.total_overtime_hours or Decimal("0"),
            "attendance_percentage": round(attendance_pct, 1),
        }

    def get_attendance_by_employee_report(
        self,
        org_id: UUID,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
        department_id: UUID | None = None,
    ) -> dict:
        """
        Get attendance breakdown by employee.

        Returns list of employees with their attendance metrics.
        """
        from app.models.people.hr import Department, Employee
        from app.models.person import Person

        today = self.get_org_today(org_id)
        if not start_date:
            start_date = today.replace(day=1)
        if not end_date:
            end_date = today

        # Query by employee
        query = (
            select(
                Employee.employee_id,
                Person.first_name,
                Person.last_name,
                Department.department_name.label("department_name"),
                func.count(Attendance.attendance_id).label("total_days"),
                func.count(
                    case((Attendance.status == AttendanceStatus.PRESENT, 1))
                ).label("present"),
                func.count(
                    case((Attendance.status == AttendanceStatus.ABSENT, 1))
                ).label("absent"),
                func.count(case((Attendance.late_entry == True, 1))).label(
                    "late_entries"
                ),
                func.count(case((Attendance.early_exit == True, 1))).label(
                    "early_exits"
                ),
                func.sum(Attendance.working_hours).label("total_hours"),
                func.sum(Attendance.overtime_hours).label("overtime_hours"),
            )
            .join(Attendance, Attendance.employee_id == Employee.employee_id)
            .join(Person, Employee.person_id == Person.id)
            .outerjoin(Department, Employee.department_id == Department.department_id)
            .where(
                Attendance.organization_id == org_id,
                Attendance.attendance_date >= start_date,
                Attendance.attendance_date <= end_date,
            )
        )

        if department_id:
            query = query.where(Employee.department_id == department_id)

        results = self.db.execute(
            query.group_by(
                Employee.employee_id,
                Person.first_name,
                Person.last_name,
                Department.department_name,
            )
        ).all()

        employees = []
        for row in results:
            total_days = row.total_days or 0
            present = row.present or 0
            attendance_pct = (present / total_days * 100) if total_days > 0 else 0
            employees.append(
                {
                    "employee_id": str(row.employee_id),
                    "employee_name": f"{row.first_name} {row.last_name}",
                    "department_name": row.department_name or "No Department",
                    "total_days": total_days,
                    "present": present,
                    "absent": row.absent or 0,
                    "late_entries": row.late_entries or 0,
                    "early_exits": row.early_exits or 0,
                    "total_hours": row.total_hours or Decimal("0"),
                    "overtime_hours": row.overtime_hours or Decimal("0"),
                    "attendance_percentage": round(attendance_pct, 1),
                }
            )

        # Sort by attendance percentage descending
        employees.sort(key=lambda x: x["attendance_percentage"], reverse=True)

        return {
            "start_date": start_date,
            "end_date": end_date,
            "employees": employees,
            "total_employees": len(employees),
        }

    def get_late_early_report(
        self,
        org_id: UUID,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
        department_id: UUID | None = None,
    ) -> dict:
        """
        Get detailed late arrivals and early departures report.

        Returns list of late/early records with employee details.
        """
        from app.models.people.hr import Department, Employee
        from app.models.person import Person

        today = self.get_org_today(org_id)
        if not start_date:
            start_date = today.replace(day=1)
        if not end_date:
            end_date = today

        # Query late/early records
        query = (
            select(
                Attendance,
                Person.first_name,
                Person.last_name,
                Department.department_name.label("department_name"),
            )
            .join(Employee, Employee.employee_id == Attendance.employee_id)
            .join(Person, Employee.person_id == Person.id)
            .outerjoin(Department, Employee.department_id == Department.department_id)
            .where(
                Attendance.organization_id == org_id,
                Attendance.attendance_date >= start_date,
                Attendance.attendance_date <= end_date,
                or_(Attendance.late_entry == True, Attendance.early_exit == True),
            )
        )

        if department_id:
            query = query.where(Employee.department_id == department_id)

        results = self.db.execute(
            query.order_by(Attendance.attendance_date.desc())
        ).all()

        late_entries = []
        early_exits = []

        for attendance, first_name, last_name, dept_name in results:
            record = {
                "attendance_id": str(attendance.attendance_id),
                "employee_name": f"{first_name} {last_name}",
                "department_name": dept_name or "No Department",
                "date": attendance.attendance_date.isoformat(),
                "check_in": attendance.check_in.strftime("%H:%M")
                if attendance.check_in
                else None,
                "check_out": attendance.check_out.strftime("%H:%M")
                if attendance.check_out
                else None,
            }

            if attendance.late_entry:
                late_entries.append(record)
            if attendance.early_exit:
                early_exits.append(record)

        return {
            "start_date": start_date,
            "end_date": end_date,
            "late_entries": late_entries,
            "early_exits": early_exits,
            "total_late": len(late_entries),
            "total_early": len(early_exits),
        }

    def get_attendance_trends_report(
        self,
        org_id: UUID,
        *,
        months: int = 12,
    ) -> dict:
        """
        Get attendance trends over time.

        Returns monthly breakdown of attendance metrics.
        """
        from dateutil.relativedelta import relativedelta

        today = self.get_org_today(org_id)
        end_date = today.replace(day=1)
        start_date = end_date - relativedelta(months=months - 1)

        # Query monthly aggregates
        month_bucket = func.date_trunc(
            literal_column("'month'"),
            Attendance.attendance_date,
        ).label("month")
        results = self.db.execute(
            select(
                month_bucket,
                func.count(Attendance.attendance_id).label("total_records"),
                func.count(
                    case((Attendance.status == AttendanceStatus.PRESENT, 1))
                ).label("present"),
                func.count(
                    case((Attendance.status == AttendanceStatus.ABSENT, 1))
                ).label("absent"),
                func.count(case((Attendance.late_entry == True, 1))).label(
                    "late_entries"
                ),
                func.sum(Attendance.working_hours).label("total_hours"),
            )
            .where(
                Attendance.organization_id == org_id,
                Attendance.attendance_date >= start_date,
                Attendance.attendance_date <= today,
            )
            .group_by(month_bucket)
            .order_by(month_bucket)
        ).all()

        # Build results dict by month
        monthly_data = {}
        for row in results:
            month_key = row.month.strftime("%Y-%m")
            total = row.total_records or 0
            present = row.present or 0
            att_pct = (present / total * 100) if total > 0 else 0
            monthly_data[month_key] = {
                "month": month_key,
                "month_label": row.month.strftime("%b %Y"),
                "total_records": total,
                "present": present,
                "absent": row.absent or 0,
                "late_entries": row.late_entries or 0,
                "total_hours": row.total_hours or Decimal("0"),
                "attendance_percentage": round(att_pct, 1),
            }

        # Fill in missing months with zeros
        months_list = []
        current = start_date
        total_records = 0
        total_present = 0

        while current <= today:
            month_key = current.strftime("%Y-%m")
            if month_key in monthly_data:
                months_list.append(monthly_data[month_key])
                total_records += monthly_data[month_key]["total_records"]
                total_present += monthly_data[month_key]["present"]
            else:
                months_list.append(
                    {
                        "month": month_key,
                        "month_label": current.strftime("%b %Y"),
                        "total_records": 0,
                        "present": 0,
                        "absent": 0,
                        "late_entries": 0,
                        "total_hours": Decimal("0"),
                        "attendance_percentage": 0,
                    }
                )
            current = current + relativedelta(months=1)

        num_months = len(months_list)
        average_attendance_pct = (
            (total_present / total_records * 100) if total_records > 0 else 0
        )

        return {
            "months": months_list,
            "total_months": num_months,
            "total_records": total_records,
            "total_present": total_present,
            "average_attendance_percentage": round(average_attendance_pct, 1),
        }
