"""ERP -> dotmac_sub staff sync — sync_employee decision logic (hermetic)."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.config import settings
from app.models.people.hr.employee import EmployeeStatus
from app.services.dotmac_sub import staff_sync


class FakeClient:
    def __init__(self, existing: dict | None = None):
        self.existing = existing
        self.created = []
        self.active_calls = []

    def get_staff_account(self, email):
        return self.existing

    def create_staff_account(self, **kwargs):
        self.created.append(kwargs)
        return {"id": "acc-123", "email": kwargs["email"], "created": True}

    def set_staff_account_active(self, account_id, *, is_active):
        self.active_calls.append((account_id, is_active))
        return {"id": account_id, "is_active": is_active}

    def close(self):
        pass


def _employee(status, *, email="tech@dotmac.io", account_id=None):
    return SimpleNamespace(
        employee_id=uuid4(),
        organization_id=uuid4(),
        employee_code="EMP-1",
        status=status,
        work_email=email,
        personal_email=None,
        first_name="Field",
        last_name="Tech",
        dotmac_sub_account_id=account_id,
        dotmac_sub_staff_synced_at=None,
    )


@pytest.fixture(autouse=True)
def _enable_staff_sync(monkeypatch):
    monkeypatch.setattr(settings, "dotmac_sub_staff_sync_enabled", True, raising=False)
    monkeypatch.setattr(
        settings, "dotmac_sub_staff_default_role", "staff", raising=False
    )


def test_active_employee_without_account_is_created_and_invited():
    client = FakeClient(existing=None)
    emp = _employee(EmployeeStatus.ACTIVE)

    result = staff_sync.sync_employee(None, emp, client=client)

    assert result["action"] == "created"
    assert emp.dotmac_sub_account_id == "acc-123"
    assert emp.dotmac_sub_staff_synced_at is not None
    assert client.created[0]["email"] == "tech@dotmac.io"
    assert client.created[0]["send_invite"] is True
    assert client.created[0]["role"] == "staff"


def test_active_employee_with_inactive_account_is_reenabled():
    client = FakeClient(existing={"id": "acc-9", "is_active": False})
    emp = _employee(EmployeeStatus.ACTIVE)

    result = staff_sync.sync_employee(None, emp, client=client)

    assert result["action"] == "enabled"
    assert client.active_calls == [("acc-9", True)]
    assert not client.created


def test_terminated_employee_account_is_disabled():
    client = FakeClient(existing={"id": "acc-9", "is_active": True})
    emp = _employee(EmployeeStatus.TERMINATED, account_id="acc-9")

    result = staff_sync.sync_employee(None, emp, client=client)

    assert result["action"] == "disabled"
    assert client.active_calls == [("acc-9", False)]


def test_terminated_employee_without_account_is_skipped():
    client = FakeClient(existing=None)
    emp = _employee(EmployeeStatus.TERMINATED)

    result = staff_sync.sync_employee(None, emp, client=client)

    assert result["action"] == "skipped"
    assert not client.active_calls


def test_draft_and_missing_email_are_skipped():
    client = FakeClient()
    assert (
        staff_sync.sync_employee(None, _employee(EmployeeStatus.DRAFT), client=client)[
            "action"
        ]
        == "skipped"
    )
    assert (
        staff_sync.sync_employee(
            None, _employee(EmployeeStatus.ACTIVE, email=None), client=client
        )["action"]
        == "skipped"
    )


def test_disabled_flag_skips_everything(monkeypatch):
    monkeypatch.setattr(settings, "dotmac_sub_staff_sync_enabled", False, raising=False)
    client = FakeClient()
    result = staff_sync.sync_employee(
        None, _employee(EmployeeStatus.ACTIVE), client=client
    )
    assert result["action"] == "skipped"
    assert not client.created
