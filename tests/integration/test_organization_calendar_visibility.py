"""PostgreSQL coverage for exact calendar recipient visibility."""

from datetime import date, datetime, timezone
from uuid import uuid4

import pytest

from app.models.organization_calendar import (
    CalendarBusinessStatus,
    OrganizationCalendarEvent,
)
from app.models.people.hr.department import Department
from app.models.people.hr.designation import Designation
from app.models.people.hr.employee import Employee, EmployeeStatus
from app.models.person import Person
from app.services.organization_calendar import (
    CalendarNotFoundError,
    OrganizationCalendarService,
)

pytestmark = pytest.mark.integration


def _employee(db, org_id, group_field, group_id, *, status=EmployeeStatus.ACTIVE):
    person = Person(
        organization_id=org_id,
        first_name="Calendar",
        last_name="Tester",
        email=f"{uuid4()}@example.test",
    )
    db.add(person)
    db.flush()
    db.add(
        Employee(
            organization_id=org_id,
            person_id=person.id,
            employee_code=uuid4().hex[:20],
            date_of_joining=date(2026, 1, 1),
            status=status,
            **{group_field: group_id},
        )
    )
    db.flush()
    return person


@pytest.mark.parametrize(
    (
        "group_model",
        "target_key",
        "group_field",
        "code_field",
        "name_field",
        "id_field",
    ),
    [
        (
            Department,
            "departments",
            "department_id",
            "department_code",
            "department_name",
            "department_id",
        ),
        (
            Designation,
            "designations",
            "designation_id",
            "designation_code",
            "designation_name",
            "designation_id",
        ),
    ],
)
def test_dynamic_recipient_list_and_detail_are_exact_and_active(
    db,
    organization,
    group_model,
    target_key,
    group_field,
    code_field,
    name_field,
    id_field,
):
    org_id = organization.organization_id
    target_group = group_model(
        organization_id=org_id,
        **{code_field: uuid4().hex[:12], name_field: "Target group"},
    )
    other_group = group_model(
        organization_id=org_id,
        **{code_field: uuid4().hex[:12], name_field: "Other group"},
    )
    db.add_all([target_group, other_group])
    db.flush()

    target_id = getattr(target_group, id_field)
    recipient = _employee(db, org_id, group_field, target_id)
    unrelated = _employee(db, org_id, group_field, getattr(other_group, id_field))
    inactive = _employee(
        db, org_id, group_field, target_id, status=EmployeeStatus.TERMINATED
    )
    event = OrganizationCalendarEvent(
        organization_id=org_id,
        ical_uid=f"calendar-visibility-{uuid4()}@example.test",
        title="Restricted calendar event",
        recipient_targets={
            "departments": [str(target_id)] if target_key == "departments" else [],
            "designations": [str(target_id)] if target_key == "designations" else [],
        },
        all_day=True,
        start_date=date(2026, 9, 21),
        end_date_exclusive=date(2026, 9, 22),
        business_status=CalendarBusinessStatus.PUBLISHED.value,
        created_by_id=recipient.id,
        updated_by_id=recipient.id,
    )
    db.add(event)
    db.flush()

    service = OrganizationCalendarService(db, org_id)
    range_start = datetime(2026, 9, 21, tzinfo=timezone.utc)
    range_end = datetime(2026, 9, 22, tzinfo=timezone.utc)

    assert [
        item.event_id
        for item in service.list_visible_events_for_person(
            recipient.id, range_start, range_end
        )
    ] == [event.event_id]
    assert (
        service.get_visible_event_for_person(event.event_id, recipient.id).event_id
        == event.event_id
    )

    for person in (unrelated, inactive):
        assert (
            service.list_visible_events_for_person(person.id, range_start, range_end)
            == []
        )
        with pytest.raises(CalendarNotFoundError):
            service.get_visible_event_for_person(event.event_id, person.id)
