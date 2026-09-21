"""ERP-owned calendar service with queued Talk notification consequences."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import Text, and_, cast, exists, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models.notification import EntityType, NotificationChannel, NotificationType
from app.models.organization_calendar import (
    CalendarBusinessStatus,
    CalendarEventScope,
    OrganizationCalendarAudit,
    OrganizationCalendarEvent,
    OrganizationCalendarParticipant,
    OrganizationCalendarReminder,
    ParticipantMembershipStatus,
)
from app.models.people.hr.department import Department
from app.models.people.hr.designation import Designation
from app.models.people.hr.employee import Employee, EmployeeStatus
from app.models.person import Person, PersonStatus
from app.services.notification import NotificationService

DEFAULT_TIMEZONE = "Africa/Lagos"
MAX_REMINDERS = 3
MAX_PARTICIPANTS = 1000
_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
UTC = timezone.utc


class CalendarError(ValueError):
    """Base domain error safe to display in the calendar form."""


class CalendarNotFoundError(CalendarError):
    pass


class CalendarConflictError(CalendarError):
    pass


@dataclass(frozen=True)
class ParticipantCandidate:
    person_id: uuid.UUID
    employee_id: uuid.UUID
    name: str
    email: str
    nextcloud_user_id: str | None
    department_name: str | None
    department_id: uuid.UUID | None = None
    designation_id: uuid.UUID | None = None
    designation_name: str | None = None

    @property
    def has_nextcloud_identity(self) -> bool:
        return bool(self.nextcloud_user_id)


@dataclass(frozen=True)
class ExcludedEmployee:
    employee_id: uuid.UUID
    name: str
    reason: str


@dataclass(frozen=True)
class CalendarEventData:
    title: str
    description: str | None
    event_details: str | None
    location: str | None
    meeting_url: str | None
    timezone: str
    all_day: bool
    start_at: datetime | None
    end_at: datetime | None
    start_date: date | None
    end_date_exclusive: date | None
    color: str = "#4F46E5"


class OrganizationCalendarService:
    """Tenant-safe calendar writes. Methods flush; the route owns commit."""

    def __init__(self, db: Session, organization_id: uuid.UUID) -> None:
        self.db = db
        self.organization_id = organization_id

    def eligible_participants(
        self,
    ) -> tuple[list[ParticipantCandidate], list[ExcludedEmployee]]:
        rows = self.db.execute(
            select(
                Employee,
                Person,
                Department.department_id,
                Department.department_name,
                Designation.designation_id,
                Designation.designation_name,
            )
            .join(Person, Person.id == Employee.person_id)
            .outerjoin(Department, Department.department_id == Employee.department_id)
            .outerjoin(Designation, Designation.designation_id == Employee.designation_id)
            .where(Employee.organization_id == self.organization_id)
            .order_by(Person.first_name, Person.last_name, Employee.employee_code)
        ).all()
        eligible: list[ParticipantCandidate] = []
        excluded: list[ExcludedEmployee] = []
        today = date.today()
        for employee, person, department_id, department_name, designation_id, designation_name in rows:
            reason = self._eligibility_failure(employee, person, today=today)
            if reason:
                excluded.append(
                    ExcludedEmployee(employee.employee_id, person.name, reason)
                )
                continue
            eligible.append(
                ParticipantCandidate(
                    person_id=person.id,
                    employee_id=employee.employee_id,
                    name=person.name or employee.employee_code,
                    email=person.email.strip().lower(),
                    nextcloud_user_id=(person.nextcloud_user_id or "").strip() or None,
                    department_id=department_id,
                    department_name=department_name,
                    designation_id=designation_id,
                    designation_name=designation_name,
                )
            )
        return eligible, excluded

    def recipient_groups(self) -> dict[str, list[dict[str, object]]]:
        departments = self.db.scalars(
            select(Department).where(
                Department.organization_id == self.organization_id,
                Department.is_active.is_(True),
            ).order_by(Department.department_name)
        ).all()
        designations = self.db.scalars(
            select(Designation).where(
                Designation.organization_id == self.organization_id,
                Designation.is_active.is_(True),
            ).order_by(Designation.designation_name)
        ).all()
        return {
            "departments": [{"id": item.department_id, "name": item.department_name} for item in departments],
            "designations": [{"id": item.designation_id, "name": item.designation_name} for item in designations],
        }

    @staticmethod
    def _eligibility_failure(
        employee: Employee, person: Person, *, today: date
    ) -> str | None:
        if employee.status != EmployeeStatus.ACTIVE:
            return "Employee is not active"
        if employee.date_of_leaving and employee.date_of_leaving < today:
            return "Employment has ended"
        if not person.is_active or person.status != PersonStatus.active:
            return "Person record is inactive"
        if not (person.email or "").strip() or "@" not in person.email:
            return "Work email is missing or invalid"
        return None

    def get_event(
        self,
        event_id: uuid.UUID,
        *,
        for_update: bool = False,
        event_scope: str | None = CalendarEventScope.ORGANIZATIONAL.value,
    ) -> OrganizationCalendarEvent:
        stmt = (
            select(OrganizationCalendarEvent)
            .options(
                selectinload(OrganizationCalendarEvent.participants),
                selectinload(OrganizationCalendarEvent.reminders),
            )
            .where(
                OrganizationCalendarEvent.organization_id == self.organization_id,
                OrganizationCalendarEvent.event_id == event_id,
            )
        )
        if event_scope is not None:
            stmt = stmt.where(OrganizationCalendarEvent.event_scope == event_scope)
        if for_update:
            stmt = stmt.with_for_update()
        event = self.db.scalar(stmt)
        if event is None:
            raise CalendarNotFoundError("Calendar event was not found.")
        return event

    def _dynamic_target_exists(self, person_id: uuid.UUID):
        return exists().where(
            Employee.organization_id == self.organization_id,
            Employee.person_id == person_id,
            Employee.status == EmployeeStatus.ACTIVE,
            or_(
                func.position(cast(Employee.department_id, Text), cast(OrganizationCalendarEvent.recipient_targets, Text)) > 0,
                func.position(cast(Employee.designation_id, Text), cast(OrganizationCalendarEvent.recipient_targets, Text)) > 0,
            ),
        )

    def list_events(
        self, range_start: datetime, range_end: datetime
    ) -> list[OrganizationCalendarEvent]:
        start_date = range_start.date()
        end_date = range_end.date()
        events = list(
            self.db.scalars(
                select(OrganizationCalendarEvent)
                .options(
                    selectinload(OrganizationCalendarEvent.participants),
                    selectinload(OrganizationCalendarEvent.reminders),
                )
                .where(
                    OrganizationCalendarEvent.organization_id == self.organization_id,
                    OrganizationCalendarEvent.event_scope
                    == CalendarEventScope.ORGANIZATIONAL.value,
                    OrganizationCalendarEvent.business_status
                    != CalendarBusinessStatus.CANCELLED.value,
                    or_(
                        and_(
                            OrganizationCalendarEvent.all_day.is_(False),
                            OrganizationCalendarEvent.start_at < range_end,
                            OrganizationCalendarEvent.end_at > range_start,
                        ),
                        and_(
                            OrganizationCalendarEvent.all_day.is_(True),
                            OrganizationCalendarEvent.start_date < end_date,
                            OrganizationCalendarEvent.end_date_exclusive > start_date,
                        ),
                    ),
                )
                .order_by(
                    OrganizationCalendarEvent.start_date,
                    OrganizationCalendarEvent.start_at,
                    OrganizationCalendarEvent.title,
                )
            ).all()
        )

    def list_visible_events_for_person(
        self,
        person_id: uuid.UUID,
        range_start: datetime,
        range_end: datetime,
    ) -> list[OrganizationCalendarEvent]:
        """Return only published events the employee owns or participates in."""
        start_date = range_start.date()
        end_date = range_end.date()
        active_participation = exists().where(
            OrganizationCalendarParticipant.event_id
            == OrganizationCalendarEvent.event_id,
            OrganizationCalendarParticipant.organization_id == self.organization_id,
            OrganizationCalendarParticipant.person_id == person_id,
            OrganizationCalendarParticipant.membership_status
            == ParticipantMembershipStatus.ACTIVE.value,
        )
        return list(
            self.db.scalars(
                select(OrganizationCalendarEvent)
                .options(
                    selectinload(OrganizationCalendarEvent.participants),
                    selectinload(OrganizationCalendarEvent.reminders),
                )
                .where(
                    OrganizationCalendarEvent.organization_id == self.organization_id,
                    OrganizationCalendarEvent.business_status
                    == CalendarBusinessStatus.PUBLISHED.value,
                    or_(
                        or_(active_participation, self._dynamic_target_exists(person_id)),
                        and_(
                            OrganizationCalendarEvent.event_scope
                            == CalendarEventScope.PERSONAL.value,
                            OrganizationCalendarEvent.created_by_id == person_id,
                        ),
                    ),
                    or_(
                        and_(
                            OrganizationCalendarEvent.all_day.is_(False),
                            OrganizationCalendarEvent.start_at < range_end,
                            OrganizationCalendarEvent.end_at > range_start,
                        ),
                        and_(
                            OrganizationCalendarEvent.all_day.is_(True),
                            OrganizationCalendarEvent.start_date < end_date,
                            OrganizationCalendarEvent.end_date_exclusive > start_date,
                        ),
                    ),
                )
                .order_by(
                    OrganizationCalendarEvent.start_date,
                    OrganizationCalendarEvent.start_at,
                    OrganizationCalendarEvent.title,
                )
            )
            .unique()
            .all()
        )
        return [event for event in events if self._event_matches_current_target(event, person_id)]

    def _event_matches_current_target(self, event: OrganizationCalendarEvent, person_id: uuid.UUID) -> bool:
        if any(
            item.person_id == person_id
            and item.membership_status == ParticipantMembershipStatus.ACTIVE.value
            for item in event.participants
        ):
            return True
        targets = event.recipient_targets or {}
        if not targets.get("departments") and not targets.get("designations"):
            return False
        employee = self.db.scalar(
            select(Employee).where(
                Employee.organization_id == self.organization_id,
                Employee.person_id == person_id,
                Employee.status == EmployeeStatus.ACTIVE,
            )
        )
        if employee is None:
            return False
        return str(employee.department_id) in set(targets.get("departments", [])) or str(employee.designation_id) in set(targets.get("designations", []))

    def get_visible_event_for_person(
        self, event_id: uuid.UUID, person_id: uuid.UUID
    ) -> OrganizationCalendarEvent:
        """Fetch an assigned/owned event without granting role-based bypasses."""
        active_participation = exists().where(
            OrganizationCalendarParticipant.event_id
            == OrganizationCalendarEvent.event_id,
            OrganizationCalendarParticipant.organization_id == self.organization_id,
            OrganizationCalendarParticipant.person_id == person_id,
            OrganizationCalendarParticipant.membership_status
            == ParticipantMembershipStatus.ACTIVE.value,
        )
        event = self.db.scalar(
            select(OrganizationCalendarEvent)
            .options(
                selectinload(OrganizationCalendarEvent.participants),
                selectinload(OrganizationCalendarEvent.reminders),
            )
            .where(
                OrganizationCalendarEvent.organization_id == self.organization_id,
                OrganizationCalendarEvent.event_id == event_id,
                OrganizationCalendarEvent.business_status
                != CalendarBusinessStatus.CANCELLED.value,
                or_(
                    or_(active_participation, self._dynamic_target_exists(person_id)),
                    and_(
                        OrganizationCalendarEvent.event_scope
                        == CalendarEventScope.PERSONAL.value,
                        OrganizationCalendarEvent.created_by_id == person_id,
                    ),
                ),
            )
        )
        if event is None or not self._event_matches_current_target(event, person_id):
            raise CalendarNotFoundError("Calendar event was not found.")
        return event

    def create_personal_event(
        self,
        data: CalendarEventData,
        *,
        actor_person_id: uuid.UUID,
        participant_person_ids: list[uuid.UUID],
        reminder_offsets: list[int],
        department_ids: list[uuid.UUID] | None = None,
        designation_ids: list[uuid.UUID] | None = None,
    ) -> OrganizationCalendarEvent:
        """Create a published personal event owned by the self-service user."""
        participants = list(dict.fromkeys([actor_person_id, *participant_person_ids]))
        return self.create_event(
            data,
            actor_person_id=actor_person_id,
            participant_person_ids=participants,
            reminder_offsets=reminder_offsets,
            department_ids=department_ids,
            designation_ids=designation_ids,
            publish=True,
            _event_scope=CalendarEventScope.PERSONAL.value,
        )

    def update_personal_event(
        self,
        event_id: uuid.UUID,
        data: CalendarEventData,
        *,
        expected_version: int,
        actor_person_id: uuid.UUID,
        participant_person_ids: list[uuid.UUID],
        reminder_offsets: list[int],
        department_ids: list[uuid.UUID] | None = None,
        designation_ids: list[uuid.UUID] | None = None,
    ) -> OrganizationCalendarEvent:
        """Update only a personal event owned by the self-service user."""
        participants = list(dict.fromkeys([actor_person_id, *participant_person_ids]))
        return self.update_event(
            event_id,
            data,
            expected_version=expected_version,
            actor_person_id=actor_person_id,
            participant_person_ids=participants,
            reminder_offsets=reminder_offsets,
            department_ids=department_ids,
            designation_ids=designation_ids,
            publish=True,
            _event_scope=CalendarEventScope.PERSONAL.value,
            _owner_person_id=actor_person_id,
        )

    def cancel_personal_event(
        self,
        event_id: uuid.UUID,
        *,
        expected_version: int,
        actor_person_id: uuid.UUID,
    ) -> OrganizationCalendarEvent:
        """Cancel only a personal event owned by the self-service user."""
        return self.cancel_event(
            event_id,
            expected_version=expected_version,
            actor_person_id=actor_person_id,
            _event_scope=CalendarEventScope.PERSONAL.value,
            _owner_person_id=actor_person_id,
        )

    def create_event(
        self,
        data: CalendarEventData,
        *,
        actor_person_id: uuid.UUID,
        participant_person_ids: list[uuid.UUID],
        reminder_offsets: list[int],
        department_ids: list[uuid.UUID] | None = None,
        designation_ids: list[uuid.UUID] | None = None,
        publish: bool,
        add_everyone: bool = False,
        _event_scope: str = CalendarEventScope.ORGANIZATIONAL.value,
    ) -> OrganizationCalendarEvent:
        self._validate_data(data)
        event_id = uuid.uuid4()
        event = OrganizationCalendarEvent(
            event_id=event_id,
            organization_id=self.organization_id,
            ical_uid=f"erp-event-{self.organization_id}-{event_id}@dotmac.ng",
            title=data.title.strip(),
            description=self._clean(data.description),
            event_details=self._clean(data.event_details),
            location=self._clean(data.location),
            meeting_url=self._clean(data.meeting_url),
            timezone=data.timezone,
            all_day=data.all_day,
            start_at=data.start_at,
            end_at=data.end_at,
            start_date=data.start_date,
            end_date_exclusive=data.end_date_exclusive,
            color=data.color.upper(),
            event_scope=_event_scope,
            business_status=(
                CalendarBusinessStatus.PUBLISHED.value
                if publish
                else CalendarBusinessStatus.DRAFT.value
            ),
            created_by_id=actor_person_id,
            updated_by_id=actor_person_id,
            published_by_id=actor_person_id if publish else None,
            published_at=datetime.now(UTC) if publish else None,
        )
        self.db.add(event)
        self.db.flush()
        candidates, targets = self._resolve_candidates(
            participant_person_ids,
            add_everyone,
            department_ids or [],
            designation_ids or [],
        )
        event.recipient_targets = targets
        if publish and not candidates:
            raise CalendarError("A published event must have at least one participant.")
        newly_added, _removed = self._replace_participants(event, candidates)
        self._replace_reminders(event, reminder_offsets)
        self._audit(event, actor_person_id, "PUBLISHED" if publish else "CREATED")
        if publish:
            self._notify_calendar_participants(
                event,
                newly_added,
                actor_person_id,
                notification_type=NotificationType.ASSIGNED,
                title_prefix="New calendar event",
                message="You have been added to this event.",
            )
        self.db.flush()
        return event

    def update_event(
        self,
        event_id: uuid.UUID,
        data: CalendarEventData,
        *,
        expected_version: int,
        actor_person_id: uuid.UUID,
        participant_person_ids: list[uuid.UUID],
        reminder_offsets: list[int],
        department_ids: list[uuid.UUID] | None = None,
        designation_ids: list[uuid.UUID] | None = None,
        publish: bool,
        add_everyone: bool = False,
        _event_scope: str = CalendarEventScope.ORGANIZATIONAL.value,
        _owner_person_id: uuid.UUID | None = None,
    ) -> OrganizationCalendarEvent:
        self._validate_data(data)
        event = self.get_event(event_id, for_update=True, event_scope=_event_scope)
        if _owner_person_id is not None and event.created_by_id != _owner_person_id:
            raise CalendarNotFoundError("Calendar event was not found.")
        if event.version != expected_version:
            raise CalendarConflictError(
                "This event was updated by another user. Reload it before saving."
            )
        if event.business_status == CalendarBusinessStatus.CANCELLED.value:
            raise CalendarConflictError("A cancelled event cannot be edited.")

        old = self._event_snapshot(event)
        was_published = event.business_status == CalendarBusinessStatus.PUBLISHED.value
        candidates, targets = self._resolve_candidates(
            participant_person_ids,
            add_everyone,
            department_ids or [],
            designation_ids or [],
        )
        target_published = publish or (
            event.business_status == CalendarBusinessStatus.PUBLISHED.value
        )
        if target_published and not candidates:
            raise CalendarError("A published event must have at least one participant.")

        event.title = data.title.strip()
        event.description = self._clean(data.description)
        event.event_details = self._clean(data.event_details)
        event.location = self._clean(data.location)
        event.meeting_url = self._clean(data.meeting_url)
        event.timezone = data.timezone
        event.all_day = data.all_day
        event.start_at = data.start_at
        event.end_at = data.end_at
        event.start_date = data.start_date
        event.end_date_exclusive = data.end_date_exclusive
        event.color = data.color.upper()
        event.recipient_targets = targets
        event.updated_by_id = actor_person_id
        event.version += 1
        if target_published:
            event.business_status = CalendarBusinessStatus.PUBLISHED.value
            if event.published_at is None:
                event.published_at = datetime.now(UTC)
                event.published_by_id = actor_person_id

        newly_added, removed = self._replace_participants(event, candidates)
        self._replace_reminders(event, reminder_offsets)
        self._audit(event, actor_person_id, "UPDATED", old_values=old)
        if target_published:
            active_recipients = {candidate.person_id for candidate in candidates}
            if was_published:
                self._notify_calendar_participants(
                    event,
                    active_recipients - newly_added,
                    actor_person_id,
                    notification_type=NotificationType.STATUS_CHANGE,
                    title_prefix="Calendar event updated",
                    message="Event details have changed.",
                )
                self._notify_calendar_participants(
                    event,
                    removed,
                    actor_person_id,
                    notification_type=NotificationType.STATUS_CHANGE,
                    title_prefix="Removed from calendar event",
                    message="You are no longer a participant in this event.",
                    action_url="/people/self/calendar",
                )
            self._notify_calendar_participants(
                event,
                newly_added if was_published else active_recipients,
                actor_person_id,
                notification_type=NotificationType.ASSIGNED,
                title_prefix="New calendar event",
                message="You have been added to this event.",
            )
        self.db.flush()
        return event

    def cancel_event(
        self,
        event_id: uuid.UUID,
        *,
        expected_version: int,
        actor_person_id: uuid.UUID,
        _event_scope: str = CalendarEventScope.ORGANIZATIONAL.value,
        _owner_person_id: uuid.UUID | None = None,
    ) -> OrganizationCalendarEvent:
        event = self.get_event(event_id, for_update=True, event_scope=_event_scope)
        if _owner_person_id is not None and event.created_by_id != _owner_person_id:
            raise CalendarNotFoundError("Calendar event was not found.")
        if event.version != expected_version:
            raise CalendarConflictError(
                "This event was updated by another user. Reload it before deleting."
            )
        if event.business_status == CalendarBusinessStatus.CANCELLED.value:
            return event
        was_published = event.business_status == CalendarBusinessStatus.PUBLISHED.value
        active_recipients = {
            participant.person_id
            for participant in event.participants
            if participant.membership_status == ParticipantMembershipStatus.ACTIVE.value
        }
        old = self._event_snapshot(event)
        event.business_status = CalendarBusinessStatus.CANCELLED.value
        event.cancelled_at = datetime.now(UTC)
        event.cancelled_by_id = actor_person_id
        event.updated_by_id = actor_person_id
        event.version += 1
        for participant in event.participants:
            if (
                participant.membership_status
                == ParticipantMembershipStatus.ACTIVE.value
            ):
                participant.membership_status = (
                    ParticipantMembershipStatus.CANCELLED.value
                )
        self._audit(event, actor_person_id, "CANCELLED", old_values=old)
        if was_published:
            self._notify_calendar_participants(
                event,
                active_recipients,
                actor_person_id,
                notification_type=NotificationType.STATUS_CHANGE,
                title_prefix="Calendar event cancelled",
                message="This event has been cancelled.",
                action_url="/people/self/calendar",
            )
        self.db.flush()
        return event

    def remove_offboarded_employee_from_future_events(
        self,
        person_id: uuid.UUID,
        *,
        actor_person_id: uuid.UUID | None = None,
    ) -> int:
        """Remove an offboarded employee from future events and queue updates."""
        now = datetime.now(UTC)
        today = date.today()
        events = list(
            self.db.scalars(
                select(OrganizationCalendarEvent)
                .join(OrganizationCalendarParticipant)
                .options(
                    selectinload(OrganizationCalendarEvent.participants),
                    selectinload(OrganizationCalendarEvent.reminders),
                )
                .where(
                    OrganizationCalendarEvent.organization_id == self.organization_id,
                    OrganizationCalendarEvent.business_status
                    != CalendarBusinessStatus.CANCELLED.value,
                    OrganizationCalendarParticipant.person_id == person_id,
                    OrganizationCalendarParticipant.membership_status
                    == ParticipantMembershipStatus.ACTIVE.value,
                    or_(
                        and_(
                            OrganizationCalendarEvent.all_day.is_(False),
                            OrganizationCalendarEvent.start_at >= now,
                        ),
                        and_(
                            OrganizationCalendarEvent.all_day.is_(True),
                            OrganizationCalendarEvent.start_date >= today,
                        ),
                    ),
                )
                .with_for_update()
            )
            .unique()
            .all()
        )
        changed = 0
        for event in events:
            participant = next(
                (
                    item
                    for item in event.participants
                    if item.person_id == person_id
                    and item.membership_status
                    == ParticipantMembershipStatus.ACTIVE.value
                ),
                None,
            )
            if participant is None:
                continue
            published = event.business_status == CalendarBusinessStatus.PUBLISHED.value
            participant.membership_status = ParticipantMembershipStatus.REMOVED.value
            participant.removed_at = now
            event.version += 1
            if actor_person_id:
                event.updated_by_id = actor_person_id
            self._audit(
                event,
                actor_person_id,
                "PARTICIPANT_REMOVED_OFFBOARDING",
            )
            if published:
                self._notify_calendar_participants(
                    event,
                    {person_id},
                    actor_person_id,
                    notification_type=NotificationType.STATUS_CHANGE,
                    title_prefix="Removed from calendar event",
                    message="You are no longer a participant in this event.",
                    action_url="/people/self/calendar",
                )
            changed += 1
        self.db.flush()
        return changed

    def _resolve_candidates(
        self,
        person_ids: list[uuid.UUID],
        add_everyone: bool,
        department_ids: list[uuid.UUID],
        designation_ids: list[uuid.UUID],
    ) -> tuple[list[ParticipantCandidate], dict[str, list[str]]]:
        eligible, _excluded = self.eligible_participants()
        by_id = {candidate.person_id: candidate for candidate in eligible}
        departments = set(department_ids)
        designations = set(designation_ids)
        requested = set(person_ids)
        if add_everyone:
            requested.update(by_id)
        requested.update(
            candidate.person_id
            for candidate in eligible
            if candidate.department_id in departments or candidate.designation_id in designations
        )
        invalid = [person_id for person_id in requested if person_id not in by_id]
        if invalid:
            raise CalendarError("One or more selected employees are not eligible for the ERP calendar. Refresh the participant list and try again.")
        if len(requested) > MAX_PARTICIPANTS:
            raise CalendarError(f"An event cannot exceed {MAX_PARTICIPANTS} participants.")
        targets = {
            "departments": [str(value) for value in sorted(departments, key=str)],
            "designations": [str(value) for value in sorted(designations, key=str)],
        }
        return [by_id[person_id] for person_id in requested], targets

    def _replace_participants(
        self,
        event: OrganizationCalendarEvent,
        candidates: list[ParticipantCandidate],
    ) -> tuple[set[uuid.UUID], set[uuid.UUID]]:
        desired = {candidate.person_id: candidate for candidate in candidates}
        existing = {
            participant.person_id: participant for participant in event.participants
        }
        now = datetime.now(UTC)
        newly_added: set[uuid.UUID] = set()
        removed: set[uuid.UUID] = set()
        for person_id, participant in existing.items():
            candidate = desired.get(person_id)
            if candidate is None:
                if (
                    participant.membership_status
                    == ParticipantMembershipStatus.ACTIVE.value
                ):
                    removed.add(person_id)
                    participant.membership_status = (
                        ParticipantMembershipStatus.REMOVED.value
                    )
                    participant.removed_at = now
                continue
            if (
                participant.membership_status
                != ParticipantMembershipStatus.ACTIVE.value
            ):
                newly_added.add(person_id)
            participant.employee_id = candidate.employee_id
            participant.participant_name = candidate.name
            participant.participant_email = candidate.email
            participant.nextcloud_user_id = candidate.nextcloud_user_id
            participant.identity_status = (
                "VERIFIED"
                if candidate.has_nextcloud_identity
                else "MISSING_NEXTCLOUD_ACCOUNT"
            )
            participant.membership_status = ParticipantMembershipStatus.ACTIVE.value
            participant.removed_at = None
        for person_id, candidate in desired.items():
            if person_id in existing:
                continue
            newly_added.add(person_id)
            event.participants.append(
                OrganizationCalendarParticipant(
                    organization_id=self.organization_id,
                    person_id=candidate.person_id,
                    employee_id=candidate.employee_id,
                    participant_name=candidate.name,
                    participant_email=candidate.email,
                    nextcloud_user_id=candidate.nextcloud_user_id,
                    identity_status=(
                        "VERIFIED"
                        if candidate.has_nextcloud_identity
                        else "MISSING_NEXTCLOUD_ACCOUNT"
                    ),
                    membership_status=ParticipantMembershipStatus.ACTIVE.value,
                )
            )
        return newly_added, removed

    def _notify_calendar_participants(
        self,
        event: OrganizationCalendarEvent,
        recipient_ids: set[uuid.UUID],
        actor_person_id: uuid.UUID | None,
        *,
        notification_type: NotificationType,
        title_prefix: str,
        message: str,
        action_url: str | None = None,
    ) -> None:
        """Create an ERP alert for everyone and Talk delivery when available."""
        notification_service = NotificationService()
        action_url = (
            action_url
            if action_url is not None
            else f"/people/self/calendar/events/{event.event_id}"
        )
        notification_service.create_many(
            self.db,
            organization_id=self.organization_id,
            recipient_ids=sorted(recipient_ids, key=str),
            entity_type=EntityType.SYSTEM,
            entity_id=event.event_id,
            notification_type=notification_type,
            title=f"{title_prefix}: {event.title}",
            message=message,
            channel=NotificationChannel.IN_APP,
            action_url=action_url,
            actor_id=actor_person_id,
        )
        talk_recipients = {
            participant.person_id
            for participant in event.participants
            if participant.person_id in recipient_ids
            and participant.membership_status
            == ParticipantMembershipStatus.ACTIVE.value
            and participant.nextcloud_user_id
        }
        if talk_recipients:
            notification_service.create_many(
                self.db,
                organization_id=self.organization_id,
                recipient_ids=sorted(talk_recipients, key=str),
                entity_type=EntityType.SYSTEM,
                entity_id=event.event_id,
                notification_type=notification_type,
                title=f"{title_prefix}: {event.title}",
                message=message,
                channel=NotificationChannel.NEXTCLOUD,
                action_url=action_url,
                actor_id=actor_person_id,
            )

    def _replace_reminders(
        self, event: OrganizationCalendarEvent, reminder_offsets: list[int]
    ) -> None:
        offsets = sorted(set(reminder_offsets), reverse=True)
        if len(offsets) > MAX_REMINDERS:
            raise CalendarError(f"Use no more than {MAX_REMINDERS} reminders.")
        if any(offset < 0 or offset > 525600 for offset in offsets):
            raise CalendarError("Reminder offsets must be between now and one year.")
        # Delete existing rows before inserting replacement offsets so a
        # changed set cannot trip the event/offset unique constraint while the
        # unit of work is ordering deletes and inserts.
        event.reminders.clear()
        self.db.flush()
        event.reminders.extend(
            OrganizationCalendarReminder(
                organization_id=self.organization_id,
                offset_minutes=offset,
                scheduled_for=self._reminder_time(event, offset),
            )
            for offset in offsets
        )

    @staticmethod
    def _reminder_time(
        event: OrganizationCalendarEvent, offset_minutes: int
    ) -> datetime:
        if event.all_day:
            if event.start_date is None:
                raise CalendarError("All-day event start date is missing.")
            start = datetime.combine(
                event.start_date,
                time.min,
                tzinfo=ZoneInfo(event.timezone),
            ).astimezone(UTC)
        else:
            if event.start_at is None:
                raise CalendarError("Timed event start is missing.")
            start = event.start_at.astimezone(UTC)
        return start - timedelta(minutes=offset_minutes)

    def _audit(
        self,
        event: OrganizationCalendarEvent,
        actor_person_id: uuid.UUID | None,
        action: str,
        *,
        old_values: dict[str, object] | None = None,
        correlation_id: str | None = None,
    ) -> None:
        self.db.add(
            OrganizationCalendarAudit(
                organization_id=self.organization_id,
                event_id=event.event_id,
                actor_person_id=actor_person_id,
                action=action,
                old_values=old_values,
                new_values=self._event_snapshot(event),
                correlation_id=correlation_id or str(uuid.uuid4()),
            )
        )

    @staticmethod
    def _event_snapshot(event: OrganizationCalendarEvent) -> dict[str, object]:
        return {
            "title": event.title,
            "event_scope": event.event_scope,
            "business_status": event.business_status,
            "version": event.version,
            "all_day": event.all_day,
            "start": event.start_at.isoformat() if event.start_at else None,
            "end": event.end_at.isoformat() if event.end_at else None,
            "start_date": event.start_date.isoformat() if event.start_date else None,
            "end_date_exclusive": (
                event.end_date_exclusive.isoformat()
                if event.end_date_exclusive
                else None
            ),
        }

    @staticmethod
    def _validate_data(data: CalendarEventData) -> None:
        if not data.title.strip():
            raise CalendarError("Event title is required.")
        if len(data.title.strip()) > 200:
            raise CalendarError("Event title must be 200 characters or fewer.")
        try:
            ZoneInfo(data.timezone)
        except ZoneInfoNotFoundError as exc:
            raise CalendarError("Select a valid IANA time zone.") from exc
        if not _HEX_COLOR.fullmatch(data.color):
            raise CalendarError("Select a valid event colour.")
        if data.meeting_url:
            parsed_url = urlparse(data.meeting_url.strip())
            if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
                raise CalendarError("Meeting links must use a valid http or https URL.")
        if data.all_day:
            if not data.start_date or not data.end_date_exclusive:
                raise CalendarError("All-day events require start and end dates.")
            if data.end_date_exclusive <= data.start_date:
                raise CalendarError("The end date must be after the start date.")
            if data.start_at or data.end_at:
                raise CalendarError("All-day events cannot also contain times.")
        else:
            if not data.start_at or not data.end_at:
                raise CalendarError("Timed events require start and end times.")
            if data.end_at <= data.start_at:
                raise CalendarError("The event must end after it starts.")
            if data.start_date or data.end_date_exclusive:
                raise CalendarError("Timed events cannot also contain all-day dates.")

    @staticmethod
    def _clean(value: str | None) -> str | None:
        cleaned = (value or "").strip()
        return cleaned or None


def build_event_data(
    *,
    title: str,
    description: str | None,
    event_details: str | None,
    location: str | None,
    meeting_url: str | None,
    timezone_name: str,
    all_day: bool,
    start_date_value: date,
    end_date_value: date,
    start_time_value: time | None,
    end_time_value: time | None,
    color: str,
) -> CalendarEventData:
    """Convert local form fields to explicit all-day dates or UTC instants."""
    timezone_name = timezone_name or DEFAULT_TIMEZONE
    if all_day:
        return CalendarEventData(
            title=title,
            description=description,
            event_details=event_details,
            location=location,
            meeting_url=meeting_url,
            timezone=timezone_name,
            all_day=True,
            start_at=None,
            end_at=None,
            start_date=start_date_value,
            end_date_exclusive=end_date_value + timedelta(days=1),
            color=color,
        )
    if start_time_value is None or end_time_value is None:
        raise CalendarError("Start and end times are required.")
    try:
        tz = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise CalendarError("Select a valid IANA time zone.") from exc
    local_start = datetime.combine(start_date_value, start_time_value, tzinfo=tz)
    local_end = datetime.combine(end_date_value, end_time_value, tzinfo=tz)
    return CalendarEventData(
        title=title,
        description=description,
        event_details=event_details,
        location=location,
        meeting_url=meeting_url,
        timezone=timezone_name,
        all_day=False,
        start_at=local_start.astimezone(UTC),
        end_at=local_end.astimezone(UTC),
        start_date=None,
        end_date_exclusive=None,
        color=color,
    )


__all__ = [
    "CalendarConflictError",
    "CalendarError",
    "CalendarEventData",
    "CalendarNotFoundError",
    "DEFAULT_TIMEZONE",
    "MAX_REMINDERS",
    "OrganizationCalendarService",
    "ParticipantCandidate",
    "build_event_data",
]
