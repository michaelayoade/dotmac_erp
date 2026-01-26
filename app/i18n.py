"""
Internationalization (i18n) module using JSON dictionaries.

Provides simple key-value translation lookup with:
- Locale fallback (missing key falls back to 'en')
- String interpolation via .format()
- LRU caching for performance
- Nested key support (e.g., "landing.hero.title")
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, cast

# Base directory for locale files
LOCALES_DIR = Path(__file__).parent.parent / "locales"
DEFAULT_LOCALE = "en"


@lru_cache(maxsize=16)
def _load_locale(locale: str) -> dict[str, Any]:
    """Load locale dictionary from JSON file.

    Args:
        locale: Locale code (e.g., 'en', 'fr', 'de')

    Returns:
        Dictionary of translation strings
    """
    path = LOCALES_DIR / f"{locale}.json"
    if not path.exists():
        if locale != DEFAULT_LOCALE:
            # Fallback to default locale
            return _load_locale(DEFAULT_LOCALE)
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        return cast(dict[str, Any], raw)
    return {}


def _get_nested(data: Mapping[str, Any], key: str, default: Any = None) -> Any:
    """Get nested dictionary value using dot notation.

    Args:
        data: Dictionary to search
        key: Dot-separated key (e.g., "landing.hero.title")
        default: Default value if key not found

    Returns:
        Value at key path or default
    """
    keys = key.split(".")
    value: Any = data
    for k in keys:
        if isinstance(value, dict):
            value = value.get(k)
            if value is None:
                return default
        else:
            return default
    return value


def t(key: str, locale: str = DEFAULT_LOCALE, **kwargs) -> str:
    """Get translated string by key.

    Args:
        key: Translation key (e.g., "btn.save" or "landing.hero.title")
        locale: Locale code (defaults to 'en')
        **kwargs: Interpolation values for .format()

    Returns:
        Translated string, or the key itself if not found

    Examples:
        >>> t("btn.save")
        "Save"
        >>> t("welcome", name="Alice")
        "Welcome, Alice!"
        >>> t("landing.hero.title")
        "Close faster with audit-ready accounting"
    """
    strings = _load_locale(locale)
    value = _get_nested(strings, key)

    if value is None:
        # Try fallback locale
        if locale != DEFAULT_LOCALE:
            strings = _load_locale(DEFAULT_LOCALE)
            value = _get_nested(strings, key)

    if value is None:
        # Return key as fallback (helps identify missing translations)
        return key

    if not isinstance(value, str):
        return str(value)

    if kwargs:
        try:
            return value.format(**kwargs)
        except KeyError:
            # Return unformatted if interpolation fails
            return value

    return value


def get_locale_strings(locale: str = DEFAULT_LOCALE) -> dict[str, Any]:
    """Get all strings for a locale (for template context).

    Args:
        locale: Locale code

    Returns:
        Full dictionary of strings for the locale
    """
    return _load_locale(locale)


def clear_cache():
    """Clear the locale cache (useful for development/testing)."""
    _load_locale.cache_clear()


# Alias for convenience
_ = t
