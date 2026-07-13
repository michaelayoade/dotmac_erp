from argparse import Namespace
from unittest.mock import Mock, patch

from scripts.mailcow_sogo_cleanup.sogo_cleanup_queue import CleanupRequest
from scripts.mailcow_sogo_cleanup.sogo_forward_cleanup import main


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
