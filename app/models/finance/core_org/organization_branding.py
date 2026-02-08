"""
OrganizationBranding Model - Per-organization visual identity configuration.

This model enables multi-tenant branding with:
- Custom logos (light/dark variants)
- Color palettes (primary, accent, semantic)
- Typography selection (Google Fonts)
- UI style preferences (border radius, button style, sidebar theme)
- Custom CSS injection for advanced customization
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class BorderRadiusStyle(str, enum.Enum):
    """Border radius presets."""

    SHARP = "sharp"  # 0px - angular, corporate
    ROUNDED = "rounded"  # 8px - balanced, friendly
    PILL = "pill"  # 16px+ - soft, modern


class ButtonStyle(str, enum.Enum):
    """Button styling presets."""

    SOLID = "solid"  # Flat solid color
    GRADIENT = "gradient"  # Gradient fill (default ERP style)
    OUTLINE = "outline"  # Bordered transparent


class SidebarStyle(str, enum.Enum):
    """Sidebar theme presets."""

    DARK = "dark"  # Dark background (default)
    LIGHT = "light"  # Light background
    BRAND = "brand"  # Primary brand color background


class OrganizationBranding(Base):
    """
    Per-organization branding configuration.

    Stores all visual identity elements that can be customized:
    - Logos and favicons
    - Color palette (primary, accent, semantic colors)
    - Typography (font families)
    - UI style preferences

    One-to-one relationship with Organization.
    """

    __tablename__ = "organization_branding"
    __table_args__ = {"schema": "core_org"}

    branding_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    # ─────────────────────────────────────────────────────────────────
    # Identity
    # ─────────────────────────────────────────────────────────────────
    display_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="Display name (can differ from legal name)",
    )
    tagline: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        comment="Brand tagline or slogan",
    )
    logo_url: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        comment="Primary logo URL (for light backgrounds)",
    )
    logo_dark_url: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        comment="Logo for dark mode/backgrounds",
    )
    favicon_url: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        comment="Browser favicon URL",
    )
    brand_mark: Mapped[str | None] = mapped_column(
        String(4),
        nullable=True,
        comment="2-4 letter mark (auto-derived from name if empty)",
    )

    # ─────────────────────────────────────────────────────────────────
    # Primary Color Palette
    # ─────────────────────────────────────────────────────────────────
    primary_color: Mapped[str | None] = mapped_column(
        String(7),
        nullable=True,
        comment="Primary brand color (hex #RRGGBB)",
    )
    primary_light: Mapped[str | None] = mapped_column(
        String(7),
        nullable=True,
        comment="Light variant (auto-calculated if empty)",
    )
    primary_dark: Mapped[str | None] = mapped_column(
        String(7),
        nullable=True,
        comment="Dark variant (auto-calculated if empty)",
    )

    # ─────────────────────────────────────────────────────────────────
    # Accent Color Palette
    # ─────────────────────────────────────────────────────────────────
    accent_color: Mapped[str | None] = mapped_column(
        String(7),
        nullable=True,
        comment="Secondary accent color (hex)",
    )
    accent_light: Mapped[str | None] = mapped_column(
        String(7),
        nullable=True,
        comment="Light accent variant",
    )
    accent_dark: Mapped[str | None] = mapped_column(
        String(7),
        nullable=True,
        comment="Dark accent variant",
    )

    # ─────────────────────────────────────────────────────────────────
    # Semantic Color Overrides (optional)
    # ─────────────────────────────────────────────────────────────────
    success_color: Mapped[str | None] = mapped_column(
        String(7),
        nullable=True,
        comment="Success/positive color (default: emerald)",
    )
    warning_color: Mapped[str | None] = mapped_column(
        String(7),
        nullable=True,
        comment="Warning color (default: amber)",
    )
    danger_color: Mapped[str | None] = mapped_column(
        String(7),
        nullable=True,
        comment="Danger/error color (default: rose)",
    )

    # ─────────────────────────────────────────────────────────────────
    # Typography
    # ─────────────────────────────────────────────────────────────────
    font_family_display: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="Heading/display font (Google Fonts name)",
    )
    font_family_body: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="Body text font",
    )
    font_family_mono: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="Monospace font for numbers/code",
    )

    # ─────────────────────────────────────────────────────────────────
    # UI Style Preferences
    # ─────────────────────────────────────────────────────────────────
    border_radius: Mapped[BorderRadiusStyle | None] = mapped_column(
        Enum(
            BorderRadiusStyle,
            name="border_radius_style",
            schema="core_org",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=True,
        default=BorderRadiusStyle.ROUNDED,
        comment="Border radius preset",
    )
    button_style: Mapped[ButtonStyle | None] = mapped_column(
        Enum(
            ButtonStyle,
            name="button_style",
            schema="core_org",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=True,
        default=ButtonStyle.GRADIENT,
        comment="Button styling preset",
    )
    sidebar_style: Mapped[SidebarStyle | None] = mapped_column(
        Enum(
            SidebarStyle,
            name="sidebar_style",
            schema="core_org",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=True,
        default=SidebarStyle.DARK,
        comment="Sidebar theme preset",
    )

    # ─────────────────────────────────────────────────────────────────
    # Advanced Customization
    # ─────────────────────────────────────────────────────────────────
    custom_css: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Custom CSS injected after generated styles",
    )

    # ─────────────────────────────────────────────────────────────────
    # Metadata
    # ─────────────────────────────────────────────────────────────────
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("people.id", ondelete="SET NULL"),
        nullable=True,
    )

    # ─────────────────────────────────────────────────────────────────
    # Relationships
    # ─────────────────────────────────────────────────────────────────
    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="branding",
    )


# Forward reference
from app.models.finance.core_org.organization import Organization  # noqa: E402
