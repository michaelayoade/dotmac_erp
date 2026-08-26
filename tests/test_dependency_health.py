import app.dependency_health as dependency_health_module


class _DummySession:
    def close(self) -> None:
        pass


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
