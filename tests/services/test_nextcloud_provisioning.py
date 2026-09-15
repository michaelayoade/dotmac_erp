from datetime import datetime, timezone
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest

from app.models.people.hr.employee import EmployeeStatus
from app.services.nextcloud.client import (
    NextcloudError,
    NextcloudProvisioningConfig,
    NextcloudTalkClient,
)
from app.services.people.hr.nextcloud_provisioning import (
    EmployeeNextcloudProvisioningService,
    NextcloudIdentityConflictError,
)


def _config(*, enabled: bool = True) -> NextcloudProvisioningConfig:
    return NextcloudProvisioningConfig(
        enabled=enabled,
        server_url="https://next.dotmac.ng",
        username="erp-provisioning",
        app_password="app-password",
        group="erp-employees",
        quota="1 GB",
        timeout=20.0,
    )


def _employee() -> Mock:
    employee = Mock()
    employee.employee_id = uuid4()
    employee.organization_id = uuid4()
    employee.employee_code = "EMP-1"
    employee.status = EmployeeStatus.ACTIVE
    employee.mailcow_provisioning_requested_at = datetime.now(timezone.utc)
    employee.mailcow_mailbox_provisioned_at = datetime.now(timezone.utc)
    employee.person.email = "ada@dotmac.ng"
    employee.person.name = "Ada Lovelace"
    employee.person.nextcloud_user_id = None
    return employee


@patch("app.services.nextcloud.client.httpx.Client")
def test_create_user_requests_nextcloud_welcome_email(
    mock_client_class: Mock,
) -> None:
    response = Mock()
    response.status_code = 200
    response.json.return_value = {
        "ocs": {"meta": {"statuscode": 100, "message": "OK"}, "data": {}}
    }
    http_client = mock_client_class.return_value.__enter__.return_value
    http_client.request.return_value = response
    client = NextcloudTalkClient(_config())

    client.create_user(
        "ada@dotmac.ng",
        email="ada@dotmac.ng",
        display_name="Ada Lovelace",
        group="erp-employees",
        quota="1 GB",
    )

    request = http_client.request.call_args
    assert request.args == (
        "POST",
        "https://next.dotmac.ng/ocs/v1.php/cloud/users",
    )
    assert request.kwargs["auth"] == ("erp-provisioning", "app-password")
    assert request.kwargs["headers"] == {
        "OCS-APIRequest": "true",
        "Accept": "application/json",
    }
    assert request.kwargs["params"] == {"format": "json"}
    assert request.kwargs["data"] == {
        "userid": "ada@dotmac.ng",
        "email": "ada@dotmac.ng",
        "displayName": "Ada Lovelace",
        "groups[]": ["erp-employees"],
        "quota": "1 GB",
    }
    assert "password" not in request.kwargs["data"]


@patch("app.services.nextcloud.client.httpx.Client")
def test_get_user_returns_none_for_ocs_not_found(mock_client_class: Mock) -> None:
    response = Mock()
    response.status_code = 404
    response.json.return_value = {
        "ocs": {
            "meta": {
                "statuscode": 998,
                "message": "The requested user could not be found",
            },
            "data": [],
        }
    }
    mock_client_class.return_value.__enter__.return_value.request.return_value = (
        response
    )
    client = NextcloudTalkClient(_config())

    assert client.get_user("missing@dotmac.ng") is None


@patch("app.services.nextcloud.client.httpx.Client")
def test_get_user_returns_none_for_ocs_404_inside_http_200(
    mock_client_class: Mock,
) -> None:
    response = Mock()
    response.status_code = 200
    response.json.return_value = {
        "ocs": {
            "meta": {
                "statuscode": 404,
                "message": "User does not exist",
            },
            "data": [],
        }
    }
    mock_client_class.return_value.__enter__.return_value.request.return_value = (
        response
    )
    client = NextcloudTalkClient(_config())

    assert client.get_user("missing@dotmac.ng") is None


@patch("app.services.nextcloud.client.httpx.Client")
def test_disable_user_url_encodes_its_identifier(mock_client_class: Mock) -> None:
    response = Mock()
    response.status_code = 200
    response.json.return_value = {
        "ocs": {"meta": {"statuscode": 100, "message": "OK"}, "data": {}}
    }
    http_client = mock_client_class.return_value.__enter__.return_value
    http_client.request.return_value = response
    client = NextcloudTalkClient(_config())

    client.disable_user("ada+field@dotmac.ng")

    assert http_client.request.call_args.args == (
        "PUT",
        "https://next.dotmac.ng/ocs/v1.php/cloud/users/ada%2Bfield%40dotmac.ng/disable",
    )


@patch("app.services.nextcloud.client.httpx.Client")
def test_http_success_with_ocs_error_is_rejected(mock_client_class: Mock) -> None:
    response = Mock()
    response.status_code = 200
    response.json.return_value = {
        "ocs": {
            "meta": {"statuscode": 102, "message": "Invalid input"},
            "data": {},
        }
    }
    mock_client_class.return_value.__enter__.return_value.request.return_value = (
        response
    )
    client = NextcloudTalkClient(_config())

    with pytest.raises(NextcloudError, match="Invalid input"):
        client.create_user(
            "ada@dotmac.ng",
            email="ada@dotmac.ng",
            display_name="Ada",
            group="erp-employees",
        )


