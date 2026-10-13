"""ERP candidate policy and resolver redaction; HTTP fixtures stay in-process."""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import sys
from pathlib import Path

import httpx
import pytest

from app.services import secrets
from app.services.boot_secret_validation import validate_secret_value
from app.services.deployment_secret_readiness import (
    BootCheckName,
    BootMaterialCheck,
    BootMaterialResult,
    MaterialStatus,
    inspect_boot_material,
    serialize_candidate_result,
)

ROOT = Path(__file__).resolve().parents[1]
VALID_KEY = base64.urlsafe_b64encode(bytes(range(32))).decode()


@pytest.fixture
def boot_env(monkeypatch):
    for name in tuple(os.environ):
        if name.startswith(("OPENBAO_", "VAULT_")):
            monkeypatch.delenv(name)
    monkeypatch.setenv("DATABASE_URL", "not-a-database-connection")
    monkeypatch.setenv("JWT_SECRET", VALID_KEY)
    monkeypatch.setenv("TOTP_ENCRYPTION_KEY", VALID_KEY)


def _statuses(result):
    return {check.name: check.status for check in result.checks}


def _assert_wire(raw):
    assert len(raw.encode("utf-8")) <= 16384
    wire = json.loads(raw)
    assert set(wire) == {"schema", "status", "checks"}
    assert wire["schema"] == "CandidateMaterialProbeResult.v1"
    assert 1 <= len(wire["checks"]) <= 64
    names = []
    for check in wire["checks"]:
        assert set(check) == {"name", "status"}
        assert re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,95}", check["name"])
        assert check["status"] in ("ready", "refused", "unavailable")
        names.append(check["name"])
    assert len(names) == len(set(names))
    states = [check["status"] for check in wire["checks"]]
    assert wire["status"] == (
        "refused"
        if "refused" in states
        else "unavailable"
        if "unavailable" in states
        else "ready"
    )
    return wire


def test_local_boot_values_skip_even_configured_unreachable_store(
    boot_env, monkeypatch
):
    monkeypatch.setenv("OPENBAO_ADDR", "https://unused.invalid")
    monkeypatch.setenv("OPTIONAL_CONNECTOR_SECRET", "bao://optional/unreachable")
    monkeypatch.setattr(
        httpx, "get", lambda *a, **kw: pytest.fail("unexpected network")
    )
    result = inspect_boot_material()
    assert result.status == MaterialStatus.READY
    _assert_wire(serialize_candidate_result(result))


@pytest.mark.parametrize("name", ["JWT_SECRET", "TOTP_ENCRYPTION_KEY", "DATABASE_URL"])
def test_missing_required_material_refuses(boot_env, monkeypatch, name):
    monkeypatch.delenv(name)
    assert inspect_boot_material().status == MaterialStatus.REFUSED


@pytest.mark.parametrize(
    "name,value",
    [
        ("JWT_SECRET", "changeme"),
        ("JWT_SECRET", "short"),
        ("TOTP_ENCRYPTION_KEY", "placeholder"),
        ("TOTP_ENCRYPTION_KEY", "invalid!"),
        ("TOTP_ENCRYPTION_KEY", base64.urlsafe_b64encode(b"tiny").decode()),
    ],
)
def test_invalid_format_matches_shared_startup_policy(
    boot_env, monkeypatch, name, value
):
    assert validate_secret_value(name, value)
    monkeypatch.setenv(name, value)
    assert inspect_boot_material().status == MaterialStatus.REFUSED


@pytest.mark.parametrize("scheme", ["bao", "vault", "openbao"])
def test_required_reference_uses_existing_resolver_without_database(
    boot_env, monkeypatch, scheme
):
    monkeypatch.setenv("JWT_SECRET", f"{scheme}://secret/path#jwt")
    monkeypatch.setenv("OPENBAO_ADDR", "https://store.invalid")
    monkeypatch.setenv("OPENBAO_TOKEN", "test-only-not-a-real-token")
    calls = []

    def resolve(reference, db=None):
        calls.append(db)
        return VALID_KEY

    monkeypatch.setattr(secrets, "resolve_openbao_ref", resolve)
    assert inspect_boot_material().status == MaterialStatus.READY
    assert calls == [None]


@pytest.mark.parametrize("missing", ["OPENBAO_ADDR", "OPENBAO_TOKEN"])
def test_required_reference_needs_openbao_configuration(boot_env, monkeypatch, missing):
    monkeypatch.setenv("JWT_SECRET", "bao://secret/path#jwt")
    monkeypatch.setenv("OPENBAO_ADDR", "https://store.invalid")
    monkeypatch.setenv("OPENBAO_TOKEN", "test-only-not-a-real-token")
    monkeypatch.delenv(missing)
    assert (
        _statuses(inspect_boot_material())[BootCheckName.OPENBAO]
        == MaterialStatus.REFUSED
    )


