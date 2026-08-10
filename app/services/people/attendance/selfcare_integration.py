"""Selfcare attendance adapter around the canonical AttendanceService."""

from __future__ import annotations

import logging
from datetime import timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.audit.audit_log import AuditAction
from app.models.finance.core_org.organization import Organization
from app.models.people.hr.employee import Employee, EmployeeStatus
from app.schemas.sync.sub_attendance import (
    SelfcareAttendanceLocation,
    SelfcareAttendanceRead,
    SelfcareAttendanceState,
)
from app.services.audit_dispatcher import fire_audit_event
from app.services.common import ValidationError
from app.services.people.attendance.attendance_service import (
    AttendanceService,
    AttendanceServiceError,
)

logger = logging.getLogger(__name__)


class SelfcareAttendanceError(Exception):
    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class SelfcareAttendanceIntegrationService:
    """Resolve trusted Selfcare subjects and delegate punches to ERP."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.attendance = AttendanceService(db)

    def resolve_employee(self, organization_id: UUID, subject: UUID) -> Employee:
        matches = list(
            self.db.scalars(
                select(Employee)
                .where(
                    Employee.organization_id == organization_id,
                    Employee.dotmac_sub_account_id == str(subject),
                )
                .limit(2)
            ).all()
        )
        if not matches:
            raise SelfcareAttendanceError(
                "employee_not_linked",
                "Attendance is not available for this account.",
                status_code=404,
            )
        if len(matches) != 1:
            logger.error(
                "Duplicate Selfcare employee mapping rejected: org=%s subject=%s",
                organization_id,
                subject,
            )
            raise SelfcareAttendanceError(
                "employee_mapping_ambiguous",
                "Attendance is not available for this account.",
                status_code=409,
            )
        employee = matches[0]
        if employee.status != EmployeeStatus.ACTIVE:
            raise SelfcareAttendanceError(
                "employee_inactive",
                "Attendance is not available for inactive employees.",
                status_code=403,
            )
        if not employee.dotmac_sub_access_enabled:
            raise SelfcareAttendanceError(
                "attendance_disabled",
                "Attendance is not enabled for this account.",
                status_code=403,
            )
        return employee

    def today(self, organization_id: UUID, subject: UUID) -> SelfcareAttendanceRead:
        employee = self.resolve_employee(organization_id, subject)
        return self._state(organization_id, employee)

    def check_in(
        self,
        organization_id: UUID,
        subject: UUID,
        location: SelfcareAttendanceLocation,
        *,
        request_id: str | None,
        service_person_id: UUID | None,
    ) -> SelfcareAttendanceRead:
        employee = self.resolve_employee(organization_id, subject)
        self._reject_overnight(organization_id, employee)
        try:
            record = self.attendance.check_in(
                organization_id,
                employee.employee_id,
                latitude=location.latitude,
                longitude=location.longitude,
                marked_by="SELFCARE",
            )
        except (AttendanceServiceError, ValidationError) as exc:
            raise self._map_domain_error(exc) from exc
        self._audit(
            organization_id,
            subject,
            employee,
            record.attendance_id,
            "check_in",
            request_id,
            location.accuracy_m,
            service_person_id,
        )
        return self._state(organization_id, employee)

    def check_out(
        self,
        organization_id: UUID,
        subject: UUID,
        location: SelfcareAttendanceLocation,
        *,
        request_id: str | None,
        service_person_id: UUID | None,
    ) -> SelfcareAttendanceRead:
        employee = self.resolve_employee(organization_id, subject)
        self._reject_overnight(organization_id, employee)
        try:
            record = self.attendance.check_out(
                organization_id,
                employee.employee_id,
                latitude=location.latitude,
                longitude=location.longitude,
            )
        except (AttendanceServiceError, ValidationError) as exc:
            raise self._map_domain_error(exc) from exc
        self._audit(
            organization_id,
            subject,
            employee,
            record.attendance_id,
            "check_out",
            request_id,
            location.accuracy_m,
            service_person_id,
        )
        return self._state(organization_id, employee)

    def _state(
        self, organization_id: UUID, employee: Employee
    ) -> SelfcareAttendanceRead:
        timezone_name = self._timezone_name(organization_id)
        today = self.attendance.get_org_today(organization_id)
        overnight_reason = self._overnight_reason(organization_id, employee)
        record = self.attendance.get_attendance_by_date(
            organization_id, employee.employee_id, today
        )
        if overnight_reason:
            state = SelfcareAttendanceState.INELIGIBLE
            actions: list[str] = []
        elif not record or not record.check_in:
            state = SelfcareAttendanceState.NOT_CHECKED_IN
            actions = ["check_in"]
        elif record.check_out:
            state = SelfcareAttendanceState.CHECKED_OUT
            actions = []
        else:
            state = SelfcareAttendanceState.CHECKED_IN
            actions = ["check_out"]

        tzinfo = self.attendance.get_org_tzinfo(organization_id)
        return SelfcareAttendanceRead(
            state=state,
            attendance_date=today,
            timezone=timezone_name,
            check_in_at=(
                record.check_in.astimezone(tzinfo)
                if record and record.check_in
                else None
            ),
            check_out_at=(
                record.check_out.astimezone(tzinfo)
                if record and record.check_out
                else None
            ),
            working_hours=record.working_hours if record and record.check_out else None,
            status=record.status.value if record else None,
            allowed_actions=actions,
            reason=overnight_reason,
        )

    def _timezone_name(self, organization_id: UUID) -> str:
        organization = self.db.get(Organization, organization_id)
        return (
            organization.timezone if organization and organization.timezone else "UTC"
        )

    def _overnight_reason(
        self, organization_id: UUID, employee: Employee
    ) -> str | None:
        today = self.attendance.get_org_today(organization_id)
        yesterday = today - timedelta(days=1)
        previous = self.attendance.get_attendance_by_date(
            organization_id, employee.employee_id, yesterday
        )
        if previous and previous.check_in and not previous.check_out:
            shift = (
                self.attendance.get_shift_type(organization_id, previous.shift_type_id)
                if previous.shift_type_id
                else self.attendance.get_employee_shift(
                    organization_id, employee.employee_id, yesterday
                )
            )
            if shift and shift.end_time <= shift.start_time:
                return "overnight_shift_not_supported"
        shift = self.attendance.get_employee_shift(
            organization_id, employee.employee_id, today
        )
        if shift and shift.end_time <= shift.start_time:
            return "overnight_shift_not_supported"
        return None

    def _reject_overnight(self, organization_id: UUID, employee: Employee) -> None:
        if reason := self._overnight_reason(organization_id, employee):
            raise SelfcareAttendanceError(
                reason,
                "Selfcare attendance is not yet available for overnight shifts.",
                status_code=409,
            )

    @staticmethod
    def _map_domain_error(exc: Exception) -> SelfcareAttendanceError:
        message = str(exc).lower()
        if "already checked in" in message:
            return SelfcareAttendanceError(
                "already_checked_in", "Already checked in.", status_code=409
            )
        if "no check-in" in message or "without checking in" in message:
            return SelfcareAttendanceError(
                "check_in_required",
                "Check in is required before checkout.",
                status_code=409,
            )
        if "location is required" in message:
            return SelfcareAttendanceError(
                "location_required",
                "Location is required to record attendance.",
                status_code=422,
            )
        if "outside" in message or "radius" in message:
            return SelfcareAttendanceError(
                "outside_geofence",
                "You are outside the permitted attendance location.",
                status_code=422,
            )
        if "location" in message or "coordinates" in message:
            return SelfcareAttendanceError(
                "invalid_location",
                "The supplied location could not be validated.",
                status_code=422,
            )
        return SelfcareAttendanceError(
            "attendance_unavailable",
            "Attendance could not be recorded.",
            status_code=409,
        )

    def _audit(
        self,
        organization_id: UUID,
        subject: UUID,
        employee: Employee,
        attendance_id: UUID,
        action: str,
        request_id: str | None,
        accuracy_m: float | None,
        service_person_id: UUID | None,
    ) -> None:
        fire_audit_event(
            self.db,
            organization_id,
            "attendance",
            "attendance",
            attendance_id,
            AuditAction.UPDATE,
            new_values={
                "source": "SELFCARE",
                "action": action,
                "selfcare_subject": str(subject),
                "employee_id": str(employee.employee_id),
                "request_id": request_id,
                "location_accuracy_m": accuracy_m,
            },
            user_id=service_person_id,
            reason=f"Selfcare attendance {action}",
        )
