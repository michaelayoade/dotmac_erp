from contextlib import contextmanager
import sys
from types import ModuleType, SimpleNamespace
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch
from uuid import uuid4

import httpx
import pytest

from app.models.people.hr.employee import EmployeeStatus
from app.models.person import PersonStatus
from app.services.mailcow.client import (
    MailcowAmbiguousMailboxError,
    MailcowClient,
    MailcowClientError,
)
from app.services.mailcow.config import (
    MailcowProvisioningConfig,
    get_mailcow_provisioning_config,
)
from app.services.people.hr.mailbox_provisioning import (
    EmployeeMailboxProvisioningService,
    activate_employee_mailbox,
    hash_mailbox_activation_token,
)


def _config(
    *, enabled: bool = True, profile_sync_enabled: bool = False
) -> MailcowProvisioningConfig:
    return MailcowProvisioningConfig(
        enabled=enabled,
        base_url="https://mail.dotmac.ng/api/v1",
        api_key="test-key",
        request_timeout=20.0,
        domain="dotmac.ng",
        quota_mb=1024,
        profile_sync_enabled=profile_sync_enabled,
    )


def _profile_employee(organization_id):
    return SimpleNamespace(
        employee_id=uuid4(),
        organization_id=organization_id,
        status=EmployeeStatus.ACTIVE,
        date_of_leaving=None,
        designation_id=uuid4(),
        designation=SimpleNamespace(
            organization_id=organization_id,
            is_active=True,
            designation_name="Network Engineer",
        ),
        person=SimpleNamespace(
            organization_id=organization_id,
            is_active=True,
            status=PersonStatus.active,
            email="ada@dotmac.ng",
            name="Ada Lovelace",
        ),
    )


def test_profile_sync_config_is_independent_of_provisioning() -> None:
    settings = SimpleNamespace(
        mailcow_provisioning_enabled=False,
        mailcow_profile_sync_enabled=True,
        mailcow_base_url="https://mail.dotmac.ng/api/v1",
        mailcow_api_key="test-key",
        mailcow_request_timeout=20.0,
        mailcow_provisioning_domain="dotmac.ng",
        mailcow_provisioning_quota_mb=1024,
        mailcow_activation_ttl_hours=24,
    )
    with patch("app.services.mailcow.config.settings", settings):
        config = get_mailcow_provisioning_config()

    assert config.enabled is False
    assert config.profile_sync_enabled is True


@patch("app.services.mailcow.client.httpx.Client")
def test_create_mailbox_sends_mailcow_payload(mock_client_class: Mock) -> None:
    response = Mock()
    response.status_code = 200
    response.json.return_value = [{"type": "success", "msg": "mailbox_added"}]
    response.raise_for_status.return_value = None
    http_client = mock_client_class.return_value.__enter__.return_value
    http_client.post.return_value = response
    client = MailcowClient(
        base_url="https://mail.dotmac.ng/api/v1",
        api_key="test-key",
    )

    client.create_mailbox(
        "Ada.Lovelace@dotmac.ng",
        name="Ada Lovelace",
        password="temporary-secret",
        quota_mb=1024,
    )

    request = http_client.post.call_args
    assert request.args == ("https://mail.dotmac.ng/api/v1/add/mailbox",)
    assert request.kwargs["headers"] == {"X-API-Key": "test-key"}
    assert request.kwargs["json"] == {
        "active": "1",
        "domain": "dotmac.ng",
        "local_part": "ada.lovelace",
        "name": "Ada Lovelace",
        "password": "temporary-secret",
        "password2": "temporary-secret",
        "quota": 1024,
        "force_pw_update": "1",
    }


