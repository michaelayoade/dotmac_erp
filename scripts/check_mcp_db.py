"""
Validate MCP DB integration prerequisites for the `erp-db` server.

Checks:
1. `.mcp.json` has an `erp-db` entry.
2. `DOTMAC_ERP_DB_DSN` is set (env or .env).
3. DSN can connect and run safe read-only SELECT queries.
4. Optional: `npx @bytebase/dbhub` is runnable.

Usage:
    poetry run python scripts/check_mcp_db.py
    poetry run python scripts/check_mcp_db.py --skip-npx
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
MCP_CONFIG = ROOT / ".mcp.json"


def _ok(msg: str) -> None:
    print(f"[OK] {msg}")


def _warn(msg: str) -> None:
    print(f"[WARN] {msg}")


def _fail(msg: str) -> None:
    print(f"[FAIL] {msg}")
    sys.exit(1)


def _redact_dsn(dsn: str) -> str:
    try:
        parts = urlsplit(dsn)
        if not parts.username and not parts.password:
            return dsn
        host = parts.hostname or ""
        if parts.port:
            host = f"{host}:{parts.port}"
        user = parts.username or ""
        netloc = f"{user}:***@{host}" if user else host
        return urlunsplit(
            (parts.scheme, netloc, parts.path, parts.query, parts.fragment)
        )
    except Exception:
        return "<redacted>"


def _check_mcp_config() -> None:
    if not MCP_CONFIG.exists():
        _fail(".mcp.json is missing")

    try:
        data = json.loads(MCP_CONFIG.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _fail(f".mcp.json is invalid JSON: {exc}")

    server = data.get("mcpServers", {}).get("erp-db")
    if not isinstance(server, dict):
        _fail("`.mcp.json` is missing `mcpServers.erp-db`")

    command = server.get("command")
    args = server.get("args")
    if not command or not isinstance(args, list):
        _fail("`mcpServers.erp-db` must include `command` and array `args`")

    _ok("Found MCP server config for `erp-db`")

    arg_text = " ".join(str(a) for a in args)
    if "DOTMAC_ERP_DB_DSN" in arg_text:
        _ok("MCP server reads DSN from DOTMAC_ERP_DB_DSN")
    else:
        _warn("MCP args do not reference DOTMAC_ERP_DB_DSN; verify secret handling")


def _check_db_connectivity(dsn: str) -> None:
    redacted = _redact_dsn(dsn)
    _ok(f"Using DSN: {redacted}")

    try:
        with psycopg.connect(dsn, connect_timeout=5) as conn, conn.cursor() as cur:
            cur.execute(
                """
                    SELECT current_user, current_database(),
                           current_setting('server_version')
                    """
            )
            user, db, version = cur.fetchone()
            _ok(f"Connected to `{db}` as `{user}` (Postgres {version})")

            cur.execute(
                """
                    SELECT EXISTS (
                      SELECT 1
                      FROM information_schema.tables
                      WHERE table_schema = 'core_org'
                        AND table_name = 'organization'
                    )
                    """
            )
            (has_org_table,) = cur.fetchone()
            if not has_org_table:
                _warn("`core_org.organization` table not found (unexpected schema?)")
                return

            cur.execute("SELECT COUNT(*) FROM core_org.organization")
            (org_count,) = cur.fetchone()
            _ok(f"`core_org.organization` is queryable (rows: {org_count})")

            cur.execute(
                """
                    SELECT
                      has_table_privilege(current_user, 'core_org.organization', 'INSERT'),
                      has_table_privilege(current_user, 'core_org.organization', 'UPDATE'),
                      has_table_privilege(current_user, 'core_org.organization', 'DELETE')
                    """
            )
            can_insert, can_update, can_delete = cur.fetchone()
            if any((can_insert, can_update, can_delete)):
                _warn(
                    "Current DB user appears to have write table privileges; "
                    "read-only user is recommended for MCP"
                )
            else:
                _ok("Current DB user does not have table write privileges")
    except Exception as exc:
        _fail(f"DB connectivity check failed: {exc}")


def _check_npx(skip_npx: bool) -> None:
    if skip_npx:
        _warn("Skipping npx/dbhub check (--skip-npx)")
        return

    cmd = ["npx", "-y", "@bytebase/dbhub", "--help"]
    try:
        result = subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except FileNotFoundError:
        _fail("`npx` is not installed or not on PATH")
    except subprocess.TimeoutExpired:
        _fail("`npx @bytebase/dbhub --help` timed out")

    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        _fail(f"`@bytebase/dbhub` is not runnable via npx: {detail}")

    _ok("`npx @bytebase/dbhub` is runnable")


def main() -> None:
    parser = argparse.ArgumentParser(description="Check MCP DB integration health")
    parser.add_argument(
        "--skip-npx",
        action="store_true",
        help="Skip npx/dbhub availability check",
    )
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    _check_mcp_config()

    dsn = os.getenv("DOTMAC_ERP_DB_DSN")
    if not dsn:
        _fail("DOTMAC_ERP_DB_DSN is not set (export it or add to .env)")

    _check_db_connectivity(dsn)
    _check_npx(args.skip_npx)
    _ok("MCP DB integration health check passed")


if __name__ == "__main__":
    main()
