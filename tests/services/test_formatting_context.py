"""
Tests for app/services/formatting_context.py and org-aware formatters.

Covers:
- OrgFormattingPrefs dataclass and resolve_from_org()
- ContextVar lifecycle (set / get / clear)
- format_date() with and without org context
- format_datetime() with timezone conversion
- format_currency() with European and US separators
- format_number() with org context
- parse_decimal() with org-aware separator handling
- _format_number_with_seps() edge cases
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.config import settings
from app.services.formatters import (
    _format_number_with_seps,
    format_currency,
    format_currency_compact,
    format_date,
    format_datetime,
    format_number,
    parse_decimal,
)
from app.services.formatting_context import (
    DATE_FORMAT_MAP,
    NUMBER_FORMAT_MAP,
    OrgFormattingPrefs,
    clear_formatting_prefs,
    get_formatting_prefs,
    resolve_from_org,
    set_formatting_prefs,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_org(**kwargs) -> SimpleNamespace:
    """Create a minimal Organisation-like object with defaults."""
    defaults = {
        "date_format": None,
        "number_format": None,
        "timezone": None,
        "presentation_currency_code": "NGN",
        "functional_currency_code": "NGN",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


@pytest.fixture(autouse=True)
def _clean_context():
    """Ensure formatting context is clean before and after each test."""
    clear_formatting_prefs()
    yield
    clear_formatting_prefs()


# ============================================================================
# TestOrgFormattingPrefs
# ============================================================================


class TestOrgFormattingPrefs:
    """Tests for the OrgFormattingPrefs frozen dataclass."""

    def test_defaults(self):
        prefs = OrgFormattingPrefs()
        assert prefs.date_strftime == "%Y-%m-%d"
        assert prefs.thousand_sep == ","
        assert prefs.decimal_sep == "."
        assert prefs.currency_code == settings.default_presentation_currency_code
        assert prefs.timezone_name is None

    def test_frozen(self):
        prefs = OrgFormattingPrefs()
        with pytest.raises(AttributeError):
            prefs.date_strftime = "%d/%m/%Y"  # type: ignore[misc]


# ============================================================================
# TestResolveFromOrg
# ============================================================================


class TestResolveFromOrg:
    """Tests for resolve_from_org()."""

    def test_none_org_returns_defaults(self):
        prefs = resolve_from_org(None)
        assert prefs == OrgFormattingPrefs()

    def test_european_date_format(self):
        org = _make_org(date_format="DD/MM/YYYY")
        prefs = resolve_from_org(org)
        assert prefs.date_strftime == "%d/%m/%Y"
        assert prefs.datetime_strftime == "%d/%m/%Y %H:%M"

    def test_us_date_format(self):
        org = _make_org(date_format="MM/DD/YYYY")
        prefs = resolve_from_org(org)
        assert prefs.date_strftime == "%m/%d/%Y"

    def test_dot_date_format(self):
        org = _make_org(date_format="DD.MM.YYYY")
        prefs = resolve_from_org(org)
        assert prefs.date_strftime == "%d.%m.%Y"

    def test_european_number_format(self):
        org = _make_org(number_format="1.234,56")
        prefs = resolve_from_org(org)
        assert prefs.thousand_sep == "."
        assert prefs.decimal_sep == ","

    def test_space_number_format(self):
        org = _make_org(number_format="1 234,56")
        prefs = resolve_from_org(org)
        assert prefs.thousand_sep == "\u00a0"
        assert prefs.decimal_sep == ","

    def test_unknown_format_uses_defaults(self):
        org = _make_org(date_format="UNKNOWN", number_format="weird")
        prefs = resolve_from_org(org)
        assert prefs.date_strftime == "%Y-%m-%d"
        assert prefs.thousand_sep == ","
        assert prefs.decimal_sep == "."

    def test_currency_from_presentation(self):
        org = _make_org(
            presentation_currency_code="USD", functional_currency_code="NGN"
        )
        prefs = resolve_from_org(org)
        assert prefs.currency_code == "USD"

    def test_currency_fallback_to_functional(self):
        org = _make_org(presentation_currency_code=None, functional_currency_code="GBP")
        prefs = resolve_from_org(org)
        assert prefs.currency_code == "GBP"

    def test_timezone_passthrough(self):
        org = _make_org(timezone="Africa/Lagos")
        prefs = resolve_from_org(org)
        assert prefs.timezone_name == "Africa/Lagos"

    def test_none_fields_use_defaults(self):
        org = _make_org()  # all None
        prefs = resolve_from_org(org)
        assert prefs.date_strftime == "%Y-%m-%d"
        assert prefs.thousand_sep == ","
        assert prefs.decimal_sep == "."


# ============================================================================
# TestContextVarLifecycle
# ============================================================================


class TestContextVarLifecycle:
    """Tests for set/get/clear formatting prefs."""

    def test_get_returns_none_without_set(self):
        assert get_formatting_prefs() is None

    def test_set_then_get(self):
        prefs = OrgFormattingPrefs(currency_code="USD")
        set_formatting_prefs(prefs)
        assert get_formatting_prefs() is prefs

    def test_clear_resets_to_none(self):
        set_formatting_prefs(OrgFormattingPrefs())
        clear_formatting_prefs()
        assert get_formatting_prefs() is None


# ============================================================================
# TestFormatDateOrgAware
# ============================================================================


class TestFormatDateOrgAware:
    """Tests for format_date() with org context."""

    def test_no_context_uses_iso(self):
        d = date(2025, 1, 15)
        assert format_date(d) == "2025-01-15"

    def test_with_european_context(self):
        set_formatting_prefs(OrgFormattingPrefs(date_strftime="%d/%m/%Y"))
        d = date(2025, 1, 15)
        assert format_date(d) == "15/01/2025"

    def test_explicit_fmt_overrides_context(self):
        set_formatting_prefs(OrgFormattingPrefs(date_strftime="%d/%m/%Y"))
        d = date(2025, 1, 15)
        # Explicit format should win over context
        assert format_date(d, "%d %b %Y") == "15 Jan 2025"

    def test_explicit_format_kwarg_overrides_context(self):
        set_formatting_prefs(OrgFormattingPrefs(date_strftime="%d/%m/%Y"))
        d = date(2025, 1, 15)
        assert format_date(d, format="%Y/%m/%d") == "2025/01/15"

    def test_none_returns_empty(self):
        set_formatting_prefs(OrgFormattingPrefs(date_strftime="%d/%m/%Y"))
        assert format_date(None) == ""

    def test_datetime_input_extracts_date(self):
        set_formatting_prefs(OrgFormattingPrefs(date_strftime="%d.%m.%Y"))
        dt = datetime(2025, 3, 10, 14, 30)
        assert format_date(dt) == "10.03.2025"


# ============================================================================
# TestFormatDatetimeOrgAware
# ============================================================================


class TestFormatDatetimeOrgAware:
    """Tests for format_datetime() with org context."""

    def test_no_context_uses_iso(self):
        dt = datetime(2025, 1, 15, 14, 30)
        assert format_datetime(dt) == "2025-01-15 14:30"

    def test_with_european_context(self):
        set_formatting_prefs(OrgFormattingPrefs(datetime_strftime="%d/%m/%Y %H:%M"))
        dt = datetime(2025, 1, 15, 14, 30)
        assert format_datetime(dt) == "15/01/2025 14:30"

    def test_explicit_fmt_overrides_context(self):
        set_formatting_prefs(OrgFormattingPrefs(datetime_strftime="%d/%m/%Y %H:%M"))
        dt = datetime(2025, 1, 15, 14, 30)
        assert format_datetime(dt, "%Y-%m-%d") == "2025-01-15"

    def test_timezone_conversion(self):
        """TZ-aware datetime is converted to org timezone."""
        set_formatting_prefs(
            OrgFormattingPrefs(
                datetime_strftime="%Y-%m-%d %H:%M",
                timezone_name="America/New_York",
            )
        )
        # 14:00 UTC → 09:00 EST (UTC-5)
        dt_utc = datetime(2025, 1, 15, 14, 0, tzinfo=UTC)
        result = format_datetime(dt_utc)
        assert result == "2025-01-15 09:00"

    def test_naive_datetime_no_tz_conversion(self):
        """Naive datetimes should NOT be converted even if org has TZ."""
        set_formatting_prefs(
            OrgFormattingPrefs(
                datetime_strftime="%Y-%m-%d %H:%M",
                timezone_name="America/New_York",
            )
        )
        dt_naive = datetime(2025, 1, 15, 14, 0)
        result = format_datetime(dt_naive)
        assert result == "2025-01-15 14:00"

    def test_none_returns_empty(self):
        assert format_datetime(None) == ""


# ============================================================================
# TestFormatCurrencyOrgAware
# ============================================================================


class TestFormatCurrencyOrgAware:
    """Tests for format_currency() with org context."""

    def test_no_context_us_style(self):
        result = format_currency(Decimal("1234.56"), "USD")
        assert result == "USD 1,234.56"

    def test_european_separators(self):
        set_formatting_prefs(
            OrgFormattingPrefs(thousand_sep=".", decimal_sep=",", currency_code="EUR")
        )
        result = format_currency(Decimal("1234567.89"))
        assert result == "EUR 1.234.567,89"

    def test_explicit_currency_overrides_context(self):
        set_formatting_prefs(OrgFormattingPrefs(currency_code="EUR"))
        result = format_currency(Decimal("100"), "GBP")
        assert result == "GBP 100.00"

    def test_none_returns_none_value(self):
        set_formatting_prefs(OrgFormattingPrefs(currency_code="EUR"))
        assert format_currency(None, none_value="N/A") == "N/A"

    def test_show_symbol_false(self):
        set_formatting_prefs(OrgFormattingPrefs(thousand_sep=".", decimal_sep=","))
        result = format_currency(Decimal("1234.56"), show_symbol=False)
        assert result == "1.234,56"

    def test_compact_uses_context(self):
        set_formatting_prefs(OrgFormattingPrefs(thousand_sep=".", decimal_sep=","))
        result = format_currency_compact(Decimal("9876.54"))
        assert result == "9.876,54"

    def test_space_thousand_separator(self):
        set_formatting_prefs(
            OrgFormattingPrefs(
                thousand_sep="\u00a0", decimal_sep=",", currency_code="CZK"
            )
        )
        result = format_currency(Decimal("1234567.89"))
        assert result == "CZK 1\u00a0234\u00a0567,89"


# ============================================================================
# TestFormatNumber
# ============================================================================


class TestFormatNumber:
    """Tests for the public format_number() function."""

    def test_no_context_us_style(self):
        assert format_number(Decimal("1234.56")) == "1,234.56"

    def test_european_context(self):
        set_formatting_prefs(OrgFormattingPrefs(thousand_sep=".", decimal_sep=","))
        assert format_number(Decimal("1234.56")) == "1.234,56"

    def test_zero_decimal_places(self):
        assert format_number(Decimal("1234.56"), decimal_places=0) == "1,235"

    def test_none_returns_default(self):
        assert format_number(None) == "0"
        assert format_number(None, none_value="—") == "—"

    def test_float_input(self):
        assert format_number(1234.5) == "1,234.50"

    def test_int_input(self):
        assert format_number(1000) == "1,000.00"


# ============================================================================
# TestFormatNumberWithSeps (private helper)
# ============================================================================


class TestFormatNumberWithSeps:
    """Tests for _format_number_with_seps() edge cases."""

    def test_us_fast_path(self):
        assert _format_number_with_seps(Decimal("1234.56")) == "1,234.56"

    def test_european_separators(self):
        result = _format_number_with_seps(
            Decimal("1234.56"), thousand_sep=".", decimal_sep=","
        )
        assert result == "1.234,56"

    def test_negative_number(self):
        result = _format_number_with_seps(
            Decimal("-1234.56"), thousand_sep=".", decimal_sep=","
        )
        assert result == "-1.234,56"

    def test_zero(self):
        result = _format_number_with_seps(Decimal("0"))
        assert result == "0.00"

    def test_large_number(self):
        result = _format_number_with_seps(
            Decimal("1234567890.12"), thousand_sep=",", decimal_sep="."
        )
        assert result == "1,234,567,890.12"

    def test_small_number_no_thousand_sep(self):
        result = _format_number_with_seps(
            Decimal("123.45"), thousand_sep=".", decimal_sep=","
        )
        assert result == "123,45"

    def test_space_separator(self):
        result = _format_number_with_seps(
            Decimal("1234567.89"), thousand_sep="\u00a0", decimal_sep=","
        )
        assert result == "1\u00a0234\u00a0567,89"


# ============================================================================
# TestParseDecimalOrgAware
# ============================================================================


class TestParseDecimalOrgAware:
    """Tests for parse_decimal() with org-aware separator handling."""

    def test_no_context_strips_commas(self):
        assert parse_decimal("1,234.56") == Decimal("1234.56")

    def test_european_input(self):
        set_formatting_prefs(OrgFormattingPrefs(thousand_sep=".", decimal_sep=","))
        result = parse_decimal("1.234,56")
        assert result == Decimal("1234.56")

    def test_space_thousand_separator(self):
        set_formatting_prefs(OrgFormattingPrefs(thousand_sep="\u00a0", decimal_sep=","))
        result = parse_decimal("1\u00a0234,56")
        assert result == Decimal("1234.56")

    def test_none_returns_default(self):
        assert parse_decimal(None) is None
        assert parse_decimal(None, Decimal("0")) == Decimal("0")

    def test_empty_returns_default(self):
        assert parse_decimal("") is None

    def test_invalid_returns_default(self):
        assert parse_decimal("not-a-number") is None

    def test_plain_number_no_separators(self):
        set_formatting_prefs(OrgFormattingPrefs(thousand_sep=".", decimal_sep=","))
        # A number without separators should still parse
        result = parse_decimal("42")
        assert result == Decimal("42")


# ============================================================================
# TestMappingConstants
# ============================================================================


class TestMappingConstants:
    """Verify the mapping constants cover all expected formats."""

    def test_date_format_map_keys(self):
        expected = {
            "YYYY-MM-DD",
            "DD/MM/YYYY",
            "MM/DD/YYYY",
            "DD-MM-YYYY",
            "DD.MM.YYYY",
            "DD MMM YYYY",
        }
        assert set(DATE_FORMAT_MAP.keys()) == expected

    def test_number_format_map_keys(self):
        expected = {"1,234.56", "1.234,56", "1 234.56", "1 234,56"}
        assert set(NUMBER_FORMAT_MAP.keys()) == expected

    def test_all_strftime_formats_valid(self):
        """All mapped strftime formats should produce a valid result."""
        d = date(2025, 6, 15)
        for key, fmt in DATE_FORMAT_MAP.items():
            result = d.strftime(fmt)
            assert result, f"Empty result for {key} → {fmt}"