@patch("app.services.mailcow.client.httpx.Client")
def test_create_mailbox_raises_for_mailcow_error_payload(
    mock_client_class: Mock,
) -> None:
    response = Mock()
    response.status_code = 200
    response.json.return_value = [{"type": "error", "msg": "domain_not_found"}]
    response.raise_for_status.return_value = None
    mock_client_class.return_value.__enter__.return_value.post.return_value = response
    client = MailcowClient(
        base_url="https://mail.dotmac.ng/api/v1",
        api_key="test-key",
    )

    with pytest.raises(MailcowClientError, match="domain_not_found"):
        client.create_mailbox(
            "ada@dotmac.ng",
            name="Ada Lovelace",
            password="temporary-secret",
            quota_mb=1024,
        )


@patch("app.services.mailcow.client.httpx.Client")
def test_get_mailbox_reports_authentication_error(mock_client_class: Mock) -> None:
    request = httpx.Request(
        "GET", "https://mail.dotmac.ng/api/v1/get/mailbox/ada@dotmac.ng"
    )
    response = httpx.Response(
        401,
        request=request,
        json={"type": "error", "msg": "authentication failed"},
    )
    mock_client_class.return_value.__enter__.return_value.get.return_value = response
    client = MailcowClient(
        base_url="https://mail.dotmac.ng/api/v1",
        api_key="wrong-key",
    )

    with pytest.raises(
        MailcowClientError,
        match="HTTP 401: authentication failed",
    ):
        client.get_mailbox("ada@dotmac.ng")


@patch("app.services.mailcow.client.httpx.Client")
def test_get_mailbox_rejects_ambiguous_response(mock_client_class: Mock) -> None:
    response = Mock(status_code=200)
    response.json.return_value = [{"username": "ada@dotmac.ng"}] * 2
    mock_client_class.return_value.__enter__.return_value.get.return_value = response
    client = MailcowClient(base_url="https://mail.dotmac.ng/api/v1", api_key="test-key")

    with pytest.raises(MailcowAmbiguousMailboxError):
        client.get_mailbox("ada@dotmac.ng")


@patch("app.services.mailcow.client.httpx.Client")
@pytest.mark.parametrize("payload", [ValueError("bad JSON"), {"type": "error"}])
def test_get_mailbox_reports_invalid_api_response(
    mock_client_class: Mock, payload
) -> None:
    response = Mock(status_code=200)
    if isinstance(payload, Exception):
        response.json.side_effect = payload
    else:
        response.json.return_value = payload
    mock_client_class.return_value.__enter__.return_value.get.return_value = response
    client = MailcowClient(base_url="https://mail.dotmac.ng/api/v1", api_key="test-key")

    with pytest.raises(MailcowClientError, match="Mailbox lookup"):
        client.get_mailbox("ada@dotmac.ng")


@patch("app.services.mailcow.client.httpx.Client")
def test_set_mailbox_custom_attribute_preserves_and_verifies_other_values(
    mock_client_class: Mock,
) -> None:
    initial = Mock()
    initial.status_code = 200
    initial.json.return_value = {
        "username": "ada@dotmac.ng",
        "active": "1",
        "custom_attributes": {"department": "Engineering"},
    }
    initial.raise_for_status.return_value = None
    write = Mock()
    write.status_code = 200
    write.json.return_value = {"type": "success", "msg": "mailbox_modified"}
    write.raise_for_status.return_value = None
    verified = Mock()
    verified.status_code = 200
    verified.json.return_value = {
        "username": "ada@dotmac.ng",
        "active": "1",
        "custom_attributes": {
            "department": "Engineering",
            "designation": "Network Engineer",
        },
    }
    verified.raise_for_status.return_value = None
    http_client = mock_client_class.return_value.__enter__.return_value
    http_client.get.side_effect = [initial, verified]
    http_client.post.return_value = write
    client = MailcowClient(
        base_url="https://mail.dotmac.ng/api/v1",
        api_key="test-key",
    )

    changed = client.set_mailbox_custom_attribute(
        "ada@dotmac.ng",
        "designation",
        "Network Engineer",
    )

    assert changed is True
    assert http_client.post.call_args.kwargs["json"] == {
        "items": ["ada@dotmac.ng"],
        "attr": {
            "attribute": ["department", "designation"],
            "value": ["Engineering", "Network Engineer"],
        },
    }


