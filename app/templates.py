"""
Centralized Jinja2 template configuration.

Import `templates` from this module instead of creating new Jinja2Templates instances.
This ensures consistent globals (i18n, datetime, etc.) across all routes.
"""

import html
import re
from datetime import datetime
from decimal import Decimal
from typing import Union
from urllib.parse import unquote_plus

from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from app.i18n import t

# Single shared templates instance
templates = Jinja2Templates(directory="templates")

# Register global functions
templates.env.globals["now"] = datetime.now
templates.env.globals["t"] = t      # Translation function
templates.env.globals["_"] = t      # Alias for convenience


# Custom filters
def format_currency(value: Union[Decimal, float, int, None], symbol: str = "", decimals: int = 2) -> str:
    """Format a number as currency with thousand separators."""
    if value is None:
        return f"{symbol}0.00" if symbol else "0.00"
    try:
        num = float(value)
        formatted = f"{num:,.{decimals}f}"
        return f"{symbol}{formatted}" if symbol else formatted
    except (ValueError, TypeError):
        return str(value)


def format_number(value: Union[Decimal, float, int, None], decimals: int = 2) -> str:
    """Format a number with thousand separators."""
    if value is None:
        return "0"
    try:
        num = float(value)
        if decimals == 0:
            return f"{num:,.0f}"
        return f"{num:,.{decimals}f}"
    except (ValueError, TypeError):
        return str(value)


def urldecode(value: str | None) -> str:
    """Decode URL-encoded string (handles + and %XX sequences)."""
    if value is None:
        return ""
    return unquote_plus(value)


# HTML Sanitization for safe rendering
# Whitelist of allowed HTML tags
ALLOWED_TAGS = frozenset([
    "p", "br", "b", "i", "u", "strong", "em", "s", "strike",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li",
    "a", "span", "div",
    "table", "thead", "tbody", "tr", "th", "td",
    "blockquote", "pre", "code",
])

# Allowed attributes for specific tags
ALLOWED_ATTRS = {
    "a": {"href", "title", "target"},
    "span": {"class"},
    "div": {"class"},
    "table": {"class"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
}

# Pattern to match HTML tags
TAG_PATTERN = re.compile(r"<(/?)(\w+)([^>]*)>", re.IGNORECASE)
# Pattern to match attributes
ATTR_PATTERN = re.compile(r'(\w+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|(\S+))', re.IGNORECASE)
# Pattern for dangerous protocols
DANGEROUS_PROTOCOLS = re.compile(r"^\s*(?:javascript|vbscript|data):", re.IGNORECASE)


def sanitize_html(value: str | None) -> Markup:
    """
    Sanitize HTML content to prevent XSS attacks.

    Allows only safe HTML tags and attributes.
    Strips event handlers and javascript: URLs.

    Usage in templates:
        {{ content | sanitize_html }}

    Instead of unsafe:
        {{ content | safe }}
    """
    if value is None:
        return Markup("")

    if not isinstance(value, str):
        value = str(value)

    def replace_tag(match: re.Match[str]) -> str:
        closing, tag_name, attrs = match.groups()
        tag_lower = tag_name.lower()

        # Remove disallowed tags entirely
        if tag_lower not in ALLOWED_TAGS:
            return ""

        # Closing tag - just return it
        if closing:
            return f"</{tag_lower}>"

        # Process attributes
        allowed_attrs = ALLOWED_ATTRS.get(tag_lower, set())
        safe_attrs = []

        for attr_match in ATTR_PATTERN.finditer(attrs):
            attr_name = attr_match.group(1).lower()
            attr_value = attr_match.group(2) or attr_match.group(3) or attr_match.group(4) or ""

            # Skip disallowed attributes
            if attr_name not in allowed_attrs:
                continue

            # Skip event handlers (onclick, onload, etc.)
            if attr_name.startswith("on"):
                continue

            # Sanitize href/src to prevent javascript: URLs
            if attr_name in ("href", "src"):
                if DANGEROUS_PROTOCOLS.match(attr_value):
                    continue
                # Allow only http, https, mailto, tel, and relative URLs
                if attr_value and not attr_value.startswith(("/", "#", "http://", "https://", "mailto:", "tel:")):
                    continue

            # Escape attribute value
            safe_value = html.escape(attr_value, quote=True)
            safe_attrs.append(f'{attr_name}="{safe_value}"')

        if safe_attrs:
            return f"<{tag_lower} {' '.join(safe_attrs)}>"
        return f"<{tag_lower}>"

    # Replace tags with sanitized versions
    sanitized = TAG_PATTERN.sub(replace_tag, value)

    # Mark as safe for Jinja2
    return Markup(sanitized)


def nl2br(value: str | None) -> Markup:
    """
    Convert newlines to <br> tags safely.

    Escapes HTML content first, then replaces newlines.
    Returns a safe Markup object.

    Usage in templates:
        {{ content | nl2br }}

    Instead of unsafe:
        {{ content | replace('\\n', '<br>') | safe }}
    """
    if value is None:
        return Markup("")

    # Escape HTML characters first
    escaped = html.escape(str(value))
    # Replace newlines with <br> tags
    with_breaks = escaped.replace("\n", "<br>")
    return Markup(with_breaks)


# Register custom filters
templates.env.filters["format_currency"] = format_currency
templates.env.filters["format_number"] = format_number
templates.env.filters["urldecode"] = urldecode
templates.env.filters["sanitize_html"] = sanitize_html
templates.env.filters["nl2br"] = nl2br
