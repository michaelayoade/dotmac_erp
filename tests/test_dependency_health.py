import httpx
import pytest

import app.dependency_health as dependency_health_module
from app.models.domain_settings import SettingDomain


class _DummySession:
    def close(self) -> None:
        pass


def test_global_setting_read_uses_owner_with_explicit_global_scope(monkeypatch) -> None:
    observed: dict[str, object] = {}

    class _Setting:
        value_json = None
        value_text = "smtp.example.com"

    class _SettingsOwner:
        def get_by_key(self, db, key, *, organization_id, inherit):
            observed.update(
                db=db,
                key=key,
                organization_id=organization_id,
                inherit=inherit,
            )
            return _Setting()

    db = _DummySession()
    monkeypatch.setitem(
        dependency_health_module.DOMAIN_SETTINGS_SERVICE,
        SettingDomain.email,
        _SettingsOwner(),
    )

    value = dependency_health_module._global_stored_setting(
        db, SettingDomain.email, "smtp_host"
    )

    assert value == "smtp.example.com"
    assert observed == {
        "db": db,
        "key": "smtp_host",
        "organization_id": None,
        "inherit": True,
    }


def test_tenant_configured_dependency_checks_use_explicit_global_scope(
    monkeypatch,
) -> None:
    smtp_scopes: list[object] = []
    nextcloud_scopes: list[tuple[str, object]] = []
    resolved_scopes: list[object] = []

    monkeypatch.setattr(
        dependency_health_module,
        "_setting_configured",
        lambda db, domain, key: True,
    )

    def _smtp_config(db, *, organization_id):
        smtp_scopes.append(organization_id)
        return {}

    monkeypatch.setattr(dependency_health_module, "_get_smtp_config", _smtp_config)
    monkeypatch.setattr(
        dependency_health_module,
        "validate_smtp_config",
        lambda config, timeout_seconds: (True, None),
    )

    def _resolve_value(db, domain, key, *, organization_id):
        resolved_scopes.append(organization_id)
        return False

    monkeypatch.setattr(dependency_health_module, "resolve_value", _resolve_value)

    def _nextcloud_configured(db, *, organization_id):
        nextcloud_scopes.append(("configured", organization_id))
        return True

    monkeypatch.setattr(
        dependency_health_module, "is_configured", _nextcloud_configured
    )

    class _NextcloudConfig:
        def __init__(self, *, server_url, username, password, timeout):
            self.server_url = server_url
            self.username = username
            self.password = password
            self.timeout = timeout

        @classmethod
        def from_db(cls, db, *, organization_id):
            nextcloud_scopes.append(("config", organization_id))
            return cls(
                server_url="https://cloud.example.com",
                username="bot",
                password="secret",
                timeout=30.0,
            )

    class _NextcloudClient:
        def __init__(self, config):
            pass

        def _request(self, method, path):
            return {}

    monkeypatch.setattr(dependency_health_module, "NextcloudConfig", _NextcloudConfig)
    monkeypatch.setattr(
        dependency_health_module, "NextcloudTalkClient", _NextcloudClient
    )

    db = _DummySession()
    assert dependency_health_module._check_smtp(db)["healthy"] is True
    assert dependency_health_module._check_paystack(db)["configured"] is False
    assert dependency_health_module._check_nextcloud(db)["healthy"] is True

    assert smtp_scopes == [None]
    assert resolved_scopes == [None, None, None]
    assert nextcloud_scopes == [("configured", None), ("config", None)]


def test_collect_dependency_health_marks_required_dependencies(monkeypatch) -> None:
    monkeypatch.delenv("READINESS_CHECK_ALL_CONFIGURED_DEPENDENCIES", raising=False)
    monkeypatch.setenv("READINESS_REQUIRED_DEPENDENCIES", "storage,paystack")
    monkeypatch.setattr(
        dependency_health_module,
        "SessionLocal",
        lambda: _DummySession(),
    )
    monkeypatch.setattr(
        dependency_health_module,
        "_check_smtp",
        lambda db: {
            "configured": True,
            "healthy": True,
            "status": "healthy",
            "message": "ok",
        },
    )
    monkeypatch.setattr(
        dependency_health_module,
        "_check_storage",
        lambda: {
            "configured": True,
            "healthy": True,
            "status": "healthy",
            "message": "ok",
        },
    )
    monkeypatch.setattr(
        dependency_health_module,
        "_check_openbao",
        lambda db: {
            "configured": True,
            "healthy": True,
            "status": "healthy",
            "message": "ok",
        },
    )
    monkeypatch.setattr(
        dependency_health_module,
        "_check_paystack",
        lambda db: {
            "configured": True,
            "healthy": True,
            "status": "healthy",
            "message": "ok",
        },
    )
    monkeypatch.setattr(
        dependency_health_module,
        "_check_nextcloud",
        lambda db: {
            "configured": False,
            "healthy": False,
            "status": "not_configured",
            "message": "missing",
        },
    )
    monkeypatch.setattr(
        dependency_health_module,
        "_check_dotmac_sub",
        lambda: {
            "configured": True,
            "healthy": True,
            "status": "healthy",
            "message": "ok",
        },
    )
    monkeypatch.setattr(
        dependency_health_module,
        "_check_remita",
        lambda: {
            "configured": True,
            "healthy": True,
            "status": "healthy",
            "message": "ok",
        },
    )

    checks = dependency_health_module.collect_dependency_health()

    assert checks["storage"]["required"] is True
    assert checks["openbao"]["required"] is True
    assert checks["paystack"]["required"] is True
    assert checks["nextcloud"]["required"] is False
    assert checks["dotmac_sub"]["required"] is False


