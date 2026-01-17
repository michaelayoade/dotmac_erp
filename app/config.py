import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg://postgres:postgres@localhost:5434/dotmac_books",
    )
    db_pool_size: int = int(os.getenv("DB_POOL_SIZE", "5"))
    db_max_overflow: int = int(os.getenv("DB_MAX_OVERFLOW", "10"))
    db_pool_timeout: int = int(os.getenv("DB_POOL_TIMEOUT", "30"))
    db_pool_recycle: int = int(os.getenv("DB_POOL_RECYCLE", "1800"))

    # Avatar settings
    avatar_upload_dir: str = os.getenv("AVATAR_UPLOAD_DIR", "static/avatars")
    avatar_max_size_bytes: int = int(os.getenv("AVATAR_MAX_SIZE_BYTES", str(2 * 1024 * 1024)))  # 2MB
    avatar_allowed_types: str = os.getenv("AVATAR_ALLOWED_TYPES", "image/jpeg,image/png,image/gif,image/webp")
    avatar_url_prefix: str = os.getenv("AVATAR_URL_PREFIX", "/static/avatars")

    # Branding
    brand_name: str = os.getenv("BRAND_NAME", "DotMac Books")
    brand_tagline: str = os.getenv("BRAND_TAGLINE", "IFRS accounting that closes faster")
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
    landing_hero_badge: str = os.getenv("LANDING_HERO_BADGE", "IFRS-ready accounting")
    landing_hero_title: str = os.getenv(
        "LANDING_HERO_TITLE", "Close faster with audit-ready accounting"
    )
    landing_hero_subtitle: str = os.getenv(
        "LANDING_HERO_SUBTITLE",
        "Multi-entity support, clean audit trail, and accurate AR/AP aging for growing finance teams.",
    )
    landing_cta_primary: str = os.getenv("LANDING_CTA_PRIMARY", "Start trial")
    landing_cta_secondary: str = os.getenv("LANDING_CTA_SECONDARY", "View sample reports")
    landing_content_json: str | None = os.getenv("LANDING_CONTENT_JSON") or None



settings = Settings()