@pytest.mark.parametrize(
    "code,expected",
    [
        (200, MaterialStatus.READY),
        (401, MaterialStatus.REFUSED),
        (403, MaterialStatus.REFUSED),
        (404, MaterialStatus.REFUSED),
        (500, MaterialStatus.UNAVAILABLE),
    ],
)
def test_actual_resolver_http_outcomes(boot_env, monkeypatch, code, expected):
    monkeypatch.setenv("JWT_SECRET", "bao://secret/path#jwt")
    monkeypatch.setenv("OPENBAO_ADDR", "https://store.invalid")
    monkeypatch.setenv("OPENBAO_TOKEN", "test-only-not-a-real-token")

    def get(url, **kwargs):
        return httpx.Response(
            code,
            request=httpx.Request("GET", url),
            json={"data": {"data": {"jwt": VALID_KEY}}},
        )

    monkeypatch.setattr(httpx, "get", get)
    assert _statuses(inspect_boot_material())[BootCheckName.JWT] == expected


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"data": {"data": {}}}, MaterialStatus.REFUSED),
        ({"data": {"data": {"jwt": "short"}}}, MaterialStatus.REFUSED),
        ({"data": {"data": {"jwt": ""}}}, MaterialStatus.REFUSED),
        ([], MaterialStatus.UNAVAILABLE),
    ],
)
def test_resolved_value_must_satisfy_required_policy(
    boot_env, monkeypatch, payload, expected
):
    monkeypatch.setenv("JWT_SECRET", "bao://secret/path#jwt")
    monkeypatch.setenv("OPENBAO_ADDR", "https://store.invalid")
    monkeypatch.setenv("OPENBAO_TOKEN", "test-only-not-a-real-token")

    def get(url, **kwargs):
        return httpx.Response(200, request=httpx.Request("GET", url), json=payload)

    monkeypatch.setattr(httpx, "get", get)
    assert _statuses(inspect_boot_material())[BootCheckName.JWT] == expected


def test_malformed_required_reference_refuses_without_network(boot_env, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "bao://missing-path")
    monkeypatch.setenv("OPENBAO_ADDR", "https://store.invalid")
    monkeypatch.setenv("OPENBAO_TOKEN", "test-only-not-a-real-token")
    monkeypatch.setattr(
        httpx, "get", lambda *a, **kw: pytest.fail("unexpected network")
    )
    assert inspect_boot_material().status == MaterialStatus.REFUSED


@pytest.mark.parametrize(
    "name,value",
    [
        ("JWT_SECRET", VALID_KEY),
        ("JWT_SECRET", "short"),
        ("TOTP_ENCRYPTION_KEY", VALID_KEY),
        ("TOTP_ENCRYPTION_KEY", "bad!"),
    ],
)
def test_existing_startup_uses_identical_extracted_value_policy(
    boot_env, monkeypatch, name, value
):
    from app import startup

    monkeypatch.setenv(name, value)
    assert startup.validate_required_secrets() == validate_secret_value(name, value)


def test_unreachable_store_and_secret_canary_never_escape(
    boot_env, monkeypatch, capsys, caplog
):
    monkeypatch.setenv("JWT_SECRET", "bao://secret/private-reference-canary#jwt")
    monkeypatch.setenv("OPENBAO_ADDR", "https://store.invalid")
    monkeypatch.setenv("OPENBAO_TOKEN", "test-only-not-a-real-token")
    previous = logging.root.manager.disable

    def get(*args, **kwargs):
        print("secret-canary-output")  # noqa: T201 - deliberate leakage canary
        print("secret-canary-error", file=sys.stderr)  # noqa: T201
        logging.critical("secret-canary-log")
        raise httpx.ConnectError("secret-canary-transport")

    monkeypatch.setattr(httpx, "get", get)
    result = inspect_boot_material()
    assert result.status == MaterialStatus.UNAVAILABLE
    raw = serialize_candidate_result(result)
    _assert_wire(raw)
    assert "canary" not in raw
    assert capsys.readouterr() == ("", "")
    assert not caplog.records
    assert logging.root.manager.disable == previous


@pytest.mark.parametrize(
    "states,expected",
    [
        ((MaterialStatus.READY,), MaterialStatus.READY),
        (
            (MaterialStatus.READY, MaterialStatus.UNAVAILABLE),
            MaterialStatus.UNAVAILABLE,
        ),
        ((MaterialStatus.UNAVAILABLE, MaterialStatus.REFUSED), MaterialStatus.REFUSED),
    ],
)
def test_wire_status_is_derived(states, expected):
    result = BootMaterialResult(
        tuple(
            BootMaterialCheck(name, status)
            for name, status in zip(BootCheckName, states, strict=False)
        )
    )
    assert result.status == expected
    _assert_wire(serialize_candidate_result(result))


def test_domain_refuses_empty_duplicate_or_untyped_checks():
    check = BootMaterialCheck(BootCheckName.JWT, MaterialStatus.READY)
    with pytest.raises(ValueError):
        BootMaterialResult(())
    with pytest.raises(ValueError):
        BootMaterialResult((check, check))
    with pytest.raises(ValueError):
        BootMaterialResult((BootMaterialCheck("raw-secret", "ready"),))


def test_both_runtime_images_ship_named_probe():
    directive = "COPY scripts/verify_deployment_secrets.py ./scripts/verify_deployment_secrets.py"
    for name in ("Dockerfile", "Dockerfile.hardened"):
        runtime = (ROOT / name).read_text().rsplit("FROM python:3.12-slim", 1)[1]
        assert directive in runtime


def test_probe_covers_exact_startup_required_secret_roster():
    from app.services.boot_secret_validation import REQUIRED_SECRETS

    assert {name for name, _ in REQUIRED_SECRETS} == {
        "JWT_SECRET",
        "TOTP_ENCRYPTION_KEY",
    }
