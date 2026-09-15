"""Mailbox fan-out and Nextcloud-to-Selfcare relay ordering."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from app.config import settings
from app.tasks import email as email_tasks


def test_mailbox_completion_fans_out_activation_and_nextcloud(monkeypatch) -> None:
    organization_id = uuid4()
    employee_id = uuid4()
    events: list[str] = []
    db = MagicMock()
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
                created=True,
                already_exists=False,
                skipped=[],
            )

    activation_task = MagicMock()
    activation_task.apply_async.side_effect = lambda **_kwargs: events.append(
        "activation"
    )
    nextcloud_task = MagicMock()
    nextcloud_task.apply_async.side_effect = lambda **_kwargs: events.append(
        "nextcloud"
    )
    monkeypatch.setattr(email_tasks, "session_for_org", session_for_org)
    monkeypatch.setattr(
        email_tasks, "_record_workforce_stage", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        email_tasks, "send_employee_mailbox_activation", activation_task
    )
    monkeypatch.setattr(
        email_tasks, "run_employee_nextcloud_provisioning", nextcloud_task
    )
    monkeypatch.setattr(
        "app.services.people.hr.mailbox_provisioning.EmployeeMailboxProvisioningService",
        MailboxService,
    )
    monkeypatch.setattr(settings, "nextcloud_provisioning_enabled", True)

    result = email_tasks.run_employee_mailcow_provisioning.run(
        str(employee_id), str(organization_id)
    )

    assert result["email"] == "person@dotmac.ng"
    assert events == ["mailcow", "commit", "activation", "nextcloud"]
    activation_task.apply_async.assert_called_once_with(
        args=[str(employee_id), str(organization_id)]
    )
    nextcloud_task.apply_async.assert_called_once_with(
        args=[str(employee_id), str(organization_id)]
    )


def test_nextcloud_task_enqueues_selfcare_after_binding(monkeypatch) -> None:
    organization_id = uuid4()
    employee_id = uuid4()
    employee = SimpleNamespace(dotmac_sub_access_enabled=True)
    db = MagicMock()
    db.get.return_value = employee

    @contextmanager
    def session_for_org(_organization_id):
        yield db

    class NextcloudService:
        def __init__(self, _db):
            pass

        def ensure_account(self, _organization_id, _employee_id):
            return SimpleNamespace(
                user_id="person@dotmac.ng",
                created=True,
                already_exists=False,
                enabled=False,
                skipped=[],
            )

    selfcare_task = MagicMock()
    monkeypatch.setattr(email_tasks, "session_for_org", session_for_org)
    monkeypatch.setattr(
        email_tasks, "_record_workforce_stage", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        "app.services.people.hr.nextcloud_provisioning.EmployeeNextcloudProvisioningService",
        NextcloudService,
    )
    monkeypatch.setattr(
        "app.tasks.staff_sync.sync_employee_staff_account", selfcare_task
    )
    monkeypatch.setattr(settings, "dotmac_sub_staff_sync_enabled", True, raising=False)

    result = email_tasks.run_employee_nextcloud_provisioning.run(
        str(employee_id), str(organization_id)
    )

    assert result["user_id"] == "person@dotmac.ng"
    selfcare_task.apply_async.assert_called_once_with(
        args=[str(employee_id), str(organization_id)]
    )
