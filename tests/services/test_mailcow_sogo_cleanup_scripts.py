from argparse import Namespace
from unittest.mock import Mock, patch

from scripts.mailcow_sogo_cleanup.sogo_designation_sync import (
    merge_managed_designation,
    project_designation_to_defaults,
    reconcile_designations,
)
from scripts.mailcow_sogo_cleanup.sogo_cleanup_queue import CleanupRequest
from scripts.mailcow_sogo_cleanup.sogo_forward_cleanup import (
    MailboxProfile,
    SogoProfile,
    main,
)


@patch("scripts.mailcow_sogo_cleanup.sogo_forward_cleanup.restart_sogo_services")
@patch("scripts.mailcow_sogo_cleanup.sogo_forward_cleanup.mark_request_completed")
@patch("scripts.mailcow_sogo_cleanup.sogo_forward_cleanup.cleanup_request")
@patch("scripts.mailcow_sogo_cleanup.sogo_forward_cleanup.load_pending_requests")
@patch("scripts.mailcow_sogo_cleanup.sogo_forward_cleanup.parse_args")
def test_noop_cleanup_completes_without_restarting_sogo(
    parse_args: Mock,
    load_pending_requests: Mock,
    cleanup_request: Mock,
    mark_request_completed: Mock,
    restart_sogo_services: Mock,
) -> None:
    parse_args.return_value = Namespace(
        config=None,
        apply=True,
        dry_run=False,
        limit=100,
        no_restart=False,
    )
    request = CleanupRequest(
        request_id=1,
        email="former.employee@dotmac.ng",
        created_at="2026-07-13T12:00:00+00:00",
        created_by="erp_receiver",
    )
    load_pending_requests.return_value = [request]
    cleanup_request.return_value = 0

    assert main() == 0

    restart_sogo_services.assert_not_called()
    mark_request_completed.assert_called_once_with(
        "/var/lib/dotmac-mailcow-offboarding/sogo_cleanup_queue.sqlite3",
        request.request_id,
    )


def test_managed_designation_preserves_existing_signature_and_escapes_html() -> None:
    updated = merge_managed_designation(
        "<strong>Ada Lovelace</strong>",
        "Network & Infrastructure <Lead>",
    )

    assert updated.startswith("<strong>Ada Lovelace</strong><br>")
    assert "Network &amp; Infrastructure &lt;Lead&gt;" in updated
    assert 'data-dotmac-erp-designation="true"' in updated


def test_designation_change_replaces_only_managed_signature_block() -> None:
    defaults = {
        "SOGoLanguage": "English",
        "SOGoMailIdentities": [
            {
                "email": "ada@dotmac.ng",
                "fullName": "Ada Lovelace",
                "signature": (
                    "<strong>Ada Lovelace</strong><br>"
                    '<span data-dotmac-erp-designation="true">Engineer</span>'
                ),
                "replyTo": "team@dotmac.ng",
                "isDefault": 1,
            }
        ],
    }

    updated, changed = project_designation_to_defaults(
        defaults,
        email="ada@dotmac.ng",
        full_name="Ada Lovelace",
        designation="Network Engineer",
    )

    assert changed
    identity = updated["SOGoMailIdentities"][0]
    assert identity["replyTo"] == "team@dotmac.ng"
    assert identity["signature"].count("data-dotmac-erp-designation") == 1
    assert "Network Engineer" in identity["signature"]
    assert "<strong>Ada Lovelace</strong>" in identity["signature"]
    assert updated["SOGoLanguage"] == "English"
    assert updated["SOGoMailUseSignatureOnNew"] == 1
    assert updated["SOGoMailUseSignatureOnReply"] == 1
    assert updated["SOGoMailUseSignatureOnForward"] == 1


def test_reconcile_designations_updates_only_matching_sogo_profiles() -> None:
    database = Mock()
    database.list_mailboxes.return_value = [
        MailboxProfile(
            username="ada@dotmac.ng",
            full_name="Ada Lovelace",
            custom_attributes={"designation": "Network Engineer"},
        )
    ]
    database.list_profiles.return_value = [
        SogoProfile(uid="ada@dotmac.ng", defaults={}),
        SogoProfile(uid="unmanaged@example.com", defaults={}),
    ]

    changed_count = reconcile_designations(database, apply=True)

    assert changed_count == 1
    changed_profiles = database.update_profiles.call_args.args[0]
    assert [profile.uid for profile in changed_profiles] == ["ada@dotmac.ng"]
    identity = changed_profiles[0].defaults["SOGoMailIdentities"][0]
    assert identity["email"] == "ada@dotmac.ng"
    assert "Network Engineer" in identity["signature"]
