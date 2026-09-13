"""Mailcow -> Nextcloud -> Selfcare workforce relay ordering."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from app.config import settings
from app.tasks import email as email_tasks


def test_mailcow_task_enqueues_selfcare_only_after_nextcloud_binding(
    monkeypatch,
) -> None:
    organization_id = uuid4()
    employee_id = uuid4()
    events: list[str] = []
    employee = SimpleNamespace(
        employee_id=employee_id,
        mailcow_mailbox_provisioned_at=object(),
        dotmac_sub_access_enabled=True,
        person=SimpleNamespace(name="Test Person", nextcloud_user_id=None),
    )
    db = MagicMock()
    db.get.return_value = employee
    db.commit.side_effect = lambda: events.append("commit")

    @contextmanager
    def session_for_org(_organization_id):
        yield db

    class MailboxService:
        def __init__(self, _db):
            pass

        def ensure_mailbox(self, _organization_id, _employee_id):
            events.append("mailcow")
            return SimpleNamespace(
                employee_id=str(employee_id),
                email="person@dotmac.ng",
                personal_email="person@example.test",
                activation_token=None,
                created=True,
                already_exists=False,
                skipped=[],
            )

    class NextcloudService:
        def __init__(self, _db):
            pass

        def ensure_account(self, _organization_id, _employee_id):
            events.append("nextcloud")
            employee.person.nextcloud_user_id = "person@dotmac.ng"
            return SimpleNamespace(
                user_id="person@dotmac.ng",
                created=True,
                already_exists=False,
                enabled=False,
                skipped=[],
            )

    selfcare_task = MagicMock()
    selfcare_task.apply_async.side_effect = lambda **_kwargs: events.append("selfcare")
    monkeypatch.setattr(email_tasks, "session_for_org", session_for_org)
    monkeypatch.setattr(
        "app.services.people.hr.mailbox_provisioning.EmployeeMailboxProvisioningService",
        MailboxService,
    )
    monkeypatch.setattr(
        "app.services.people.hr.nextcloud_provisioning.EmployeeNextcloudProvisioningService",
        NextcloudService,
    )
    monkeypatch.setattr(
        "app.tasks.staff_sync.sync_employee_staff_account",
        selfcare_task,
    )
    monkeypatch.setattr(settings, "nextcloud_provisioning_enabled", True)
    monkeypatch.setattr(settings, "dotmac_sub_staff_sync_enabled", True, raising=False)

    result = email_tasks.run_employee_mailcow_provisioning.run(
        str(employee_id), str(organization_id)
    )

    assert result["nextcloud"]["user_id"] == "person@dotmac.ng"
    assert events == ["mailcow", "commit", "nextcloud", "commit", "selfcare"]
    selfcare_task.apply_async.assert_called_once_with(
        args=[str(employee_id), str(organization_id)]
    )
