import logging
from dataclasses import dataclass
from typing import cast

from fastapi import HTTPException

from app.models.domain_settings import SettingDomain, SettingValueType
from app.services import domain_settings as settings_service
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SettingSpec(ListResponseMixin):
    domain: SettingDomain
    key: str
    env_var: str | None
    value_type: SettingValueType
    default: object | None
    required: bool = False
    allowed: set[str] | None = None
    min_value: int | None = None
    max_value: int | None = None
    is_secret: bool = False
    label: str | None = None
    description: str | None = None


SETTINGS_SPECS: list[SettingSpec] = [
    SettingSpec(
        domain=SettingDomain.auth,
        key="jwt_secret",
        env_var="JWT_SECRET",
        value_type=SettingValueType.string,
        default=None,
        required=True,
        is_secret=True,
    ),
    SettingSpec(
        domain=SettingDomain.auth,
        key="jwt_algorithm",
        env_var="JWT_ALGORITHM",
        value_type=SettingValueType.string,
        default="HS256",
    ),
    SettingSpec(
        domain=SettingDomain.auth,
        key="jwt_access_ttl_minutes",
        env_var="JWT_ACCESS_TTL_MINUTES",
        value_type=SettingValueType.integer,
        default=15,
        min_value=1,
    ),
    SettingSpec(
        domain=SettingDomain.auth,
        key="jwt_refresh_ttl_days",
        env_var="JWT_REFRESH_TTL_DAYS",
        value_type=SettingValueType.integer,
        default=30,
        min_value=1,
    ),
    SettingSpec(
        domain=SettingDomain.auth,
        key="refresh_cookie_name",
        env_var="REFRESH_COOKIE_NAME",
        value_type=SettingValueType.string,
        default="refresh_token",
    ),
    SettingSpec(
        domain=SettingDomain.auth,
        key="refresh_cookie_secure",
        env_var="REFRESH_COOKIE_SECURE",
        value_type=SettingValueType.boolean,
        default=False,
    ),
    SettingSpec(
        domain=SettingDomain.auth,
        key="refresh_cookie_samesite",
        env_var="REFRESH_COOKIE_SAMESITE",
        value_type=SettingValueType.string,
        default="lax",
        allowed={"lax", "strict", "none"},
    ),
    SettingSpec(
        domain=SettingDomain.auth,
        key="refresh_cookie_domain",
        env_var="REFRESH_COOKIE_DOMAIN",
        value_type=SettingValueType.string,
        default=None,
    ),
    SettingSpec(
        domain=SettingDomain.auth,
        key="refresh_cookie_path",
        env_var="REFRESH_COOKIE_PATH",
        value_type=SettingValueType.string,
        default="/auth",
    ),
    SettingSpec(
        domain=SettingDomain.auth,
        key="totp_issuer",
        env_var="TOTP_ISSUER",
        value_type=SettingValueType.string,
        default="dotmac_erp",
    ),
    SettingSpec(
        domain=SettingDomain.auth,
        key="totp_encryption_key",
        env_var="TOTP_ENCRYPTION_KEY",
        value_type=SettingValueType.string,
        default=None,
        required=True,
        is_secret=True,
    ),
    SettingSpec(
        domain=SettingDomain.auth,
        key="api_key_rate_window_seconds",
        env_var="API_KEY_RATE_WINDOW_SECONDS",
        value_type=SettingValueType.integer,
        default=60,
        min_value=1,
    ),
    SettingSpec(
        domain=SettingDomain.auth,
        key="api_key_rate_max",
        env_var="API_KEY_RATE_MAX",
        value_type=SettingValueType.integer,
        default=5,
        min_value=1,
    ),
    SettingSpec(
        domain=SettingDomain.auth,
        key="default_auth_provider",
        env_var="AUTH_DEFAULT_AUTH_PROVIDER",
        value_type=SettingValueType.string,
        default="local",
        allowed={"local", "sso"},
    ),
    SettingSpec(
        domain=SettingDomain.audit,
        key="enabled",
        env_var="AUDIT_ENABLED",
        value_type=SettingValueType.boolean,
        default=True,
    ),
    SettingSpec(
        domain=SettingDomain.audit,
        key="methods",
        env_var="AUDIT_METHODS",
        value_type=SettingValueType.json,
        default=["POST", "PUT", "PATCH", "DELETE"],
    ),
    SettingSpec(
        domain=SettingDomain.audit,
        key="skip_paths",
        env_var="AUDIT_SKIP_PATHS",
        value_type=SettingValueType.json,
        default=["/static", "/web", "/health"],
    ),
    SettingSpec(
        domain=SettingDomain.audit,
        key="read_trigger_header",
        env_var="AUDIT_READ_TRIGGER_HEADER",
        value_type=SettingValueType.string,
        default="x-audit-read",
    ),
    SettingSpec(
        domain=SettingDomain.audit,
        key="read_trigger_query",
        env_var="AUDIT_READ_TRIGGER_QUERY",
        value_type=SettingValueType.string,
        default="audit",
    ),
    SettingSpec(
        domain=SettingDomain.scheduler,
        key="broker_url",
        env_var="CELERY_BROKER_URL",
        value_type=SettingValueType.string,
        default=None,
    ),
    SettingSpec(
        domain=SettingDomain.scheduler,
        key="result_backend",
        env_var="CELERY_RESULT_BACKEND",
        value_type=SettingValueType.string,
        default=None,
    ),
    SettingSpec(
        domain=SettingDomain.scheduler,
        key="timezone",
        env_var="CELERY_TIMEZONE",
        value_type=SettingValueType.string,
        default="UTC",
    ),
    SettingSpec(
        domain=SettingDomain.scheduler,
        key="beat_max_loop_interval",
        env_var="CELERY_BEAT_MAX_LOOP_INTERVAL",
        value_type=SettingValueType.integer,
        default=5,
        min_value=1,
    ),
    SettingSpec(
        domain=SettingDomain.scheduler,
        key="beat_refresh_seconds",
        env_var="CELERY_BEAT_REFRESH_SECONDS",
        value_type=SettingValueType.integer,
        default=30,
        min_value=1,
    ),
    # Email Domain Settings
    SettingSpec(
        domain=SettingDomain.email,
        key="smtp_host",
        env_var="SMTP_HOST",
        value_type=SettingValueType.string,
        default="localhost",
    ),
    SettingSpec(
        domain=SettingDomain.email,
        key="smtp_port",
        env_var="SMTP_PORT",
        value_type=SettingValueType.integer,
        default=587,
        min_value=1,
        max_value=65535,
    ),
    SettingSpec(
        domain=SettingDomain.email,
        key="smtp_username",
        env_var="SMTP_USERNAME",
        value_type=SettingValueType.string,
        default=None,
    ),
    SettingSpec(
        domain=SettingDomain.email,
        key="smtp_password",
        env_var="SMTP_PASSWORD",
        value_type=SettingValueType.string,
        default=None,
        is_secret=True,
    ),
    SettingSpec(
        domain=SettingDomain.email,
        key="smtp_use_tls",
        env_var="SMTP_USE_TLS",
        value_type=SettingValueType.boolean,
        default=True,
    ),
    SettingSpec(
        domain=SettingDomain.email,
        key="smtp_use_ssl",
        env_var="SMTP_USE_SSL",
        value_type=SettingValueType.boolean,
        default=False,
    ),
    SettingSpec(
        domain=SettingDomain.email,
        key="smtp_from_email",
        env_var="SMTP_FROM_EMAIL",
        value_type=SettingValueType.string,
        default="noreply@example.com",
    ),
    SettingSpec(
        domain=SettingDomain.email,
        key="smtp_from_name",
        env_var="SMTP_FROM_NAME",
        value_type=SettingValueType.string,
        default="Dotmac ERP",
    ),
    SettingSpec(
        domain=SettingDomain.email,
        key="email_reply_to",
        env_var="EMAIL_REPLY_TO",
        value_type=SettingValueType.string,
        default=None,
    ),
    # Automation Domain Settings
    SettingSpec(
        domain=SettingDomain.automation,
        key="recurring_default_frequency",
        env_var=None,
        value_type=SettingValueType.string,
        default="MONTHLY",
        allowed={"DAILY", "WEEKLY", "MONTHLY", "QUARTERLY", "YEARLY"},
    ),
    SettingSpec(
        domain=SettingDomain.automation,
        key="recurring_max_occurrences",
        env_var=None,
        value_type=SettingValueType.integer,
        default=999,
        min_value=1,
        max_value=9999,
    ),
    SettingSpec(
        domain=SettingDomain.automation,
        key="recurring_lookback_days",
        env_var=None,
        value_type=SettingValueType.integer,
        default=7,
        min_value=1,
        max_value=90,
    ),
    SettingSpec(
        domain=SettingDomain.automation,
        key="workflow_max_actions_per_event",
        env_var=None,
        value_type=SettingValueType.integer,
        default=10,
        min_value=1,
        max_value=100,
    ),
    SettingSpec(
        domain=SettingDomain.automation,
        key="workflow_async_timeout_seconds",
        env_var=None,
        value_type=SettingValueType.integer,
        default=300,
        min_value=30,
        max_value=3600,
    ),
    SettingSpec(
        domain=SettingDomain.automation,
        key="custom_fields_max_per_entity",
        env_var=None,
        value_type=SettingValueType.integer,
        default=20,
        min_value=1,
        max_value=100,
    ),
    # Webhook Security Settings
    SettingSpec(
        domain=SettingDomain.automation,
        key="webhook_allowed_hosts",
        env_var="WEBHOOK_ALLOWED_HOSTS",
        value_type=SettingValueType.string,
        default="",
    ),
    SettingSpec(
        domain=SettingDomain.automation,
        key="webhook_allowed_domains",
        env_var="WEBHOOK_ALLOWED_DOMAINS",
        value_type=SettingValueType.string,
        default="",
    ),
    SettingSpec(
        domain=SettingDomain.automation,
        key="webhook_allow_insecure",
        env_var="WEBHOOK_ALLOW_INSECURE",
        value_type=SettingValueType.boolean,
        default=False,
    ),
    SettingSpec(
        domain=SettingDomain.automation,
        key="webhook_allow_localhost",
        env_var="WEBHOOK_ALLOW_LOCALHOST",
        value_type=SettingValueType.boolean,
        default=False,
    ),
    SettingSpec(
        domain=SettingDomain.automation,
        key="webhook_timeout_seconds",
        env_var="WEBHOOK_TIMEOUT_SECONDS",
        value_type=SettingValueType.integer,
        default=10,
        min_value=1,
        max_value=300,
    ),
    # Secrets Provider Settings
    SettingSpec(
        domain=SettingDomain.automation,
        key="openbao_allow_insecure",
        env_var="OPENBAO_ALLOW_INSECURE",
        value_type=SettingValueType.boolean,
        default=False,
    ),
    # Features Domain Settings (Feature Flags)
    SettingSpec(
        domain=SettingDomain.features,
        key="enable_multi_currency",
        env_var=None,
        value_type=SettingValueType.boolean,
        default=True,
    ),
    SettingSpec(
        domain=SettingDomain.features,
        key="enable_budgeting",
        env_var=None,
        value_type=SettingValueType.boolean,
        default=False,
    ),
    SettingSpec(
        domain=SettingDomain.features,
        key="enable_project_accounting",
        env_var=None,
        value_type=SettingValueType.boolean,
        default=False,
    ),
    SettingSpec(
        domain=SettingDomain.features,
        key="enable_bank_reconciliation",
        env_var=None,
        value_type=SettingValueType.boolean,
        default=True,
    ),
    SettingSpec(
        domain=SettingDomain.features,
        key="enable_recurring_transactions",
        env_var=None,
        value_type=SettingValueType.boolean,
        default=True,
    ),
    SettingSpec(
        domain=SettingDomain.features,
        key="enable_inventory",
        env_var=None,
        value_type=SettingValueType.boolean,
        default=True,
    ),
    SettingSpec(
        domain=SettingDomain.features,
        key="enable_fixed_assets",
        env_var=None,
        value_type=SettingValueType.boolean,
        default=True,
    ),
    SettingSpec(
        domain=SettingDomain.features,
        key="enable_leases",
        env_var=None,
        value_type=SettingValueType.boolean,
        default=False,
    ),
    SettingSpec(
        domain=SettingDomain.features,
        key="enable_procurement",
        env_var=None,
        value_type=SettingValueType.boolean,
        default=True,
    ),
    SettingSpec(
        domain=SettingDomain.features,
        key="enable_ipsas",
        env_var=None,
        value_type=SettingValueType.boolean,
        default=False,
    ),
    SettingSpec(
        domain=SettingDomain.features,
        key="enable_fund_accounting",
        env_var=None,
        value_type=SettingValueType.boolean,
        default=False,
    ),
    # Reporting Domain Settings
    SettingSpec(
        domain=SettingDomain.reporting,
        key="default_export_format",
        env_var=None,
        value_type=SettingValueType.string,
        default="PDF",
        allowed={"PDF", "EXCEL", "CSV"},
    ),
    SettingSpec(
        domain=SettingDomain.reporting,
        key="report_page_size",
        env_var=None,
        value_type=SettingValueType.string,
        default="A4",
        allowed={"A4", "LETTER", "LEGAL"},
    ),
    SettingSpec(
        domain=SettingDomain.reporting,
        key="report_orientation",
        env_var=None,
        value_type=SettingValueType.string,
        default="PORTRAIT",
        allowed={"PORTRAIT", "LANDSCAPE"},
    ),
    SettingSpec(
        domain=SettingDomain.reporting,
        key="include_logo_in_reports",
        env_var=None,
        value_type=SettingValueType.boolean,
        default=True,
    ),
    SettingSpec(
        domain=SettingDomain.reporting,
        key="report_watermark_text",
        env_var=None,
        value_type=SettingValueType.string,
        default=None,
    ),
    # Payments Domain Settings (Paystack Integration)
    SettingSpec(
        domain=SettingDomain.payments,
        key="paystack_enabled",
        env_var="PAYSTACK_ENABLED",
        value_type=SettingValueType.boolean,
        default=False,
    ),
    SettingSpec(
        domain=SettingDomain.payments,
        key="paystack_public_key",
        env_var="PAYSTACK_PUBLIC_KEY",
        value_type=SettingValueType.string,
        default=None,
    ),
    SettingSpec(
        domain=SettingDomain.payments,
        key="paystack_secret_key",
        env_var="PAYSTACK_SECRET_KEY",
        value_type=SettingValueType.string,
        default=None,
        is_secret=True,
    ),
    SettingSpec(
        domain=SettingDomain.payments,
        key="paystack_webhook_secret",
        env_var="PAYSTACK_WEBHOOK_SECRET",
        value_type=SettingValueType.string,
        default=None,
        is_secret=True,
    ),
    SettingSpec(
        domain=SettingDomain.payments,
        key="paystack_callback_base_url",
        env_var="PAYSTACK_CALLBACK_BASE_URL",
        value_type=SettingValueType.string,
        default=None,
    ),
    # Paystack Bank Account Linkage
    # Bank account UUID where Paystack collections are settled
    SettingSpec(
        domain=SettingDomain.payments,
        key="paystack_collection_bank_account_id",
        env_var=None,
        value_type=SettingValueType.string,
        default=None,
    ),
    # Bank account UUID used as source for Paystack transfers (payouts)
    SettingSpec(
        domain=SettingDomain.payments,
        key="paystack_transfer_bank_account_id",
        env_var=None,
        value_type=SettingValueType.string,
        default=None,
    ),
    # Enable Paystack Transfer API for expense reimbursements
    SettingSpec(
        domain=SettingDomain.payments,
        key="paystack_transfers_enabled",
        env_var="PAYSTACK_TRANSFERS_ENABLED",
        value_type=SettingValueType.boolean,
        default=False,
    ),
    # GL Account for posting transfer fees (bank charges)
    SettingSpec(
        domain=SettingDomain.payments,
        key="paystack_transfer_fee_account_id",
        env_var=None,
        value_type=SettingValueType.string,
        default=None,
    ),
    # Module Settings: Support
    SettingSpec(
        domain=SettingDomain.support,
        key="support_default_sla_response_hours",
        env_var=None,
        value_type=SettingValueType.integer,
        default=24,
        min_value=1,
        max_value=168,
        label="Default SLA Response Time (hours)",
        description="Time allowed for initial response to tickets",
    ),
    SettingSpec(
        domain=SettingDomain.support,
        key="support_default_sla_resolution_hours",
        env_var=None,
        value_type=SettingValueType.integer,
        default=72,
        min_value=1,
        max_value=720,
        label="Default SLA Resolution Time (hours)",
        description="Time allowed for ticket resolution",
    ),
    SettingSpec(
        domain=SettingDomain.support,
        key="support_auto_assignment_enabled",
        env_var=None,
        value_type=SettingValueType.boolean,
        default=False,
        label="Auto-assignment Enabled",
        description="Automatically assign tickets to available agents",
    ),
    SettingSpec(
        domain=SettingDomain.support,
        key="support_ticket_prefix",
        env_var=None,
        value_type=SettingValueType.string,
        default="TKT",
        label="Ticket Number Prefix",
        description="Prefix for support ticket numbers",
    ),
    # Module Settings: Inventory
    SettingSpec(
        domain=SettingDomain.inventory,
        key="inventory_low_stock_threshold_percent",
        env_var=None,
        value_type=SettingValueType.integer,
        default=20,
        min_value=1,
        max_value=100,
        label="Low Stock Threshold (%)",
        description="Percentage of minimum stock level to trigger alerts",
    ),
    SettingSpec(
        domain=SettingDomain.inventory,
        key="inventory_default_warehouse_id",
        env_var=None,
        value_type=SettingValueType.string,
        default=None,
        label="Default Warehouse",
        description="Default warehouse for new inventory transactions",
    ),
    SettingSpec(
        domain=SettingDomain.inventory,
        key="inventory_enable_lot_tracking",
        env_var=None,
        value_type=SettingValueType.boolean,
        default=False,
        label="Enable Lot Tracking",
        description="Track inventory items by lot/batch number",
    ),
    SettingSpec(
        domain=SettingDomain.inventory,
        key="inventory_enable_serial_tracking",
        env_var=None,
        value_type=SettingValueType.boolean,
        default=False,
        label="Enable Serial Tracking",
        description="Track inventory items by serial number",
    ),
    # Module Settings: Projects
    SettingSpec(
        domain=SettingDomain.projects,
        key="project_default_status",
        env_var=None,
        value_type=SettingValueType.string,
        default="PLANNING",
        allowed={"PLANNING", "ACTIVE", "ON_HOLD"},
        label="Default Project Status",
        description="Initial status for new projects",
    ),
    SettingSpec(
        domain=SettingDomain.projects,
        key="project_enable_time_tracking",
        env_var=None,
        value_type=SettingValueType.boolean,
        default=True,
        label="Enable Time Tracking",
        description="Allow time entries on project tasks",
    ),
    SettingSpec(
        domain=SettingDomain.projects,
        key="project_task_prefix",
        env_var=None,
        value_type=SettingValueType.string,
        default="TASK",
        label="Task Number Prefix",
        description="Prefix for task numbers",
    ),
    # Module Settings: Fleet
    SettingSpec(
        domain=SettingDomain.fleet,
        key="fleet_reservation_lead_days",
        env_var=None,
        value_type=SettingValueType.integer,
        default=3,
        min_value=0,
        max_value=30,
        label="Minimum Reservation Lead (days)",
        description="Minimum lead time required before a reservation starts",
    ),
    SettingSpec(
        domain=SettingDomain.fleet,
        key="fleet_reservation_default_duration_hours",
        env_var=None,
        value_type=SettingValueType.integer,
        default=8,
        min_value=1,
        max_value=168,
        label="Default Reservation Duration (hours)",
        description="Default duration for new reservations",
    ),
    SettingSpec(
        domain=SettingDomain.fleet,
        key="fleet_require_driver_license",
        env_var=None,
        value_type=SettingValueType.boolean,
        default=True,
        label="Require Driver License",
        description="Require a license on file to create reservations",
    ),
    # Module Settings: Procurement
    SettingSpec(
        domain=SettingDomain.procurement,
        key="procurement_default_payment_terms_days",
        env_var=None,
        value_type=SettingValueType.integer,
        default=30,
        min_value=0,
        max_value=180,
        label="Default Payment Terms (days)",
        description="Default payment terms for purchase documents",
    ),
    SettingSpec(
        domain=SettingDomain.procurement,
        key="procurement_require_rfq_for_po",
        env_var=None,
        value_type=SettingValueType.boolean,
        default=False,
        label="Require RFQ Before PO",
        description="Require RFQ completion before creating purchase orders",
    ),
    SettingSpec(
        domain=SettingDomain.procurement,
        key="procurement_threshold_direct_max",
        env_var=None,
        value_type=SettingValueType.integer,
        default=2500000,
        min_value=0,
        label="Direct Procurement Threshold (NGN)",
        description="Maximum value for direct procurement method (PPA 2007 default: 2,500,000)",
    ),
    SettingSpec(
        domain=SettingDomain.procurement,
        key="procurement_threshold_selective_max",
        env_var=None,
        value_type=SettingValueType.integer,
        default=50000000,
        min_value=0,
        label="Selective Procurement Threshold (NGN)",
        description="Maximum value for selective procurement method (PPA 2007 default: 50,000,000)",
    ),
    SettingSpec(
        domain=SettingDomain.procurement,
        key="procurement_threshold_ministerial_max",
        env_var=None,
        value_type=SettingValueType.integer,
        default=1000000000,
        min_value=0,
        label="Ministerial Threshold (NGN)",
        description="Maximum value for Ministerial Tenders Board (PPA 2007 default: 1,000,000,000)",
    ),
    # Payroll Domain Settings
    SettingSpec(
        domain=SettingDomain.payroll,
        key="auto_generate_enabled",
        env_var=None,
        value_type=SettingValueType.boolean,
        default=False,
    ),
    SettingSpec(
        domain=SettingDomain.payroll,
        key="auto_generate_days_before",
        env_var=None,
        value_type=SettingValueType.integer,
        default=5,
        min_value=1,
        max_value=15,
    ),
    SettingSpec(
        domain=SettingDomain.payroll,
        key="auto_generate_notify_emails",
        env_var=None,
        value_type=SettingValueType.json,
        default=[],
    ),
    SettingSpec(
        domain=SettingDomain.payroll,
        key="auto_post_gl_on_approval",
        env_var=None,
        value_type=SettingValueType.boolean,
        default=True,
    ),
    SettingSpec(
        domain=SettingDomain.payroll,
        key="payroll_rounding_account_id",
        env_var=None,
        value_type=SettingValueType.string,
        default=None,
    ),
]

