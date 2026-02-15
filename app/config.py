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
    avatar_max_size_bytes: int = int(
        os.getenv("AVATAR_MAX_SIZE_BYTES", str(2 * 1024 * 1024))
    )  # 2MB
    avatar_allowed_types: str = os.getenv(
        "AVATAR_ALLOWED_TYPES", "image/jpeg,image/png,image/gif,image/webp"
    )
    avatar_url_prefix: str = os.getenv("AVATAR_URL_PREFIX", "/static/avatars")

    # Branding asset uploads
    branding_upload_dir: str = os.getenv("BRANDING_UPLOAD_DIR", "static/branding")
    branding_max_size_bytes: int = int(
        os.getenv("BRANDING_MAX_SIZE_BYTES", str(5 * 1024 * 1024))
    )  # 5MB
    branding_allowed_types: str = os.getenv(
        "BRANDING_ALLOWED_TYPES",
        "image/jpeg,image/png,image/gif,image/webp,image/svg+xml,image/x-icon,image/vnd.microsoft.icon",
    )
    branding_url_prefix: str = os.getenv("BRANDING_URL_PREFIX", "/static/branding")

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
    crm_api_url: str = os.getenv("CRM_API_URL", "")
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
    # CRM inventory webhook URL (for pushing inventory updates TO CRM)
    crm_inventory_webhook_url: str | None = (
        os.getenv("CRM_INVENTORY_WEBHOOK_URL") or None
    )
    # Push inventory changes when stock changes by this percentage (0 = push all changes)
    crm_inventory_push_threshold_percent: int = int(
        os.getenv("CRM_INVENTORY_PUSH_THRESHOLD_PERCENT", "10")
    )

    # ==========================================================================
    # Remita Integration (RRR for government payments)
    # ==========================================================================
    # Remita merchant ID
    remita_merchant_id: str = os.getenv("REMITA_MERCHANT_ID", "")
    # Remita API key
    remita_api_key: str = os.getenv("REMITA_API_KEY", "")
    # Production mode (True for live, False for demo/sandbox)
    remita_is_live: bool = os.getenv("REMITA_IS_LIVE", "false").lower() == "true"

    # ==========================================================================
    # Splynx Integration (ISP billing - selfcare.dotmac.ng)
    # ==========================================================================
    # Splynx API base URL
    splynx_api_url: str = os.getenv("SPLYNX_API_URL", "")
    # Splynx API key (first part of Basic auth)
    splynx_api_key: str = os.getenv("SPLYNX_API_KEY", "")
    # Splynx API secret (second part of Basic auth)
    splynx_api_secret: str = os.getenv("SPLYNX_API_SECRET", "")
    # Request timeout in seconds
    splynx_request_timeout: float = float(os.getenv("SPLYNX_REQUEST_TIMEOUT", "60.0"))
    # Max retries for failed requests
    splynx_max_retries: int = int(os.getenv("SPLYNX_MAX_RETRIES", "3"))

    # ==========================================================================
    # Analytics (pre-computed metric snapshots)
    # ==========================================================================
    analytics_enabled: bool = os.getenv("ANALYTICS_ENABLED", "false").lower() == "true"

    # ==========================================================================
    # Coach / Intelligence Engine (hosted Llama + DeepSeek)
    # ==========================================================================
    coach_enabled: bool = os.getenv("COACH_ENABLED", "false").lower() == "true"

    # Backends are expected to expose an OpenAI-compatible Chat Completions API.
    coach_llm_backends: str = os.getenv("COACH_LLM_BACKENDS", "llama,deepseek")
    coach_llm_default_backend: str = os.getenv("COACH_LLM_DEFAULT_BACKEND", "deepseek")
    coach_llm_fast_backend: str = os.getenv("COACH_LLM_FAST_BACKEND", "llama")
    coach_llm_standard_backend: str = os.getenv(
        "COACH_LLM_STANDARD_BACKEND", "deepseek"
    )
    coach_llm_deep_backend: str = os.getenv("COACH_LLM_DEEP_BACKEND", "deepseek")

    # Llama backend
    coach_llm_llama_base_url: str = os.getenv("COACH_LLM_LLAMA_BASE_URL", "")
    coach_llm_llama_api_key: str = os.getenv("COACH_LLM_LLAMA_API_KEY", "")
    coach_llm_llama_model_fast: str = os.getenv("COACH_LLM_LLAMA_MODEL_FAST", "")
    coach_llm_llama_model_standard: str = os.getenv(
        "COACH_LLM_LLAMA_MODEL_STANDARD", ""
    )
    coach_llm_llama_model_deep: str = os.getenv("COACH_LLM_LLAMA_MODEL_DEEP", "")

    # DeepSeek backend
    coach_llm_deepseek_base_url: str = os.getenv("COACH_LLM_DEEPSEEK_BASE_URL", "")
    coach_llm_deepseek_api_key: str = os.getenv("COACH_LLM_DEEPSEEK_API_KEY", "")
    coach_llm_deepseek_model_fast: str = os.getenv("COACH_LLM_DEEPSEEK_MODEL_FAST", "")
    coach_llm_deepseek_model_standard: str = os.getenv(
        "COACH_LLM_DEEPSEEK_MODEL_STANDARD", ""
    )
    coach_llm_deepseek_model_deep: str = os.getenv("COACH_LLM_DEEPSEEK_MODEL_DEEP", "")

    # Reliability + safety
    coach_llm_timeout_s: int = int(os.getenv("COACH_LLM_TIMEOUT_S", "30"))
    coach_llm_max_retries: int = int(os.getenv("COACH_LLM_MAX_RETRIES", "2"))
    coach_llm_max_output_tokens: int = int(
        os.getenv("COACH_LLM_MAX_OUTPUT_TOKENS", "1200")
    )

    # Budgeting + caching
    coach_monthly_token_budget: int = int(
        os.getenv("COACH_MONTHLY_TOKEN_BUDGET", "500000")
    )
    coach_cache_ttl_hours: int = int(os.getenv("COACH_CACHE_TTL_HOURS", "24"))
    coach_max_insights_per_run: int = int(os.getenv("COACH_MAX_INSIGHTS_PER_RUN", "20"))


settings = Settings()
