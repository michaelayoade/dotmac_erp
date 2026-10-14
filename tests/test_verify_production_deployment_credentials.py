"""Offline fakes only; executed by the hosted repository test owner."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import signal
import subprocess
import sys

import pytest

from scripts import verify_production_deployment_credentials as proof

IMAGE = "ghcr.io/michaelayoade/dotmac_erp@sha256:" + "a" * 64
CONTAINER = "b" * 64
DSN = "postgresql+psycopg://app_admin:sentinel-secret@db:5432/dotmac_erp"
REQUEST = proof.VerificationRequest(
    host="erp.example.test",
    expected_image=IMAGE,
    expected_database="dotmac_erp",
    expected_authentication="scram-sha-256:app_admin",
)


@pytest.mark.parametrize(
    "change",
    [
        {"host": "-oProxyCommand=bad"},
        {"ssh_user": "root;id"},
        {"expected_image": "latest"},
        {"expected_image": "sha256:" + "a" * 64},
        {"expected_database": ""},
        {"expected_database": "db;DROP"},
        {"expected_authentication": "scram-sha-256:postgres"},
        {"expected_authentication": ""},
        {"expected_authentication": " scram-sha-256:app_admin"},
        {"openbao_address": "http://remote.test"},
        {"openbao_timeout": float("nan")},
        {"identity_file": Path("relative")},
    ],
)
def test_invalid_selectors_before_material_or_ssh(monkeypatch, change):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid request reached material or host")

    monkeypatch.setattr(proof, "_ssh", forbidden)
    monkeypatch.setattr(proof.credentials, "fetch_migration_database_url", forbidden)
    assert proof.verify(replace(REQUEST, **change)) == proof.VerificationResult(
        proof.Stage.REQUEST, proof.Status.REFUSED
    )


def test_inspection_precedes_fetch_and_probe_uses_stdin(monkeypatch):
    calls = []

    def ssh(request, phase, pinned="", material=b""):
        calls.append(phase)
        if phase == "inspect":
            assert not material
            return {"container_id": CONTAINER}
        assert pinned == CONTAINER
        assert material == DSN.encode()
        return {"stage": "database", "status": "verified"}

    def fetch(**kwargs):
        calls.append("fetch")
        return DSN

    monkeypatch.setattr(proof, "_ssh", ssh)
    monkeypatch.setattr(proof.credentials, "fetch_migration_database_url", fetch)
    assert proof.verify(REQUEST).status is proof.Status.VERIFIED
    assert calls == ["inspect", "fetch", "probe"]


@pytest.mark.parametrize(
    "dsn",
    [
        DSN.replace("app_admin", "postgres"),
        DSN.replace("dotmac_erp", "wrong"),
        DSN + "\nsecret",
        "not-a-dsn",
    ],
)
def test_bad_material_never_streamed(monkeypatch, dsn):
    monkeypatch.setattr(proof, "_ssh", lambda *args: {"container_id": CONTAINER})
    monkeypatch.setattr(
        proof.credentials, "fetch_migration_database_url", lambda **kwargs: dsn
    )
    assert proof.verify(REQUEST) == proof.VerificationResult(
        proof.Stage.CREDENTIAL, proof.Status.REFUSED
    )


def test_credential_echo_and_exception_never_escape(monkeypatch, capsys):
    monkeypatch.setattr(proof, "_ssh", lambda *args: {"container_id": CONTAINER})

    def fetch(**kwargs):
        sys.stdout.write(DSN)
        sys.stderr.write(DSN)
        raise RuntimeError(DSN)

    monkeypatch.setattr(proof.credentials, "fetch_migration_database_url", fetch)
    assert proof.verify(REQUEST).status is proof.Status.REFUSED
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize(
    "output",
    [
        DSN.encode(),
        b'noise\n{"stage":"database","status":"verified"}',
        b'{"stage":"database","status":"verified","secret":"x"}',
        b"[]",
        b'{"stage":"runtime","status":"verified"}',
    ],
)
def test_noisy_or_malformed_transport_refuses(monkeypatch, output):
    monkeypatch.setattr(proof, "_bounded_run", lambda *args: (0, output))
    with pytest.raises((ValueError, KeyError)):
        proof._ssh(REQUEST, "probe", CONTAINER, DSN.encode())


def test_ssh_timeout_is_closed(monkeypatch):
    def timeout(*args):
        raise subprocess.TimeoutExpired(DSN, 45, output=DSN)

    monkeypatch.setattr(proof, "_bounded_run", timeout)
    assert proof.verify(REQUEST) == proof.VerificationResult(
        proof.Stage.RUNTIME, proof.Status.REFUSED
    )


def test_ssh_argv_contains_no_material(monkeypatch):
    def runner(command, material, timeout):
        assert DSN not in repr(command)
        assert material == DSN.encode()
        assert timeout == 45
        assert "StrictHostKeyChecking=yes" in command
        return 0, b'{"stage":"database","status":"verified"}'

    monkeypatch.setattr(proof, "_bounded_run", runner)
    assert proof._ssh(REQUEST, "probe", CONTAINER, DSN.encode())["status"] == "verified"


def runtime_fake(
    monkeypatch,
    capsys,
    *,
    change=None,
    image_change=None,
    phase="inspect",
    diff=b"",
    child=None,
    source_status="verified",
):
    info = {
        "Id": CONTAINER,
        "Name": "/dotmac_erp_app",
        "State": {"Running": True},
        "Image": "sha256:" + "c" * 64,
        "Config": {"Image": IMAGE, "WorkingDir": "/app", "ProcessOverride": False},
        "Mounts": [],
    }
    image = {"Id": info["Image"], "RepoDigests": [IMAGE]}
    if change:
        change(info)
    if image_change:
        image_change(image)
    calls = []

    def bounded(command, material, timeout, limit):
        assert command[:3] == ["docker", "--host", "unix:///var/run/docker.sock"]
        command = ["docker", *command[3:]]
        calls.append((command, material))
        assert timeout == 35
        if command[1] == "inspect":
            return 0, json.dumps([info]).encode()
        if command[1:3] == ["image", "inspect"]:
            return 0, json.dumps([image]).encode()
        if command[1] == "diff":
            return 0, diff
        assert command[:7] == [
            "docker",
            "exec",
            "-i",
            CONTAINER,
            "/usr/bin/env",
            "-i",
            "/opt/venv/bin/python",
        ]
        if command[-1] == proof.SOURCE_DRIVER:
            assert material == b""
            return 0, json.dumps({"stage": "runtime", "status": source_status}).encode()
        assert material == DSN.encode()
        return 0, child or b'{"stage":"database","status":"verified"}'

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "remote",
            phase,
            IMAGE,
            CONTAINER if phase == "probe" else "",
            REQUEST.expected_database,
            REQUEST.expected_authentication,
        ],
    )
    monkeypatch.setattr(
        sys,
        "stdin",
        type("Input", (), {"buffer": __import__("io").BytesIO(DSN.encode())})(),
    )
    exec(  # noqa: S102 -- execute only the checked-in driver with offline fakes
        proof.REMOTE_DRIVER,
        {
            "bounded_run": bounded,
            "database_driver": proof.DATABASE_DRIVER,
            "source_driver": proof.SOURCE_DRIVER,
        },
    )
    return json.loads(capsys.readouterr().out), calls


@pytest.mark.parametrize(
    "change,image_change,diff",
    [
        (lambda x: x["State"].update(Running=False), None, b""),
        (lambda x: x.update(Id="d" * 64), None, b""),
        (lambda x: x["Config"].update(Image="latest"), None, b""),
        (lambda x: x["Config"].update(WorkingDir="/untrusted"), None, b""),
        (lambda x: x["Config"].update(ProcessOverride=True), None, b""),
        (lambda x: x.update(Mounts=[{"Destination": "/app/app"}]), None, b""),
        (lambda x: x.update(Mounts=[{"Destination": "/"}]), None, b""),
        (lambda x: x.update(Mounts=[{"Destination": "/opt/venv"}]), None, b""),
        (None, lambda x: x.update(RepoDigests=[]), b""),
        (None, lambda x: x.update(Id="wrong"), b""),
        (None, None, b"C /app/scripts/bootstrap_database_roles.py\n"),
    ],
)
def test_runtime_refusal_before_docker_exec(
    monkeypatch, capsys, change, image_change, diff
):
    result, calls = runtime_fake(
        monkeypatch,
        capsys,
        change=change,
        image_change=image_change,
        diff=diff,
        phase="probe",
    )
    assert result == {"stage": "runtime", "status": "refused"}
    assert all(command[1] != "exec" for command, _ in calls)


def test_runtime_probe_only_executes_exact_container(monkeypatch, capsys):
    result, calls = runtime_fake(monkeypatch, capsys, phase="probe")
    assert result == {"stage": "database", "status": "verified"}
    assert [command[1] for command, _ in calls] == [
        "inspect",
        "image",
        "diff",
        "exec",
        "exec",
    ]


@pytest.mark.parametrize(
    "child",
    [b"secret\n{}", b'{"stage":"database","status":"verified","extra":"secret"}'],
)
def test_remote_never_forwards_noisy_child(monkeypatch, capsys, child):
    result, _ = runtime_fake(monkeypatch, capsys, phase="probe", child=child)
    assert result == {"stage": "runtime", "status": "refused"}


# Execute only the isolated Python driver against fake modules; no database or
# Docker command is used. Assertions after the driver prove rollback order even
# though its stdout/stderr file descriptors intentionally discard diagnostics.
@pytest.mark.parametrize(
    "verdict,fail,cleanup,expected",
    [
        (0, False, "", "verified"),
        (1, False, "", "refused"),
        (False, False, "", "refused"),
        (0, True, "", "refused"),
        (0, False, "rollback", "refused"),
        (0, False, "close", "refused"),
        (0, False, "hang_rollback", None),
        (0, False, "hang_close", None),
    ],
)
def test_database_driver_readonly_and_rollback(verdict, fail, cleanup, expected):
    harness = r"""