DOMAIN_SETTINGS_SERVICE = {
    SettingDomain.auth: settings_service.auth_settings,
    SettingDomain.audit: settings_service.audit_settings,
    SettingDomain.scheduler: settings_service.scheduler_settings,
    SettingDomain.automation: settings_service.automation_settings,
    SettingDomain.email: settings_service.email_settings,
    SettingDomain.features: settings_service.features_settings,
    SettingDomain.reporting: settings_service.reporting_settings,
    SettingDomain.payments: settings_service.payments_settings,
    SettingDomain.support: settings_service.support_settings,
    SettingDomain.inventory: settings_service.inventory_settings,
    SettingDomain.projects: settings_service.projects_settings,
    SettingDomain.fleet: settings_service.fleet_settings,
    SettingDomain.procurement: settings_service.procurement_settings,
    SettingDomain.settings: settings_service.settings_settings,
    SettingDomain.payroll: settings_service.payroll_settings,
}


def get_spec(domain: SettingDomain, key: str) -> SettingSpec | None:
    for spec in SETTINGS_SPECS:
        if spec.domain == domain and spec.key == key:
            return spec
    return None


def list_specs(domain: SettingDomain) -> list[SettingSpec]:
    return [spec for spec in SETTINGS_SPECS if spec.domain == domain]