@patch("app.services.mailcow.client.httpx.Client")
def test_set_mailbox_custom_attribute_is_idempotent(
    mock_client_class: Mock,
) -> None:
    response = Mock()
    response.status_code = 200
    response.json.return_value = {
        "username": "ada@dotmac.ng",
        "active": "1",
        "custom_attributes": {"designation": "Network Engineer"},
    }
    response.raise_for_status.return_value = None
    http_client = mock_client_class.return_value.__enter__.return_value
    http_client.get.return_value = response
    client = MailcowClient(
        base_url="https://mail.dotmac.ng/api/v1",
        api_key="test-key",
    )

    changed = client.set_mailbox_custom_attribute(
        "ada@dotmac.ng",
        "designation",
        "Network Engineer",
    )

    assert changed is False
    http_client.post.assert_not_called()


@patch("app.services.mailcow.client.httpx.Client")
def test_custom_attribute_rejects_unsupported_map_without_write(
    mock_client_class: Mock,
) -> None:
    response = Mock(status_code=200)
    response.json.return_value = {
        "username": "ada@dotmac.ng",
        "active": "1",
        "custom_attributes": ["department", "Engineering"],
    }
    http_client = mock_client_class.return_value.__enter__.return_value
    http_client.get.return_value = response
    client = MailcowClient(base_url="https://mail.dotmac.ng/api/v1", api_key="test-key")

    with pytest.raises(MailcowClientError, match="not a string map"):
        client.set_mailbox_custom_attribute("ada@dotmac.ng", "designation", "Engineer")

    http_client.post.assert_not_called()


@patch("app.services.mailcow.client.httpx.Client")
def test_custom_attribute_removal_preserves_other_values(
    mock_client_class: Mock,
) -> None:
    initial = Mock(status_code=200)
    initial.json.return_value = {
        "username": "ada@dotmac.ng",
        "active": "1",
        "custom_attributes": {
            "designation": "Engineer",
            "department": "Engineering",
            "Designation": "Legacy",
        },
    }
    verified = Mock(status_code=200)
    verified.json.return_value = {
        "username": "ada@dotmac.ng",
        "active": "1",
        "custom_attributes": {"department": "Engineering", "Designation": "Legacy"},
    }
    write = Mock(status_code=200)
    write.json.return_value = {"type": "success", "msg": "mailbox_modified"}
    http_client = mock_client_class.return_value.__enter__.return_value
    http_client.get.side_effect = [initial, verified]
    http_client.post.return_value = write
    client = MailcowClient(base_url="https://mail.dotmac.ng/api/v1", api_key="test-key")

    assert client.set_mailbox_custom_attribute("ada@dotmac.ng", "designation", None)

    assert http_client.post.call_args.kwargs["json"]["attr"] == {
        "attribute": ["department", "Designation"],
        "value": ["Engineering", "Legacy"],
    }


@patch("app.services.mailcow.client.httpx.Client")
def test_custom_attribute_readback_mismatch_fails(
    mock_client_class: Mock,
) -> None:
    response = Mock(status_code=200)
    response.json.return_value = {
        "username": "ada@dotmac.ng",
        "active": "1",
        "custom_attributes": {"department": "Engineering"},
    }
    write = Mock(status_code=200)
    write.json.return_value = {"type": "success", "msg": "mailbox_modified"}
    http_client = mock_client_class.return_value.__enter__.return_value
    http_client.get.return_value = response
    http_client.post.return_value = write
    client = MailcowClient(base_url="https://mail.dotmac.ng/api/v1", api_key="test-key")

    with pytest.raises(MailcowClientError, match="could not be verified"):
        client.set_mailbox_custom_attribute("ada@dotmac.ng", "designation", "Engineer")


