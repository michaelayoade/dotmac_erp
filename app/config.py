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



settings = Settings()
