#!/usr/bin/python3 -I
"""Root-owned fixed ERP credential proof server; stdin is data, never code."""

from __future__ import annotations
import json
import os
import posixpath
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import threading
from urllib.parse import unquote, urlsplit

MARKER = "dotmac-erp-credential-proof-v1"
POLICY_PATH = Path("/etc/dotmac-erp/credential-proof.json")
HELPER_PATH = Path("/usr/local/libexec/dotmac-erp-credential-proof.py")
MAX_REQUEST = 8192


def _bounded_run(
    command: list[str], material: bytes, timeout: float, limit: int = 4096
) -> tuple[int, bytes]:
    """Drain but never retain unlimited or stderr output from an untrusted child."""
    environment = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}
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

            def feed() -> None:
                try:
                    child.stdin.write(material)
                    child.stdin.close()
                except (BrokenPipeError, OSError):
                    pass

            writer = threading.Thread(target=feed, daemon=True)
            writer.start()
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
    signal.signal(signal.SIGALRM, signal.SIG_DFL)
    # Keep the kernel deadline armed through imports and the final receipt.
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
    signal.signal(signal.SIGALRM, signal.SIG_DFL)
    # SIG_DFL hard-terminates even blocked native calls or cleanup; never disarm.
    signal.alarm(25)
    import pathlib
    import logging
    logging.disable(logging.CRITICAL)
    import psycopg
    from psycopg.conninfo import conninfo_to_dict
    database, authentication, host, port = sys.argv[1:]
    dsn = sys.stdin.buffer.read(4097)
    if len(dsn) > 4096 or b"\n" in dsn or b"\x00" in dsn:
        raise ValueError()
    connect_url = dsn.decode().replace("postgresql+psycopg://", "postgresql://", 1)
    settings = conninfo_to_dict(connect_url)
    if settings.get("user") != "app_admin" or settings.get("dbname") != database:
        raise ValueError()
    if set(settings) != {"user", "password", "dbname", "host", "port"}:
        raise ValueError()
    if settings["host"] != host or settings["port"] != port:
        raise ValueError()
    owner = installed_verifier()
    os.environ[owner.EXPECTED_DATABASE_VAR] = database
    os.environ[owner.EXPECTED_AUTHENTICATION_VAR] = authentication
    conn = psycopg.connect(connect_url,
        autocommit=False, connect_timeout=5,
        options="-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=1000 -c idle_in_transaction_session_timeout=5000")
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


def _run(args, material=b"", limit=65536):
    code, output = _bounded_run(args, material, 35, limit)
    if code:
        raise ValueError("child")
    return output


container_projection = r"""{{ $override := false }}{{range .Config.Env}}{{ $key := index (split . "=") 0 }}{{if and (ne . (printf "%s=" $key)) (or (eq $key "PYTHONPATH") (eq $key "PYTHONHOME") (eq (index (split $key "_") 0) "LD") (eq $key "GCONV_PATH") (eq $key "GLIBC_TUNABLES"))}}{{$override = true}}{{end}}{{end}}[{"Id":{{json .Id}},"Name":{{json .Name}},"State":{"Running":{{json .State.Running}}},"Image":{{json .Image}},"Config":{"Image":{{json .Config.Image}},"WorkingDir":{{json .Config.WorkingDir}},"ProcessOverride":{{$override}}},"Mounts":[{{range $i,$m := .Mounts}}{{if $i}},{{end}}{"Destination":{{json $m.Destination}}}{{end}}]}"""
image_projection = r"""[{"Id":{{json .Id}},"RepoDigests":{{json .RepoDigests}}}]"""


def docker(*args):
    return ["/usr/bin/docker", "--host", "unix:///var/run/docker.sock", *args]


def inspect_runtime(expected, pinned=""):
    phase = "probe" if pinned else "inspect"
    selector = pinned if phase == "probe" else "dotmac_erp_app"
    rows = json.loads(
        _run(
            docker(
                "inspect",
                "--type",
                "container",
                "--format",
                container_projection,
                selector,
            )
        )
    )
    if not isinstance(rows, list) or len(rows) != 1:
        raise ValueError()
    info = rows[0]
    cid = info["Id"]
    if not re.fullmatch(r"[0-9a-f]{64}", cid) or (pinned and cid != pinned):
        raise ValueError()
    if info["Name"] != "/dotmac_erp_app" or info["State"]["Running"] is not True:
        raise ValueError()
    image = json.loads(
        _run(docker("image", "inspect", "--format", image_projection, expected))
    )[0]
    if expected not in image["RepoDigests"] or image["Id"] != info["Image"]:
        raise ValueError()
    if info["Config"]["Image"] != expected or info["Config"]["WorkingDir"] != "/app":
        raise ValueError()
    if info["Config"]["ProcessOverride"] is not False:
        raise ValueError()
    protected = ("/app", "/opt", "/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc")

    def overlaps(raw):
        path = posixpath.normpath(raw)
        return path == "/" or any(
            path == root or path.startswith(root + "/") or root.startswith(path + "/")
            for root in protected
        )

    for mount in info["Mounts"]:
        if overlaps(mount["Destination"]):
            raise ValueError()
    data = ("/app/static/avatars", "/app/static/branding", "/app/uploads/resumes")
    for line in _run(docker("diff", cid)).decode().splitlines():
        if len(line) < 3 or line[0] not in "ACD":
            raise ValueError()
        path = posixpath.normpath(line[2:])
        if line[0] == "C" and path in ("/app", "/app/static", "/app/uploads"):
            continue
        if any(path == root or path.startswith(root + "/") for root in data):
            continue
        if overlaps(path):
            raise ValueError()
    source_result = decode_object(
        _run(
            docker(
                "exec",
                "-i",
                cid,
                "/usr/bin/env",
                "-i",
                "/opt/venv/bin/python",
                "-I",
                "-B",
                "-c",
                SOURCE_DRIVER,
            ),
            limit=4096,
        )
    )
    if source_result != {"stage": "runtime", "status": "verified"}:
        raise ValueError()
    return cid


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate")
        result[key] = value
    return result