import io, sys, types
from pathlib import PurePosixPath
calls = []
class FakePath(PurePosixPath):
    def is_symlink(self): return False
    def exists(self): return True
    def resolve(self): return self
sys.modules["pathlib"] = types.SimpleNamespace(Path=FakePath)
class Connection:
    read_only = False
    def rollback(self):
        calls.append("rollback")
        if CLEANUP == "rollback": raise RuntimeError("sentinel-secret")
        if CLEANUP == "hang_rollback": __import__("signal").pause()
    def close(self):
        calls.append("close")
        if CLEANUP == "close": raise RuntimeError("sentinel-secret")
        if CLEANUP == "hang_close": __import__("signal").pause()
conn = Connection()
def connect(dsn, **kwargs):
    import os
    assert not any(key.startswith("PG") for key in os.environ)
    assert set(os.environ) == {"MIGRATION_EXPECTED_DATABASE", "MIGRATION_EXPECTED_AUTHENTICATION"}
    assert dsn.startswith("postgresql://")
    assert kwargs == {"autocommit":False,"connect_timeout":5,"options":"-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=1000 -c idle_in_transaction_session_timeout=5000"}
    calls.append("connect")
    return conn
sys.modules["psycopg"] = types.SimpleNamespace(connect=connect)
sys.modules["psycopg.conninfo"] = types.SimpleNamespace(conninfo_to_dict=lambda dsn: {"user":"app_admin", "dbname":"dotmac_erp"})
def verify(c):
    assert c is conn and c.read_only is True
    calls.append("verify")
    print("sentinel-secret")
    print("sentinel-secret", file=sys.stderr)
    if FAIL: raise RuntimeError("sentinel-secret")
    return VERDICT
