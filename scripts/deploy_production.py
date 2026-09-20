#!/usr/bin/env python3
"""Deploy ERP to an explicitly named production host with OpenBao injection.

This command runs on the deployment controller, beside OpenBao. It exchanges a
controller-local AppRole credential for a short-lived token, reads the canonical
``app_admin`` DSN, and streams that DSN over SSH to ``scripts/deploy.sh``.
Neither the OpenBao token nor the DSN is placed in argv, an environment file,
the checkout, or a long-running production container.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

DEFAULT_OPENBAO_ADDR = "http://127.0.0.1:8200"
DEFAULT_CREDENTIAL_DIR = Path.home() / ".config" / "dotmac-erp"
DEFAULT_ROLE_ID_FILE = DEFAULT_CREDENTIAL_DIR / "production-openbao-role-id"
DEFAULT_SECRET_ID_FILE = DEFAULT_CREDENTIAL_DIR / "production-openbao-secret-id"
CANONICAL_SECRET_ENDPOINT = (
    "/v1/secret/data/dotmac/postgres/erp-shared-primary/app_admin"
)
CANONICAL_SECRET_FIELD = "MIGRATION_DATABASE_URL"


class DeploymentCredentialError(RuntimeError):
    """A credential could not be obtained without weakening its boundary."""


def _read_private_file(path: Path, *, label: str) -> str:
    try:
        file_stat = path.stat()
    except OSError as exc:
        raise DeploymentCredentialError(f"{label} file is unavailable: {path}") from exc
    if not stat.S_ISREG(file_stat.st_mode):
        raise DeploymentCredentialError(f"{label} path is not a regular file: {path}")
    if file_stat.st_uid != os.geteuid():
        raise DeploymentCredentialError(
            f"{label} file must be owned by the deployment user: {path}"
        )
    if stat.S_IMODE(file_stat.st_mode) & 0o077:
        raise DeploymentCredentialError(
            f"{label} file permissions must be 0600 or stricter: {path}"
        )
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise DeploymentCredentialError(f"{label} file is unreadable: {path}") from exc
    if not value or "\n" in value:
        raise DeploymentCredentialError(f"{label} file must contain exactly one value")
    return value


def _validated_openbao_address(raw: str) -> str:
    address = raw.rstrip("/")
    try:
        parsed = urlsplit(address)
        port = parsed.port
    except ValueError as exc:
        raise DeploymentCredentialError(
            "OpenBao address has an invalid host or port"
        ) from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise DeploymentCredentialError(
            "OpenBao address must be an absolute HTTP(S) URL"
        )
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise DeploymentCredentialError(
            "OpenBao address must not carry credentials, a query, or a fragment"
        )
    if parsed.path not in {"", "/"}:
        raise DeploymentCredentialError("OpenBao address must not carry a path")
    if parsed.scheme == "http" and parsed.hostname not in {
        "127.0.0.1",
        "::1",
        "localhost",
    }:
        raise DeploymentCredentialError(
            "plain HTTP is permitted only for a loopback OpenBao endpoint"
        )
    if port is not None and not 1 <= port <= 65535:
        raise DeploymentCredentialError("OpenBao address port is invalid")
    return address


def _request_json(
    request: Request, *, timeout: float, operation: str
) -> dict[str, Any]:
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310
            payload = json.load(response)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        # OpenBao response bodies can contain request material or implementation
        # details. Keep the refusal credential-free by design.
        raise DeploymentCredentialError(f"OpenBao {operation} failed") from exc
    if not isinstance(payload, dict):
        raise DeploymentCredentialError(f"OpenBao {operation} returned malformed JSON")
    return payload


def fetch_migration_database_url(
    *,
    address: str,
    role_id_file: Path,
    secret_id_file: Path,
    timeout: float = 10.0,
) -> str:
    """Return canonical migration material without logging or persisting it."""
    address = _validated_openbao_address(address)
    role_id = _read_private_file(role_id_file, label="OpenBao role ID")
    secret_id = _read_private_file(secret_id_file, label="OpenBao secret ID")

    login_request = Request(
        f"{address}/v1/auth/approle/login",
        data=json.dumps({"role_id": role_id, "secret_id": secret_id}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    login = _request_json(login_request, timeout=timeout, operation="AppRole login")
    auth = login.get("auth")
    token = auth.get("client_token") if isinstance(auth, dict) else None
    if not isinstance(token, str) or not token:
        raise DeploymentCredentialError("OpenBao AppRole login returned no token")

    secret_request = Request(
        f"{address}{CANONICAL_SECRET_ENDPOINT}",
        headers={"X-Vault-Token": token},
        method="GET",
    )
    secret = _request_json(secret_request, timeout=timeout, operation="secret read")
    outer = secret.get("data")
    inner = outer.get("data") if isinstance(outer, dict) else None
    dsn = inner.get(CANONICAL_SECRET_FIELD) if isinstance(inner, dict) else None
    if not isinstance(dsn, str) or not dsn:
        raise DeploymentCredentialError(
            "canonical OpenBao path does not contain MIGRATION_DATABASE_URL"
        )
    try:
        parsed = urlsplit(dsn)
    except ValueError as exc:
        raise DeploymentCredentialError("canonical migration DSN is malformed") from exc
    if not parsed.scheme.startswith("postgresql") or parsed.username != "app_admin":
        raise DeploymentCredentialError(
            "canonical migration DSN must authenticate as app_admin"
        )
    return dsn


def _deploy_arguments(values: list[str]) -> list[str]:
    accepted: list[str] = []
    for value in values:
        if value in {"--quick", "--people-employment-type-activation"}:
            accepted.append(value)
            continue
        if value.startswith("sha256:") and len(value) == 71:
            digest = value.removeprefix("sha256:")
            if all(character in "0123456789abcdef" for character in digest):
                accepted.append(value)
                continue
        raise DeploymentCredentialError(f"unsupported deploy argument: {value}")
    return accepted


def run_deployment(args: argparse.Namespace) -> int:
    deploy_arguments = _deploy_arguments(
        [
            *(["--quick"] if args.quick else []),
            *(
                ["--people-employment-type-activation"]
                if args.people_employment_type_activation
                else []
            ),
            *([args.image_digest] if args.image_digest else []),
        ]
    )
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", args.host):
        raise DeploymentCredentialError("production host has an invalid hostname")
    if not re.fullmatch(r"[a-z_][a-z0-9_-]*", args.ssh_user):
        raise DeploymentCredentialError("SSH user has an invalid name")
    dsn = fetch_migration_database_url(
        address=args.openbao_address,
        role_id_file=args.role_id_file,
        secret_id_file=args.secret_id_file,
        timeout=args.openbao_timeout,
    )
    remote_script = (
        "set -euo pipefail; "
        "IFS= read -r MIGRATION_DATABASE_URL; "
        'test -n "$MIGRATION_DATABASE_URL"; '
        "export MIGRATION_DATABASE_URL; "
        f"cd {shlex.quote(args.checkout)}; "
        'exec ./scripts/deploy.sh "$@"'
    )
    remote_command = shlex.join(
        ["bash", "-ceu", remote_script, "deploy-production", *deploy_arguments]
    )
    command = [
        "ssh",
        "-F",
        "/dev/null",
        "-i",
        str(args.identity_file),
        "-o",
        "BatchMode=yes",
        f"{args.ssh_user}@{args.host}",
        remote_command,
    ]
    try:
        child_environment = os.environ.copy()
        child_environment.pop("MIGRATION_DATABASE_URL", None)
        child_environment.pop("OPENBAO_TOKEN", None)
        child_environment.pop("BAO_TOKEN", None)
        completed = subprocess.run(  # noqa: S603
            command,
            input=f"{dsn}\n".encode(),
            env=child_environment,
            check=False,
        )
    finally:
        dsn = ""
    return completed.returncode


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Deploy ERP with the canonical OpenBao migration credential"
    )
    parser.add_argument(
        "--host",
        required=True,
        help="explicit production hostname (for example erp.dotmac.io)",
    )
    parser.add_argument("--ssh-user", default="root")
    parser.add_argument(
        "--identity-file", type=Path, default=Path.home() / ".ssh" / "id_ed25519"
    )
    parser.add_argument("--checkout", default="/root/dotmac")
    parser.add_argument(
        "--openbao-address",
        default=os.environ.get("MIGRATION_OPENBAO_ADDR", DEFAULT_OPENBAO_ADDR),
    )
    parser.add_argument("--role-id-file", type=Path, default=DEFAULT_ROLE_ID_FILE)
    parser.add_argument("--secret-id-file", type=Path, default=DEFAULT_SECRET_ID_FILE)
    parser.add_argument("--openbao-timeout", type=float, default=10.0)
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--people-employment-type-activation", action="store_true")
    parser.add_argument("image_digest", nargs="?")
    return parser


def main() -> int:
    try:
        return run_deployment(_parser().parse_args())
    except DeploymentCredentialError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