@patch("app.services.mailcow.client.httpx.Client")
def test_custom_attribute_invalid_write_response_is_retryable(
    mock_client_class: Mock,
) -> None:
    initial = Mock(status_code=200)
    initial.json.return_value = {
        "username": "ada@dotmac.ng",
        "active": "1",
        "custom_attributes": {},
    }
    write = Mock(status_code=200)
    write.json.side_effect = ValueError("bad JSON")
    http_client = mock_client_class.return_value.__enter__.return_value
    http_client.get.return_value = initial
    http_client.post.return_value = write
    client = MailcowClient(base_url="https://mail.dotmac.ng/api/v1", api_key="test-key")

    with pytest.raises(MailcowClientError, match="returned invalid JSON"):
        client.set_mailbox_custom_attribute("ada@dotmac.ng", "designation", "Engineer")


def test_ensure_mailbox_is_idempotent() -> None:
    employee_id = uuid4()
    employee = Mock()
    employee.employee_id = employee_id
    employee.employee_code = "EMP-1"
    employee.status = EmployeeStatus.ACTIVE
    employee.personal_email = "ada.personal@example.com"
    employee.mailcow_activated_at = None
    employee.mailcow_provisioning_requested_at = datetime.now(timezone.utc)
    employee.mailcow_mailbox_provisioned_at = None
    employee.mailcow_activation_expires_at = None
    employee.mailcow_activation_sent_at = None
    employee.organization_id = uuid4()
    employee.person.email = "ada@dotmac.ng"
    employee.person.name = "Ada Lovelace"
    db = Mock()
    db.scalar.return_value = employee
    mailcow = Mock()
    mailcow.get_mailbox.return_value = {"username": "ada@dotmac.ng"}
    service = EmployeeMailboxProvisioningService(
        db,
        config=_config(),
        mailcow_client=mailcow,
    )

    result = service.ensure_mailbox(uuid4(), employee_id)

    assert result.already_exists
    assert not result.created
    assert employee.mailcow_mailbox_provisioned_at is not None
    mailcow.create_mailbox.assert_not_called()


def test_ensure_mailbox_does_not_backfill_without_forward_request() -> None:
    employee = Mock()
    employee.status = EmployeeStatus.ACTIVE
    employee.mailcow_provisioning_requested_at = None
    db = Mock()
    db.scalar.return_value = employee
    mailcow = Mock()
    service = EmployeeMailboxProvisioningService(
        db,
        config=_config(),
        mailcow_client=mailcow,
    )

    result = service.ensure_mailbox(uuid4(), uuid4())

    assert result.skipped == ["employee has no forward provisioning request"]
    mailcow.get_mailbox.assert_not_called()


def test_ensure_mailbox_creates_and_verifies() -> None:
    employee_id = uuid4()
    organization_id = uuid4()
    employee = Mock()
    employee.employee_id = employee_id
    employee.organization_id = organization_id
    employee.employee_code = "EMP-1"
    employee.status = EmployeeStatus.ACTIVE
    employee.personal_email = "ada.personal@example.com"
    employee.mailcow_activated_at = None
    employee.mailcow_provisioning_requested_at = datetime.now(timezone.utc)
    employee.mailcow_activation_expires_at = None
    employee.mailcow_activation_sent_at = None
    employee.person.email = "ada@dotmac.ng"
    employee.person.name = "Ada Lovelace"
    db = Mock()
    db.scalar.return_value = employee
    mailcow = Mock()
    mailcow.get_mailbox.side_effect = [None, {"username": "ada@dotmac.ng"}]
    service = EmployeeMailboxProvisioningService(
        db,
        config=_config(),
        mailcow_client=mailcow,
    )

    result = service.ensure_mailbox(organization_id, employee_id)

    assert result.created
    mailcow.create_mailbox.assert_called_once()
    assert mailcow.create_mailbox.call_args.args == ("ada@dotmac.ng",)
    assert mailcow.create_mailbox.call_args.kwargs["force_password_update"] is True
    assert result.activation_token
    assert result.activation_token.startswith(f"{organization_id}.")
    assert employee.mailcow_activation_token_hash == hash_mailbox_activation_token(
        result.activation_token
    )
    assert result.activation_token != employee.mailcow_activation_token_hash
    assert employee.mailcow_activation_expires_at > datetime.now(timezone.utc)


