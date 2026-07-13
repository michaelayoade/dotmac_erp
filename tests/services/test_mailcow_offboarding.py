import json
from datetime import datetime
from unittest.mock import Mock, patch

from app.models.people.hr.employee import EmployeeStatus
from app.services.mailcow.cleanup_queue import SogoCleanupQueueClient
from app.services.mailcow.config import MailcowOffboardingConfig
from app.services.mailcow.sieve import (
    build_offboarding_sieve_script,
    remove_redirect_from_sieve,
    render_autoresponder_message,
)
from app.services.mailcow.sogo import (
    remove_forward_address,
    set_forward_to_inactive,
)
from app.services.people.hr.offboarding import (
    EmployeeOffboardingResult,
    EmployeeOffboardingService,
    should_offboard_status,
)


def _mailcow_config(
    *,
    cleanup_url: str = "",
    cleanup_token: str | None = None,
) -> MailcowOffboardingConfig:
    return MailcowOffboardingConfig(
        enabled=True,
        base_url="https://mail.dotmac.ng/api/v1",
        api_key="mailcow-api-key",
        request_timeout=20.0,
        inactive_forward_to="inactives@dotmac.ng",
        autoresponder_subject="Mailbox no longer monitored",
        autoresponder_template="{full_name} ({email}) is no longer here.",
        sieve_host="mail.dotmac.ng",
        sieve_port=4190,
        sieve_master_user="master",
        sieve_master_password="password",
        sieve_script_name="sogo",
        sieve_use_starttls=True,
        sogo_db_host="",
        sogo_db_port=3306,
        sogo_db_name="mailcow",
        sogo_db_user=None,
        sogo_db_password=None,
        sogo_cleanup_url=cleanup_url,
        sogo_cleanup_token=cleanup_token,
    )


def test_offboarding_statuses_only_include_resigned_and_terminated() -> None:
    assert should_offboard_status(EmployeeStatus.RESIGNED)
    assert should_offboard_status(EmployeeStatus.TERMINATED)
    assert not should_offboard_status(EmployeeStatus.ACTIVE)
    assert not should_offboard_status(EmployeeStatus.ON_LEAVE)


@patch("app.services.mailcow.cleanup_queue.httpx.Client")
def test_cleanup_request_client_sends_expected_post(mock_client_class: Mock) -> None:
    response = Mock()
    response.json.return_value = {
        "ok": True,
        "email": "john@dotmac.ng",
        "queued": True,
    }
    http_client = mock_client_class.return_value.__enter__.return_value
    http_client.post.return_value = response
    client = SogoCleanupQueueClient(
        url="http://10.0.0.20:8765/cleanup",
        token="cleanup-secret",
        timeout=7.0,
    )

    queued = client.enqueue("john@dotmac.ng")

    assert queued
    mock_client_class.assert_called_once_with(timeout=7.0)
    response.raise_for_status.assert_called_once_with()
    request = http_client.post.call_args
    assert request.args == ("http://10.0.0.20:8765/cleanup",)
    assert request.kwargs["headers"] == {"Authorization": "Bearer cleanup-secret"}
    assert request.kwargs["json"]["email"] == "john@dotmac.ng"
    assert request.kwargs["json"]["event"] == "employee_offboarding"
    requested_at = datetime.fromisoformat(request.kwargs["json"]["requested_at"])
    assert requested_at.tzinfo is not None


def test_cleanup_request_is_skipped_when_config_is_missing() -> None:
    incomplete_configs = (
        _mailcow_config(cleanup_url="http://10.0.0.20:8765/cleanup"),
        _mailcow_config(cleanup_token="cleanup-secret"),
    )

    for config in incomplete_configs:
        service = EmployeeOffboardingService(Mock(), config=config)
        result = EmployeeOffboardingResult(employee_id="employee-1")

        service._request_sogo_cleanup("john@dotmac.ng", result)

        assert not result.sogo_cleanup_request_queued
        assert "Mailcow SOGo cleanup receiver is not configured" in result.skipped


