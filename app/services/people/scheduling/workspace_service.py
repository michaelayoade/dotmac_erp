"""Composable weekly shift scheduler workflow service."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.notification import EntityType, NotificationChannel, NotificationType
from app.models.people.attendance.shift_type import ShiftType
from app.models.people.hr.employee import Employee, EmployeeStatus
from app.models.people.scheduling import (
    ScheduleAuditAction,
    ScheduleAuditEvent,
    ScheduleNotificationLog,
    ScheduleStatus,
    ShiftSchedule,
    WorkSchedule,
)
from app.services.notification import NotificationService
from app.services.people.scheduling.access import SchedulerAccessService
from app.services.people.scheduling.rules import (
    ScheduleRuleEvaluator,
    ScheduleRuleResult,
)

logger = logging.getLogger(__name__)
UTC = timezone.utc


class ScheduleWorkflowError(Exception):
    """Base scheduler workflow error."""


class ScheduleConcurrencyError(ScheduleWorkflowError):
    """Raised when a stale schedule version is edited."""


class ScheduleWorkspaceService:
    """Manage weekly schedules, assignments, lifecycle, and publication."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.access = SchedulerAccessService(db)

    @staticmethod
    def week_end(period_start: date) -> date:
        return period_start + timedelta(days=6)

    def get_or_create_week(
        self,
        organization_id: UUID,
        *,
        department_id: UUID,
        period_start: date,
        actor_person_id: UUID | None,
        actor_employee_id: UUID | None,
        can_view_all: bool,
    ) -> WorkSchedule:
        self.access.assert_department_access(
            organization_id,
            department_id,
            actor_employee_id=actor_employee_id,
            can_view_all=can_view_all,
        )
        period_end = self.week_end(period_start)
        schedule = self.db.scalar(
            select(WorkSchedule)
            .where(
                WorkSchedule.organization_id == organization_id,
                WorkSchedule.department_id == department_id,
                WorkSchedule.period_start == period_start,
                WorkSchedule.period_end == period_end,
            )
            .order_by(WorkSchedule.revision.desc())
            .limit(1)
        )
        if schedule:
            return schedule
        schedule = WorkSchedule(
            organization_id=organization_id,
            department_id=department_id,
            period_start=period_start,
            period_end=period_end,
            created_by_id=actor_person_id,
        )
        self.db.add(schedule)
        self.db.flush()
        self._audit(
            schedule,
            actor_person_id,
            ScheduleAuditAction.CREATED,
            None,
            schedule.status.value,
        )
        return schedule

    def get_workspace(self, organization_id: UUID, schedule_id: UUID) -> WorkSchedule:
        schedule = self.db.scalar(
            select(WorkSchedule).where(
                WorkSchedule.organization_id == organization_id,
                WorkSchedule.work_schedule_id == schedule_id,
            )
        )
        if schedule is None:
            raise ScheduleWorkflowError("Schedule not found")
        return schedule

    def list_assignments(
        self, organization_id: UUID, schedule_id: UUID
    ) -> list[ShiftSchedule]:
        return list(
            self.db.scalars(
                select(ShiftSchedule)
                .options(
                    joinedload(ShiftSchedule.employee).joinedload(Employee.person),
                    joinedload(ShiftSchedule.shift_type),
                )
                .where(
                    ShiftSchedule.organization_id == organization_id,
                    ShiftSchedule.work_schedule_id == schedule_id,
                )
                .order_by(ShiftSchedule.shift_date, ShiftSchedule.employee_id)
            )
            .unique()
            .all()
        )

    def assign_shift(
        self,
        organization_id: UUID,
        *,
        schedule_id: UUID,
        employee_id: UUID,
        shift_date: date,
        shift_type_id: UUID,
        actor_person_id: UUID | None,
        actor_employee_id: UUID | None,
        expected_version: int | None,
        can_view_all: bool,
    ) -> tuple[ShiftSchedule, ScheduleRuleResult]:
        schedule = self._editable_schedule(
            organization_id, schedule_id, expected_version
        )
        self.access.assert_department_access(
            organization_id,
            schedule.department_id,
            actor_employee_id=actor_employee_id,
            can_view_all=can_view_all,
        )
        if not (schedule.period_start <= shift_date <= schedule.period_end):
            raise ScheduleWorkflowError("Shift date is outside the schedule period")
        employee = self.db.scalar(
            select(Employee).where(
                Employee.organization_id == organization_id,
                Employee.employee_id == employee_id,
                Employee.department_id == schedule.department_id,
                Employee.status == EmployeeStatus.ACTIVE,
            )
        )
        if employee is None:
            raise ScheduleWorkflowError(
                "Employee is not schedulable in this department"
            )
        shift = self.db.scalar(
            select(ShiftType).where(
                ShiftType.organization_id == organization_id,
                ShiftType.shift_type_id == shift_type_id,
                ShiftType.is_active.is_(True),
            )
        )
        if shift is None:
            raise ScheduleWorkflowError("Shift template not found or inactive")
        assignment = self.db.scalar(
            select(ShiftSchedule).where(
                ShiftSchedule.organization_id == organization_id,
                ShiftSchedule.work_schedule_id == schedule.work_schedule_id,
                ShiftSchedule.employee_id == employee_id,
                ShiftSchedule.shift_date == shift_date,
                ShiftSchedule.revision == schedule.revision,
            )
        )
        if assignment is None:
            assignment = ShiftSchedule(
                organization_id=organization_id,
                work_schedule_id=schedule.work_schedule_id,
                revision=schedule.revision,
                employee_id=employee_id,
                department_id=schedule.department_id,
                shift_date=shift_date,
                shift_type_id=shift_type_id,
                schedule_month=shift_date.strftime("%Y-%m"),
                status=ScheduleStatus.DRAFT,
                created_by_id=actor_employee_id,
            )
            self.db.add(assignment)
            action = ScheduleAuditAction.ASSIGNED
        else:
            assignment.shift_type_id = shift_type_id
            assignment.updated_by_id = actor_person_id
            action = ScheduleAuditAction.MOVED
        schedule.version += 1
        self.db.flush()
        self._audit(
            schedule,
            actor_person_id,
            action,
            schedule.status.value,
            schedule.status.value,
            {"employee_id": str(employee_id), "shift_date": shift_date.isoformat()},
        )
        return assignment, ScheduleRuleEvaluator(self.db).evaluate(
            organization_id, schedule
        )

    def remove_assignment(
        self,
        organization_id: UUID,
        *,
        schedule_id: UUID,
        assignment_id: UUID,
        actor_person_id: UUID | None,
        actor_employee_id: UUID | None,
        expected_version: int | None,
        can_view_all: bool,
    ) -> ScheduleRuleResult:
        schedule = self._editable_schedule(
            organization_id, schedule_id, expected_version
        )
        self.access.assert_department_access(
            organization_id,
            schedule.department_id,
            actor_employee_id=actor_employee_id,
            can_view_all=can_view_all,
        )
        assignment = self.db.scalar(
            select(ShiftSchedule).where(
                ShiftSchedule.organization_id == organization_id,
                ShiftSchedule.shift_schedule_id == assignment_id,
                ShiftSchedule.work_schedule_id == schedule.work_schedule_id,
            )
        )
        if assignment is None:
            raise ScheduleWorkflowError("Assignment not found")
        self.db.delete(assignment)
        schedule.version += 1
        self.db.flush()
        self._audit(
            schedule,
            actor_person_id,
            ScheduleAuditAction.REMOVED,
            schedule.status.value,
            schedule.status.value,
        )
        return ScheduleRuleEvaluator(self.db).evaluate(organization_id, schedule)

    def submit(
        self, organization_id: UUID, schedule_id: UUID, actor_person_id: UUID | None
    ) -> ScheduleRuleResult:
        schedule = self.get_workspace(organization_id, schedule_id)
        if schedule.status not in {ScheduleStatus.DRAFT, ScheduleStatus.REJECTED}:
            raise ScheduleWorkflowError(
                "Only draft or rejected schedules can be submitted"
            )
        result = ScheduleRuleEvaluator(self.db).evaluate(organization_id, schedule)
        if not result.valid:
            return result
        previous = schedule.status.value
        schedule.status = ScheduleStatus.SUBMITTED
        schedule.submitted_by_id = actor_person_id
        schedule.submitted_at = datetime.now(UTC)
        schedule.version += 1
        self._sync_assignment_status(schedule)
        self._audit(
            schedule,
            actor_person_id,
            ScheduleAuditAction.SUBMITTED,
            previous,
            schedule.status.value,
        )
        self.db.flush()
        return result

    def approve(
        self, organization_id: UUID, schedule_id: UUID, actor_person_id: UUID | None
    ) -> None:
        schedule = self.get_workspace(organization_id, schedule_id)
        if schedule.status != ScheduleStatus.SUBMITTED:
            raise ScheduleWorkflowError("Only submitted schedules can be approved")
        if schedule.submitted_by_id and schedule.submitted_by_id == actor_person_id:
            raise ScheduleWorkflowError(
                "Schedule submitter cannot approve their own schedule"
            )
        previous = schedule.status.value
        schedule.status = ScheduleStatus.APPROVED
        schedule.approved_by_id = actor_person_id
        schedule.approved_at = datetime.now(UTC)
        schedule.version += 1
        self._sync_assignment_status(schedule)
        self._audit(
            schedule,
            actor_person_id,
            ScheduleAuditAction.APPROVED,
            previous,
            schedule.status.value,
        )
        self.db.flush()

    def reject(
        self,
        organization_id: UUID,
        schedule_id: UUID,
        actor_person_id: UUID | None,
        reason: str,
    ) -> None:
        if not reason.strip():
            raise ScheduleWorkflowError("Rejection reason is required")
        schedule = self.get_workspace(organization_id, schedule_id)
        if schedule.status != ScheduleStatus.SUBMITTED:
            raise ScheduleWorkflowError("Only submitted schedules can be rejected")
        previous = schedule.status.value
        schedule.status = ScheduleStatus.REJECTED
        schedule.rejected_by_id = actor_person_id
        schedule.rejected_at = datetime.now(UTC)
        schedule.rejection_reason = reason.strip()
        schedule.version += 1
        self._sync_assignment_status(schedule)
        self._audit(
            schedule,
            actor_person_id,
            ScheduleAuditAction.REJECTED,
            previous,
            schedule.status.value,
            reason=reason.strip(),
        )
        self.db.flush()

    def publish(
        self, organization_id: UUID, schedule_id: UUID, actor_person_id: UUID | None
    ) -> int:
        schedule = self.get_workspace(organization_id, schedule_id)
        if schedule.status != ScheduleStatus.APPROVED:
            raise ScheduleWorkflowError("Only approved schedules can be published")
        previous = schedule.status.value
        schedule.status = ScheduleStatus.PUBLISHED
        schedule.published_by_id = actor_person_id
        schedule.published_at = datetime.now(UTC)
        schedule.version += 1
        self._sync_assignment_status(schedule)
        self._audit(
            schedule,
            actor_person_id,
            ScheduleAuditAction.PUBLISHED,
            previous,
            schedule.status.value,
        )
        count = self._notify_published(schedule, actor_person_id)
        self.db.flush()
        return count

    def create_amendment(
        self,
        organization_id: UUID,
        schedule_id: UUID,
        *,
        actor_person_id: UUID | None,
        actor_employee_id: UUID | None,
        can_view_all: bool,
        reason: str | None = None,
    ) -> WorkSchedule:
        """Clone a published schedule into a new draft revision for controlled changes."""
        source = self.get_workspace(organization_id, schedule_id)
        if source.status != ScheduleStatus.PUBLISHED:
            raise ScheduleWorkflowError("Only published schedules can be amended")
        self.access.assert_department_access(
            organization_id,
            source.department_id,
            actor_employee_id=actor_employee_id,
            can_view_all=can_view_all,
        )
        existing_draft = self.db.scalar(
            select(WorkSchedule)
            .where(
                WorkSchedule.organization_id == organization_id,
                WorkSchedule.department_id == source.department_id,
                WorkSchedule.period_start == source.period_start,
                WorkSchedule.period_end == source.period_end,
                WorkSchedule.revision > source.revision,
                WorkSchedule.status.in_(
                    [
                        ScheduleStatus.DRAFT,
                        ScheduleStatus.REJECTED,
                        ScheduleStatus.SUBMITTED,
                        ScheduleStatus.APPROVED,
                    ]
                ),
            )
            .order_by(WorkSchedule.revision.desc())
            .limit(1)
        )
        if existing_draft:
            return existing_draft

        amendment = WorkSchedule(
            organization_id=organization_id,
            department_id=source.department_id,
            period_start=source.period_start,
            period_end=source.period_end,
            status=ScheduleStatus.DRAFT,
            revision=source.revision + 1,
            parent_schedule_id=source.work_schedule_id,
            created_by_id=actor_person_id,
        )
        self.db.add(amendment)
        self.db.flush()

        for assignment in self.list_assignments(
            organization_id, source.work_schedule_id
        ):
            self.db.add(
                ShiftSchedule(
                    organization_id=organization_id,
                    work_schedule_id=amendment.work_schedule_id,
                    revision=amendment.revision,
                    employee_id=assignment.employee_id,
                    department_id=assignment.department_id,
                    shift_date=assignment.shift_date,
                    shift_type_id=assignment.shift_type_id,
                    schedule_month=assignment.schedule_month,
                    status=ScheduleStatus.DRAFT,
                    notes=assignment.notes,
                    created_by_id=actor_employee_id,
                )
            )
        self._audit(
            amendment,
            actor_person_id,
            ScheduleAuditAction.AMENDED,
            source.status.value,
            amendment.status.value,
            metadata={"source_schedule_id": str(source.work_schedule_id)},
            reason=(reason or "").strip() or None,
        )
        self.db.flush()
        return amendment

    def evaluation(
        self, organization_id: UUID, schedule_id: UUID
    ) -> ScheduleRuleResult:
        return ScheduleRuleEvaluator(self.db).evaluate(
            organization_id, self.get_workspace(organization_id, schedule_id)
        )

    def _editable_schedule(
        self, organization_id: UUID, schedule_id: UUID, expected_version: int | None
    ) -> WorkSchedule:
        schedule = self.get_workspace(organization_id, schedule_id)
        if expected_version is not None and schedule.version != expected_version:
            raise ScheduleConcurrencyError(
                "Schedule has changed; reload before editing"
            )
        if schedule.status not in {ScheduleStatus.DRAFT, ScheduleStatus.REJECTED}:
            raise ScheduleWorkflowError("Only draft schedules can be edited")
        return schedule

    def _sync_assignment_status(self, schedule: WorkSchedule) -> None:
        for assignment in self.list_assignments(
            schedule.organization_id, schedule.work_schedule_id
        ):
            assignment.status = schedule.status
            assignment.revision = schedule.revision

    def _audit(
        self,
        schedule: WorkSchedule,
        actor_id: UUID | None,
        action: ScheduleAuditAction,
        previous: str | None,
        new: str | None,
        metadata: dict[str, str] | None = None,
        reason: str | None = None,
    ) -> None:
        self.db.add(
            ScheduleAuditEvent(
                organization_id=schedule.organization_id,
                work_schedule_id=schedule.work_schedule_id,
                actor_id=actor_id,
                action=action,
                previous_status=previous,
                new_status=new,
                reason=reason,
                event_metadata=metadata or {},
            )
        )

    def _notify_published(self, schedule: WorkSchedule, actor_id: UUID | None) -> int:
        notification_service = NotificationService()
        sent = 0
        employee_ids = {
            assignment.employee_id
            for assignment in self.list_assignments(
                schedule.organization_id, schedule.work_schedule_id
            )
        }
        employees = list(
            self.db.scalars(
                select(Employee).where(
                    Employee.organization_id == schedule.organization_id,
                    Employee.employee_id.in_(employee_ids),
                )
            ).all()
        )
        for employee in employees:
            if not employee.person_id:
                continue
            existing = self.db.scalar(
                select(ScheduleNotificationLog).where(
                    ScheduleNotificationLog.organization_id == schedule.organization_id,
                    ScheduleNotificationLog.work_schedule_id
                    == schedule.work_schedule_id,
                    ScheduleNotificationLog.revision == schedule.revision,
                    ScheduleNotificationLog.employee_id == employee.employee_id,
                )
            )
            if existing:
                continue
            notification = notification_service.create(
                self.db,
                organization_id=schedule.organization_id,
                recipient_id=employee.person_id,
                entity_type=EntityType.SYSTEM,
                entity_id=schedule.work_schedule_id,
                notification_type=NotificationType.INFO,
                title="Schedule Published"
                if schedule.revision == 1
                else "Schedule Updated",
                message=f"Your schedule for {schedule.period_start.isoformat()} to {schedule.period_end.isoformat()} has been published.",
                channel=NotificationChannel.BOTH,
                action_url="/people/self/scheduling/schedules",
                actor_id=actor_id,
            )
            self.db.add(
                ScheduleNotificationLog(
                    organization_id=schedule.organization_id,
                    work_schedule_id=schedule.work_schedule_id,
                    revision=schedule.revision,
                    employee_id=employee.employee_id,
                    notification_id=getattr(notification, "notification_id", None),
                )
            )
            sent += 1
        return sent