def decode_object(raw, limit=MAX_REQUEST):
    if len(raw) > limit:
        raise ValueError("size")
    value = json.loads(raw, object_pairs_hook=_pairs)
    if type(value) is not dict:
        raise ValueError("object")
    return value


def validate_request(value):
    common = {"version", "operation", "image", "database", "authentication"}
    if value.get("operation") == "probe":
        common |= {"container_id", "dsn"}
    elif value.get("operation") != "inspect":
        raise ValueError("operation")
    if (
        set(value) != common
        or type(value["version"]) is not int
        or value["version"] != 1
    ):
        raise ValueError("protocol")
    if any(type(value[k]) is not str for k in common - {"version"}):
        raise ValueError("type")
    if not re.fullmatch(
        r"ghcr\.io/michaelayoade/dotmac_erp@sha256:[0-9a-f]{64}", value["image"]
    ):
        raise ValueError("image")
    if value["operation"] == "probe" and not re.fullmatch(
        r"[0-9a-f]{64}", value["container_id"]
    ):
        raise ValueError("container")
    return value


def trusted_file(path):
    # Check every parent independently; symlinks and writable ownership refuse.
    for item in (path, *path.parents):
        info = item.lstat()
        if info.st_uid != 0 or info.st_mode & 0o022 or stat.S_ISLNK(info.st_mode):
            raise ValueError("ownership")
        if item == path:
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("file")
        elif not stat.S_ISDIR(info.st_mode):
            raise ValueError("parent")


def load_policy():
    if os.geteuid() != 0 or Path(__file__) != HELPER_PATH:
        raise ValueError("installation")
    trusted_file(HELPER_PATH)
    trusted_file(POLICY_PATH)
    with POLICY_PATH.open("rb") as stream:
        policy = decode_object(stream.read(4097), 4096)
    keys = {
        "version",
        "container",
        "image_repository",
        "database",
        "authentication",
        "database_host",
        "database_port",
    }
    if (
        set(policy) != keys
        or type(policy["version"]) is not int
        or policy["version"] != 1
    ):
        raise ValueError("policy")
    if (
        policy["container"] != "dotmac_erp_app"
        or policy["image_repository"] != "ghcr.io/michaelayoade/dotmac_erp"
        or policy["database"] != "dotmac_erp"
        or policy["authentication"] != "scram-sha-256:app_admin"
    ):
        raise ValueError("policy")
    if type(policy["database_host"]) is not str or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]{0,252}", policy["database_host"]
    ):
        raise ValueError("endpoint")
    if (
        type(policy["database_port"]) is not int
        or not 1 <= policy["database_port"] <= 65535
    ):
        raise ValueError("port")
    return policy


def validate_dsn(dsn, policy):
    if len(dsn.encode()) > 4096 or any(c in dsn for c in ("\n", "\r", "\x00")):
        raise ValueError("dsn")
    parsed = urlsplit(dsn)
    # No query parameters: this closes hostaddr/options/service/passfile and all
    # other libpq override routes before material reaches the installed driver.
    if (
        parsed.scheme not in {"postgresql", "postgresql+psycopg"}
        or parsed.username != "app_admin"
        or not parsed.password
        or unquote(parsed.path) != "/" + policy["database"]
        or parsed.hostname != policy["database_host"]
        or (parsed.port or 5432) != policy["database_port"]
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("destination")
    # Make the explicit port part of the libpq driver's exact-key contract.
    if parsed.port is None:
        raise ValueError("explicit port")


def handle(value, policy):
    request = validate_request(value)
    if (
        request["database"] != policy["database"]
        or request["authentication"] != policy["authentication"]
        or request["image"].split("@", 1)[0] != policy["image_repository"]
    ):
        raise ValueError("selector")
    if request["operation"] == "probe":
        validate_dsn(request["dsn"], policy)
    cid = inspect_runtime(request["image"], request.get("container_id", ""))
    if request["operation"] == "inspect":
        return {"stage": "runtime", "status": "verified", "container_id": cid}
    # Repeat the complete attestation immediately before material-bearing exec.
    inspect_runtime(request["image"], cid)
    output = _run(
        docker(
            "exec",
            "-i",
            cid,
            "/usr/bin/env",
            "-i",
            "/opt/venv/bin/python",
            "-I",
            "-B",
            "-c",
            DATABASE_DRIVER,
            policy["database"],
            policy["authentication"],
            policy["database_host"],
            str(policy["database_port"]),
        ),
        request["dsn"].encode(),
        4096,
    )
    result = decode_object(output, 4096)
    if result not in (
        {"stage": "database", "status": "verified"},
        {"stage": "database", "status": "refused"},
    ):
        raise ValueError("receipt")
    return result


def main():
    result = {"stage": "runtime", "status": "refused"}
    try:
        signal.signal(signal.SIGALRM, signal.SIG_DFL)
        signal.alarm(120)
        if len(sys.argv) != 1:
            raise ValueError("arguments")
        policy = load_policy()
        os.environ.clear()
        request = decode_object(sys.stdin.buffer.read(MAX_REQUEST + 1))
        result = handle(request, policy)
    except BaseException:
        pass
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "verified" else 2


if __name__ == "__main__":
    raise SystemExit(main())
