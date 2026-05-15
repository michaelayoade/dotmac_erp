import os
import logging
import time

from celery import Celery
from celery.signals import (
    beat_init,
    setup_logging,
    task_postrun,
    task_prerun,
    worker_process_init,
)

from app.logging import configure_logging
from app.metrics import observe_job
from app.monitoring import setup_monitoring
from app.telemetry import setup_otel

from app.services.audit_listener import register_audit_listeners
from app.services.scheduler_config import build_beat_schedule, get_celery_config

logger = logging.getLogger(__name__)
_logging_bootstrapped_pid: int | None = None
_runtime_bootstrapped_pid: int | None = None
_task_start_times: dict[tuple[int, str], float] = {}


def configure_celery_runtime_logging() -> None:
    """Install the repo's logging config in the current Celery process."""
    global _logging_bootstrapped_pid  # noqa: PLW0603

    pid = os.getpid()
    if _logging_bootstrapped_pid == pid:
        return

    configure_logging()
    _logging_bootstrapped_pid = pid
    logger.info("Celery logging initialized")


def bootstrap_celery_observability() -> None:
    """Initialise monitoring/tracing in the current Celery runtime process."""
    global _runtime_bootstrapped_pid  # noqa: PLW0603

    pid = os.getpid()
    if _runtime_bootstrapped_pid == pid:
        return

    configure_celery_runtime_logging()
    setup_monitoring()
    setup_otel()
    # Register the ORM audit listener in this Celery process. Without this,
    # task-driven data changes (Mono sync, payroll runs, statement imports,
    # reminders) commit without producing audit_log rows because
    # ``event.listen`` only attaches in the process that called it — and
    # ``app/main.py`` is never imported by the worker or beat.
    register_audit_listeners()
    _runtime_bootstrapped_pid = pid
    logger.info("Celery observability initialized")


def _task_metric_key(task_id: str | None, task) -> tuple[int, str]:
    task_name = getattr(task, "name", None) or "unknown"
    return (os.getpid(), task_id or task_name)


def _normalize_task_state(state: str | None) -> str:
    normalized = (state or "unknown").strip().lower()
    if normalized in {"success", "failure", "retry", "revoked", "ignored"}:
        return normalized
    return "unknown"


@setup_logging.connect
def _setup_celery_logging(**kwargs) -> None:
    configure_celery_runtime_logging()


@worker_process_init.connect
def _bootstrap_worker_process_observability(**kwargs) -> None:
    bootstrap_celery_observability()


@beat_init.connect
def _bootstrap_beat_observability(**kwargs) -> None:
    bootstrap_celery_observability()


@task_prerun.connect
def _record_task_start(task_id=None, task=None, **kwargs) -> None:
    if task is None:
        return
    _task_start_times[_task_metric_key(task_id, task)] = time.perf_counter()


@task_postrun.connect
def _record_task_metrics(task_id=None, task=None, state=None, **kwargs) -> None:
    if task is None:
        return

    key = _task_metric_key(task_id, task)
    started_at = _task_start_times.pop(key, None)
    if started_at is None:
        return

    observe_job(
        getattr(task, "name", "unknown"),
        _normalize_task_state(state),
        max(time.perf_counter() - started_at, 0.0),
    )


celery_app = Celery("dotmac_erp")
celery_app.conf.update(get_celery_config())
celery_app.conf.beat_schedule = build_beat_schedule()
celery_app.conf.beat_scheduler = "app.celery_scheduler.DbScheduler"
celery_app.autodiscover_tasks(["app.tasks"])
