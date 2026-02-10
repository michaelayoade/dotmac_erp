"""
Branding Service.

Per-organization branding configuration with CSS generation.
Provides CRUD operations for OrganizationBranding and dynamic CSS generation.
"""

from __future__ import annotations

import colorsys
import logging
import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.core_org import Organization, OrganizationBranding
from app.schemas.finance.branding import (
    BrandingCreate,
    BrandingUpdate,
    ColorPaletteResponse,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Color Utilities
# ─────────────────────────────────────────────────────────────────────────────


def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """Convert hex color to RGB tuple."""
    hex_color = hex_color.lstrip("#")
    return (
        int(hex_color[0:2], 16),
        int(hex_color[2:4], 16),
        int(hex_color[4:6], 16),
    )


def rgb_to_hex(r: int, g: int, b: int) -> str:
    """Convert RGB to hex color."""
    return f"#{r:02X}{g:02X}{b:02X}"


def hex_to_hsl(hex_color: str) -> tuple[float, float, float]:
    """Convert hex color to HSL (hue 0-360, saturation 0-1, lightness 0-1)."""
    r, g, b = hex_to_rgb(hex_color)
    rf = r / 255.0
    gf = g / 255.0
    bf = b / 255.0
    h, l, s = colorsys.rgb_to_hls(rf, gf, bf)
    return h * 360, s, l


def hsl_to_hex(h: float, s: float, l: float) -> str:
    """Convert HSL to hex color (hue 0-360, saturation 0-1, lightness 0-1)."""
    h = h / 360
    s = max(0, min(1, s))
    l = max(0, min(1, l))
    r, g, b = colorsys.hls_to_rgb(h, l, s)
    return rgb_to_hex(int(r * 255), int(g * 255), int(b * 255))


def generate_color_palette(base_color: str) -> ColorPaletteResponse:
    """
    Generate a full color palette from a base color.

    Uses HSL color space to create consistent shades from light (50) to dark (950).
    The base color is placed at shade 500.
    """
    h, s, l = hex_to_hsl(base_color)

    # Generate shades - lighter to darker
    # Shade 50 is very light (95% lightness), 950 is very dark (10% lightness)
    shades = {
        50: hsl_to_hex(h, s * 0.3, 0.97),
        100: hsl_to_hex(h, s * 0.4, 0.94),
        200: hsl_to_hex(h, s * 0.5, 0.86),
        300: hsl_to_hex(h, s * 0.6, 0.74),
        400: hsl_to_hex(h, s * 0.8, 0.60),
        500: base_color.upper(),  # Original color
        600: hsl_to_hex(h, s * 1.0, l * 0.85),
        700: hsl_to_hex(h, s * 1.05, l * 0.70),
        800: hsl_to_hex(h, s * 1.1, l * 0.55),
        900: hsl_to_hex(h, s * 1.15, l * 0.40),
        950: hsl_to_hex(h, s * 1.2, l * 0.25),
    }

    return ColorPaletteResponse(
        base=base_color.upper(),
        shade_50=shades[50],
        shade_100=shades[100],
        shade_200=shades[200],
        shade_300=shades[300],
        shade_400=shades[400],
        shade_500=shades[500],
        shade_600=shades[600],
        shade_700=shades[700],
        shade_800=shades[800],
        shade_900=shades[900],
        shade_950=shades[950],
    )


def derive_brand_mark(name: str) -> str:
    """
    Derive a 2-letter brand mark from a name.

    Examples:
        "Acme Corporation" -> "AC"
        "DotMac" -> "DM"
        "XYZ" -> "XY"
    """
    if not name:
        return "??"

    # Split into words and take first letter of each
    words = [str(w) for w in re.findall(r"[A-Z][a-z]*|[a-z]+", name)]
    if len(words) >= 2:
        return (words[0][0] + words[1][0]).upper()
    elif len(words) == 1:
        word = words[0]
        if len(word) >= 2:
            return word[:2].upper()
        return (word[0] * 2).upper()
    return name[:2].upper()


# ─────────────────────────────────────────────────────────────────────────────
# CSS Generator
# ─────────────────────────────────────────────────────────────────────────────


class CSSGenerator:
    """
    Generates dynamic CSS from branding configuration.

    Produces CSS custom properties that override the default theme,
    enabling per-organization visual customization.
    """

    # Default values matching the ERP's base theme
    DEFAULTS = {
        "primary": "#0D9488",  # Teal
        "primary_light": "#CCFBF1",
        "primary_dark": "#115E59",
        "accent": "#D97706",  # Gold
        "accent_light": "#FEF3C7",
        "accent_dark": "#92400E",
        "success": "#10B981",  # Emerald
        "warning": "#F59E0B",  # Amber
        "danger": "#EF4444",  # Rose
        "font_display": "DM Sans",
        "font_body": "DM Sans",
        "font_mono": "JetBrains Mono",
        "border_radius": "8px",
    }

    BORDER_RADIUS_MAP = {
        "sharp": "0px",
        "rounded": "8px",
        "pill": "16px",
    }

    def __init__(self, branding: OrganizationBranding | None = None):
        self.branding = branding

    def generate(self) -> str:
        """Generate complete CSS for the branding configuration."""
        branding = self.branding
        if not branding:
            return ""

        lines = []
        lines.append("/* Organization Branding - Auto-generated */")
        lines.append(":root {")

        # Primary color
        if branding.primary_color:
            palette = generate_color_palette(branding.primary_color)
            lines.append(f"  --teal: {palette.shade_500};")
            lines.append(
                f"  --teal-light: {branding.primary_light or palette.shade_100};"
            )
            lines.append(
                f"  --teal-dark: {branding.primary_dark or palette.shade_700};"
            )
            lines.append(f"  --brand-primary: {palette.shade_500};")
            lines.append(f"  --brand-primary-50: {palette.shade_50};")
            lines.append(f"  --brand-primary-100: {palette.shade_100};")
            lines.append(f"  --brand-primary-200: {palette.shade_200};")
            lines.append(f"  --brand-primary-500: {palette.shade_500};")
            lines.append(f"  --brand-primary-600: {palette.shade_600};")
            lines.append(f"  --brand-primary-700: {palette.shade_700};")
            lines.append(f"  --brand-primary-900: {palette.shade_900};")

        # Accent color
        if branding.accent_color:
            palette = generate_color_palette(branding.accent_color)
            lines.append(f"  --gold: {palette.shade_500};")
            lines.append(
                f"  --gold-light: {branding.accent_light or palette.shade_100};"
            )
            lines.append(f"  --gold-dark: {branding.accent_dark or palette.shade_700};")
            lines.append(f"  --brand-accent: {palette.shade_500};")

        # Semantic colors
        if branding.success_color:
            lines.append(f"  --brand-success: {branding.success_color};")
        if branding.warning_color:
            lines.append(f"  --brand-warning: {branding.warning_color};")
        if branding.danger_color:
            lines.append(f"  --brand-danger: {branding.danger_color};")

        # Typography
        if branding.font_family_display:
            lines.append(
                f'  --font-display: "{branding.font_family_display}", system-ui, sans-serif;'
            )
        if branding.font_family_body:
            lines.append(
                f'  --font-body: "{branding.font_family_body}", system-ui, sans-serif;'
            )
        if branding.font_family_mono:
            lines.append(f'  --font-mono: "{branding.font_family_mono}", monospace;')

        # Border radius
        if branding.border_radius:
            radius = self.BORDER_RADIUS_MAP.get(branding.border_radius.value, "8px")
            lines.append(f"  --border-radius-base: {radius};")
            lines.append(f"  --border-radius-card: {radius};")
            lines.append(f"  --border-radius-btn: {radius};")

        lines.append("}")

        # Button style overrides
        if branding.button_style:
            lines.extend(self._generate_button_styles())

        # Sidebar style overrides
        if branding.sidebar_style:
            lines.extend(self._generate_sidebar_styles())

        # Custom CSS injection
        if branding.custom_css:
            lines.append("")
            lines.append("/* Custom CSS */")
            lines.append(branding.custom_css)

        return "\n".join(lines)

    def _generate_button_styles(self) -> list[str]:
        """Generate button style overrides."""
        branding = self.branding
        if not branding:
            return []
        lines = []
        style = branding.button_style.value if branding.button_style else "gradient"

        if style == "solid":
            lines.append("")
            lines.append("/* Button Style: Solid */")
            lines.append(".btn-primary, .btn-teal {")
            lines.append("  background: var(--brand-primary, var(--teal)) !important;")
            lines.append("  background-image: none !important;")
            lines.append("}")
        elif style == "outline":
            lines.append("")
            lines.append("/* Button Style: Outline */")
            lines.append(".btn-primary, .btn-teal {")
            lines.append("  background: transparent !important;")
            lines.append(
                "  border: 2px solid var(--brand-primary, var(--teal)) !important;"
            )
            lines.append("  color: var(--brand-primary, var(--teal)) !important;")
            lines.append("}")
            lines.append(".btn-primary:hover, .btn-teal:hover {")
            lines.append("  background: var(--brand-primary, var(--teal)) !important;")
            lines.append("  color: white !important;")
            lines.append("}")
        # gradient is default, no override needed

        return lines

    def _generate_sidebar_styles(self) -> list[str]:
        """Generate sidebar style overrides."""
        branding = self.branding
        if not branding:
            return []
        lines = []
        style = branding.sidebar_style.value if branding.sidebar_style else "dark"

        if style == "light":
            lines.append("")
            lines.append("/* Sidebar Style: Light */")
            lines.append(".sidebar, [data-sidebar] {")
            lines.append("  background: var(--parchment, #fafaf9) !important;")
            lines.append("  border-right: 1px solid var(--card-border) !important;")
            lines.append("}")
            lines.append(".sidebar a, [data-sidebar] a {")
            lines.append("  color: var(--ink, #0f172a) !important;")
            lines.append("}")
        elif style == "brand":
            lines.append("")
            lines.append("/* Sidebar Style: Brand */")
            lines.append(".sidebar, [data-sidebar] {")
            lines.append(
                "  background: var(--brand-primary-700, var(--teal-dark)) !important;"
            )
            lines.append("}")
            lines.append(".sidebar a, [data-sidebar] a {")
            lines.append("  color: rgba(255, 255, 255, 0.9) !important;")
            lines.append("}")
            lines.append(".sidebar a:hover, [data-sidebar] a:hover {")
            lines.append("  background: rgba(255, 255, 255, 0.1) !important;")
            lines.append("}")

        return lines

    def get_google_fonts_url(self) -> str | None:
        """Generate Google Fonts import URL for custom fonts."""
        fonts = []

        if self.branding and self.branding.font_family_display:
            font = self.branding.font_family_display.replace(" ", "+")
            fonts.append(f"{font}:wght@400;500;600;700")

        if self.branding and self.branding.font_family_body:
            font = self.branding.font_family_body.replace(" ", "+")
            if font not in [f.split(":")[0] for f in fonts]:
                fonts.append(f"{font}:wght@400;500;600")

        if self.branding and self.branding.font_family_mono:
            font = self.branding.font_family_mono.replace(" ", "+")
            fonts.append(f"{font}:wght@400;500")

        if not fonts:
            return None

        return f"https://fonts.googleapis.com/css2?family={'&family='.join(fonts)}&display=swap"


def generate_branding_css(branding: OrganizationBranding | None) -> str:
    """Convenience function to generate CSS from branding."""
    return CSSGenerator(branding).generate()


# ─────────────────────────────────────────────────────────────────────────────
# Branding Service
# ─────────────────────────────────────────────────────────────────────────────


class BrandingService:
    """
    Service for managing organization branding configurations.

    Provides CRUD operations and CSS generation for per-org theming.
    """

    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, branding_id: UUID) -> OrganizationBranding | None:
        """Get branding by ID."""
        return self.db.get(OrganizationBranding, branding_id)

    def get_by_org_id(self, org_id: UUID) -> OrganizationBranding | None:
        """Get branding for an organization."""
        stmt = select(OrganizationBranding).where(
            OrganizationBranding.organization_id == org_id
        )
        return self.db.execute(stmt).scalar_one_or_none()

    def get_or_create(
        self, org_id: UUID, user_id: UUID | None = None
    ) -> OrganizationBranding:
        """Get existing branding or create a new one with defaults."""
        branding = self.get_by_org_id(org_id)
        if branding:
            return branding

        # Get org for default display name
        org = self.db.get(Organization, org_id)
        display_name = org.trading_name or org.legal_name if org else None
        brand_mark = derive_brand_mark(display_name) if display_name else None

        branding = OrganizationBranding(
            organization_id=org_id,
            display_name=display_name,
            brand_mark=brand_mark,
            created_by_id=user_id,
        )
        self.db.add(branding)
        self.db.flush()
        return branding

    def create(
        self, data: BrandingCreate, user_id: UUID | None = None
    ) -> OrganizationBranding:
        """Create new branding configuration."""
        # Check if branding already exists
        existing = self.get_by_org_id(data.organization_id)
        if existing:
            raise ValueError(
                f"Branding already exists for organization {data.organization_id}"
            )

        # Auto-derive brand mark if not provided
        brand_mark = data.brand_mark
        if not brand_mark and data.display_name:
            brand_mark = derive_brand_mark(data.display_name)

        # Auto-generate color variants if base color provided but variants missing
        primary_light = data.primary_light
        primary_dark = data.primary_dark
        if data.primary_color and not (primary_light and primary_dark):
            palette = generate_color_palette(data.primary_color)
            primary_light = primary_light or palette.shade_100
            primary_dark = primary_dark or palette.shade_700

        accent_light = data.accent_light
        accent_dark = data.accent_dark
        if data.accent_color and not (accent_light and accent_dark):
            palette = generate_color_palette(data.accent_color)
            accent_light = accent_light or palette.shade_100
            accent_dark = accent_dark or palette.shade_700

        branding = OrganizationBranding(
            organization_id=data.organization_id,
            display_name=data.display_name,
            tagline=data.tagline,
            logo_url=data.logo_url,
            logo_dark_url=data.logo_dark_url,
            favicon_url=data.favicon_url,
            brand_mark=brand_mark,
            primary_color=data.primary_color,
            primary_light=primary_light,
            primary_dark=primary_dark,
            accent_color=data.accent_color,
            accent_light=accent_light,
            accent_dark=accent_dark,
            success_color=data.success_color,
            warning_color=data.warning_color,
            danger_color=data.danger_color,
            font_family_display=data.font_family_display,
            font_family_body=data.font_family_body,
            font_family_mono=data.font_family_mono,
            border_radius=data.border_radius,
            button_style=data.button_style,
            sidebar_style=data.sidebar_style,
            custom_css=data.custom_css,
            created_by_id=user_id,
        )
        self.db.add(branding)
        self.db.flush()
        self._invalidate_branding_cache(branding.organization_id)
        return branding

    def update(
        self,
        branding_id: UUID,
        data: BrandingUpdate,
    ) -> OrganizationBranding | None:
        """Update branding configuration."""
        branding = self.get_by_id(branding_id)
        if not branding:
            return None

        update_data = data.model_dump(exclude_unset=True)

        # Auto-derive brand mark if display name changed but brand mark not provided
        if "display_name" in update_data and "brand_mark" not in update_data:
            if update_data["display_name"] and not branding.brand_mark:
                update_data["brand_mark"] = derive_brand_mark(
                    update_data["display_name"]
                )

        # Auto-generate color variants if base color changed
        if "primary_color" in update_data and update_data["primary_color"]:
            if "primary_light" not in update_data or "primary_dark" not in update_data:
                palette = generate_color_palette(update_data["primary_color"])
                if "primary_light" not in update_data:
                    update_data["primary_light"] = palette.shade_100
                if "primary_dark" not in update_data:
                    update_data["primary_dark"] = palette.shade_700

        if "accent_color" in update_data and update_data["accent_color"]:
            if "accent_light" not in update_data or "accent_dark" not in update_data:
                palette = generate_color_palette(update_data["accent_color"])
                if "accent_light" not in update_data:
                    update_data["accent_light"] = palette.shade_100
                if "accent_dark" not in update_data:
                    update_data["accent_dark"] = palette.shade_700

        for field, value in update_data.items():
            setattr(branding, field, value)

        self.db.flush()
        self._invalidate_branding_cache(branding.organization_id)
        return branding

    def delete(self, branding_id: UUID) -> bool:
        """Delete branding configuration."""
        branding = self.get_by_id(branding_id)
        if not branding:
            return False

        org_id = branding.organization_id
        self.db.delete(branding)
        self.db.flush()
        self._invalidate_branding_cache(org_id)
        return True

    @staticmethod
    def _invalidate_branding_cache(org_id: UUID) -> None:
        """Invalidate cached branding CSS for an organization."""
        try:
            from app.services.cache import CacheKeys, cache_service

            cache_service.delete(CacheKeys.org_branding_css(org_id))
        except Exception:
            logger.exception("Ignored exception")  # Cache invalidation is best-effort

    def generate_css(self, org_id: UUID) -> str:
        """Generate CSS for an organization's branding."""
        branding = self.get_by_org_id(org_id)
        return generate_branding_css(branding)

    def get_fonts_url(self, org_id: UUID) -> str | None:
        """Get Google Fonts URL for an organization's custom fonts."""
        branding = self.get_by_org_id(org_id)
        if not branding:
            return None
        return CSSGenerator(branding).get_google_fonts_url()