def resolve_value(
    db, domain: SettingDomain, key: str, strict: bool = False
) -> object | None:
    """
    Resolve a setting value from database, falling back to spec defaults.

    Args:
        db: Database session
        domain: Setting domain
        key: Setting key
        strict: If True, raise ValueError for required settings that are missing
                or have no default. Use strict=True during startup validation.

    Returns:
        Resolved setting value, or None if not found and no default

    Raises:
        ValueError: If strict=True and a required setting is missing/invalid
    """
    spec = get_spec(domain, key)
    if not spec:
        if strict:
            raise ValueError(f"Unknown setting: {domain.value}/{key}")
        return None

    service = DOMAIN_SETTINGS_SERVICE.get(domain)
    setting = None
    db_error = None
    if service:
        try:
            setting = service.get_by_key(db, key)
        except HTTPException as e:
            db_error = e
            setting = None

    raw = extract_db_value(setting)

    # For required settings with no value and no default, fail in strict mode
    if raw is None and spec.required and spec.default is None:
        if strict:
            raise ValueError(
                f"Required setting '{domain.value}/{key}' is not configured "
                f"and has no default value"
            )
        # In non-strict mode, log a warning but continue
        import logging

        logging.getLogger(__name__).warning(
            "Required setting %s/%s is missing (no DB value, no default)",
            domain.value,
            key,
        )

    if raw is None:
        raw = spec.default

    value, error = coerce_value(spec, raw)
    if error:
        if strict:
            raise ValueError(f"Invalid value for {domain.value}/{key}: {error}")
        value = spec.default

    if spec.allowed and value is not None and value not in spec.allowed:
        if strict:
            allowed_str = ", ".join(str(v) for v in spec.allowed)
            raise ValueError(
                f"Invalid value '{value}' for {domain.value}/{key}. "
                f"Allowed: {allowed_str}"
            )
        value = spec.default

    if spec.value_type == SettingValueType.integer and value is not None:
        parsed: int | None
        try:
            parsed = int(str(value))
        except (TypeError, ValueError):
            if strict:
                raise ValueError(
                    f"Setting {domain.value}/{key} must be an integer, got: {value}"
                )
            parsed = spec.default if isinstance(spec.default, int) else None
        if (
            spec.min_value is not None
            and parsed is not None
            and parsed < spec.min_value
        ):
            if strict:
                raise ValueError(
                    f"Setting {domain.value}/{key} must be >= {spec.min_value}, got: {parsed}"
                )
            parsed = spec.default if isinstance(spec.default, int) else None
        if (
            spec.max_value is not None
            and parsed is not None
            and parsed > spec.max_value
        ):
            if strict:
                raise ValueError(
                    f"Setting {domain.value}/{key} must be <= {spec.max_value}, got: {parsed}"
                )
            parsed = spec.default if isinstance(spec.default, int) else None
        value = parsed

    return value


