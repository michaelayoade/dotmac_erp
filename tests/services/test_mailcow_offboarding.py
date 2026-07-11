import json

from app.models.people.hr.employee import EmployeeStatus
from app.services.mailcow.sieve import (
    build_offboarding_sieve_script,
    remove_redirect_from_sieve,
    render_autoresponder_message,
)
from app.services.mailcow.sogo import (
    remove_forward_address,
    set_forward_to_inactive,
)
from app.services.people.hr.offboarding import should_offboard_status


def test_offboarding_statuses_only_include_resigned_and_terminated() -> None:
    assert should_offboard_status(EmployeeStatus.RESIGNED)
    assert should_offboard_status(EmployeeStatus.TERMINATED)
    assert not should_offboard_status(EmployeeStatus.ACTIVE)
    assert not should_offboard_status(EmployeeStatus.ON_LEAVE)


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
