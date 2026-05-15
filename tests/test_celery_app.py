import app.celery_app as celery_module


def test_configure_celery_runtime_logging_is_idempotent_per_process(
    monkeypatch,
) -> None:
    calls: list[str] = []

    monkeypatch.setattr(celery_module.logger, "info", lambda *args, **kwargs: None)
    monkeypatch.setattr(celery_module, "configure_logging", lambda: calls.append("log"))
    monkeypatch.setattr(celery_module.os, "getpid", lambda: 1001)
    monkeypatch.setattr(celery_module, "_logging_bootstrapped_pid", None)

    celery_module.configure_celery_runtime_logging()
    celery_module.configure_celery_runtime_logging()

    assert calls == ["log"]
    assert celery_module._logging_bootstrapped_pid == 1001


def test_bootstrap_celery_observability_is_idempotent_per_process(
    monkeypatch,
) -> None:
    calls: list[str] = []

    monkeypatch.setattr(celery_module.logger, "info", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        celery_module,
        "configure_celery_runtime_logging",
        lambda: calls.append("logging"),
    )
    monkeypatch.setattr(
        celery_module, "setup_monitoring", lambda: calls.append("monitoring")
    )
    monkeypatch.setattr(celery_module, "setup_otel", lambda: calls.append("otel"))
    monkeypatch.setattr(
        celery_module,
        "register_audit_listeners",
        lambda: calls.append("audit"),
    )
    monkeypatch.setattr(celery_module.os, "getpid", lambda: 2002)
    monkeypatch.setattr(celery_module, "_runtime_bootstrapped_pid", None)

    celery_module.bootstrap_celery_observability()
    celery_module.bootstrap_celery_observability()

    assert calls == ["logging", "monitoring", "otel", "audit"]
    assert celery_module._runtime_bootstrapped_pid == 2002


def test_bootstrap_celery_observability_reinitializes_after_fork(
    monkeypatch,
) -> None:
    calls: list[str] = []
    pids = iter((3003, 4004))

    monkeypatch.setattr(celery_module.logger, "info", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        celery_module,
        "configure_celery_runtime_logging",
        lambda: calls.append("logging"),
    )
    monkeypatch.setattr(
        celery_module, "setup_monitoring", lambda: calls.append("monitoring")
    )
    monkeypatch.setattr(celery_module, "setup_otel", lambda: calls.append("otel"))
    monkeypatch.setattr(
        celery_module,
        "register_audit_listeners",
        lambda: calls.append("audit"),
    )
    monkeypatch.setattr(celery_module.os, "getpid", lambda: next(pids))
    monkeypatch.setattr(celery_module, "_runtime_bootstrapped_pid", None)

    celery_module.bootstrap_celery_observability()
    celery_module.bootstrap_celery_observability()

    assert calls == [
        "logging",
        "monitoring",
        "otel",
        "audit",
        "logging",
        "monitoring",
        "otel",
        "audit",
    ]
    assert celery_module._runtime_bootstrapped_pid == 4004


def test_task_metrics_record_duration_and_status(monkeypatch) -> None:
    observed: list[tuple[str, str, float]] = []
    times = iter((10.0, 12.5))

    class _Task:
        name = "app.tasks.example.sync"

    monkeypatch.setattr(celery_module.os, "getpid", lambda: 7007)
    monkeypatch.setattr(celery_module.time, "perf_counter", lambda: next(times))
    monkeypatch.setattr(
        celery_module,
        "observe_job",
        lambda task, status, duration: observed.append((task, status, duration)),
    )
    monkeypatch.setattr(celery_module, "_task_start_times", {})

    task = _Task()
    celery_module._record_task_start(task_id="job-1", task=task)
    celery_module._record_task_metrics(task_id="job-1", task=task, state="SUCCESS")

    assert observed == [("app.tasks.example.sync", "success", 2.5)]


def test_task_metrics_ignore_postrun_without_matching_start(monkeypatch) -> None:
    observed: list[tuple[str, str, float]] = []

    class _Task:
        name = "app.tasks.example.sync"

    monkeypatch.setattr(celery_module.os, "getpid", lambda: 8008)
    monkeypatch.setattr(
        celery_module,
        "observe_job",
        lambda task, status, duration: observed.append((task, status, duration)),
    )
    monkeypatch.setattr(celery_module, "_task_start_times", {})

    celery_module._record_task_metrics(
        task_id="missing",
        task=_Task(),
        state="FAILURE",
    )

    assert observed == []
