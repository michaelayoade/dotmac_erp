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
import shlex
import subprocess
import sys
import threading
from dataclasses import dataclass
from enum import Enum
from urllib.parse import unquote, urlsplit

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import deploy_production as credentials
from app.migration_authority import parse_authentication_expectation


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
    ssh_user: str = "root"
    identity_file: Path = Path.home() / ".ssh" / "id_ed25519"
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


def _bounded_run(
    command: list[str], material: bytes, timeout: float, limit: int = 4096
) -> tuple[int, bytes]:
    """Drain but never retain unlimited or stderr output from an untrusted child."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "LANG", "LC_ALL", "LC_CTYPE"}
    }
    with subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=environment,
    ) as child:
        output = bytearray()
        overflow = False

        def drain() -> None:
            nonlocal overflow
            assert child.stdout is not None
            while chunk := child.stdout.read(1024):
                if len(output) + len(chunk) <= limit:
                    output.extend(chunk)
                else:
                    overflow = True

        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        try:
            assert child.stdin is not None
            child.stdin.write(material)
            child.stdin.close()
            code = child.wait(timeout=timeout)
        except BaseException:
            child.kill()
            child.wait()
            raise
        finally:
            reader.join(timeout=1)
        if reader.is_alive() or overflow:
            return 2, b""
        return code, bytes(output)


# Executed with -I -B: inherited Python paths and bytecode writes are disabled.
# File descriptors are silenced BEFORE importing the installed image verifier.
INSTALLED_VERIFIER = r"""
def installed_verifier():
    import pathlib, sys
    for target in ("/app", "/app/scripts", "/app/app", "/app/scripts/bootstrap_database_roles.py"):
        path = pathlib.Path(target)
        if path.is_symlink() or not path.exists():
            raise ValueError()
    sys.path.insert(0, "/app")
    from scripts import bootstrap_database_roles as owner
    if pathlib.Path(owner.__file__).resolve() != pathlib.Path("/app/scripts/bootstrap_database_roles.py"):
        raise ValueError()
    if not callable(owner.verify_migration_connection):
        raise ValueError()
    return owner
"""

SOURCE_DRIVER = (
    r"""
import os, sys
result_fd = os.dup(1)
null = os.open(os.devnull, os.O_WRONLY)
os.dup2(null, 1)
os.dup2(null, 2)
os.close(null)
os.environ.clear()
"""
    + INSTALLED_VERIFIER
    + r"""
status = "refused"
try:
    import signal, logging
    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError()))
    signal.alarm(10)
    logging.disable(logging.CRITICAL)
    installed_verifier()
    status = "verified"
except BaseException:
    status = "refused"
os.write(result_fd, ('{"stage":"runtime","status":"' + status + '"}\n').encode())
os.close(result_fd)
"""
)

DATABASE_DRIVER = (
    INSTALLED_VERIFIER
    + r"""
import os, sys
result_fd = os.dup(1)
null = os.open(os.devnull, os.O_WRONLY)
os.dup2(null, 1)
os.dup2(null, 2)
os.close(null)
os.environ.clear()
status = "refused"
conn = None
try:
    import signal
    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError()))
    signal.alarm(25)
    import pathlib
    import logging
    logging.disable(logging.CRITICAL)
    import psycopg
    from psycopg.conninfo import conninfo_to_dict
    database, authentication = sys.argv[1:]
    dsn = sys.stdin.buffer.read(4097)
    if len(dsn) > 4096 or b"\n" in dsn or b"\x00" in dsn:
        raise ValueError()
    connect_url = dsn.decode().replace("postgresql+psycopg://", "postgresql://", 1)
    settings = conninfo_to_dict(connect_url)
    if settings.get("user") != "app_admin" or settings.get("dbname") != database:
        raise ValueError()
    if any(key in settings for key in ("options", "service", "passfile")):
        raise ValueError()
    owner = installed_verifier()
    os.environ[owner.EXPECTED_DATABASE_VAR] = database
    os.environ[owner.EXPECTED_AUTHENTICATION_VAR] = authentication
    conn = psycopg.connect(connect_url,
        autocommit=False, connect_timeout=5,
        options="-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=1000")
    conn.read_only = True
    verdict = owner.verify_migration_connection(conn)
    if type(verdict) is int and verdict == 0:
        status = "verified"
except BaseException:
    status = "refused"
finally:
    if conn is not None:
        try:
            conn.rollback()
        except BaseException:
            status = "refused"
        try:
            conn.close()
        except BaseException:
            status = "refused"
    os.write(result_fd, ('{"stage":"database","status":"' + status + '"}\n').encode())
    os.close(result_fd)
"""
)


# This source contains no credential. Runtime inspection returns only a closed
# status plus the immutable container ID used to bind the second invocation.
REMOTE_DRIVER = r"""
import json, os, posixpath, re, subprocess, sys
phase, expected, pinned, database, authentication = sys.argv[1:]
def run(args, material=b"", limit=65536):
    code, output = bounded_run(args, material, 35, limit)
    if code:
        raise ValueError()
    return output

