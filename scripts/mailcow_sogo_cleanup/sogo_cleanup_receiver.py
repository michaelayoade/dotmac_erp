#!/usr/bin/env python3
"""Receive authenticated ERP offboarding requests and enqueue them locally."""

from __future__ import annotations

import argparse
import hmac
import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

try:
    from .sogo_cleanup_queue import (
        DEFAULT_QUEUE_DB,
        allowed_domains_from_env,
        enqueue_cleanup_request,
        load_config_file,
        normalize_email,
    )
except ImportError:  # pragma: no cover - direct script execution
    from sogo_cleanup_queue import (
        DEFAULT_QUEUE_DB,
        allowed_domains_from_env,
        enqueue_cleanup_request,
        load_config_file,
        normalize_email,
    )

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
MAX_REQUEST_BYTES = 16 * 1024
EXPECTED_EVENT = "employee_offboarding"

logger = logging.getLogger("sogo_cleanup_receiver")


def validate_email(value: Any, allowed_domains: frozenset[str]) -> str:
    return normalize_email(value, allowed_domains)


def enqueue_request(queue_db: str, email: str) -> None:
    """Insert a pending request into the cleanup queue, ignoring duplicates."""
    enqueue_cleanup_request(queue_db, email, created_by="erp_receiver")


class CleanupRequestHandler(BaseHTTPRequestHandler):
    server: CleanupHTTPServer

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _reject_non_post(self) -> None:
        if self.path != "/cleanup":
            self._send_json(404, {"ok": False, "error": "not found"})
            return
        self._send_json(405, {"ok": False, "error": "method not allowed"})

    def do_GET(self) -> None:  # noqa: N802
        self._reject_non_post()

    def do_PUT(self) -> None:  # noqa: N802
        self._reject_non_post()

    def do_PATCH(self) -> None:  # noqa: N802
        self._reject_non_post()

    def do_DELETE(self) -> None:  # noqa: N802
        self._reject_non_post()

    def do_HEAD(self) -> None:  # noqa: N802
        self._reject_non_post()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._reject_non_post()

    def do_TRACE(self) -> None:  # noqa: N802
        self._reject_non_post()

    def do_CONNECT(self) -> None:  # noqa: N802
        self._reject_non_post()

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/cleanup":
            self._send_json(404, {"ok": False, "error": "not found"})
            return

        authorization = self.headers.get("Authorization", "")
        expected = f"Bearer {self.server.cleanup_token}"
        if not hmac.compare_digest(authorization, expected):
            self._send_json(401, {"ok": False, "error": "unauthorized"})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            if content_length <= 0 or content_length > MAX_REQUEST_BYTES:
                raise ValueError("invalid request size")
            payload = json.loads(self.rfile.read(content_length))
            if not isinstance(payload, dict):
                raise ValueError("JSON body must be an object")
            if payload.get("event") != EXPECTED_EVENT:
                raise ValueError("invalid event")
            email = validate_email(payload.get("email"), self.server.allowed_domains)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
            return

        try:
            enqueue_request(self.server.queue_db, email)
        except Exception:
            logger.exception("Failed to enqueue cleanup request")
            self._send_json(500, {"ok": False, "error": "internal server error"})
            return

        self._send_json(200, {"ok": True, "email": email, "queued": True})

    def log_message(self, message_format: str, *args: Any) -> None:
        logger.info(
            "Request from %s: %s",
            self.client_address[0],
            message_format % args,
        )


class CleanupHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        *,
        cleanup_token: str,
        queue_db: str,
        allowed_domains: frozenset[str],
    ) -> None:
        super().__init__(address, CleanupRequestHandler)
        self.cleanup_token = cleanup_token
        self.queue_db = queue_db
        self.allowed_domains = allowed_domains


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        help="Optional environment-style configuration file",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_config_file(args.config)
    host = os.getenv("CLEANUP_RECEIVER_HOST", DEFAULT_HOST)
    port = int(os.getenv("CLEANUP_RECEIVER_PORT", str(DEFAULT_PORT)))
    cleanup_token = os.getenv("CLEANUP_RECEIVER_TOKEN", "")
    queue_db = os.getenv("QUEUE_DB", DEFAULT_QUEUE_DB)
    if not cleanup_token:
        raise SystemExit("CLEANUP_RECEIVER_TOKEN must be configured")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    server = CleanupHTTPServer(
        (host, port),
        cleanup_token=cleanup_token,
        queue_db=queue_db,
        allowed_domains=allowed_domains_from_env(),
    )
    logger.info("Cleanup receiver listening on %s:%s", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Cleanup receiver stopping")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
