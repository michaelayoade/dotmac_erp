import os

from sqlalchemy.orm import Session

from app.models.domain_settings import SettingValueType
from app.services.domain_settings import (
    audit_settings,
    auth_settings,
    automation_settings,
    email_settings,
    features_settings,
    payments_settings,
    reporting_settings,
    scheduler_settings,
)
from app.services.secrets import is_openbao_ref


def _csv_list(raw: str | None, upper: bool = True) -> list[str] | None:
    if not raw:
        return None
    items = [item.strip() for item in raw.split(",") if item.strip()]
    if upper:
        return [item.upper() for item in items]
    return items


def seed_auth_settings(db: Session) -> None:
    auth_settings.ensure_by_key(
        db,
        key="jwt_algorithm",
        value_type=SettingValueType.string,
        value_text=os.getenv("JWT_ALGORITHM", "HS256"),
    )
    auth_settings.ensure_by_key(
        db,
        key="jwt_access_ttl_minutes",
        value_type=SettingValueType.integer,
        value_text=os.getenv("JWT_ACCESS_TTL_MINUTES", "15"),
    )
    auth_settings.ensure_by_key(
        db,
        key="jwt_refresh_ttl_days",
        value_type=SettingValueType.integer,
        value_text=os.getenv("JWT_REFRESH_TTL_DAYS", "30"),
    )
    auth_settings.ensure_by_key(
        db,
        key="refresh_cookie_name",
        value_type=SettingValueType.string,
        value_text=os.getenv("REFRESH_COOKIE_NAME", "refresh_token"),
    )
    auth_settings.ensure_by_key(
        db,
        key="refresh_cookie_secure",
        value_type=SettingValueType.boolean,
        value_text=os.getenv("REFRESH_COOKIE_SECURE", "false"),
    )
    auth_settings.ensure_by_key(
        db,
        key="refresh_cookie_samesite",
        value_type=SettingValueType.string,
        value_text=os.getenv("REFRESH_COOKIE_SAMESITE", "lax"),
    )
    auth_settings.ensure_by_key(
        db,
        key="refresh_cookie_domain",
        value_type=SettingValueType.string,
        value_text=os.getenv("REFRESH_COOKIE_DOMAIN"),
    )
    auth_settings.ensure_by_key(
        db,
        key="refresh_cookie_path",
        value_type=SettingValueType.string,
        value_text=os.getenv("REFRESH_COOKIE_PATH", "/auth"),
    )
    auth_settings.ensure_by_key(
        db,
        key="totp_issuer",
        value_type=SettingValueType.string,
        value_text=os.getenv("TOTP_ISSUER", "dotmac_erp"),
    )
    auth_settings.ensure_by_key(
        db,
        key="api_key_rate_window_seconds",
        value_type=SettingValueType.integer,
        value_text=os.getenv("API_KEY_RATE_WINDOW_SECONDS", "60"),
    )
    auth_settings.ensure_by_key(
        db,
        key="api_key_rate_max",
        value_type=SettingValueType.integer,
        value_text=os.getenv("API_KEY_RATE_MAX", "5"),
    )
    auth_settings.ensure_by_key(
        db,
        key="default_auth_provider",
        value_type=SettingValueType.string,
        value_text=os.getenv("AUTH_DEFAULT_AUTH_PROVIDER", "local"),
    )
    jwt_secret = os.getenv("JWT_SECRET")
    if jwt_secret and is_openbao_ref(jwt_secret):
        auth_settings.ensure_by_key(
            db,
            key="jwt_secret",
            value_type=SettingValueType.string,
            value_text=jwt_secret,
            is_secret=True,
        )
    totp_key = os.getenv("TOTP_ENCRYPTION_KEY")
    if totp_key and is_openbao_ref(totp_key):
        auth_settings.ensure_by_key(
            db,
            key="totp_encryption_key",
            value_type=SettingValueType.string,
            value_text=totp_key,
            is_secret=True,
        )


