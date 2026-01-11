import logging
import os
from datetime import timedelta

from app.db import SessionLocal
from app.models.domain_settings import DomainSetting, SettingDomain
from app.models.scheduler import ScheduleType, ScheduledTask
logger = logging.getLogger(__name__)


def _env_value(name: str) -> str | None:
    value = os.getenv(name)
    if value is None or value == "":
        return None
    return value


def _env_int(name: str) -> int | None:
    raw = _env_value(name)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _get_setting_value(db, domain: SettingDomain, key: str) -> str | None:
    setting = (
        db.query(DomainSetting)
        .filter(DomainSetting.domain == domain)
        .filter(DomainSetting.key == key)
        .filter(DomainSetting.is_active.is_(True))
        .first()
    )
    if not setting:
        return None
    if setting.value_text:
        return str(setting.value_text)
    if setting.value_json is not None:
        return str(setting.value_json)
    return None


def _effective_int(
    db, domain: SettingDomain, key: str, env_key: str, default: int
) -> int:
    env_value = _env_int(env_key)
    if env_value is not None:
        return env_value
    value = _get_setting_value(db, domain, key)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _effective_str(
    db, domain: SettingDomain, key: str, env_key: str, default: str | None
) -> str | None:
    env_value = _env_value(env_key)
    if env_value is not None:
        return env_value
    value = _get_setting_value(db, domain, key)
    if value is None:
        return default
    return str(value)


def get_celery_config() -> dict:
    broker = None
    backend = None
    timezone = None
    beat_max_loop_interval = 5
    beat_refresh_seconds = 30
    session = SessionLocal()
    try:
        broker = _effective_str(
            session, SettingDomain.scheduler, "broker_url", "CELERY_BROKER_URL", None
        )
        backend = _effective_str(
            session,
            SettingDomain.scheduler,
            "result_backend",
            "CELERY_RESULT_BACKEND",
            None,
        )
        timezone = _effective_str(
            session, SettingDomain.scheduler, "timezone", "CELERY_TIMEZONE", None
        )
        beat_max_loop_interval = _effective_int(
            session,
            SettingDomain.scheduler,
            "beat_max_loop_interval",
            "CELERY_BEAT_MAX_LOOP_INTERVAL",
            5,
        )
        beat_refresh_seconds = _effective_int(
            session,
            SettingDomain.scheduler,
            "beat_refresh_seconds",
            "CELERY_BEAT_REFRESH_SECONDS",
            30,
        )
    except Exception:
        logger.exception("Failed to load scheduler settings from database.")
    finally:
        session.close()

    broker = (
        broker
        or _env_value("REDIS_URL")
        or "redis://localhost:6379/0"
    )
    backend = (
        backend
        or _env_value("REDIS_URL")
        or "redis://localhost:6379/1"
    )
    timezone = timezone or "UTC"
    config: dict[str, str | int] = {
        "broker_url": broker,
        "result_backend": backend,
        "timezone": timezone,
        "beat_max_loop_interval": beat_max_loop_interval,
        "beat_refresh_seconds": beat_refresh_seconds,
    }
    return config


def build_beat_schedule() -> dict:
    schedule: dict[str, dict] = {}
    session = SessionLocal()
    try:
        tasks = (
            session.query(ScheduledTask)
            .filter(ScheduledTask.enabled.is_(True))
            .all()
        )
        for task in tasks:
            if task.schedule_type != ScheduleType.interval:
                continue
            interval_seconds = max(task.interval_seconds or 0, 1)
            schedule[f"scheduled_task_{task.id}"] = {
                "task": task.task_name,
                "schedule": timedelta(seconds=interval_seconds),
                "args": task.args_json or [],
                "kwargs": task.kwargs_json or {},
            }
    except Exception:
        logger.exception("Failed to build Celery beat schedule.")
    finally:
        session.close()
    return schedule