owner = types.SimpleNamespace(__file__="/app/scripts/bootstrap_database_roles.py",verify_migration_connection=verify,EXPECTED_DATABASE_VAR="MIGRATION_EXPECTED_DATABASE",EXPECTED_AUTHENTICATION_VAR="MIGRATION_EXPECTED_AUTHENTICATION")
sys.modules["scripts"] = types.SimpleNamespace(bootstrap_database_roles=owner)
sys.argv = ["driver", "dotmac_erp", "scram-sha-256:app_admin"]
import os
os.environ["PGHOSTADDR"] = "wrong"
os.environ["PGOPTIONS"] = "-c role=postgres"
os.environ["MIGRATION_DATABASE_URL"] = "sentinel-secret"
os.environ["CUSTOM_RUNTIME_CREDENTIAL"] = "sentinel-secret"
sys.stdin = types.SimpleNamespace(buffer=io.BytesIO(b"postgresql+psycopg://app_admin:sentinel-secret@db/dotmac_erp"))
if CLEANUP.startswith("hang_"):
    import signal
    actual_alarm = signal.alarm
    def short_test_deadline(seconds):
        assert seconds == 25
        return actual_alarm(1)
    # Shorten only duration; execute the unchanged driver's real SIG_DFL policy.
    signal.alarm = short_test_deadline