def seed_audit_settings(db: Session) -> None:
    audit_settings.ensure_by_key(
        db,
        key="enabled",
        value_type=SettingValueType.boolean,
        value_text=os.getenv("AUDIT_ENABLED", "true"),
    )
    methods_env = os.getenv("AUDIT_METHODS")
    methods_value = _csv_list(methods_env, upper=True)
    audit_settings.ensure_by_key(
        db,
        key="methods",
        value_type=SettingValueType.json,
        value_json=methods_value or ["POST", "PUT", "PATCH", "DELETE"],
    )
    skip_paths_env = os.getenv("AUDIT_SKIP_PATHS")
    skip_paths_value = _csv_list(skip_paths_env, upper=False)
    audit_settings.ensure_by_key(
        db,
        key="skip_paths",
        value_type=SettingValueType.json,
        value_json=skip_paths_value or ["/static", "/web", "/health"],
    )
    audit_settings.ensure_by_key(
        db,
        key="read_trigger_header",
        value_type=SettingValueType.string,
        value_text=os.getenv("AUDIT_READ_TRIGGER_HEADER", "x-audit-read"),
    )
    audit_settings.ensure_by_key(
        db,
        key="read_trigger_query",
        value_type=SettingValueType.string,
        value_text=os.getenv("AUDIT_READ_TRIGGER_QUERY", "audit"),
    )


def seed_scheduler_settings(db: Session) -> None:
    broker = (
        os.getenv("CELERY_BROKER_URL")
        or os.getenv("REDIS_URL")
        or "redis://localhost:6379/0"
    )
    backend = (
        os.getenv("CELERY_RESULT_BACKEND")
        or os.getenv("REDIS_URL")
        or "redis://localhost:6379/1"
    )
    scheduler_settings.ensure_by_key(
        db,
        key="broker_url",
        value_type=SettingValueType.string,
        value_text=broker,
    )
    scheduler_settings.ensure_by_key(
        db,
        key="result_backend",
        value_type=SettingValueType.string,
        value_text=backend,
    )
    scheduler_settings.ensure_by_key(
        db,
        key="timezone",
        value_type=SettingValueType.string,
        value_text=os.getenv("CELERY_TIMEZONE", "UTC"),
    )
    scheduler_settings.ensure_by_key(
        db,
        key="beat_max_loop_interval",
        value_type=SettingValueType.integer,
        value_text=os.getenv("CELERY_BEAT_MAX_LOOP_INTERVAL", "5"),
    )
    scheduler_settings.ensure_by_key(
        db,
        key="beat_refresh_seconds",
        value_type=SettingValueType.integer,
        value_text=os.getenv("CELERY_BEAT_REFRESH_SECONDS", "30"),
    )


def seed_email_settings(db: Session) -> None:
    """Seed email/SMTP settings from environment variables."""
    email_settings.ensure_by_key(
        db,
        key="smtp_host",
        value_type=SettingValueType.string,
        value_text=os.getenv("SMTP_HOST", "localhost"),
    )
    email_settings.ensure_by_key(
        db,
        key="smtp_port",
        value_type=SettingValueType.integer,
        value_text=os.getenv("SMTP_PORT", "587"),
    )
    smtp_user = os.getenv("SMTP_USERNAME")
    if smtp_user:
        email_settings.ensure_by_key(
            db,
            key="smtp_username",
            value_type=SettingValueType.string,
            value_text=smtp_user,
        )
    smtp_pass = os.getenv("SMTP_PASSWORD")
    if smtp_pass:
        email_settings.ensure_by_key(
            db,
            key="smtp_password",
            value_type=SettingValueType.string,
            value_text=smtp_pass,
            is_secret=True,
        )
    email_settings.ensure_by_key(
        db,
        key="smtp_use_tls",
        value_type=SettingValueType.boolean,
        value_text=os.getenv("SMTP_USE_TLS", "true"),
    )
    email_settings.ensure_by_key(
        db,
        key="smtp_use_ssl",
        value_type=SettingValueType.boolean,
        value_text=os.getenv("SMTP_USE_SSL", "false"),
    )
    email_settings.ensure_by_key(
        db,
        key="smtp_from_email",
        value_type=SettingValueType.string,
        value_text=os.getenv("SMTP_FROM_EMAIL", "noreply@example.com"),
    )
    email_settings.ensure_by_key(
        db,
        key="smtp_from_name",
        value_type=SettingValueType.string,
        value_text=os.getenv("SMTP_FROM_NAME", "Dotmac ERP"),
    )
    reply_to = os.getenv("EMAIL_REPLY_TO")
    if reply_to:
        email_settings.ensure_by_key(
            db,
            key="email_reply_to",
            value_type=SettingValueType.string,
            value_text=reply_to,
        )


