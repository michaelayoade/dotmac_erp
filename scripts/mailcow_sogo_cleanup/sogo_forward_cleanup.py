#!/usr/bin/env python3
"""Process queued requests and remove SOGo forwarding references in Mailcow."""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import subprocess
from dataclasses import dataclass
from typing import Any

try:
    from .sogo_cleanup_queue import (
        DEFAULT_QUEUE_DB,
        CleanupRequest,
        load_config_file,
        load_pending_requests,
        mark_request_completed,
        record_request_error,
    )
except ImportError:  # pragma: no cover - direct script execution
    from sogo_cleanup_queue import (
        DEFAULT_QUEUE_DB,
        CleanupRequest,
        load_config_file,
        load_pending_requests,
        mark_request_completed,
        record_request_error,
    )


logger = logging.getLogger("sogo_forward_cleanup")


@dataclass(frozen=True)
class SogoProfile:
    uid: str
    defaults: dict[str, Any]


def remove_forward_address(
    defaults: dict[str, Any],
    email: str,
) -> tuple[dict[str, Any], bool]:
    forward = defaults.get("Forward")
    if not isinstance(forward, dict):
        return defaults, False
    addresses = forward.get("forwardAddress")
    if not isinstance(addresses, list):
        return defaults, False

    normalized = email.strip().lower()
    updated_addresses = [
        value
        for value in addresses
        if not (isinstance(value, str) and value.strip().lower() == normalized)
    ]
    if len(updated_addresses) == len(addresses):
        return defaults, False

    updated = dict(defaults)
    updated_forward = dict(forward)
    updated_forward["forwardAddress"] = updated_addresses
    if not updated_addresses:
        updated_forward["enabled"] = 0
    updated["Forward"] = updated_forward
    return updated, True