def extract_db_value(setting) -> object | None:
    if not setting:
        return None
    if setting.value_text is not None:
        return cast(object, setting.value_text)
    if setting.value_json is not None:
        return cast(object, setting.value_json)
    return None


def coerce_value(spec: SettingSpec, raw: object) -> tuple[object | None, str | None]:
    if raw is None:
        return None, None
    if spec.value_type == SettingValueType.boolean:
        if isinstance(raw, bool):
            return raw, None
        if isinstance(raw, str):
            normalized = raw.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True, None
            if normalized in {"0", "false", "no", "off"}:
                return False, None
        return None, "Value must be boolean"
    if spec.value_type == SettingValueType.integer:
        if isinstance(raw, int):
            return raw, None
        if isinstance(raw, str):
            try:
                return int(raw), None
            except ValueError:
                return None, "Value must be an integer"
        return None, "Value must be an integer"
    if spec.value_type == SettingValueType.string:
        if isinstance(raw, str):
            return raw, None
        return str(raw), None
    return raw, None


def normalize_for_db(
    spec: SettingSpec, value: object
) -> tuple[str | None, object | None]:
    if spec.value_type == SettingValueType.boolean:
        bool_value = bool(value)
        return ("true" if bool_value else "false"), bool_value
    if spec.value_type == SettingValueType.integer:
        return str(int(str(value))), None
    if spec.value_type == SettingValueType.string:
        return str(value), None
    return None, value


def validate_required_settings(db) -> list[str]:
    """
    Validate all required settings are configured.

    Call this during application startup to catch missing configuration early.

    Args:
        db: Database session

    Returns:
        List of error messages for missing/invalid required settings.
        Empty list if all required settings are valid.
    """
    errors = []
    required_specs = [spec for spec in SETTINGS_SPECS if spec.required]

    for spec in required_specs:
        try:
            value = resolve_value(db, spec.domain, spec.key, strict=True)
            if value is None and spec.default is None:
                errors.append(
                    f"Required setting '{spec.domain.value}/{spec.key}' is not configured"
                )
        except ValueError as e:
            errors.append(str(e))

    return errors