def seed_automation_settings(db: Session) -> None:
    """Seed automation settings with sensible defaults."""
    automation_settings.ensure_by_key(
        db,
        key="recurring_default_frequency",
        value_type=SettingValueType.string,
        value_text="MONTHLY",
    )
    automation_settings.ensure_by_key(
        db,
        key="recurring_max_occurrences",
        value_type=SettingValueType.integer,
        value_text="999",
    )
    automation_settings.ensure_by_key(
        db,
        key="recurring_lookback_days",
        value_type=SettingValueType.integer,
        value_text="7",
    )
    automation_settings.ensure_by_key(
        db,
        key="workflow_max_actions_per_event",
        value_type=SettingValueType.integer,
        value_text="10",
    )
    automation_settings.ensure_by_key(
        db,
        key="workflow_async_timeout_seconds",
        value_type=SettingValueType.integer,
        value_text="300",
    )
    automation_settings.ensure_by_key(
        db,
        key="custom_fields_max_per_entity",
        value_type=SettingValueType.integer,
        value_text="20",
    )
    # Webhook security settings
    automation_settings.ensure_by_key(
        db,
        key="webhook_allowed_hosts",
        value_type=SettingValueType.string,
        value_text=os.getenv("WEBHOOK_ALLOWED_HOSTS", ""),
    )
    automation_settings.ensure_by_key(
        db,
        key="webhook_allowed_domains",
        value_type=SettingValueType.string,
        value_text=os.getenv("WEBHOOK_ALLOWED_DOMAINS", ""),
    )
    automation_settings.ensure_by_key(
        db,
        key="webhook_allow_insecure",
        value_type=SettingValueType.boolean,
        value_text=os.getenv("WEBHOOK_ALLOW_INSECURE", "false"),
    )
    automation_settings.ensure_by_key(
        db,
        key="webhook_allow_localhost",
        value_type=SettingValueType.boolean,
        value_text=os.getenv("WEBHOOK_ALLOW_LOCALHOST", "false"),
    )
    automation_settings.ensure_by_key(
        db,
        key="webhook_timeout_seconds",
        value_type=SettingValueType.integer,
        value_text=os.getenv("WEBHOOK_TIMEOUT_SECONDS", "10"),
    )
    automation_settings.ensure_by_key(
        db,
        key="openbao_allow_insecure",
        value_type=SettingValueType.boolean,
        value_text=os.getenv("OPENBAO_ALLOW_INSECURE", "false"),
    )


def seed_features_settings(db: Session) -> None:
    """Seed feature flags with default values."""
    features_settings.ensure_by_key(
        db,
        key="enable_multi_currency",
        value_type=SettingValueType.boolean,
        value_json=True,
    )
    features_settings.ensure_by_key(
        db,
        key="enable_budgeting",
        value_type=SettingValueType.boolean,
        value_json=False,
    )
    features_settings.ensure_by_key(
        db,
        key="enable_project_accounting",
        value_type=SettingValueType.boolean,
        value_json=False,
    )
    features_settings.ensure_by_key(
        db,
        key="enable_bank_reconciliation",
        value_type=SettingValueType.boolean,
        value_json=True,
    )
    features_settings.ensure_by_key(
        db,
        key="enable_recurring_transactions",
        value_type=SettingValueType.boolean,
        value_json=True,
    )
    features_settings.ensure_by_key(
        db,
        key="enable_inventory",
        value_type=SettingValueType.boolean,
        value_json=True,
    )
    features_settings.ensure_by_key(
        db,
        key="enable_fixed_assets",
        value_type=SettingValueType.boolean,
        value_json=True,
    )
    features_settings.ensure_by_key(
        db,
        key="enable_leases",
        value_type=SettingValueType.boolean,
        value_json=False,
    )


def seed_reporting_settings(db: Session) -> None:
    """Seed reporting settings with defaults."""
    reporting_settings.ensure_by_key(
        db,
        key="default_export_format",
        value_type=SettingValueType.string,
        value_text="PDF",
    )
    reporting_settings.ensure_by_key(
        db,
        key="report_page_size",
        value_type=SettingValueType.string,
        value_text="A4",
    )
    reporting_settings.ensure_by_key(
        db,
        key="report_orientation",
        value_type=SettingValueType.string,
        value_text="PORTRAIT",
    )
    reporting_settings.ensure_by_key(
        db,
        key="include_logo_in_reports",
        value_type=SettingValueType.boolean,
        value_json=True,
    )
    # watermark_text is optional, no default


