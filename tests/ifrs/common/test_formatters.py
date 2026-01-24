"""
Unit tests for common formatting utilities.

Tests for date, currency, enum, and other formatting functions.
"""

from datetime import date
from decimal import Decimal
from enum import Enum

import pytest

from app.services.finance.common.formatters import (
    parse_date,
    format_date,
    format_date_display,
    parse_decimal,
    format_currency,
    format_currency_compact,
    parse_enum_safe,
    format_enum,
    format_enum_display,
    format_file_size,
    format_percentage,
    format_boolean,
    truncate_text,
)


# Test enum for enum tests
class SampleStatus(Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    PENDING = "pending"


class SampleStatusUpperCase(Enum):
    DRAFT = "DRAFT"
    POSTED = "POSTED"


# Tests for parse_date
class TestParseDate:
    """Tests for parse_date function."""

    def test_valid_date(self):
        """Test parsing a valid date string."""
        result = parse_date("2024-01-15")
        assert result == date(2024, 1, 15)

    def test_empty_string(self):
        """Test parsing empty string returns None."""
        assert parse_date("") is None
        assert parse_date(None) is None

    def test_invalid_format(self):
        """Test parsing invalid format returns None."""
        assert parse_date("15-01-2024") is None
        assert parse_date("invalid") is None

    def test_custom_format(self):
        """Test parsing with custom format."""
        result = parse_date("15/01/2024", format="%d/%m/%Y")
        assert result == date(2024, 1, 15)


# Tests for format_date
class TestFormatDate:
    """Tests for format_date function."""

    def test_valid_date(self):
        """Test formatting a valid date."""
        result = format_date(date(2024, 1, 15))
        assert result == "2024-01-15"

    def test_none_returns_empty(self):
        """Test formatting None returns empty string."""
        assert format_date(None) == ""

    def test_custom_format(self):
        """Test formatting with custom format."""
        result = format_date(date(2024, 1, 15), format="%d/%m/%Y")
        assert result == "15/01/2024"


# Tests for format_date_display
class TestFormatDateDisplay:
    """Tests for format_date_display function."""

    def test_display_format(self):
        """Test display format."""
        result = format_date_display(date(2024, 1, 15))
        assert result == "15 Jan 2024"

    def test_none_returns_empty(self):
        """Test formatting None returns empty string."""
        assert format_date_display(None) == ""


# Tests for parse_decimal
class TestParseDecimal:
    """Tests for parse_decimal function."""

    def test_valid_decimal(self):
        """Test parsing a valid decimal string."""
        result = parse_decimal("123.45")
        assert result == Decimal("123.45")

    def test_with_commas(self):
        """Test parsing decimal with thousand separators."""
        result = parse_decimal("1,234.56")
        assert result == Decimal("1234.56")

    def test_empty_returns_default(self):
        """Test parsing empty string returns default."""
        assert parse_decimal("") is None
        assert parse_decimal("", default=Decimal("0")) == Decimal("0")

    def test_invalid_returns_default(self):
        """Test parsing invalid string returns default."""
        assert parse_decimal("invalid") is None
        assert parse_decimal("invalid", default=Decimal("0")) == Decimal("0")


# Tests for format_currency
class TestFormatCurrency:
    """Tests for format_currency function."""

    def test_basic_formatting(self):
        """Test basic currency formatting."""
        result = format_currency(Decimal("1234.56"), currency="USD")
        assert result == "USD 1,234.56"

    def test_none_returns_empty(self):
        """Test formatting None returns empty string."""
        assert format_currency(None) == ""
        assert format_currency(None, none_value="N/A") == "N/A"

    def test_without_symbol(self):
        """Test formatting without currency symbol."""
        result = format_currency(Decimal("1234.56"), show_symbol=False)
        assert result == "1,234.56"

    def test_custom_decimal_places(self):
        """Test formatting with custom decimal places."""
        result = format_currency(Decimal("1234.5678"), decimal_places=4)
        assert "1234.5678" in result.replace(",", "")

    def test_large_numbers(self):
        """Test formatting large numbers."""
        result = format_currency(Decimal("1234567.89"), currency="NGN")
        assert result == "NGN 1,234,567.89"


# Tests for format_currency_compact
class TestFormatCurrencyCompact:
    """Tests for format_currency_compact function."""

    def test_compact_formatting(self):
        """Test compact currency formatting (no symbol)."""
        result = format_currency_compact(Decimal("1234.56"))
        assert result == "1,234.56"

    def test_none_returns_empty(self):
        """Test formatting None returns empty string."""
        assert format_currency_compact(None) == ""


# Tests for parse_enum_safe
class TestParseEnumSafe:
    """Tests for parse_enum_safe function."""

    def test_exact_match(self):
        """Test exact value match."""
        result = parse_enum_safe(SampleStatus, "active")
        assert result == SampleStatus.ACTIVE

    def test_uppercase_match(self):
        """Test uppercase match."""
        result = parse_enum_safe(SampleStatus, "ACTIVE")
        assert result == SampleStatus.ACTIVE

    def test_lowercase_match(self):
        """Test lowercase match for uppercase enum."""
        result = parse_enum_safe(SampleStatusUpperCase, "draft")
        assert result == SampleStatusUpperCase.DRAFT

    def test_empty_returns_default(self):
        """Test empty value returns default."""
        assert parse_enum_safe(SampleStatus, "") is None
        assert parse_enum_safe(SampleStatus, "", SampleStatus.PENDING) == SampleStatus.PENDING

    def test_none_returns_default(self):
        """Test None value returns default."""
        assert parse_enum_safe(SampleStatus, None) is None
        assert parse_enum_safe(SampleStatus, None, SampleStatus.ACTIVE) == SampleStatus.ACTIVE

    def test_invalid_returns_default(self):
        """Test invalid value returns default."""
        assert parse_enum_safe(SampleStatus, "invalid") is None
        assert parse_enum_safe(SampleStatus, "invalid", SampleStatus.PENDING) == SampleStatus.PENDING


# Tests for format_enum
class TestFormatEnum:
    """Tests for format_enum function."""

    def test_format_enum_value(self):
        """Test formatting enum returns value."""
        result = format_enum(SampleStatus.ACTIVE)
        assert result == "active"

    def test_none_returns_empty(self):
        """Test formatting None returns empty string."""
        assert format_enum(None) == ""
        assert format_enum(None, none_value="N/A") == "N/A"


# Tests for format_enum_display
class TestFormatEnumDisplay:
    """Tests for format_enum_display function."""

    def test_display_format(self):
        """Test display format converts underscores to spaces and title case."""
        # For single word
        result = format_enum_display(SampleStatus.ACTIVE)
        assert result == "Active"

    def test_none_returns_empty(self):
        """Test formatting None returns empty string."""
        assert format_enum_display(None) == ""


# Tests for format_file_size
class TestFormatFileSize:
    """Tests for format_file_size function."""

    def test_bytes(self):
        """Test formatting bytes."""
        assert format_file_size(500) == "500 B"

    def test_kilobytes(self):
        """Test formatting kilobytes."""
        result = format_file_size(1536)  # 1.5 KB
        assert "1.5 KB" in result

    def test_megabytes(self):
        """Test formatting megabytes."""
        result = format_file_size(1536 * 1024)  # 1.5 MB
        assert "1.5 MB" in result

    def test_gigabytes(self):
        """Test formatting gigabytes."""
        result = format_file_size(1536 * 1024 * 1024)  # 1.5 GB
        assert "1.5 GB" in result

    def test_none_returns_zero(self):
        """Test formatting None returns 0 B."""
        assert format_file_size(None) == "0 B"


# Tests for format_percentage
class TestFormatPercentage:
    """Tests for format_percentage function."""

    def test_basic_percentage(self):
        """Test basic percentage formatting."""
        result = format_percentage(Decimal("0.15"))
        assert result == "15.00%"

    def test_without_symbol(self):
        """Test formatting without % symbol."""
        result = format_percentage(Decimal("0.15"), show_symbol=False)
        assert result == "15.00"

    def test_custom_decimal_places(self):
        """Test custom decimal places."""
        result = format_percentage(Decimal("0.1567"), decimal_places=1)
        assert result == "15.7%"

    def test_none_returns_empty(self):
        """Test formatting None returns empty string."""
        assert format_percentage(None) == ""


# Tests for format_boolean
class TestFormatBoolean:
    """Tests for format_boolean function."""

    def test_true(self):
        """Test formatting True."""
        assert format_boolean(True) == "Yes"

    def test_false(self):
        """Test formatting False."""
        assert format_boolean(False) == "No"

    def test_none(self):
        """Test formatting None."""
        assert format_boolean(None) == ""

    def test_custom_texts(self):
        """Test custom text options."""
        assert format_boolean(True, true_text="Active") == "Active"
        assert format_boolean(False, false_text="Inactive") == "Inactive"
        assert format_boolean(None, none_text="Unknown") == "Unknown"


# Tests for truncate_text
class TestTruncateText:
    """Tests for truncate_text function."""

    def test_short_text_unchanged(self):
        """Test short text is unchanged."""
        result = truncate_text("Hello", max_length=50)
        assert result == "Hello"

    def test_long_text_truncated(self):
        """Test long text is truncated."""
        result = truncate_text("Hello World", max_length=8)
        assert result == "Hello..."
        assert len(result) == 8

    def test_custom_suffix(self):
        """Test custom suffix."""
        result = truncate_text("Hello World", max_length=9, suffix="…")
        assert result == "Hello Wo…"

    def test_empty_text(self):
        """Test empty text returns empty string."""
        assert truncate_text("") == ""
        assert truncate_text(None) == ""
