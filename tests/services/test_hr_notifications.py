from types import SimpleNamespace

import pytest

from app.models.people.hr.employee import EmployeeStatus
from app.services.hr_notifications import (
    HRNotificationService,
    _get_employee_email,
)


def employee(*, work_email=None, personal_email=None, status=None):
    person = SimpleNamespace(email=work_email) if work_email is not None else None
    return SimpleNamespace(
        person=person,
        personal_email=personal_email,
        status=status,
        employee_id="employee-id",
        full_name="Test Employee",
        organization_id="organization-id",
    )


@pytest.mark.parametrize(
    ("work_email", "personal_email", "expected"),
    [
        ("work@example.test", "personal@example.test", "work@example.test"),
        (" work@example.test ", None, "work@example.test"),
        (None, " personal@example.test ", "personal@example.test"),
    ],
)
def test_get_employee_email_prefers_and_normalizes_work_email(
    work_email, personal_email, expected
):
    assert _get_employee_email(
        employee(work_email=work_email, personal_email=personal_email)
    ) == expected


def test_get_employee_email_uses_personal_email_when_no_person_is_linked():
    assert _get_employee_email(employee(personal_email=" personal@example.test ")) == (
        "personal@example.test"
    )


def test_get_employee_email_returns_none_for_ineligible_employee():
    assert (
        _get_employee_email(
            employee(
                work_email="work@example.test",
                personal_email="personal@example.test",
                status=EmployeeStatus.TERMINATED,
            )
        )
        is None
    )


def test_work_anniversary_notification_sends_to_work_email(monkeypatch):
    service = HRNotificationService(db=None)
    sent = {}
    monkeypatch.setattr(
        service,
        "_send",
        lambda template, context, **kwargs: sent.update(kwargs) or True,
    )
    manager = employee(
        work_email=" manager@example.test ",
        personal_email="manager-personal@example.test",
    )

    assert service.send_work_anniversary_notification(
        employee=employee(),
        manager=manager,
        years_of_service=5,
        is_milestone=True,
    ) is True
    assert sent["to_email"] == "manager@example.test"
