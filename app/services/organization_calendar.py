"""Organization calendar domain service and Integrator outbox contract."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models.finance.platform.event_outbox import EventOutbox, EventStatus
from app.models.finance.platform.service_hook_execution import (
    ExecutionStatus,
    ServiceHookExecution,
)
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
from app.models.people.hr.employee import Employee, EmployeeStatus
from app.models.person import Person, PersonStatus
from app.services.finance.platform.outbox_publisher import OutboxPublisher
from app.services.notification import NotificationService

CALENDAR_UPSERTED = "organization.calendar.upserted"
CALENDAR_CANCELLED = "organization.calendar.cancelled"
CALENDAR_CONTRACT_VERSION = "organization.calendar.v1"
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
    nextcloud_user_id: str
    department_name: str | None


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


@dataclass(frozen=True)
class CalendarDeliveryIssue:
    """A failed calendar delivery projected from its canonical execution log."""

    event: OrganizationCalendarEvent
    delivery: ServiceHookExecution


class OrganizationCalendarService:
    """Tenant-safe calendar writes. Methods flush; the route owns commit."""

    def __init__(self, db: Session, organization_id: uuid.UUID) -> None:
        self.db = db
        self.organization_id = organization_id

    def eligible_participants(
        self,
    ) -> tuple[list[ParticipantCandidate], list[ExcludedEmployee]]:
        from app.models.people.hr.department import Department

        rows = self.db.execute(
            select(Employee, Person, Department.department_name)
            .join(Person, Person.id == Employee.person_id)
            .outerjoin(Department, Department.department_id == Employee.department_id)
            .where(Employee.organization_id == self.organization_id)
            .order_by(Person.first_name, Person.last_name, Employee.employee_code)
        ).all()
        eligible: list[ParticipantCandidate] = []
        excluded: list[ExcludedEmployee] = []
        today = date.today()
        for employee, person, department_name in rows:
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
                    nextcloud_user_id=person.nextcloud_user_id.strip(),
                    department_name=department_name,
                )
            )
        return eligible, excluded

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
        if not (person.nextcloud_user_id or "").strip():
            return "Nextcloud identity is not mapped"
        state = employee.workforce_provisioning_state or {}
        nextcloud_state = state.get("nextcloud") if isinstance(state, dict) else None
        if (
            isinstance(nextcloud_state, dict)
            and nextcloud_state.get("status") == "failed"
        ):
            return "Nextcloud identity requires reconciliation"
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

    def list_events(
        self, range_start: datetime, range_end: datetime
    ) -> list[OrganizationCalendarEvent]:
        start_date = range_start.date()
        end_date = range_end.date()
        return list(
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
                        active_participation,
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
                    active_participation,
                    and_(
                        OrganizationCalendarEvent.event_scope
                        == CalendarEventScope.PERSONAL.value,
                        OrganizationCalendarEvent.created_by_id == person_id,
                    ),
                ),
            )
        )
        if event is None:
            raise CalendarNotFoundError("Calendar event was not found.")
        return event

    def create_personal_event(
        self,
        data: CalendarEventData,
        *,
        actor_person_id: uuid.UUID,
        participant_person_ids: list[uuid.UUID],
        reminder_offsets: list[int],
    ) -> OrganizationCalendarEvent:
        """Create a published personal event owned by the self-service user."""
        participants = list(dict.fromkeys([actor_person_id, *participant_person_ids]))
        return self.create_event(
            data,
            actor_person_id=actor_person_id,
            participant_person_ids=participants,
            reminder_offsets=reminder_offsets,
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

    def list_delivery_issues(self, *, limit: int = 200) -> list[CalendarDeliveryIssue]:
        """Project failed calendar deliveries from the hook execution owner."""
        deliveries = list(
            self.db.scalars(
                select(ServiceHookExecution)
                .where(
                    ServiceHookExecution.organization_id == self.organization_id,
                    ServiceHookExecution.event_name.in_(
                        [CALENDAR_UPSERTED, CALENDAR_CANCELLED]
                    ),
                    ServiceHookExecution.status.in_(
                        [ExecutionStatus.FAILED, ExecutionStatus.DEAD]
                    ),
                )
                .order_by(ServiceHookExecution.created_at.desc())
                .limit(max(1, min(limit, 500)))
            ).all()
        )
        issues: list[CalendarDeliveryIssue] = []
        for delivery in deliveries:
            payload = delivery.event_payload or {}
            metadata = payload.get("_hook_meta")
            event_id = metadata.get("entity_id") if isinstance(metadata, dict) else None
            event_id = event_id or payload.get("event_id")
            try:
                event = self.get_event(uuid.UUID(str(event_id)))
            except (CalendarNotFoundError, TypeError, ValueError):
                continue
            issues.append(CalendarDeliveryIssue(event=event, delivery=delivery))
        return issues

    def create_event(
        self,
        data: CalendarEventData,
        *,
        actor_person_id: uuid.UUID,
        participant_person_ids: list[uuid.UUID],
        reminder_offsets: list[int],
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
        candidates = self._resolve_candidates(participant_person_ids, add_everyone)
        if publish and not candidates:
            raise CalendarError("A published event must have at least one participant.")
        newly_added = self._replace_participants(event, candidates)
        self._replace_reminders(event, reminder_offsets)
        self._audit(event, actor_person_id, "PUBLISHED" if publish else "CREATED")
        if publish:
            self._publish_command(event, CALENDAR_UPSERTED, actor_person_id)
            if _event_scope == CalendarEventScope.ORGANIZATIONAL.value:
                self._notify_organizational_participants(
                    event, newly_added, actor_person_id
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
        candidates = self._resolve_candidates(participant_person_ids, add_everyone)
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
        event.updated_by_id = actor_person_id
        event.version += 1
        if target_published:
            event.business_status = CalendarBusinessStatus.PUBLISHED.value
            if event.published_at is None:
                event.published_at = datetime.now(UTC)
                event.published_by_id = actor_person_id

        newly_added = self._replace_participants(event, candidates)
        self._replace_reminders(event, reminder_offsets)
        self._audit(event, actor_person_id, "UPDATED", old_values=old)
        if target_published:
            self._publish_command(event, CALENDAR_UPSERTED, actor_person_id)
            if _event_scope == CalendarEventScope.ORGANIZATIONAL.value:
                recipients = (
                    newly_added
                    if was_published
                    else {candidate.person_id for candidate in candidates}
                )
                self._notify_organizational_participants(
                    event, recipients, actor_person_id
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
            self._publish_command(event, CALENDAR_CANCELLED, actor_person_id)
        self.db.flush()
        return event

    def request_retry(
        self, event_id: uuid.UUID, *, actor_person_id: uuid.UUID
    ) -> OrganizationCalendarEvent:
        event = self.get_event(event_id, for_update=True)
        if event.business_status == CalendarBusinessStatus.DRAFT.value:
            raise CalendarError("Draft events do not require synchronization.")
        active = self.db.scalar(
            select(EventOutbox)
            .where(
                EventOutbox.aggregate_type == "OrganizationCalendarEvent",
                EventOutbox.aggregate_id == str(event.event_id),
                EventOutbox.event_name.in_([CALENDAR_UPSERTED, CALENDAR_CANCELLED]),
                EventOutbox.status.in_(
                    [EventStatus.PENDING, EventStatus.FAILED, EventStatus.DEAD]
                ),
            )
            .order_by(EventOutbox.occurred_at.desc())
        )
        if active is not None and active.status == EventStatus.DEAD:
            OutboxPublisher.requeue_dead_event(self.db, active.event_id)
        elif active is None:
            action = (
                CALENDAR_CANCELLED
                if event.business_status == CalendarBusinessStatus.CANCELLED.value
                else CALENDAR_UPSERTED
            )
            self._publish_command(event, action, actor_person_id, retry=True)
        self._audit(event, actor_person_id, "RETRY_REQUESTED")
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
                self._publish_command(event, CALENDAR_UPSERTED, actor_person_id)
            changed += 1
        self.db.flush()
        return changed

    def _resolve_candidates(
        self, person_ids: list[uuid.UUID], add_everyone: bool
    ) -> list[ParticipantCandidate]:
        eligible, _excluded = self.eligible_participants()
        by_id = {candidate.person_id: candidate for candidate in eligible}
        requested = list(by_id) if add_everyone else list(dict.fromkeys(person_ids))
        invalid = [person_id for person_id in requested if person_id not in by_id]
        if invalid:
            raise CalendarError(
                "One or more selected employees are inactive or do not have a verified "
                "Nextcloud identity. Refresh the participant list and try again."
            )
        if len(requested) > MAX_PARTICIPANTS:
            raise CalendarError(
                f"An event cannot exceed {MAX_PARTICIPANTS} participants."
            )
        return [by_id[person_id] for person_id in requested]

    def _replace_participants(
        self,
        event: OrganizationCalendarEvent,
        candidates: list[ParticipantCandidate],
    ) -> set[uuid.UUID]:
        desired = {candidate.person_id: candidate for candidate in candidates}
        existing = {
            participant.person_id: participant for participant in event.participants
        }
        now = datetime.now(UTC)
        newly_added: set[uuid.UUID] = set()
        for person_id, participant in existing.items():
            candidate = desired.get(person_id)
            if candidate is None:
                if (
                    participant.membership_status
                    == ParticipantMembershipStatus.ACTIVE.value
                ):
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
            participant.identity_status = "VERIFIED"
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
                    identity_status="VERIFIED",
                    membership_status=ParticipantMembershipStatus.ACTIVE.value,
                )
            )
        return newly_added

    def _notify_organizational_participants(
        self,
        event: OrganizationCalendarEvent,
        recipient_ids: set[uuid.UUID],
        actor_person_id: uuid.UUID,
    ) -> None:
        """Create an ERP in-app alert for each newly involved participant."""
        NotificationService().create_many(
            self.db,
            organization_id=self.organization_id,
            recipient_ids=sorted(recipient_ids, key=str),
            entity_type=EntityType.SYSTEM,
            entity_id=event.event_id,
            notification_type=NotificationType.ASSIGNED,
            title=f"New calendar event: {event.title}",
            message="You have been added to an event on the Organizational Calendar.",
            channel=NotificationChannel.IN_APP,
            action_url=f"/people/self/calendar/events/{event.event_id}",
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
            )
            for offset in offsets
        )

    def _publish_command(
        self,
        event: OrganizationCalendarEvent,
        event_name: str,
        actor_person_id: uuid.UUID | None,
        *,
        retry: bool = False,
    ) -> None:
        self.db.flush()
        correlation_id = str(uuid.uuid4())
        delivery_idempotency_key = (
            f"organization-calendar:{event.event_id}:v{event.version}:{event_name}"
        )
        payload = self._contract_payload(
            event,
            event_name,
            correlation_id=correlation_id,
            idempotency_key=delivery_idempotency_key,
        )
        suffix = f":retry:{uuid.uuid4()}" if retry else ""
        OutboxPublisher.publish_event(
            self.db,
            event_name=event_name,
            event_version=1,
            aggregate_type="OrganizationCalendarEvent",
            aggregate_id=str(event.event_id),
            payload=payload,
            headers={
                "organization_id": str(self.organization_id),
                "user_id": str(actor_person_id) if actor_person_id else None,
                "request_id": None,
                "ip_address": None,
                "source": "erp.organization_calendar",
            },
            producer_module="organization_calendar",
            correlation_id=correlation_id,
            idempotency_key=(
                f"organization-calendar:{event.event_id}:v{event.version}{suffix}"
            ),
        )

    def _contract_payload(
        self,
        event: OrganizationCalendarEvent,
        event_name: str,
        *,
        correlation_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, object]:
        participants = [
            {
                "person_id": str(item.person_id),
                "employee_id": str(item.employee_id),
                "nextcloud_user_id": item.nextcloud_user_id,
                "email": item.participant_email,
                "display_name": item.participant_name,
                "membership_status": item.membership_status,
            }
            for item in event.participants
        ]
        return {
            "contract_version": CALENDAR_CONTRACT_VERSION,
            "correlation_id": correlation_id,
            "idempotency_key": idempotency_key,
            "event_type": event_name,
            "action": "CANCEL_EVENT"
            if event_name == CALENDAR_CANCELLED
            else "UPSERT_EVENT",
            "event_id": str(event.event_id),
            "organization_id": str(self.organization_id),
            "event_version": event.version,
            "uid": event.ical_uid,
            "business_status": event.business_status,
            "event_scope": event.event_scope,
            "title": event.title,
            "description": event.description,
            "event_details": event.event_details,
            "location": event.location,
            "meeting_url": event.meeting_url,
            "timezone": event.timezone,
            "all_day": event.all_day,
            "start": event.start_at.isoformat() if event.start_at else None,
            "end": event.end_at.isoformat() if event.end_at else None,
            "start_date": event.start_date.isoformat() if event.start_date else None,
            "end_date_exclusive": (
                event.end_date_exclusive.isoformat()
                if event.end_date_exclusive
                else None
            ),
            "reminders": sorted(
                [item.offset_minutes for item in event.reminders], reverse=True
            ),
            "participants": participants,
            "recurrence": None,
            "source_of_truth": "ERP",
        }

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
    "CALENDAR_CANCELLED",
    "CALENDAR_CONTRACT_VERSION",
    "CALENDAR_UPSERTED",
    "CalendarConflictError",
    "CalendarDeliveryIssue",
    "CalendarError",
    "CalendarEventData",
    "CalendarNotFoundError",
    "DEFAULT_TIMEZONE",
    "MAX_REMINDERS",
    "OrganizationCalendarService",
    "ParticipantCandidate",
    "build_event_data",
]
