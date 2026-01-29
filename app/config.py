import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg://postgres:postgres@localhost:5434/dotmac_erp",
    )
    db_pool_size: int = int(os.getenv("DB_POOL_SIZE", "5"))
    db_max_overflow: int = int(os.getenv("DB_MAX_OVERFLOW", "10"))
    db_pool_timeout: int = int(os.getenv("DB_POOL_TIMEOUT", "30"))
    db_pool_recycle: int = int(os.getenv("DB_POOL_RECYCLE", "1800"))
    # Statement timeout in milliseconds (default 30s, 0 = disabled)
    db_statement_timeout_ms: int = int(os.getenv("DB_STATEMENT_TIMEOUT_MS", "30000"))

    # Avatar settings
    avatar_upload_dir: str = os.getenv("AVATAR_UPLOAD_DIR", "static/avatars")
    avatar_max_size_bytes: int = int(os.getenv("AVATAR_MAX_SIZE_BYTES", str(2 * 1024 * 1024)))  # 2MB
    avatar_allowed_types: str = os.getenv("AVATAR_ALLOWED_TYPES", "image/jpeg,image/png,image/gif,image/webp")
    avatar_url_prefix: str = os.getenv("AVATAR_URL_PREFIX", "/static/avatars")

    # Branding
    brand_name: str = os.getenv("BRAND_NAME", "Dotmac ERP")
    brand_tagline: str = os.getenv(
        "BRAND_TAGLINE",
        "Unified ERP for finance, HR, and operations",
    )
    brand_logo_url: str | None = os.getenv("BRAND_LOGO_URL") or None
    brand_mark: str | None = os.getenv("BRAND_MARK") or None  # Auto-derived if not set

    # Single organization mode - use this org for all operations
    # Set to a UUID to enable single-org mode (no org selection needed)
    default_organization_id: str | None = os.getenv("DEFAULT_ORGANIZATION_ID") or None

    # Default currency (used for admin org creation when no org context)
    default_functional_currency_code: str = os.getenv(
        "DEFAULT_FUNCTIONAL_CURRENCY_CODE",
        "NGN",
    )
    default_presentation_currency_code: str = os.getenv(
        "DEFAULT_PRESENTATION_CURRENCY_CODE",
        "NGN",
    )

    # Landing page content (configurable without code changes)
    landing_hero_badge: str = os.getenv("LANDING_HERO_BADGE", "Dotmac ERP")
    landing_hero_title: str = os.getenv(
        "LANDING_HERO_TITLE", "Run your entire business on one ERP"
    )
    landing_hero_subtitle: str = os.getenv(
        "LANDING_HERO_SUBTITLE",
        "Finance, HR, and operations with real-time reporting.",
    )
    landing_cta_primary: str = os.getenv("LANDING_CTA_PRIMARY", "Get started")
    landing_cta_secondary: str = os.getenv("LANDING_CTA_SECONDARY", "Explore modules")
    landing_content_json: str | None = os.getenv("LANDING_CONTENT_JSON") or None

    # Resume uploads (careers portal)
    resume_upload_dir: str = os.getenv("RESUME_UPLOAD_DIR", "uploads/resumes")
    resume_max_size_bytes: int = int(
        os.getenv("RESUME_MAX_SIZE_BYTES", str(5 * 1024 * 1024))
    )  # 5MB default
    resume_allowed_extensions: str = os.getenv(
        "RESUME_ALLOWED_EXTENSIONS", ".pdf,.doc,.docx"
    )

    # CAPTCHA (Cloudflare Turnstile)
    captcha_site_key: str | None = os.getenv("CAPTCHA_SITE_KEY") or None
    captcha_secret_key: str | None = os.getenv("CAPTCHA_SECRET_KEY") or None

    # Generated documents storage
    generated_docs_dir: str = os.getenv("GENERATED_DOCS_DIR", "uploads/generated_docs")

    # Application URL (for email links)
    app_url: str = os.getenv("APP_URL", "http://localhost:8000")

    # SSO Configuration
    # Enable SSO for cross-app authentication under same parent domain
    sso_enabled: bool = os.getenv("SSO_ENABLED", "false").lower() == "true"
    # True for App #1 (hosts auth database), False for App #2/#3 (SSO clients)
    sso_provider_mode: bool = os.getenv("SSO_PROVIDER_MODE", "false").lower() == "true"
    # Shared auth database URL for SSO clients (connects to App #1's database)
    # If not set, uses main DATABASE_URL
    auth_database_url: str | None = os.getenv("AUTH_DATABASE_URL") or None
    # Cookie domain for cross-app SSO (e.g., ".company.com")
    sso_cookie_domain: str | None = os.getenv("SSO_COOKIE_DOMAIN") or None
    # Shared JWT secret (must be same across all apps)
    # Falls back to JWT_SECRET environment variable if not set
    sso_jwt_secret: str | None = os.getenv("SSO_JWT_SECRET") or None
    # SSO Provider URL for login redirects (e.g., "https://sso.company.com")
    sso_provider_url: str | None = os.getenv("SSO_PROVIDER_URL") or None

    # ==========================================================================
    # CRM Integration (crm.dotmac.io)
    # ==========================================================================
    # CRM API base URL
    crm_api_url: str = os.getenv("CRM_API_URL", "https://crm.dotmac.io/api/v1")
    # CRM API authentication token
    crm_api_token: str | None = os.getenv("CRM_API_TOKEN") or None
    # CRM webhook secret for validating incoming webhooks
    crm_webhook_secret: str | None = os.getenv("CRM_WEBHOOK_SECRET") or None
    # CRM sync interval in minutes (for periodic pull)
    crm_sync_interval_minutes: int = int(os.getenv("CRM_SYNC_INTERVAL_MINUTES", "15"))
    # CRM request timeout in seconds
    crm_request_timeout: float = float(os.getenv("CRM_REQUEST_TIMEOUT", "30.0"))
    # CRM max retries for failed requests
    crm_max_retries: int = int(os.getenv("CRM_MAX_RETRIES", "3"))


settings = Settings()