def test_ensure_mailbox_skips_unmanaged_domain() -> None:
    employee = Mock()
    employee.status = EmployeeStatus.ACTIVE
    employee.personal_email = "ada.personal@example.com"
    employee.person.email = "ada@example.com"
    db = Mock()
    db.scalar.return_value = employee
    mailcow = Mock()
    service = EmployeeMailboxProvisioningService(
        db,
        config=_config(),
        mailcow_client=mailcow,
    )

    result = service.ensure_mailbox(uuid4(), uuid4())

    assert result.skipped == ["work email domain example.com is not managed by Mailcow"]
    mailcow.get_mailbox.assert_not_called()


def test_ensure_mailbox_skips_exited_employee() -> None:
    employee = Mock()
    employee.status = EmployeeStatus.RESIGNED
    db = Mock()
    db.scalar.return_value = employee
    mailcow = Mock()
    service = EmployeeMailboxProvisioningService(
        db,
        config=_config(),
        mailcow_client=mailcow,
    )

    result = service.ensure_mailbox(uuid4(), uuid4())

    assert result.skipped == ["employee is no longer provisionable"]
    mailcow.get_mailbox.assert_not_called()


def test_sync_mailbox_profile_projects_employee_designation() -> None:
    organization_id = uuid4()
    employee = _profile_employee(organization_id)
    db = Mock()
    db.scalar.return_value = employee
    mailcow = Mock()
    mailcow.get_mailbox.return_value = {
        "username": "ada@dotmac.ng",
        "active": "1",
    }
    mailcow.set_mailbox_custom_attribute.return_value = True
    service = EmployeeMailboxProvisioningService(
        db,
        config=_config(enabled=False, profile_sync_enabled=True),
        mailcow_client=mailcow,
    )

    result = service.sync_mailbox_profile(organization_id, employee.employee_id)

    assert result.mailbox_found
    assert result.designation_changed
    assert result.designation == "Network Engineer"
    mailcow.set_mailbox_custom_attribute.assert_called_once_with(
        "ada@dotmac.ng",
        "designation",
        "Network Engineer",
    )


@pytest.mark.parametrize(
    ("provisioning_enabled", "profile_sync_enabled", "writes"),
    [
        (False, False, False),
        (False, True, True),
        (True, False, False),
        (True, True, True),
    ],
)
def test_profile_sync_has_independent_control(
    provisioning_enabled: bool, profile_sync_enabled: bool, writes: bool
) -> None:
    organization_id = uuid4()
    employee = _profile_employee(organization_id)
    db = Mock()
    db.scalar.return_value = employee
    mailcow = Mock()
    mailcow.get_mailbox.return_value = {"username": "ada@dotmac.ng", "active": "1"}
    mailcow.set_mailbox_custom_attribute.return_value = False
    service = EmployeeMailboxProvisioningService(
        db,
        config=_config(
            enabled=provisioning_enabled,
            profile_sync_enabled=profile_sync_enabled,
        ),
        mailcow_client=mailcow,
    )

    result = service.sync_mailbox_profile(organization_id, employee.employee_id)

    assert result.mailbox_found is writes
    assert result.skipped == ([] if writes else ["mailcow profile sync disabled"])
    assert mailcow.set_mailbox_custom_attribute.called is writes
    mailcow.create_mailbox.assert_not_called()