class MailcowSogoDatabase:
    """Access Mailcow MariaDB through its local Docker Compose service."""

    _MYSQL_COMMAND = (
        'client="$(command -v mariadb || command -v mysql)" || exit 127; '
        'exec "$client" --batch --raw --skip-column-names '
        '--user="$MYSQL_USER" --password="$MYSQL_PASSWORD" "$MYSQL_DATABASE"'
    )

    def __init__(
        self,
        *,
        mailcow_dir: str,
        docker_bin: str,
        mysql_service: str,
    ) -> None:
        self.mailcow_dir = mailcow_dir
        self.docker_bin = docker_bin
        self.mysql_service = mysql_service

    def _run_mysql(self, sql: str) -> str:
        command = [
            self.docker_bin,
            "compose",
            "exec",
            "-T",
            self.mysql_service,
            "sh",
            "-c",
            self._MYSQL_COMMAND,
        ]
        result = subprocess.run(
            command,
            cwd=self.mailcow_dir,
            input=sql,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or "MariaDB command failed"
            raise RuntimeError(detail[:1000])
        return result.stdout

    @staticmethod
    def _decode_column(value: str) -> str:
        return base64.b64decode(value.encode("ascii"), validate=True).decode("utf-8")

    def list_profiles(self) -> list[SogoProfile]:
        output = self._run_mysql(
            """
            SELECT
                REPLACE(TO_BASE64(c_uid), CHAR(10), ''),
                REPLACE(TO_BASE64(c_defaults), CHAR(10), '')
            FROM sogo_user_profile;
            """
        )
        profiles: list[SogoProfile] = []
        for line in output.splitlines():
            columns = line.split("\t", 1)
            if len(columns) != 2 or columns[1] == "NULL":
                continue
            uid = self._decode_column(columns[0])
            raw_defaults = self._decode_column(columns[1])
            try:
                defaults = json.loads(raw_defaults) if raw_defaults.strip() else {}
            except json.JSONDecodeError:
                logger.warning("Skipping a SOGo profile with invalid defaults JSON")
                continue
            if isinstance(defaults, dict):
                profiles.append(SogoProfile(uid=uid, defaults=defaults))
        return profiles

    def update_profiles(self, profiles: list[SogoProfile]) -> None:
        if not profiles:
            return
        statements = ["START TRANSACTION;"]
        for profile in profiles:
            defaults_json = json.dumps(
                profile.defaults,
                separators=(",", ":"),
                ensure_ascii=False,
            )
            defaults_hex = defaults_json.encode("utf-8").hex()
            uid_hex = profile.uid.encode("utf-8").hex()
            statements.append(
                "UPDATE sogo_user_profile "
                f"SET c_defaults = CONVERT(0x{defaults_hex} USING utf8mb4) "
                f"WHERE c_uid = CONVERT(0x{uid_hex} USING utf8mb4);"
            )
        statements.append("COMMIT;")
        self._run_mysql("\n".join(statements))


def cleanup_request(
    database: MailcowSogoDatabase,
    request: CleanupRequest,
    *,
    apply: bool,
) -> int:
    changed_profiles: list[SogoProfile] = []
    for profile in database.list_profiles():
        updated, changed = remove_forward_address(profile.defaults, request.email)
        if changed:
            changed_profiles.append(SogoProfile(uid=profile.uid, defaults=updated))
    if apply:
        database.update_profiles(changed_profiles)
    return len(changed_profiles)


def restart_sogo_services(
    *,
    mailcow_dir: str,
    docker_bin: str,
    services: list[str],
) -> None:
    if not services:
        return
    result = subprocess.run(
        [docker_bin, "compose", "restart", *services],
        cwd=mailcow_dir,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or "Mailcow service restart failed"
        raise RuntimeError(detail[:1000])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="Optional environment-style config file")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Apply database changes")
    mode.add_argument("--dry-run", action="store_true", help="Preview changes only")
    parser.add_argument("--limit", type=int, help="Maximum pending requests to process")
    parser.add_argument(
        "--no-restart",
        action="store_true",
        help="Do not restart SOGo and memcached after applying changes",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_config_file(args.config)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    queue_db = os.getenv("QUEUE_DB", DEFAULT_QUEUE_DB)
    limit = args.limit or int(os.getenv("CLEANUP_BATCH_SIZE", "100"))
    mailcow_dir = os.getenv("MAILCOW_DIR", "/opt/mailcow-dockerized")
    docker_bin = os.getenv("DOCKER_BIN", "docker")
    mysql_service = os.getenv("MYSQL_SERVICE", "mysql-mailcow")
    restart_services = [
        value.strip()
        for value in os.getenv(
            "SOGO_RESTART_SERVICES",
            "sogo-mailcow,memcached-mailcow",
        ).split(",")
        if value.strip()
    ]

    requests = load_pending_requests(queue_db, limit=limit)
    if not requests:
        logger.info("No pending cleanup requests")
        return 0

    apply = bool(args.apply)
    database = MailcowSogoDatabase(
        mailcow_dir=mailcow_dir,
        docker_bin=docker_bin,
        mysql_service=mysql_service,
    )
    successful: list[CleanupRequest] = []
    failures = 0
    for request in requests:
        try:
            changed_count = cleanup_request(database, request, apply=apply)
            logger.info(
                "Cleanup request %s matched %s SOGo profile(s)%s",
                request.request_id,
                changed_count,
                "" if apply else " (dry run)",
            )
            successful.append(request)
        except Exception as exc:
            failures += 1
            logger.exception("Cleanup request %s failed", request.request_id)
            if apply:
                record_request_error(queue_db, request.request_id, str(exc))

    if not apply:
        logger.info("Dry run complete; queue entries were left pending")
        return 1 if failures else 0

    if successful and not args.no_restart:
        try:
            restart_sogo_services(
                mailcow_dir=mailcow_dir,
                docker_bin=docker_bin,
                services=restart_services,
            )
        except Exception as exc:
            logger.exception("SOGo service restart failed")
            for request in successful:
                record_request_error(queue_db, request.request_id, str(exc))
            return 1

    for request in successful:
        mark_request_completed(queue_db, request.request_id)
    logger.info(
        "Processed %s cleanup request(s); %s failed",
        len(successful),
        failures,
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