# ─────────────────────────────────────────────────────────────────────────────
# Convenience Functions
# ─────────────────────────────────────────────────────────────────────────────


def get_branding_for_org(db: Session, org_id: UUID) -> OrganizationBranding | None:
    """Get branding for an organization."""
    return BrandingService(db).get_by_org_id(org_id)


def get_or_create_branding(
    db: Session,
    org_id: UUID,
    user_id: UUID | None = None,
) -> OrganizationBranding:
    """Get existing branding or create with defaults."""
    return BrandingService(db).get_or_create(org_id, user_id)


# ─────────────────────────────────────────────────────────────────────────────
# Font Presets
# ─────────────────────────────────────────────────────────────────────────────


FONT_PRESETS = [
    {
        "name": "DM Sans",
        "family": "DM Sans",
        "category": "sans-serif",
        "weights": [400, 500, 600, 700],
    },
    {
        "name": "Inter",
        "family": "Inter",
        "category": "sans-serif",
        "weights": [400, 500, 600, 700],
    },
    {
        "name": "Plus Jakarta Sans",
        "family": "Plus Jakarta Sans",
        "category": "sans-serif",
        "weights": [400, 500, 600, 700],
    },
    {
        "name": "Satoshi",
        "family": "Satoshi",
        "category": "sans-serif",
        "weights": [400, 500, 700],
    },
    {
        "name": "Space Grotesk",
        "family": "Space Grotesk",
        "category": "sans-serif",
        "weights": [400, 500, 600, 700],
    },
    {
        "name": "Outfit",
        "family": "Outfit",
        "category": "sans-serif",
        "weights": [400, 500, 600, 700],
    },
    {
        "name": "Sora",
        "family": "Sora",
        "category": "sans-serif",
        "weights": [400, 500, 600, 700],
    },
    {
        "name": "Manrope",
        "family": "Manrope",
        "category": "sans-serif",
        "weights": [400, 500, 600, 700],
    },
    {
        "name": "Source Serif 4",
        "family": "Source Serif 4",
        "category": "serif",
        "weights": [400, 600, 700],
    },
    {
        "name": "Fraunces",
        "family": "Fraunces",
        "category": "serif",
        "weights": [400, 500, 600, 700],
    },
    {
        "name": "Lora",
        "family": "Lora",
        "category": "serif",
        "weights": [400, 500, 600, 700],
    },
    {
        "name": "JetBrains Mono",
        "family": "JetBrains Mono",
        "category": "monospace",
        "weights": [400, 500, 600],
    },
    {
        "name": "Fira Code",
        "family": "Fira Code",
        "category": "monospace",
        "weights": [400, 500, 600],
    },
    {
        "name": "IBM Plex Mono",
        "family": "IBM Plex Mono",
        "category": "monospace",
        "weights": [400, 500, 600],
    },
]
