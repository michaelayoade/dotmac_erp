from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from scripts.deploy_production import (
    CANONICAL_SECRET_ENDPOINT,
    DeploymentCredentialError,
    fetch_migration_database_url,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


class _OpenBaoHandler(BaseHTTPRequestHandler):
    role_id = "role-id-for-test"
    secret_id = "secret-id-for-test"
    token = "short-lived-token-for-test"
    dsn = "postgresql+psycopg://app_admin:test-only@db.test/dotmac_erp"
    requests: list[dict[str, Any]] = []

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        self.requests.append({"method": "POST", "path": self.path, "body": body})
        if self.path != "/v1/auth/approle/login" or body != {
            "role_id": self.role_id,
            "secret_id": self.secret_id,
        }:
            self.send_response(403)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"auth": {"client_token": self.token}}).encode())

    def do_GET(self) -> None:  # noqa: N802
        self.requests.append(
            {
                "method": "GET",
                "path": self.path,
                "token": self.headers.get("X-Vault-Token"),
            }
        )
        if (
            self.path != CANONICAL_SECRET_ENDPOINT
            or self.headers.get("X-Vault-Token") != self.token
        ):
            self.send_response(403)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(
            json.dumps(
                {"data": {"data": {"MIGRATION_DATABASE_URL": self.dsn}}}
            ).encode()
        )


@pytest.fixture
def openbao_server() -> tuple[str, type[_OpenBaoHandler]]:
    _OpenBaoHandler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OpenBaoHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", _OpenBaoHandler
    finally:
        server.shutdown()
        thread.join()


def _private_file(path: Path, value: str) -> Path:
    path.write_text(value, encoding="utf-8")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return path


def test_fetches_only_the_canonical_secret_with_a_short_lived_token(
    tmp_path: Path, openbao_server: tuple[str, type[_OpenBaoHandler]]
) -> None:
    address, handler = openbao_server
    role_id = _private_file(tmp_path / "role-id", handler.role_id)
    secret_id = _private_file(tmp_path / "secret-id", handler.secret_id)

    result = fetch_migration_database_url(
        address=address,
        role_id_file=role_id,
        secret_id_file=secret_id,
    )

    assert result == handler.dsn
    assert handler.requests == [
        {
            "method": "POST",
            "path": "/v1/auth/approle/login",
            "body": {"role_id": handler.role_id, "secret_id": handler.secret_id},
        },
        {
            "method": "GET",
            "path": CANONICAL_SECRET_ENDPOINT,
            "token": handler.token,
        },
    ]


def test_canonical_endpoint_matches_the_custody_contract() -> None:
    from app.migration_credential_custody import CANONICAL_POINTER

    expected = f"/v1/{CANONICAL_POINTER.mount}/data/{CANONICAL_POINTER.path}"
    assert expected == CANONICAL_SECRET_ENDPOINT


def test_checked_in_policy_can_only_read_the_canonical_path() -> None:
    policy = (REPO_ROOT / "deploy/openbao/erp-production-deployer.hcl").read_text(
        encoding="utf-8"
    )

    assert f'path "{CANONICAL_SECRET_ENDPOINT.removeprefix("/v1/")}"' in policy
    assert 'capabilities = ["read"]' in policy
    for forbidden in ("create", "update", "delete", "list", "sudo", "*"):
        assert f'"{forbidden}"' not in policy


def test_refuses_plain_http_to_a_non_loopback_openbao() -> None:
    with pytest.raises(DeploymentCredentialError, match="loopback"):
        fetch_migration_database_url(
            address="http://192.0.2.10:8200",
            role_id_file=Path("unused"),
            secret_id_file=Path("unused"),
        )


def test_refuses_group_readable_approle_material(
    tmp_path: Path, openbao_server: tuple[str, type[_OpenBaoHandler]]
) -> None:
    address, handler = openbao_server
    role_id = _private_file(tmp_path / "role-id", handler.role_id)
    secret_id = _private_file(tmp_path / "secret-id", handler.secret_id)
    secret_id.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP)

    with pytest.raises(DeploymentCredentialError, match="0600"):
        fetch_migration_database_url(
            address=address,
            role_id_file=role_id,
            secret_id_file=secret_id,
        )


def test_cli_streams_dsn_to_ssh_without_argv_or_environment_exposure(
    tmp_path: Path, openbao_server: tuple[str, type[_OpenBaoHandler]]
) -> None:
    address, handler = openbao_server
    role_id = _private_file(tmp_path / "role-id", handler.role_id)
    secret_id = _private_file(tmp_path / "secret-id", handler.secret_id)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    capture = tmp_path / "ssh-capture.json"
    ssh = fake_bin / "ssh"
    ssh.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "Path(os.environ['SSH_CAPTURE']).write_text(json.dumps({\n"
        "    'argv': sys.argv[1:],\n"
        "    'stdin': sys.stdin.read(),\n"
        "    'env_names': sorted(os.environ),\n"
        "}), encoding='utf-8')\n",
        encoding="utf-8",
    )
    ssh.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["SSH_CAPTURE"] = str(capture)
    env["MIGRATION_DATABASE_URL"] = "must-not-reach-ssh"
    env["OPENBAO_TOKEN"] = "must-not-reach-ssh"
    env["BAO_TOKEN"] = "must-not-reach-ssh"

    result = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "scripts/deploy_production.py",
            "--host",
            "erp.dotmac.io",
            "--openbao-address",
            address,
            "--role-id-file",
            str(role_id),
            "--secret-id-file",
            str(secret_id),
            "--quick",
        ],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    recorded = json.loads(capture.read_text(encoding="utf-8"))
    argv = " ".join(recorded["argv"])
    assert "erp.dotmac.io" in argv
    assert "--quick" in argv
    assert 'deploy.sh "$@"' in argv
    assert handler.dsn not in argv
    assert "MIGRATION_DATABASE_URL" not in recorded["env_names"]
    assert "OPENBAO_TOKEN" not in recorded["env_names"]
    assert "BAO_TOKEN" not in recorded["env_names"]
    assert recorded["stdin"] == f"{handler.dsn}\n"


def test_cli_requires_an_explicit_production_host() -> None:
    result = subprocess.run(  # noqa: S603
        [sys.executable, "scripts/deploy_production.py"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "--host" in result.stderr
