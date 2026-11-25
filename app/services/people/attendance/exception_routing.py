"""Determine who can review an attendance exception and notify them."""

from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.notification import EntityType, NotificationChannel, NotificationType
from app.models.people.attendance.attendance_request import (
    ATTENDANCE_EXCEPTION_REASONS,
    AttendanceRequest,
    AttendanceRequestStatus,
)
from app.models.person import Person, PersonStatus
from app.models.rbac import PersonRole, Role
from app.services.notification import NotificationService
from app.services.people.hr.org_resolver import OrgResolver

TEAM_ATTENDANCE_URL = "/people/self/team/attendance"
HR_ATTENDANCE_REQUESTS_URL = "/people/attendance/requests?status=PENDING"


class AttendanceExceptionRouting:
    def __init__(self, db: Session, org_id: UUID, as_of: date) -> None:
        self.db = db
        self.org_id = org_id
        self.as_of = as_of
        self._managers: dict[UUID, UUID | None] = {}
        self._hr: set[UUID] | None = None

    @property
    def hr_notification_recipient_ids(self) -> set[UUID]:
        if self._hr is None:
            self._hr = set(
                self.db.scalars(
                    select(Person.id)
                    .join(PersonRole, PersonRole.person_id == Person.id)
                    .join(Role, Role.id == PersonRole.role_id)
                    .where(
                        Person.organization_id == self.org_id,
                        Person.is_active.is_(True),
                        Person.status == PersonStatus.active,
                        Role.is_active.is_(True),
                        Role.name == "hr_manager",
                    )
                ).all()
            )
        return self._hr

    def manager_id(self, employee_id: UUID) -> UUID | None:
        if employee_id not in self._managers:
            manager = OrgResolver(self.db).get_manager(
                employee_id, self.org_id, as_of=self.as_of
            )
            person_id = None
            if manager and manager.employee_id != employee_id:
                person_id = self.db.scalar(
                    select(Person.id).where(
                        Person.organization_id == self.org_id,
                        Person.id == manager.person_id,
                        Person.is_active.is_(True),
                        Person.status == PersonStatus.active,
                    )
                )
            self._managers[employee_id] = person_id
        return self._managers[employee_id]

    def can_decide(
        self,
        request: AttendanceRequest,
        person_id: UUID,
        *,
        has_hr_access: bool = False,
    ) -> bool:
        manager_id = self.manager_id(request.employee_id)
        if manager_id is None:
            return has_hr_access
        return person_id == manager_id and person_id != request.employee.person_id

    def pending_requests(
        self, *, lock: bool = False, request_id: UUID | None = None
    ) -> list[AttendanceRequest]:
        query = (
            select(AttendanceRequest)
            .options(joinedload(AttendanceRequest.employee))
            .where(
                AttendanceRequest.organization_id == self.org_id,
                AttendanceRequest.status == AttendanceRequestStatus.PENDING,
                AttendanceRequest.reason.in_(ATTENDANCE_EXCEPTION_REASONS.values()),
                AttendanceRequest.from_date == AttendanceRequest.to_date,
                AttendanceRequest.half_day.is_(False),
            )
            .order_by(AttendanceRequest.created_at.desc())
        )
        if request_id is not None:
            query = query.where(AttendanceRequest.attendance_request_id == request_id)
        if lock:
            query = query.with_for_update(of=AttendanceRequest, skip_locked=True)
        return list(self.db.scalars(query).all())

    def manager_queue(
        self, person_id: UUID, *, request_id: UUID | None = None
    ) -> list[AttendanceRequest]:
        return [
            request
            for request in self.pending_requests(request_id=request_id)
            if request.employee.person_id != person_id
            and self.manager_id(request.employee_id) == person_id
        ]

    def notify_reviewers(self, request: AttendanceRequest) -> bool:
        """Notify on submission and when the active review route changes."""
        manager_id = self.manager_id(request.employee_id)
        hr_ids = self.hr_notification_recipient_ids - {request.employee.person_id}
        previous = request.review_notification_state
        manager_key = str(manager_id) if manager_id else None
        fallback_ids = sorted(str(person_id) for person_id in hr_ids if not manager_id)
        state: dict[str, str | list[str] | None] = {
            "manager_id": manager_key,
            "fallback_ids": fallback_ids,
        }
        if previous == state:
            return False

        if manager_id:
            if previous is None or previous["manager_id"] != manager_key:
                self._notify(
                    request,
                    {manager_id},
                    action_url=(
                        f"{TEAM_ATTENDANCE_URL}?request_id={request.attendance_request_id}"
                    ),
                    instruction="Your review is needed.",
                )
            if previous is None:
                self._notify(
                    request,
                    hr_ids - {manager_id},
                    action_url=HR_ATTENDANCE_REQUESTS_URL,
                    instruction="This request is awaiting the reporting officer's review.",
                )
        else:
            already_notified = set((previous or {}).get("fallback_ids") or [])
            self._notify(
                request,
                {
                    person_id
                    for person_id in hr_ids
                    if str(person_id) not in already_notified
                },
                action_url=HR_ATTENDANCE_REQUESTS_URL,
                instruction="HR review is needed because no reporting officer is active.",
            )
        request.review_notification_state = state
        return True

    def _notify(
        self,
        request: AttendanceRequest,
        recipients: set[UUID],
        *,
        action_url: str,
        instruction: str,
    ) -> None:
        if not recipients:
            return
        exception = (request.reason or "").removeprefix("Attendance exception: ")
        NotificationService().create_many(
            self.db,
            organization_id=self.org_id,
            recipient_ids=recipients,
            entity_type=EntityType.ATTENDANCE,
            entity_id=request.attendance_request_id,
            notification_type=NotificationType.SUBMITTED,
            title=f"{exception} request submitted",
            message=(
                f"{request.employee.full_name} requested {exception.lower()} "
                f"for {request.from_date:%Y-%m-%d}. {instruction}"
            ),
            channel=NotificationChannel.IN_APP,
            action_url=action_url,
            actor_id=request.created_by_id,
        )