def test_cleanup_request_failure_does_not_stop_mailcow_offboarding() -> None:
    mailcow_client = Mock()
    mailcow_client.get_mailbox.return_value = {"username": "john@dotmac.ng"}
    sieve_client = Mock()
    sogo_service = Mock()
    sogo_service.set_inactive_forward.return_value = True
    sogo_service.cleanup_forwarding_references.return_value = []
    cleanup_client = Mock()
    cleanup_client.enqueue.side_effect = RuntimeError("receiver unavailable")
    service = EmployeeOffboardingService(
        Mock(),
        config=_mailcow_config(
            cleanup_url="http://10.0.0.20:8765/cleanup",
            cleanup_token="cleanup-secret",
        ),
        mailcow_client=mailcow_client,
        sieve_client=sieve_client,
        sogo_service=sogo_service,
        sogo_cleanup_client=cleanup_client,
    )
    employee = Mock()
    employee.full_name = "John Doe"
    person = Mock()
    person.name = "John Doe"
    result = EmployeeOffboardingResult(employee_id="employee-1")

    service._run_mailcow_steps(employee, person, "john@dotmac.ng", result)

    assert result.mailcow_password_reset
    assert result.sieve_offboarding_script_updated
    assert not result.sogo_cleanup_request_queued
    assert result.sogo_inactive_forward_updated
    assert "sogo cleanup request failed: receiver unavailable" in result.errors
    sogo_service.cleanup_forwarding_references.assert_called_once_with("john@dotmac.ng")


def test_autoresponder_template_is_rendered_before_sieve_write() -> None:
    message = render_autoresponder_message(
        "{full_name} ({email}) is no longer with Dotmac Technologies.",
        full_name="Samuel Ojo",
        email="s.ojo@dotmac.ng",
    )

    assert (
        message == "Samuel Ojo (s.ojo@dotmac.ng) is no longer with Dotmac Technologies."
    )
    assert "{{" not in message
    assert "{full_name}" not in message


def test_build_offboarding_sieve_script_contains_literal_message() -> None:
    script = build_offboarding_sieve_script(
        full_name="Samuel Ojo",
        email="s.ojo@dotmac.ng",
        forward_to="inactives@dotmac.ng",
        subject="Mailbox no longer monitored",
        message_template="Please note that {full_name} ({email}) is no longer here.",
    )

    assert 'require ["vacation", "copy"];' in script
    assert ':subject "Mailbox no longer monitored"' in script
    assert "Please note that Samuel Ojo (s.ojo@dotmac.ng) is no longer here." in script
    assert 'redirect :copy "inactives@dotmac.ng";' in script
    assert "keep;" in script
    assert "{full_name}" not in script


def test_remove_redirect_from_sieve_removes_only_target() -> None:
    script = "\n".join(
        [
            "keep;",
            'redirect "c.okaka@dotmac.ng";',
            'redirect "s.ojo@dotmac.ng";',
            'redirect :copy "other@dotmac.ng";',
            "",
        ]
    )

    updated, changed = remove_redirect_from_sieve(script, "S.OJO@dotmac.ng")

    assert changed
    assert 'redirect "s.ojo@dotmac.ng";' not in updated
    assert 'redirect "c.okaka@dotmac.ng";' in updated
    assert 'redirect :copy "other@dotmac.ng";' in updated


def test_remove_forward_address_updates_sogo_defaults() -> None:
    defaults = {
        "Forward": {
            "forwardAddress": [
                "s.ojo@dotmac.ng",
                "c.okaka@dotmac.ng",
            ],
            "enabled": 1,
            "keepCopy": 1,
            "alwaysSend": 1,
        }
    }

    updated, changed = remove_forward_address(defaults, "s.ojo@dotmac.ng")

    assert changed
    assert updated["Forward"]["forwardAddress"] == ["c.okaka@dotmac.ng"]
    assert updated["Forward"]["enabled"] == 1


def test_remove_forward_address_disables_empty_sogo_forward() -> None:
    defaults = {
        "Forward": {
            "forwardAddress": ["s.ojo@dotmac.ng"],
            "enabled": 1,
            "keepCopy": 1,
            "alwaysSend": 1,
        }
    }

    updated, changed = remove_forward_address(defaults, "s.ojo@dotmac.ng")

    assert changed
    assert updated["Forward"]["forwardAddress"] == []
    assert updated["Forward"]["enabled"] == 0


def test_set_forward_to_inactive_is_idempotent() -> None:
    defaults = {
        "Forward": {
            "forwardAddress": ["inactives@dotmac.ng"],
            "enabled": 1,
            "keepCopy": 1,
            "alwaysSend": 1,
        }
    }

    updated, changed = set_forward_to_inactive(defaults, "inactives@dotmac.ng")

    assert not changed
    assert updated["Forward"]["forwardAddress"] == ["inactives@dotmac.ng"]


def test_set_forward_to_inactive_creates_sogo_forward_block() -> None:
    updated, changed = set_forward_to_inactive({}, "inactives@dotmac.ng")

    assert changed
    assert updated["Forward"] == {
        "forwardAddress": ["inactives@dotmac.ng"],
        "enabled": 1,
        "keepCopy": 1,
        "alwaysSend": 1,
    }
    json.dumps(updated)
