from __future__ import annotations

import os
from pathlib import Path

import pytest

from app import celery_worker_entrypoint, metrics
from app.prometheus_multiprocess import (
    PROMETHEUS_MULTIPROC_ENV,
    PROMETHEUS_MULTIPROC_PATH,
    clear_metric_files,
    prepare_configured_multiprocess_directory,
)


def test_clear_metric_files_removes_only_prometheus_mmaps(tmp_path: Path) -> None:
    counter = tmp_path / "counter_10.db"
    gauge = tmp_path / "gauge_livemax_11.db"
    unrelated = tmp_path / "keep.txt"
    counter.write_bytes(b"counter")
    gauge.write_bytes(b"gauge")
    unrelated.write_text("keep", encoding="utf-8")

    assert clear_metric_files(tmp_path) == 2
    assert not counter.exists()
    assert not gauge.exists()
    assert unrelated.read_text(encoding="utf-8") == "keep"


def test_prepare_refuses_an_unreviewed_cleanup_target(monkeypatch) -> None:
    unreviewed = PROMETHEUS_MULTIPROC_PATH.with_name("not-the-reviewed-directory")
    monkeypatch.setenv(PROMETHEUS_MULTIPROC_ENV, str(unreviewed))

    with pytest.raises(RuntimeError, match="must be"):
        prepare_configured_multiprocess_directory()


def test_render_metrics_uses_fresh_multiprocess_registry(monkeypatch) -> None:
    sentinel = object()
    generated: list[object] = []
    monkeypatch.setenv(PROMETHEUS_MULTIPROC_ENV, str(PROMETHEUS_MULTIPROC_PATH))
    monkeypatch.setattr(metrics, "_export_registry", lambda: sentinel)
    monkeypatch.setattr(
        metrics,
        "generate_latest",
        lambda registry: generated.append(registry) or b"metrics",
    )

    assert metrics.render_metrics() == b"metrics"
    assert generated == [sentinel]


def test_worker_entrypoint_prepares_metrics_before_exec(monkeypatch) -> None:
    calls: list[object] = []
    monkeypatch.setattr(
        celery_worker_entrypoint,
        "prepare_configured_multiprocess_directory",
        lambda: calls.append("prepare"),
    )
    monkeypatch.setattr(
        os,
        "execvp",
        lambda executable, args: calls.append((executable, args)),
    )

    celery_worker_entrypoint.main()

    assert calls == [
        "prepare",
        (
            "celery",
            ("celery", "-A", "app.celery_app", "worker", "-l", "info"),
        ),
    ]
