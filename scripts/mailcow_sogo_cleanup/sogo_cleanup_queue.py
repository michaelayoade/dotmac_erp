#!/usr/bin/env python3
"""Manage the local SQLite queue for Mailcow SOGo forwarding cleanup."""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_QUEUE_DB = "/var/lib/dotmac-mailcow-offboarding/sogo_cleanup_queue.sqlite3"
EMAIL_RE = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS cleanup_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    processed_at TEXT,
    last_error TEXT,
    created_by TEXT NOT NULL DEFAULT 'manual'
)
"""
PENDING_INDEX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS cleanup_queue_pending_email_idx
ON cleanup_queue (email)
WHERE status = 'pending'
"""


@dataclass(frozen=True)
class CleanupRequest:
    request_id: int
    email: str
    created_at: str
    created_by: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_config_file(path: str | None) -> None:
    """Load simple KEY=VALUE entries without overriding process environment."""
    if not path:
        return
    config_path = Path(path)
    with config_path.open(encoding="utf-8") as config_file:
        for raw_line in config_file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            if key:
                os.environ.setdefault(key, value)


def allowed_domains_from_env() -> frozenset[str]:
    domains = {
        domain.strip().lower().lstrip("@")
        for domain in os.getenv("ALLOWED_DOMAINS", "dotmac.ng").split(",")
        if domain.strip()
    }
    if not domains:
        raise ValueError("ALLOWED_DOMAINS must contain at least one domain")
    return frozenset(domains)


def normalize_email(
    value: Any,
    allowed_domains: frozenset[str] | None = None,
) -> str:
    if not isinstance(value, str):
        raise ValueError("email must be a string")
    email = value.strip().lower()
    if not EMAIL_RE.fullmatch(email):
        raise ValueError("invalid email")
    if allowed_domains is not None and email.rsplit("@", 1)[1] not in allowed_domains:
        raise ValueError("email domain is not allowed")
    return email


def connect_queue(queue_db: str) -> sqlite3.Connection:
    db_path = Path(queue_db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 10000")
    connection.execute(SCHEMA_SQL)
    connection.execute(PENDING_INDEX_SQL)
    return connection


def enqueue_cleanup_request(
    queue_db: str,
    email: str,
    *,
    created_by: str = "manual",
) -> bool:
    """Add a pending cleanup request and return whether a row was inserted."""
    with connect_queue(queue_db) as connection:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO cleanup_queue
                (email, status, created_at, created_by)
            VALUES (?, 'pending', ?, ?)
            """,
            (email, utc_now(), created_by),
        )
        return cursor.rowcount == 1


def load_pending_requests(queue_db: str, *, limit: int = 100) -> list[CleanupRequest]:
    with connect_queue(queue_db) as connection:
        rows = connection.execute(
            """
            SELECT id, email, created_at, created_by
            FROM cleanup_queue
            WHERE status = 'pending'
            ORDER BY id
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [
        CleanupRequest(
            request_id=int(row["id"]),
            email=str(row["email"]),
            created_at=str(row["created_at"]),
            created_by=str(row["created_by"]),
        )
        for row in rows
    ]


def mark_request_completed(queue_db: str, request_id: int) -> None:
    with connect_queue(queue_db) as connection:
        connection.execute(
            """
            UPDATE cleanup_queue
            SET status = 'completed', processed_at = ?, last_error = NULL
            WHERE id = ? AND status = 'pending'
            """,
            (utc_now(), request_id),
        )


def record_request_error(queue_db: str, request_id: int, error: str) -> None:
    """Record an error while leaving the request pending for the next timer run."""
    with connect_queue(queue_db) as connection:
        connection.execute(
            """
            UPDATE cleanup_queue
            SET last_error = ?
            WHERE id = ? AND status = 'pending'
            """,
            (error[:2000], request_id),
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="Optional environment-style config file")
    parser.add_argument("--queue-db", help="Override QUEUE_DB")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="Initialize the queue schema")
    enqueue_parser = subparsers.add_parser(
        "enqueue",
        aliases=["add"],
        help="Add an email to the pending queue",
    )
    enqueue_parser.add_argument("email")
    enqueue_parser.add_argument("--created-by", default="manual")
    list_parser = subparsers.add_parser("list", help="List pending requests")
    list_parser.add_argument("--limit", type=int, default=100)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_config_file(args.config)
    queue_db = args.queue_db or os.getenv("QUEUE_DB", DEFAULT_QUEUE_DB)

    if args.command == "init":
        with connect_queue(queue_db):
            pass
        print(f"Initialized cleanup queue: {queue_db}")
        return 0
    if args.command in {"enqueue", "add"}:
        email = normalize_email(args.email, allowed_domains_from_env())
        inserted = enqueue_cleanup_request(
            queue_db,
            email,
            created_by=args.created_by,
        )
        print("Queued" if inserted else "Already pending")
        return 0

    requests = load_pending_requests(queue_db, limit=args.limit)
    if not requests:
        print("No pending cleanup requests")
        return 0
    for request in requests:
        print(
            f"{request.request_id}\t{request.email}\t"
            f"{request.created_at}\t{request.created_by}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
