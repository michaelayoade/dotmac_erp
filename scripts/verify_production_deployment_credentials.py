#!/usr/bin/env python3
"""Read-only proof of the canonical credential against an exact installed runtime.

This controller command does not deploy. It reuses the canonical credential
loader and the installed image's migration verifier; all diagnostics are closed
names-only JSON. No credential is sent until a separate runtime inspection passes.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import math
import os
from pathlib import Path
import re
import sys
from dataclasses import dataclass
from enum import Enum
from urllib.parse import unquote, urlsplit

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import deploy_production as credentials
from app.migration_authority import parse_authentication_expectation
from scripts.erp_credential_proof_server import MARKER, _bounded_run, decode_object


class _Discard(io.TextIOBase):
    def write(self, value: str) -> int:
        return len(value)


class Stage(str, Enum):
    REQUEST = "request"
    RUNTIME = "runtime"
    CREDENTIAL = "credential"
    DATABASE = "database"


class Status(str, Enum):
    VERIFIED = "verified"
    REFUSED = "refused"


@dataclass(frozen=True)
class VerificationResult:
    stage: Stage
    status: Status

    def as_json(self) -> str:
        return json.dumps(
            {"stage": self.stage.value, "status": self.status.value}, sort_keys=True
        )


@dataclass(frozen=True)
class VerificationRequest:
    host: str
    expected_image: str
    expected_database: str
    expected_authentication: str
    identity_file: Path
    ssh_user: str = "erp-proof"
    openbao_address: str = credentials.DEFAULT_OPENBAO_ADDR
    role_id_file: Path = credentials.DEFAULT_ROLE_ID_FILE
    secret_id_file: Path = credentials.DEFAULT_SECRET_ID_FILE
    openbao_timeout: float = 10.0

    def validate(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", self.host):
            raise ValueError("host")
        if not re.fullmatch(r"[a-z_][a-z0-9_-]*", self.ssh_user):
            raise ValueError("ssh_user")
        if not re.fullmatch(
            r"ghcr\.io/michaelayoade/dotmac_erp@sha256:[0-9a-f]{64}",
            self.expected_image,
        ):
            raise ValueError("expected_image")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,62}", self.expected_database):
            raise ValueError("expected_database")
        expected = parse_authentication_expectation(self.expected_authentication)
        if (
            expected is None
            or expected.identity != "app_admin"
            or not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", expected.method)
        ):
            raise ValueError("expected_authentication")
        if self.expected_authentication != f"{expected.method}:{expected.identity}":
            raise ValueError("expected_authentication")
        credentials._validated_openbao_address(self.openbao_address)
        if (
            not math.isfinite(self.openbao_timeout)
            or not 0 < self.openbao_timeout <= 30
        ):
            raise ValueError("openbao_timeout")
        for path in (self.identity_file, self.role_id_file, self.secret_id_file):
            if not path.is_absolute() or "\x00" in str(path) or "\n" in str(path):
                raise ValueError("path")


def _ssh(
    request: VerificationRequest, phase: str, pinned: str = "", material: bytes = b""
) -> dict[str, str]:
    payload = {
        "version": 1,
        "operation": phase,
        "image": request.expected_image,
        "database": request.expected_database,
        "authentication": request.expected_authentication,
    }
    if phase == "probe":
        payload.update(container_id=pinned, dsn=material.decode())
    elif phase != "inspect" or pinned or material:
        raise ValueError("protocol")
    command = [
        "/usr/bin/ssh",
        "-T",
        "-o",
        "ForwardAgent=no",
        "-o",
        "ClearAllForwardings=yes",
        "-o",
        "ExitOnForwardFailure=yes",
        "-F",
        "/dev/null",
        "-i",
        str(request.identity_file),
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "ConnectTimeout=5",
        f"{request.ssh_user}@{request.host}",
        MARKER,
    ]
    code, output = _bounded_run(command, json.dumps(payload).encode(), 120)
    if code:
        raise ValueError("transport")
    result = decode_object(output, 4096)
    if not isinstance(result, dict):
        raise ValueError("protocol")
    if phase == "inspect":
        if (
            set(result) != {"stage", "status", "container_id"}
            or result["stage"] != "runtime"
            or result["status"] != "verified"
            or not isinstance(result["container_id"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", result["container_id"])
        ):
            raise ValueError("runtime")
    elif result not in (
        {"stage": "database", "status": "verified"},
        {"stage": "database", "status": "refused"},
    ):
        raise ValueError("protocol")
    return result


def verify(request: VerificationRequest) -> VerificationResult:
    stage = Stage.REQUEST
    try:
        request.validate()
        stage = Stage.RUNTIME
        runtime = _ssh(request, "inspect")
        stage = Stage.CREDENTIAL
        # Owners may print diagnostics in future; suppress them at this boundary.
        with (
            contextlib.redirect_stdout(_Discard()),
            contextlib.redirect_stderr(_Discard()),
        ):
            dsn = credentials.fetch_migration_database_url(
                address=request.openbao_address,
                role_id_file=request.role_id_file,
                secret_id_file=request.secret_id_file,
                timeout=request.openbao_timeout,
            )
        parsed = urlsplit(dsn)
        if (
            len(dsn.encode()) > 4096
            or any(char in dsn for char in ("\n", "\r", "\x00"))
            or parsed.scheme not in {"postgresql", "postgresql+psycopg"}
            or parsed.username != "app_admin"
            or unquote(parsed.path) != "/" + request.expected_database
            or not parsed.hostname
            or not parsed.password
            or parsed.fragment
        ):
            raise ValueError("dsn")
        stage = Stage.DATABASE
        try:
            result = _ssh(request, "probe", runtime["container_id"], dsn.encode())
        finally:
            dsn = ""
        return VerificationResult(Stage.DATABASE, Status(result["status"]))
    except Exception:
        return VerificationResult(stage, Status.REFUSED)


class _ClosedParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError("request")


def main(argv: list[str] | None = None) -> int:
    try:
        parser = _ClosedParser(description=__doc__)
        parser.add_argument("--host", required=True)
        parser.add_argument("--expected-image", required=True)
        parser.add_argument("--expected-database", required=True)
        parser.add_argument("--expected-authentication", required=True)
        parser.add_argument("--ssh-user", default="erp-proof")
        parser.add_argument("--identity-file", type=Path, required=True)
        parser.add_argument(
            "--openbao-address",
            default=os.environ.get(
                "MIGRATION_OPENBAO_ADDR", credentials.DEFAULT_OPENBAO_ADDR
            ),
        )
        parser.add_argument(
            "--role-id-file", type=Path, default=credentials.DEFAULT_ROLE_ID_FILE
        )
        parser.add_argument(
            "--secret-id-file", type=Path, default=credentials.DEFAULT_SECRET_ID_FILE
        )
        parser.add_argument("--openbao-timeout", type=float, default=10.0)
        result = verify(VerificationRequest(**vars(parser.parse_args(argv))))
    except Exception:
        result = VerificationResult(Stage.REQUEST, Status.REFUSED)
    print(result.as_json())
    return 0 if result.status is Status.VERIFIED else 2


if __name__ == "__main__":
    raise SystemExit(main())