def test_employee_without_forward_request_is_not_backfilled() -> None:
    employee = _employee()
    employee.mailcow_provisioning_requested_at = None
    db = Mock()
    db.scalar.return_value = employee
    nextcloud = Mock()
    service = EmployeeNextcloudProvisioningService(
        db,
        config=_config(),
        nextcloud_client=nextcloud,
    )

    result = service.ensure_account(employee.organization_id, employee.employee_id)

    assert result.skipped == ["employee has no forward provisioning request"]
    nextcloud.get_user.assert_not_called()


def test_employee_waits_until_mailbox_is_provisioned() -> None:
    employee = _employee()
    employee.mailcow_mailbox_provisioned_at = None
    db = Mock()
    db.scalar.return_value = employee
    nextcloud = Mock()
    service = EmployeeNextcloudProvisioningService(
        db,
        config=_config(),
        nextcloud_client=nextcloud,
    )

    result = service.ensure_account(employee.organization_id, employee.employee_id)

    assert result.skipped == ["employee Mailcow mailbox is not provisioned"]
    nextcloud.get_user.assert_not_called()


def test_employee_nextcloud_account_is_created_and_bound() -> None:
    employee = _employee()
    db = Mock()
    db.scalar.return_value = employee
    nextcloud = Mock()
    nextcloud.get_user.side_effect = [None, {"email": "ada@dotmac.ng", "enabled": True}]
    service = EmployeeNextcloudProvisioningService(
        db,
        config=_config(),
        nextcloud_client=nextcloud,
    )

    result = service.ensure_account(employee.organization_id, employee.employee_id)

    assert result.created
    assert result.user_id == "ada@dotmac.ng"
    assert employee.person.nextcloud_user_id == "ada@dotmac.ng"
    nextcloud.create_user.assert_called_once_with(
        "ada@dotmac.ng",
        email="ada@dotmac.ng",
        display_name="Ada Lovelace",
        group="erp-employees",
        quota="1 GB",
    )


def test_unbound_existing_nextcloud_identity_is_not_claimed() -> None:
    employee = _employee()
    db = Mock()
    db.scalar.return_value = employee
    nextcloud = Mock()
    nextcloud.get_user.return_value = {
        "email": "ada@dotmac.ng",
        "enabled": True,
    }
    service = EmployeeNextcloudProvisioningService(
        db,
        config=_config(),
        nextcloud_client=nextcloud,
    )

    with pytest.raises(NextcloudIdentityConflictError, match="without an ERP"):
        service.ensure_account(employee.organization_id, employee.employee_id)

    assert employee.person.nextcloud_user_id is None
    nextcloud.create_user.assert_not_called()


def test_bound_disabled_nextcloud_identity_is_enabled_and_grouped() -> None:
    employee = _employee()
    employee.person.nextcloud_user_id = "ada@dotmac.ng"
    db = Mock()
    db.scalar.return_value = employee
    nextcloud = Mock()
    nextcloud.get_user.return_value = {
        "email": "ada@dotmac.ng",
        "enabled": False,
    }
    service = EmployeeNextcloudProvisioningService(
        db,
        config=_config(),
        nextcloud_client=nextcloud,
    )

    result = service.ensure_account(employee.organization_id, employee.employee_id)

    assert result.already_exists
    assert result.enabled
    nextcloud.enable_user.assert_called_once_with("ada@dotmac.ng")
    nextcloud.add_user_to_group.assert_called_once_with(
        "ada@dotmac.ng", "erp-employees"
    )


def test_bound_identity_already_in_group_is_a_noop() -> None:
    employee = _employee()
    employee.person.nextcloud_user_id = "ada@dotmac.ng"
    db = Mock()
    db.scalar.return_value = employee
    nextcloud = Mock()
    nextcloud.get_user.return_value = {
        "email": "ada@dotmac.ng",
        "enabled": True,
        "groups": ["erp-employees"],
    }
    service = EmployeeNextcloudProvisioningService(
        db,
        config=_config(),
        nextcloud_client=nextcloud,
    )

    result = service.ensure_account(employee.organization_id, employee.employee_id)

    assert result.already_exists
    assert not result.enabled
    nextcloud.enable_user.assert_not_called()
    nextcloud.add_user_to_group.assert_not_called()


def test_nextcloud_lock_does_not_eager_join_person() -> None:
    employee = _employee()
    employee.person.nextcloud_user_id = "ada@dotmac.ng"
    db = Mock()
    db.scalar.return_value = employee
    nextcloud = Mock()
    nextcloud.get_user.return_value = {
        "email": "ada@dotmac.ng",
        "enabled": True,
        "groups": ["erp-employees"],
    }
    service = EmployeeNextcloudProvisioningService(
        db, config=_config(), nextcloud_client=nextcloud
    )

    service.ensure_account(employee.organization_id, employee.employee_id)

    statement = db.scalar.call_args.args[0]
    assert not statement._with_options
    assert statement._for_update_arg is not None