exec(DRIVER)
assert calls == ["connect", "verify", "rollback", "close"]
"""
    source = (
        f"DRIVER={proof.DATABASE_DRIVER!r}\nVERDICT={verdict!r}\nFAIL={fail!r}\nCLEANUP={cleanup!r}\n"
        + harness
    )
    completed = subprocess.run(  # noqa: S603 -- isolated checked-in driver and fake modules
        [sys.executable, "-I", "-B", "-c", source],
        capture_output=True,
        timeout=10,
        check=False,
    )
    if expected is None:
        assert completed.returncode == -signal.SIGALRM
        assert completed.stdout == b""
        assert completed.stderr == b""
        return
    assert completed.returncode == 0
    assert json.loads(completed.stdout) == {"stage": "database", "status": expected}
    assert completed.stderr == b""


def test_cli_unknown_arguments_names_only(capsys):
    assert proof.main(["--sentinel-secret"]) == 2
    assert json.loads(capsys.readouterr().out) == {
        "stage": "request",
        "status": "refused",
    }


@pytest.mark.parametrize(
    "diff,expected",
    [
        (b"C /app\nC /app/static\nA /app/static/avatars/user.png\n", "verified"),
        (
            b"A /app/static/branding/logo.png\nC /app/uploads\nA /app/uploads/resumes/cv.pdf\n",
            "verified",
        ),
        (b"C /app/app/migration_authority.py\n", "refused"),
        (b"C /opt/venv/lib/python3.12/site-packages/psycopg/__init__.py\n", "refused"),
        (b"A /app/scripts.py\n", "refused"),
    ],
)
def test_data_changes_do_not_hide_source_drift(monkeypatch, capsys, diff, expected):
    result, _ = runtime_fake(monkeypatch, capsys, phase="probe", diff=diff)
    assert result["status"] == expected


def test_missing_source_guard_prevents_material_probe(monkeypatch, capsys):
    result, calls = runtime_fake(
        monkeypatch, capsys, phase="probe", source_status="refused"
    )
    assert result == {"stage": "runtime", "status": "refused"}
    assert all(not material for _, material in calls)


def test_failed_preinspection_never_loads_credentials(monkeypatch):
    def ssh(*args):
        raise ValueError("source unavailable")

    def fetch(**kwargs):
        pytest.fail("material fetched after inspection refusal")

    monkeypatch.setattr(proof, "_ssh", ssh)
    monkeypatch.setattr(proof.credentials, "fetch_migration_database_url", fetch)
    assert proof.verify(REQUEST) == proof.VerificationResult(
        proof.Stage.RUNTIME, proof.Status.REFUSED
    )


@pytest.mark.parametrize("mode", ["symlink", "missing", "wrong_origin", "not_callable"])
def test_installed_source_guard_refuses_unknown_authority(mode):
    harness = r"""
import sys, types
from pathlib import PurePosixPath
class FakePath(PurePosixPath):
    def is_symlink(self): return MODE == "symlink"
    def exists(self): return MODE != "missing"
    def resolve(self): return self
sys.modules["pathlib"] = types.SimpleNamespace(Path=FakePath)
owner = types.SimpleNamespace(__file__="/wrong/owner.py" if MODE == "wrong_origin" else "/app/scripts/bootstrap_database_roles.py", verify_migration_connection=None if MODE == "not_callable" else lambda conn:0)
sys.modules["scripts"] = types.SimpleNamespace(bootstrap_database_roles=owner)
exec(DRIVER)
"""
    source = f"DRIVER={proof.SOURCE_DRIVER!r}\nMODE={mode!r}\n" + harness
    completed = subprocess.run(  # noqa: S603 -- checked-in driver, no host/database calls
        [sys.executable, "-I", "-B", "-c", source],
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert completed.returncode == 0
    assert json.loads(completed.stdout) == {"stage": "runtime", "status": "refused"}
    assert completed.stderr == b""


def test_child_environment_excludes_inherited_material(monkeypatch):
    import io

    monkeypatch.setenv("PGHOSTADDR", "wrong")
    monkeypatch.setenv("MIGRATION_DATABASE_URL", DSN)
    monkeypatch.setenv("OPENBAO_TOKEN", "sentinel-secret")
    monkeypatch.setenv("DOCKER_HOST", "tcp://wrong:2375")

    class Child:
        stdout = io.BytesIO(b"{}")
        stdin = io.BytesIO()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def wait(self, timeout):
            return 0

    def popen(command, **kwargs):
        assert set(kwargs["env"]) <= {"PATH", "LANG", "LC_ALL", "LC_CTYPE"}
        assert "sentinel-secret" not in repr(kwargs["env"])
        return Child()

    monkeypatch.setattr(proof.subprocess, "Popen", popen)
    assert proof._bounded_run(["fake"], b"", 1) == (0, b"{}")


def test_projection_guards_loader_overrides_without_collecting_values(
    monkeypatch, capsys
):
    result, calls = runtime_fake(monkeypatch, capsys)
    assert result["status"] == "verified"
    projection = calls[0][0][-2]
    # LD_* is guarded as a family, including LD_PRELOAD, LD_LIBRARY_PATH and LD_AUDIT.
    assert '(index (split $key "_") 0) "LD"' in projection
    for key in ("PYTHONPATH", "PYTHONHOME", "GCONV_PATH", "GLIBC_TUNABLES"):
        assert '"' + key + '"' in projection
    assert "{{json .Config.Env}}" not in projection
    assert '"ProcessOverride":{{$override}}' in projection