container_projection = r'''{{ $override := false }}{{range .Config.Env}}{{ $key := index (split . "=") 0 }}{{if and (ne . (printf "%s=" $key)) (or (eq $key "PYTHONPATH") (eq $key "PYTHONHOME") (eq (index (split $key "_") 0) "LD") (eq $key "GCONV_PATH") (eq $key "GLIBC_TUNABLES"))}}{{$override = true}}{{end}}{{end}}[{"Id":{{json .Id}},"Name":{{json .Name}},"State":{"Running":{{json .State.Running}}},"Image":{{json .Image}},"Config":{"Image":{{json .Config.Image}},"WorkingDir":{{json .Config.WorkingDir}},"ProcessOverride":{{$override}}},"Mounts":[{{range $i,$m := .Mounts}}{{if $i}},{{end}}{"Destination":{{json $m.Destination}}}{{end}}]}'''
image_projection = r'''[{"Id":{{json .Id}},"RepoDigests":{{json .RepoDigests}}}]'''
def docker(*args):
    return ["docker", "--host", "unix:///var/run/docker.sock", *args]
def inspect():
    selector = pinned if phase == "probe" else "dotmac_erp_app"
    rows = json.loads(run(docker("inspect", "--type", "container", "--format", container_projection, selector)))
    if not isinstance(rows, list) or len(rows) != 1:
        raise ValueError()
    info = rows[0]
    cid = info["Id"]
    if not re.fullmatch(r"[0-9a-f]{64}", cid) or (pinned and cid != pinned):
        raise ValueError()
    if info["Name"] != "/dotmac_erp_app" or info["State"]["Running"] is not True:
        raise ValueError()
    image = json.loads(run(docker("image", "inspect", "--format", image_projection, expected)))[0]
    if expected not in image["RepoDigests"] or image["Id"] != info["Image"]:
        raise ValueError()
    if info["Config"]["Image"] != expected or info["Config"]["WorkingDir"] != "/app":
        raise ValueError()
    if info["Config"]["ProcessOverride"] is not False:
        raise ValueError()
    protected = ("/app", "/opt", "/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc")
    def overlaps(raw):
        path = posixpath.normpath(raw)
        return path == "/" or any(path == root or path.startswith(root + "/") or root.startswith(path + "/") for root in protected)
    for mount in info["Mounts"]:
        if overlaps(mount["Destination"]):
            raise ValueError()
    data = ("/app/static/avatars", "/app/static/branding", "/app/uploads/resumes")
    for line in run(docker("diff", cid)).decode().splitlines():
        if len(line) < 3 or line[0] not in "ACD":
            raise ValueError()
        path = posixpath.normpath(line[2:])
        if line[0] == "C" and path in ("/app", "/app/static", "/app/uploads"):
            continue
        if any(path == root or path.startswith(root + "/") for root in data):
            continue
        if overlaps(path):
            raise ValueError()
    source_result = json.loads(run(docker("exec", "-i", cid, "/usr/bin/env", "-i", "/opt/venv/bin/python", "-I", "-B", "-c", source_driver), limit=4096))
    if source_result != {"stage":"runtime", "status":"verified"}:
        raise ValueError()
    return cid
try:
    cid = inspect()
    if phase == "inspect":
        print(json.dumps({"stage":"runtime", "status":"verified", "container_id":cid}, sort_keys=True))
    elif phase == "probe":
        material = sys.stdin.buffer.read(4097)
        if len(material) > 4096:
            raise ValueError()
        output = run(docker("exec", "-i", cid, "/usr/bin/env", "-i", "/opt/venv/bin/python", "-I", "-B", "-c", database_driver, database, authentication), material, 4096)
        result = json.loads(output)
        if result not in ({"stage":"database","status":"verified"}, {"stage":"database","status":"refused"}):
            raise ValueError()
        print(json.dumps(result, sort_keys=True))
    else:
        raise ValueError()
except BaseException:
    print('{"stage":"runtime","status":"refused"}')
"""


def _remote_source() -> str:
    # Share the same bounded runner without deploying files to the host.
    import inspect

    return (
        "import os, subprocess, threading\n"
        + inspect.getsource(_bounded_run).replace("_bounded_run", "bounded_run", 1)
        + "\ndatabase_driver = "
        + repr(DATABASE_DRIVER)
        + "\nsource_driver = "
        + repr(SOURCE_DRIVER)
        + "\n"
        + REMOTE_DRIVER
    )


def _ssh(
    request: VerificationRequest, phase: str, pinned: str = "", material: bytes = b""
) -> dict[str, str]:
    remote = shlex.join(
        [
            "python3",
            "-I",
            "-B",
            "-c",
            _remote_source(),
            phase,
            request.expected_image,
            pinned,
            request.expected_database,
            request.expected_authentication,
        ]
    )
    command = [
        "ssh",
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
        remote,
    ]
    code, output = _bounded_run(command, material, 45)
    if code:
        raise ValueError("transport")
    result = json.loads(output)
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
        parser.add_argument("--ssh-user", default="root")
        parser.add_argument(
            "--identity-file", type=Path, default=Path.home() / ".ssh" / "id_ed25519"
        )
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