@patch("app.services.mailcow.client.httpx.Client")
def test_profile_sync_with_provisioning_disabled_verifies_http_write(
    mock_client_class: Mock,
) -> None:
    organization_id = uuid4()
    employee = _profile_employee(organization_id)
    db = Mock()
    db.scalar.return_value = employee
    initial = Mock(status_code=200)
    initial.json.return_value = {
        "username": "ada@dotmac.ng",
        "active": "1",
        "custom_attributes": {"department": "Engineering"},
    }
    verified = Mock(status_code=200)
    verified.json.return_value = {
        "username": "ada@dotmac.ng",
        "active": "1",
        "custom_attributes": {
            "department": "Engineering",
            "designation": "Network Engineer",
        },
    }
    write = Mock(status_code=200)
    write.json.return_value = {"type": "success", "msg": "mailbox_modified"}
    http_client = mock_client_class.return_value.__enter__.return_value
    http_client.get.side_effect = [initial, initial, verified]
    http_client.post.return_value = write
    service = EmployeeMailboxProvisioningService(
        db,
        config=_config(enabled=False, profile_sync_enabled=True),
        mailcow_client=MailcowClient(
            base_url="https://mail.dotmac.ng/api/v1", api_key="test-key"
        ),
    )

    result = service.sync_mailbox_profile(organization_id, employee.employee_id)

    assert result.designation_changed is True
    assert http_client.post.call_args.kwargs["json"]["attr"] == {
        "attribute": ["department", "designation"],
        "value": ["Engineering", "Network Engineer"],
    }
    assert http_client.get.call_count == 3


@pytest.mark.parametrize(
    "reason",
    [
        "draft",
        "on_leave",
        "suspended",
        "person_inactive",
        "person_other_tenant",
        "employee_other_tenant",
        "employment_ended",
        "unmanaged_domain",
        "designation_other_tenant",
        "designation_missing",
    ],
)
def test_profile_sync_skips_ineligible_erp_state(reason: str) -> None:
    organization_id = uuid4()
    employee = _profile_employee(organization_id)
    if reason == "draft":
        employee.status = EmployeeStatus.DRAFT
    elif reason == "on_leave":
        employee.status = EmployeeStatus.ON_LEAVE
    elif reason == "suspended":
        employee.status = EmployeeStatus.SUSPENDED
    elif reason == "person_inactive":
        employee.person.is_active = False
    elif reason == "person_other_tenant":
        employee.person.organization_id = uuid4()
    elif reason == "employee_other_tenant":
        employee.organization_id = uuid4()
    elif reason == "employment_ended":
        employee.date_of_leaving = datetime.now(timezone.utc).date() - timedelta(days=1)
    elif reason == "unmanaged_domain":
        employee.person.email = "ada@example.com"
    elif reason == "designation_other_tenant":
        employee.designation.organization_id = uuid4()
    elif reason == "designation_missing":
        employee.designation = None
    db = Mock()
    db.scalar.return_value = employee
    mailcow = Mock()
    service = EmployeeMailboxProvisioningService(
        db,
        config=_config(enabled=False, profile_sync_enabled=True),
        mailcow_client=mailcow,
    )

    result = service.sync_mailbox_profile(organization_id, employee.employee_id)

    assert result.skipped
    mailcow.get_mailbox.assert_not_called()
    mailcow.set_mailbox_custom_attribute.assert_not_called()


@pytest.mark.parametrize(
    ("mailbox", "skip_reason"),
    [
        (None, "mailbox does not exist"),
        (
            {"username": "other@dotmac.ng", "active": "1"},
            "mailbox mapping is ambiguous",
        ),
        ({"username": "ada@dotmac.ng", "active": "0"}, "mailbox is inactive"),
    ],
)
def test_profile_sync_skips_unavailable_mailbox(mailbox, skip_reason: str) -> None:
    organization_id = uuid4()
    employee = _profile_employee(organization_id)
    db = Mock()
    db.scalar.return_value = employee
    mailcow = Mock()
    mailcow.get_mailbox.return_value = mailbox
    service = EmployeeMailboxProvisioningService(
        db,
        config=_config(enabled=False, profile_sync_enabled=True),
        mailcow_client=mailcow,
    )

    result = service.sync_mailbox_profile(organization_id, employee.employee_id)

    assert result.skipped == [skip_reason]
    mailcow.set_mailbox_custom_attribute.assert_not_called()
    mailcow.create_mailbox.assert_not_called()


