"""
Branding Schemas.

Pydantic schemas for organization branding API.
"""

from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator
import re


class BorderRadiusStyle(str, Enum):
    """Border radius presets."""

    SHARP = "sharp"
    ROUNDED = "rounded"
    PILL = "pill"


class ButtonStyle(str, Enum):
    """Button styling presets."""

    SOLID = "solid"
    GRADIENT = "gradient"
    OUTLINE = "outline"


class SidebarStyle(str, Enum):
    """Sidebar theme presets."""

    DARK = "dark"
    LIGHT = "light"
    BRAND = "brand"


def validate_hex_color(v: Optional[str]) -> Optional[str]:
    """Validate hex color format."""
    if v is None:
        return None
    v = v.strip()
    if not v:
        return None
    # Ensure it starts with # and is valid hex
    if not re.match(r"^#[0-9A-Fa-f]{6}$", v):
        raise ValueError("Color must be in hex format #RRGGBB")
    return v.upper()


class BrandingBase(BaseModel):
    """Base branding fields shared between create/update."""

    # Identity
    display_name: Optional[str] = Field(
        None,
        max_length=255,
        description="Display name (can differ from legal name)",
    )
    tagline: Optional[str] = Field(
        None,
        max_length=500,
        description="Brand tagline or slogan",
    )
    logo_url: Optional[str] = Field(
        None,
        max_length=500,
        description="Primary logo URL (for light backgrounds)",
    )
    logo_dark_url: Optional[str] = Field(
        None,
        max_length=500,
        description="Logo for dark mode/backgrounds",
    )
    favicon_url: Optional[str] = Field(
        None,
        max_length=500,
        description="Browser favicon URL",
    )
    brand_mark: Optional[str] = Field(
        None,
        max_length=4,
        description="2-4 letter mark (auto-derived if empty)",
    )

    # Primary color palette
    primary_color: Optional[str] = Field(
        None,
        description="Primary brand color (hex #RRGGBB)",
    )
    primary_light: Optional[str] = Field(
        None,
        description="Light variant (auto-calculated if empty)",
    )
    primary_dark: Optional[str] = Field(
        None,
        description="Dark variant (auto-calculated if empty)",
    )

    # Accent color palette
    accent_color: Optional[str] = Field(
        None,
        description="Secondary accent color (hex)",
    )
    accent_light: Optional[str] = Field(
        None,
        description="Light accent variant",
    )
    accent_dark: Optional[str] = Field(
        None,
        description="Dark accent variant",
    )

    # Semantic color overrides
    success_color: Optional[str] = Field(
        None,
        description="Success/positive color (default: emerald)",
    )
    warning_color: Optional[str] = Field(
        None,
        description="Warning color (default: amber)",
    )
    danger_color: Optional[str] = Field(
        None,
        description="Danger/error color (default: rose)",
    )

    # Typography
    font_family_display: Optional[str] = Field(
        None,
        max_length=100,
        description="Heading/display font (Google Fonts name)",
    )
    font_family_body: Optional[str] = Field(
        None,
        max_length=100,
        description="Body text font",
    )
    font_family_mono: Optional[str] = Field(
        None,
        max_length=100,
        description="Monospace font for numbers/code",
    )

    # UI preferences
    border_radius: Optional[BorderRadiusStyle] = Field(
        None,
        description="Border radius preset",
    )
    button_style: Optional[ButtonStyle] = Field(
        None,
        description="Button styling preset",
    )
    sidebar_style: Optional[SidebarStyle] = Field(
        None,
        description="Sidebar theme preset",
    )

    # Advanced
    custom_css: Optional[str] = Field(
        None,
        description="Custom CSS injected after generated styles",
    )

    # Validators for color fields
    @field_validator(
        "primary_color",
        "primary_light",
        "primary_dark",
        "accent_color",
        "accent_light",
        "accent_dark",
        "success_color",
        "warning_color",
        "danger_color",
        mode="before",
    )
    @classmethod
    def validate_colors(cls, v: Optional[str]) -> Optional[str]:
        return validate_hex_color(v)


class BrandingCreate(BrandingBase):
    """Schema for creating organization branding."""

    organization_id: UUID = Field(
        ...,
        description="Organization to attach branding to",
    )


class BrandingUpdate(BrandingBase):
    """Schema for updating organization branding."""

    is_active: Optional[bool] = None


class BrandingResponse(BrandingBase):
    """Schema for branding response."""

    model_config = ConfigDict(from_attributes=True)

    branding_id: UUID
    organization_id: UUID
    is_active: bool
    created_at: datetime
    updated_at: Optional[datetime] = None
    created_by_id: Optional[UUID] = None


class BrandingPreview(BaseModel):
    """Schema for live preview CSS generation."""

    primary_color: Optional[str] = None
    accent_color: Optional[str] = None
    font_family_display: Optional[str] = None
    font_family_body: Optional[str] = None
    border_radius: Optional[BorderRadiusStyle] = None
    button_style: Optional[ButtonStyle] = None
    sidebar_style: Optional[SidebarStyle] = None

    @field_validator("primary_color", "accent_color", mode="before")
    @classmethod
    def validate_colors(cls, v: Optional[str]) -> Optional[str]:
        return validate_hex_color(v)


class ColorPaletteResponse(BaseModel):
    """Generated color palette from a base color."""

    base: str
    shade_50: str
    shade_100: str
    shade_200: str
    shade_300: str
    shade_400: str
    shade_500: str
    shade_600: str
    shade_700: str
    shade_800: str
    shade_900: str
    shade_950: str


class FontOption(BaseModel):
    """Available font option for selection."""

    name: str
    family: str
    category: str  # 'sans-serif', 'serif', 'monospace', 'display'
    weights: list[int] = [400, 500, 600, 700]
    preview_url: Optional[str] = None


class FontListResponse(BaseModel):
    """List of available fonts."""

    fonts: list[FontOption]


__all__ = [
    "BorderRadiusStyle",
    "ButtonStyle",
    "SidebarStyle",
    "BrandingBase",
    "BrandingCreate",
    "BrandingUpdate",
    "BrandingResponse",
    "BrandingPreview",
    "ColorPaletteResponse",
    "FontOption",
    "FontListResponse",
]
