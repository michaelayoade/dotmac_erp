from datetime import datetime, timezone
from unittest.mock import Mock, patch
from uuid import uuid4

import httpx
import pytest

from app.models.people.hr.employee import EmployeeStatus
from app.services.mailcow.client import MailcowClient, MailcowClientError
from app.services.mailcow.config import MailcowProvisioningConfig
from app.services.people.hr.mailbox_provisioning import (
    EmployeeMailboxProvisioningService,
    hash_mailbox_activation_token,
)


def _config(*, enabled: bool = True) -> MailcowProvisioningConfig:
    return MailcowProvisioningConfig(
        enabled=enabled,
        base_url="https://mail.dotmac.ng/api/v1",
        api_key="test-key",
        request_timeout=20.0,
        domain="dotmac.ng",
        quota_mb=1024,
    )


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


def test_ensure_mailbox_is_idempotent() -> None:
    employee_id = uuid4()
    employee = Mock()
    employee.employee_id = employee_id
    employee.employee_code = "EMP-1"
    employee.status = EmployeeStatus.ACTIVE
    employee.personal_email = "ada.personal@example.com"
    employee.mailcow_activated_at = None
    employee.mailcow_provisioning_requested_at = None
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
    mailcow.create_mailbox.assert_not_called()


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
