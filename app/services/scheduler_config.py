import logging
import os
from datetime import timedelta

from celery.schedules import crontab

from app.db import SessionLocal
from app.models.domain_settings import DomainSetting, SettingDomain
from app.models.scheduler import ScheduledTask, ScheduleType

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

    broker = broker or _env_value("REDIS_URL") or "redis://localhost:6379/0"
    backend = backend or _env_value("REDIS_URL") or "redis://localhost:6379/1"
    timezone = timezone or "UTC"
    config: dict[str, str | int] = {
        "broker_url": broker,
        "result_backend": backend,
        "timezone": timezone,
        "beat_max_loop_interval": beat_max_loop_interval,
        "beat_refresh_seconds": beat_refresh_seconds,
    }
    return config


def _builtin_beat_schedule() -> dict[str, dict]:
    """Built-in scheduled tasks that always run, independent of DB config."""
    return {
        "analytics-cash-flow": {
            "task": "app.tasks.analytics.refresh_cash_flow_metrics",
            "schedule": crontab(hour=5, minute=0),  # 5:00 AM — before coach tasks
        },
        "analytics-efficiency": {
            "task": "app.tasks.analytics.refresh_efficiency_metrics",
            "schedule": crontab(hour=5, minute=10),  # 5:10 AM
        },
        "analytics-revenue": {
            "task": "app.tasks.analytics.refresh_revenue_metrics",
            "schedule": crontab(hour=5, minute=20),  # 5:20 AM
        },
        "analytics-workforce": {
            "task": "app.tasks.analytics.refresh_workforce_metrics",
            "schedule": crontab(hour=5, minute=30),  # 5:30 AM
        },
        "analytics-supply-chain": {
            "task": "app.tasks.analytics.refresh_supply_chain_metrics",
            "schedule": crontab(hour=5, minute=40),  # 5:40 AM
        },
        "analytics-compliance": {
            "task": "app.tasks.analytics.refresh_compliance_metrics",
            "schedule": crontab(hour=5, minute=50),  # 5:50 AM
        },
        "coach-daily-data-quality": {
            "task": "app.tasks.coach.generate_daily_data_quality_insights",
            "schedule": crontab(hour=6, minute=0),  # Daily at 6 AM
        },
        "coach-daily-banking-health": {
            "task": "app.tasks.coach.generate_daily_banking_health_insights",
            "schedule": crontab(hour=6, minute=10),  # Daily at 6:10 AM
        },
        "coach-daily-expense-approvals": {
            "task": "app.tasks.coach.generate_daily_expense_approval_insights",
            "schedule": crontab(hour=6, minute=20),  # Daily at 6:20 AM
        },
        "coach-daily-ar-overdue": {
            "task": "app.tasks.coach.generate_daily_ar_overdue_insights",
            "schedule": crontab(hour=6, minute=30),  # Daily at 6:30 AM
        },
        "coach-daily-ap-due": {
            "task": "app.tasks.coach.generate_daily_ap_due_insights",
            "schedule": crontab(hour=6, minute=40),  # Daily at 6:40 AM
        },
        "expense-approval-reminders": {
            "task": "app.tasks.expense.process_expense_approval_reminders",
            "schedule": crontab(hour=8, minute=0),  # Daily at 8 AM
        },
        "expense-stuck-transfers": {
            "task": "app.tasks.expense.poll_stuck_expense_transfers",
            "schedule": crontab(minute="*/2"),  # Every 2 minutes
        },
        "daily-exchange-rate-fetch": {
            "task": "app.tasks.exchange_rates.fetch_daily_exchange_rates",
            "schedule": crontab(hour=14, minute=0),  # 2 PM UTC daily
        },
        "daily-leave-attendance-sync": {
            "task": "app.tasks.hr.sync_leave_attendance",
            "schedule": crontab(minute=0),  # Hourly at minute 0
        },
        "fleet-document-expiry-reminders": {
            "task": "app.tasks.fleet.process_document_expiry_notifications",
            "schedule": crontab(hour=7, minute=0),  # 7 AM daily
        },
        "banking-auto-match-statements": {
            "task": "app.tasks.banking.auto_match_unreconciled_statements",
            "schedule": crontab(hour="*/6", minute=15),  # Every 6 hours at :15
        },
        "splynx-incremental-sync": {
            "task": "app.tasks.splynx.run_splynx_incremental_sync",
            "schedule": crontab(minute="*/30"),  # Every 30 minutes
        },
        "recurring-templates": {
            "task": "app.tasks.automation.process_recurring_templates",
            "schedule": crontab(hour="*/6", minute=5),  # Every 6 hours at :05
        },
        "splynx-daily-reconciliation": {
            "task": "app.tasks.splynx.run_splynx_daily_reconciliation",
            "schedule": crontab(hour=1, minute=0),  # 1 AM daily
        },
        "splynx-full-reconciliation": {
            "task": "app.tasks.splynx.run_splynx_full_reconciliation",
            "schedule": crontab(hour=2, minute=0, day_of_week=0),  # Sunday 2 AM
        },
    }


def build_beat_schedule() -> dict:
    schedule: dict[str, dict] = _builtin_beat_schedule()
    session = SessionLocal()
    try:
        tasks = (
            session.query(ScheduledTask).filter(ScheduledTask.enabled.is_(True)).all()
        )
        for task in tasks:
            task_schedule = None

            if task.schedule_type == ScheduleType.interval:
                interval_seconds = max(task.interval_seconds or 0, 1)
                task_schedule = timedelta(seconds=interval_seconds)
            elif task.schedule_type == ScheduleType.crontab:
                task_schedule = crontab(
                    minute=task.cron_minute or "0",
                    hour=task.cron_hour or "8",
                    day_of_week=task.cron_day_of_week or "*",
                    day_of_month=task.cron_day_of_month or "*",
                    month_of_year=task.cron_month_of_year or "*",
                )

            if task_schedule is None:
                continue

            schedule[f"scheduled_task_{task.id}"] = {
                "task": task.task_name,
                "schedule": task_schedule,
                "args": task.args_json or [],
                "kwargs": task.kwargs_json or {},
            }
    except Exception:
        logger.exception("Failed to build Celery beat schedule.")
    finally:
        session.close()
    return schedule