def seed_payments_settings(db: Session) -> None:
    """Seed payments/Paystack settings from environment variables."""
    payments_settings.ensure_by_key(
        db,
        key="paystack_enabled",
        value_type=SettingValueType.boolean,
        value_text=os.getenv("PAYSTACK_ENABLED", "false"),
    )
    public_key = os.getenv("PAYSTACK_PUBLIC_KEY")
    if public_key:
        payments_settings.ensure_by_key(
            db,
            key="paystack_public_key",
            value_type=SettingValueType.string,
            value_text=public_key,
        )
    secret_key = os.getenv("PAYSTACK_SECRET_KEY")
    if secret_key:
        payments_settings.ensure_by_key(
            db,
            key="paystack_secret_key",
            value_type=SettingValueType.string,
            value_text=secret_key,
            is_secret=True,
        )
    webhook_secret = os.getenv("PAYSTACK_WEBHOOK_SECRET")
    if webhook_secret:
        payments_settings.ensure_by_key(
            db,
            key="paystack_webhook_secret",
            value_type=SettingValueType.string,
            value_text=webhook_secret,
            is_secret=True,
        )
    callback_url = os.getenv("PAYSTACK_CALLBACK_BASE_URL")
    if callback_url:
        payments_settings.ensure_by_key(
            db,
            key="paystack_callback_base_url",
            value_type=SettingValueType.string,
            value_text=callback_url,
        )
    payments_settings.ensure_by_key(
        db,
        key="paystack_transfers_enabled",
        value_type=SettingValueType.boolean,
        value_text=os.getenv("PAYSTACK_TRANSFERS_ENABLED", "false"),
    )


def seed_scheduled_tasks(db: Session) -> None:
    """Seed default scheduled tasks including finance reminders."""
    from app.models.scheduler import ScheduledTask, ScheduleType

    # Define default finance reminder tasks
    default_tasks = [
        {
            "name": "Finance: All Reminders (Master)",
            "task_name": "app.tasks.finance.process_all_finance_reminders",
            "schedule_type": ScheduleType.crontab,
            "cron_minute": "0",
            "cron_hour": "8",  # 8 AM daily
            "cron_day_of_week": "*",
            "cron_day_of_month": "*",
            "cron_month_of_year": "*",
            "enabled": True,
        },
        {
            "name": "Finance: Fiscal Period Close Reminders",
            "task_name": "app.tasks.finance.process_fiscal_period_reminders",
            "schedule_type": ScheduleType.crontab,
            "cron_minute": "0",
            "cron_hour": "8",
            "cron_day_of_week": "*",
            "cron_day_of_month": "*",
            "cron_month_of_year": "*",
            "enabled": False,  # Disabled - use master task instead
        },
        {
            "name": "Finance: Tax Period Filing Reminders",
            "task_name": "app.tasks.finance.process_tax_period_reminders",
            "schedule_type": ScheduleType.crontab,
            "cron_minute": "0",
            "cron_hour": "8",
            "cron_day_of_week": "*",
            "cron_day_of_month": "*",
            "cron_month_of_year": "*",
            "enabled": False,  # Disabled - use master task instead
        },
        {
            "name": "Finance: Bank Reconciliation Reminders",
            "task_name": "app.tasks.finance.process_bank_reconciliation_reminders",
            "schedule_type": ScheduleType.crontab,
            "cron_minute": "0",
            "cron_hour": "8",
            "cron_day_of_week": "*",
            "cron_day_of_month": "*",
            "cron_month_of_year": "*",
            "enabled": False,  # Disabled - use master task instead
        },
        {
            "name": "Finance: AR Collection Reminders",
            "task_name": "app.tasks.finance.process_ar_collection_reminders",
            "schedule_type": ScheduleType.crontab,
            "cron_minute": "0",
            "cron_hour": "9",  # 9 AM - slightly staggered
            "cron_day_of_week": "*",
            "cron_day_of_month": "*",
            "cron_month_of_year": "*",
            "enabled": False,  # Disabled - use master task instead
        },
        {
            "name": "Finance: Subledger Reconciliation Check",
            "task_name": "app.tasks.finance.process_subledger_reconciliation",
            "schedule_type": ScheduleType.crontab,
            "cron_minute": "0",
            "cron_hour": "7",  # 7 AM - before main reminders
            "cron_day_of_week": "*",
            "cron_day_of_month": "*",
            "cron_month_of_year": "*",
            "enabled": False,  # Disabled - use master task instead
        },
    ]

    for task_def in default_tasks:
        # Check if task already exists by task_name
        existing = (
            db.query(ScheduledTask)
            .filter(ScheduledTask.task_name == task_def["task_name"])
            .first()
        )
        if existing:
            continue  # Don't overwrite existing configuration

        task = ScheduledTask(**task_def)
        db.add(task)

    db.commit()


def seed_all_settings(db: Session) -> None:
    """Seed all domain settings."""
    seed_auth_settings(db)
    seed_audit_settings(db)
    seed_scheduler_settings(db)
    seed_email_settings(db)
    seed_automation_settings(db)
    seed_features_settings(db)
    seed_reporting_settings(db)
    seed_payments_settings(db)
    seed_scheduled_tasks(db)