def test_readiness_failures_only_include_required_unhealthy_dependencies() -> None:
    failures = dependency_health_module.readiness_failures(
        {
            "storage": {
                "configured": True,
                "healthy": False,
                "required": True,
            },
            "nextcloud": {
                "configured": True,
                "healthy": False,
                "required": False,
            },
            "smtp": {
                "configured": True,
                "healthy": True,
                "required": True,
            },
        }
    )

    assert list(failures) == ["storage"]


def test_dotmac_sub_is_never_a_readiness_dependency(monkeypatch) -> None:
    monkeypatch.setenv("READINESS_CHECK_ALL_CONFIGURED_DEPENDENCIES", "true")
    monkeypatch.setenv("READINESS_REQUIRED_DEPENDENCIES", "dotmac_sub")

    assert dependency_health_module._dependency_required("dotmac_sub", True) is False
    assert dependency_health_module._dependency_required("paystack", True) is True


def test_dotmac_sub_dependency_probe_is_cached(monkeypatch) -> None:
    calls: list[str] = []

    class _Config:
        timeout = 60.0
        max_retries = 3

        def is_configured(self) -> bool:
            return True

    config = _Config()

    monkeypatch.setattr(
        dependency_health_module.DotmacSubConfig,
        "from_settings",
        lambda: config,
    )
    monkeypatch.setattr(
        dependency_health_module,
        "_probe_dotmac_sub",
        lambda _config: (
            calls.append("probe")
            or {
                "configured": True,
                "healthy": True,
                "status": "healthy",
                "message": "ok",
            }
        ),
    )
    monkeypatch.setattr(dependency_health_module.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(dependency_health_module, "_DOTMAC_SUB_HEALTH_CACHE", None)

    first = dependency_health_module._check_dotmac_sub()
    second = dependency_health_module._check_dotmac_sub()

    assert calls == ["probe"]
    assert first["cached"] is False
    assert second["cached"] is True


@pytest.fixture
def openbao_probe(monkeypatch):
    calls: list[tuple[str, dict[str, object]]] = []
    outcomes: list[object] = []
    monkeypatch.setenv("OPENBAO_ADDR", "https://bao.example")
    monkeypatch.setattr(
        dependency_health_module,
        "_openbao_config",
        lambda db: ("https://bao.example", "test-token", "staging/erp", "2"),
    )
    monkeypatch.setattr(
        dependency_health_module, "_openbao_allow_insecure", lambda db: False
    )

    def _get(url, **kwargs):
        calls.append((url, kwargs))
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(dependency_health_module.httpx, "get", _get)
    return calls, outcomes


def _token_response():
    return httpx.Response(
        200,
        json={"data": {"id": "test-token", "accessor": "test-accessor", "ttl": 300}},
    )


@pytest.mark.parametrize("health_status", [200, 429, 472, 473])
def test_openbao_authenticates_root_token_before_declared_namespace(
    openbao_probe, health_status
) -> None:
    calls, outcomes = openbao_probe
    outcomes.extend([httpx.Response(health_status), _token_response()])

    result = dependency_health_module._check_openbao(_DummySession())

    assert result["healthy"] is True
    assert calls == [
        ("https://bao.example/v1/sys/health", {"timeout": 5.0, "verify": True}),
        (
            "https://bao.example/v1/auth/token/lookup-self",
            {
                "headers": {"X-Vault-Token": "test-token"},
                "timeout": 5.0,
                "verify": True,
            },
        ),
    ]
    assert "test-token" not in str(result)
    assert "test-accessor" not in str(result)


@pytest.mark.parametrize("root_status", [400, 403, 404])
def test_openbao_child_token_retries_only_declared_namespace(
    openbao_probe, root_status
) -> None:
    calls, outcomes = openbao_probe
    outcomes.extend(
        [httpx.Response(200), httpx.Response(root_status), _token_response()]
    )

    result = dependency_health_module._check_openbao(_DummySession())

    assert result["healthy"] is True
    assert len(calls) == 3
    assert calls[-1] == (
        "https://bao.example/v1/auth/token/lookup-self",
        {
            "headers": {
                "X-Vault-Token": "test-token",
                "X-Vault-Namespace": "staging/erp",
            },
            "timeout": 5.0,
            "verify": True,
        },
    )


def test_openbao_rejected_token_fails_readiness_and_redacts_body(openbao_probe) -> None:
    calls, outcomes = openbao_probe
    outcomes.extend(
        [
            httpx.Response(200),
            httpx.Response(403),
            httpx.Response(403, text="test-token"),
        ]
    )

    result = dependency_health_module._check_openbao(_DummySession())
    result["required"] = dependency_health_module._dependency_required("openbao", True)

    assert result["healthy"] is False
    assert result["message"] == "OpenBao token validation returned HTTP 403"
    assert dependency_health_module.readiness_failures({"openbao": result}) == {
        "openbao": result
    }
    assert len(calls) == 3
    assert "test-token" not in str(result)


@pytest.mark.parametrize("namespace", [None, "", "/"])
def test_openbao_root_context_without_child_has_no_retry(
    openbao_probe, monkeypatch, namespace
) -> None:
    calls, outcomes = openbao_probe
    monkeypatch.setattr(
        dependency_health_module,
        "_openbao_config",
        lambda db: ("https://bao.example", "test-token", namespace, "2"),
    )
    outcomes.extend([httpx.Response(200), httpx.Response(403)])

    assert dependency_health_module._check_openbao(_DummySession())["healthy"] is False
    assert len(calls) == 2
    assert "X-Vault-Namespace" not in calls[-1][1]["headers"]


@pytest.mark.parametrize(
    "body",
    [
        "not-json",
        "null",
        "[]",
        "{}",
        '{"data": {}}',
        '{"data": []}',
        '{"data": {"id": ""}}',
        '{"data": {"id": 123}}',
    ],
)
def test_openbao_malformed_token_response_fails_without_retry(
    openbao_probe, body
) -> None:
    calls, outcomes = openbao_probe
    outcomes.extend([httpx.Response(200), httpx.Response(200, text=body)])

    result = dependency_health_module._check_openbao(_DummySession())

    assert result["healthy"] is False
    assert result["message"] == "OpenBao token validation returned an invalid response"
    assert len(calls) == 2


@pytest.mark.parametrize("failure_stage", ["health", "root", "child"])
def test_openbao_transport_failure_is_redacted_and_not_retried(
    openbao_probe, failure_stage
) -> None:
    calls, outcomes = openbao_probe
    if failure_stage != "health":
        outcomes.append(httpx.Response(200))
    if failure_stage == "child":
        outcomes.append(httpx.Response(403))
    outcomes.append(httpx.ConnectError("test-token private-host internal-error"))

    result = dependency_health_module._check_openbao(_DummySession())

    assert result["healthy"] is False
    assert result["message"] in {
        "OpenBao health request failed",
        "OpenBao token validation request failed",
    }
    assert "test-token" not in str(result)
    assert "private-host" not in str(result)
    assert "internal-error" not in str(result)
    assert len(calls) == {"health": 1, "root": 2, "child": 3}[failure_stage]


@pytest.mark.parametrize("status", [301, 500, 503])
def test_openbao_token_server_failure_has_no_namespace_retry(
    openbao_probe, status
) -> None:
    calls, outcomes = openbao_probe
    outcomes.extend([httpx.Response(200), httpx.Response(status)])

    result = dependency_health_module._check_openbao(_DummySession())

    assert result["healthy"] is False
    assert result["message"] == f"OpenBao token validation returned HTTP {status}"
    assert len(calls) == 2


@pytest.mark.parametrize("status", [400, 403, 501, 503])
def test_openbao_unhealthy_service_does_not_send_token(openbao_probe, status) -> None:
    calls, outcomes = openbao_probe
    outcomes.append(httpx.Response(status))

    result = dependency_health_module._check_openbao(_DummySession())

    assert result["healthy"] is False
    assert result["message"] == f"OpenBao health returned HTTP {status}"
    assert len(calls) == 1
    assert "headers" not in calls[0][1]


def test_openbao_configuration_failure_is_redacted(openbao_probe, monkeypatch) -> None:
    calls, outcomes = openbao_probe

    def _bad_config(db):
        raise ValueError("test-token private-host internal-error")

    monkeypatch.setattr(dependency_health_module, "_openbao_config", _bad_config)

    result = dependency_health_module._check_openbao(_DummySession())

    assert result["healthy"] is False
    assert result["message"] == "OpenBao configuration validation failed"
    assert calls == []


def test_openbao_unconfigured_skips_requests(openbao_probe, monkeypatch) -> None:
    calls, outcomes = openbao_probe
    monkeypatch.delenv("OPENBAO_ADDR", raising=False)
    monkeypatch.delenv("VAULT_ADDR", raising=False)

    result = dependency_health_module._check_openbao(_DummySession())

    assert result["configured"] is False
    assert result["healthy"] is False
    assert calls == []