def test_profile_sync_skips_ambiguous_mailbox_response() -> None:
    organization_id = uuid4()
    employee = _profile_employee(organization_id)
    db = Mock()
    db.scalar.return_value = employee
    mailcow = Mock()
    mailcow.get_mailbox.side_effect = MailcowAmbiguousMailboxError("multiple records")
    service = EmployeeMailboxProvisioningService(
        db,
        config=_config(enabled=False, profile_sync_enabled=True),
        mailcow_client=mailcow,
    )

    result = service.sync_mailbox_profile(organization_id, employee.employee_id)

    assert result.skipped == ["mailbox mapping is ambiguous"]
    mailcow.set_mailbox_custom_attribute.assert_not_called()


def test_profile_sync_removes_only_managed_attribute() -> None:
    organization_id = uuid4()
    employee = _profile_employee(organization_id)
    employee.designation_id = None
    employee.designation = None
    db = Mock()
    db.scalar.return_value = employee
    mailcow = Mock()
    mailcow.get_mailbox.return_value = {"username": "ada@dotmac.ng", "active": "1"}
    mailcow.set_mailbox_custom_attribute.return_value = True
    service = EmployeeMailboxProvisioningService(
        db,
        config=_config(enabled=False, profile_sync_enabled=True),
        mailcow_client=mailcow,
    )

    result = service.sync_mailbox_profile(organization_id, employee.employee_id)

    assert result.designation_changed
    mailcow.set_mailbox_custom_attribute.assert_called_once_with(
        "ada@dotmac.ng", "designation", None
    )


def test_provisioning_ignores_profile_flag_for_creation_but_not_designation() -> None:
    organization_id = uuid4()
    employee = _profile_employee(organization_id)
    employee.personal_email = "ada.personal@example.com"
    employee.mailcow_provisioning_requested_at = datetime.now(timezone.utc)
    employee.mailcow_mailbox_provisioned_at = None
    employee.mailcow_activated_at = datetime.now(timezone.utc)
    db = Mock()
    db.scalar.return_value = employee
    mailcow = Mock()
    mailcow.get_mailbox.return_value = {"username": "ada@dotmac.ng"}
    service = EmployeeMailboxProvisioningService(
        db,
        config=_config(enabled=True, profile_sync_enabled=False),
        mailcow_client=mailcow,
    )

    result = service.ensure_mailbox(organization_id, employee.employee_id)

    assert result.already_exists
    mailcow.set_mailbox_custom_attribute.assert_not_called()


def test_activation_locks_only_employee_row(monkeypatch) -> None:
    organization_id = uuid4()
    token = f"{organization_id}.secret"
    employee = Mock()
    employee.mailcow_activation_expires_at = datetime.now(timezone.utc) + timedelta(
        hours=1
    )
    employee.mailcow_activated_at = None
    employee.person.email = "ada@dotmac.ng"
    db = Mock()
    db.scalar.return_value = employee

    @contextmanager
    def session_for_org(_organization_id):
        yield db

    session_module = ModuleType("app.db.session_context")
    session_module.session_for_org = session_for_org
    auth_flow_module = ModuleType("app.schemas.auth_flow")
    auth_flow_module.validate_password_strength = lambda _password: None
    monkeypatch.setitem(sys.modules, "app.db.session_context", session_module)
    monkeypatch.setitem(sys.modules, "app.schemas.auth_flow", auth_flow_module)
    monkeypatch.setattr(
        "app.services.people.hr.mailbox_provisioning.get_mailcow_provisioning_config",
        lambda: _config(),
    )
    client = Mock()
    monkeypatch.setattr(
        "app.services.people.hr.mailbox_provisioning.MailcowClient",
        lambda **_kwargs: client,
    )

    activate_employee_mailbox(token, "Strong-password-123!")

    statement = db.scalar.call_args.args[0]
    assert not statement._with_options
    assert statement._for_update_arg is not None
    client.update_mailbox_password.assert_called_once_with(
        "ada@dotmac.ng", "Strong-password-123!", active=True
    )
