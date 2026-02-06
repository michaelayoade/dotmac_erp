"""
Tests for ValidationRule class in app/services/ifrs/import_export/base.py

Tests for all 13 validation rule types:
- required, pattern, min_length, max_length
- min_value, max_value, choices
- email, phone, currency, positive, date, custom
"""

import re
from decimal import Decimal


from app.services.finance.import_export.base import ValidationRule


# ============ TestRequiredRule ============


class TestRequiredRule:
    """Tests for the 'required' validation rule."""

    def test_required_none_fails(self):
        """None value should fail required validation."""
        rule = ValidationRule("name", "required")
        is_valid, error = rule.validate(None)

        assert is_valid is False
        assert "required" in error.lower()

    def test_required_empty_string_fails(self):
        """Empty string should fail required validation."""
        rule = ValidationRule("name", "required")
        is_valid, error = rule.validate("")

        assert is_valid is False
        assert "required" in error.lower()

    def test_required_whitespace_fails(self):
        """Whitespace-only string should fail required validation."""
        rule = ValidationRule("name", "required")
        is_valid, error = rule.validate("   ")

        assert is_valid is False
        assert "required" in error.lower()

    def test_required_valid_passes(self):
        """Valid value should pass required validation."""
        rule = ValidationRule("name", "required")
        is_valid, error = rule.validate("John Doe")

        assert is_valid is True
        assert error is None

    def test_required_custom_message(self):
        """Custom error message should be used."""
        rule = ValidationRule("name", "required", message="Name is mandatory")
        is_valid, error = rule.validate("")

        assert is_valid is False
        assert error == "Name is mandatory"


# ============ TestPatternRule ============


class TestPatternRule:
    """Tests for the 'pattern' validation rule."""

    def test_pattern_matches(self):
        """Value matching pattern should pass."""
        rule = ValidationRule(
            "code", "pattern", value=re.compile(r"^[A-Z]{3}[0-9]{3}$")
        )
        is_valid, error = rule.validate("ABC123")

        assert is_valid is True
        assert error is None

    def test_pattern_not_matches(self):
        """Value not matching pattern should fail."""
        rule = ValidationRule(
            "code", "pattern", value=re.compile(r"^[A-Z]{3}[0-9]{3}$")
        )
        is_valid, error = rule.validate("abc123")

        assert is_valid is False
        assert "invalid format" in error.lower()

    def test_pattern_empty_passes(self):
        """Empty value should pass (non-required pattern)."""
        rule = ValidationRule("code", "pattern", value=re.compile(r"^[A-Z]+$"))
        is_valid, error = rule.validate("")

        assert is_valid is True


# ============ TestMinLengthRule ============


class TestMinLengthRule:
    """Tests for the 'min_length' validation rule."""

    def test_min_length_passes(self):
        """Value meeting min length should pass."""
        rule = ValidationRule("name", "min_length", value=3)
        is_valid, error = rule.validate("John")

        assert is_valid is True
        assert error is None

    def test_min_length_fails(self):
        """Value below min length should fail."""
        rule = ValidationRule("name", "min_length", value=5)
        is_valid, error = rule.validate("Jo")

        assert is_valid is False
        assert "at least 5" in error

    def test_min_length_exact(self):
        """Value exactly at min length should pass."""
        rule = ValidationRule("name", "min_length", value=3)
        is_valid, error = rule.validate("Joe")

        assert is_valid is True


# ============ TestMaxLengthRule ============


class TestMaxLengthRule:
    """Tests for the 'max_length' validation rule."""

    def test_max_length_passes(self):
        """Value within max length should pass."""
        rule = ValidationRule("code", "max_length", value=10)
        is_valid, error = rule.validate("ABC")

        assert is_valid is True
        assert error is None

    def test_max_length_fails(self):
        """Value exceeding max length should fail."""
        rule = ValidationRule("code", "max_length", value=5)
        is_valid, error = rule.validate("ABCDEFGHIJ")

        assert is_valid is False
        assert "not exceed 5" in error

    def test_max_length_exact(self):
        """Value exactly at max length should pass."""
        rule = ValidationRule("code", "max_length", value=5)
        is_valid, error = rule.validate("ABCDE")

        assert is_valid is True


# ============ TestMinValueRule ============


class TestMinValueRule:
    """Tests for the 'min_value' validation rule."""

    def test_min_value_passes(self):
        """Value above min should pass."""
        rule = ValidationRule("amount", "min_value", value=Decimal("0"))
        is_valid, error = rule.validate("100.50")

        assert is_valid is True
        assert error is None

    def test_min_value_fails(self):
        """Value below min should fail."""
        rule = ValidationRule("amount", "min_value", value=Decimal("10"))
        is_valid, error = rule.validate("5")

        assert is_valid is False
        assert "at least 10" in error

    def test_min_value_exact(self):
        """Value exactly at min should pass."""
        rule = ValidationRule("amount", "min_value", value=Decimal("10"))
        is_valid, error = rule.validate("10")

        assert is_valid is True

    def test_min_value_with_currency_symbol(self):
        """Should handle values with currency symbols."""
        rule = ValidationRule("amount", "min_value", value=Decimal("0"))
        is_valid, error = rule.validate("$100.00")

        assert is_valid is True


# ============ TestMaxValueRule ============


class TestMaxValueRule:
    """Tests for the 'max_value' validation rule."""

    def test_max_value_passes(self):
        """Value below max should pass."""
        rule = ValidationRule("amount", "max_value", value=Decimal("1000"))
        is_valid, error = rule.validate("500")

        assert is_valid is True
        assert error is None

    def test_max_value_fails(self):
        """Value above max should fail."""
        rule = ValidationRule("amount", "max_value", value=Decimal("100"))
        is_valid, error = rule.validate("150")

        assert is_valid is False
        assert "not exceed 100" in error

    def test_max_value_exact(self):
        """Value exactly at max should pass."""
        rule = ValidationRule("amount", "max_value", value=Decimal("100"))
        is_valid, error = rule.validate("100")

        assert is_valid is True


# ============ TestChoicesRule ============


class TestChoicesRule:
    """Tests for the 'choices' validation rule."""

    def test_choices_valid(self):
        """Value in choices should pass."""
        rule = ValidationRule(
            "status", "choices", value=["active", "inactive", "pending"]
        )
        is_valid, error = rule.validate("active")

        assert is_valid is True
        assert error is None

    def test_choices_invalid(self):
        """Value not in choices should fail."""
        rule = ValidationRule("status", "choices", value=["active", "inactive"])
        is_valid, error = rule.validate("deleted")

        assert is_valid is False
        assert "must be one of" in error

    def test_choices_case_insensitive(self):
        """Choices validation should be case-insensitive."""
        rule = ValidationRule("status", "choices", value=["Active", "Inactive"])
        is_valid, error = rule.validate("ACTIVE")

        assert is_valid is True

    def test_choices_with_spaces(self):
        """Choices with spaces should work with underscores."""
        rule = ValidationRule("type", "choices", value=["other asset", "fixed asset"])
        is_valid, error = rule.validate("other_asset")

        assert is_valid is True


# ============ TestEmailRule ============


class TestEmailRule:
    """Tests for the 'email' validation rule."""

    def test_email_valid(self):
        """Valid email should pass."""
        rule = ValidationRule("email", "email")
        is_valid, error = rule.validate("test@example.com")

        assert is_valid is True
        assert error is None

    def test_email_invalid_no_at(self):
        """Email without @ should fail."""
        rule = ValidationRule("email", "email")
        is_valid, error = rule.validate("testexample.com")

        assert is_valid is False
        assert "valid email" in error.lower()

    def test_email_invalid_no_domain(self):
        """Email without domain should fail."""
        rule = ValidationRule("email", "email")
        is_valid, error = rule.validate("test@")

        assert is_valid is False

    def test_email_with_subdomain(self):
        """Email with subdomain should pass."""
        rule = ValidationRule("email", "email")
        is_valid, error = rule.validate("test@mail.example.com")

        assert is_valid is True

    def test_email_empty_passes(self):
        """Empty email should pass (non-required)."""
        rule = ValidationRule("email", "email")
        is_valid, error = rule.validate("")

        assert is_valid is True


# ============ TestPhoneRule ============


class TestPhoneRule:
    """Tests for the 'phone' validation rule."""

    def test_phone_valid_us(self):
        """US phone number should pass."""
        rule = ValidationRule("phone", "phone")
        is_valid, error = rule.validate("+1-555-555-5555")

        assert is_valid is True
        assert error is None

    def test_phone_valid_international(self):
        """International phone number should pass."""
        rule = ValidationRule("phone", "phone")
        is_valid, error = rule.validate("+44 20 7946 0958")

        assert is_valid is True

    def test_phone_valid_simple(self):
        """Simple phone number should pass."""
        rule = ValidationRule("phone", "phone")
        is_valid, error = rule.validate("5555555555")

        assert is_valid is True

    def test_phone_too_short(self):
        """Too short phone number should fail."""
        rule = ValidationRule("phone", "phone")
        is_valid, error = rule.validate("123")

        assert is_valid is False


# ============ TestCurrencyRule ============


class TestCurrencyRule:
    """Tests for the 'currency' validation rule."""

    def test_currency_valid_usd(self):
        """USD currency code should pass."""
        rule = ValidationRule("currency", "currency")
        is_valid, error = rule.validate("USD")

        assert is_valid is True
        assert error is None

    def test_currency_valid_ngn(self):
        """NGN currency code should pass."""
        rule = ValidationRule("currency", "currency")
        is_valid, error = rule.validate("NGN")

        assert is_valid is True

    def test_currency_invalid(self):
        """Invalid currency code should fail."""
        rule = ValidationRule("currency", "currency")
        is_valid, error = rule.validate("INVALID")

        assert is_valid is False
        assert "ISO currency" in error

    def test_currency_lowercase(self):
        """Lowercase currency should pass (normalized to uppercase)."""
        rule = ValidationRule("currency", "currency")
        is_valid, error = rule.validate("eur")

        assert is_valid is True


# ============ TestPositiveRule ============


class TestPositiveRule:
    """Tests for the 'positive' validation rule."""

    def test_positive_passes(self):
        """Positive number should pass."""
        rule = ValidationRule("amount", "positive")
        is_valid, error = rule.validate("100.50")

        assert is_valid is True
        assert error is None

    def test_positive_zero_passes(self):
        """Zero should pass positive validation."""
        rule = ValidationRule("amount", "positive")
        is_valid, error = rule.validate("0")

        assert is_valid is True

    def test_positive_negative_fails(self):
        """Negative number should fail."""
        rule = ValidationRule("amount", "positive")
        is_valid, error = rule.validate("-50")

        assert is_valid is False
        assert "positive" in error.lower()


# ============ TestDateRule ============


class TestDateRule:
    """Tests for the 'date' validation rule."""

    def test_date_iso_format(self):
        """ISO date format should pass."""
        rule = ValidationRule("date", "date")
        is_valid, error = rule.validate("2024-01-15")

        assert is_valid is True
        assert error is None

    def test_date_us_format(self):
        """US date format should pass."""
        rule = ValidationRule("date", "date")
        is_valid, error = rule.validate("01/15/2024")

        assert is_valid is True

    def test_date_european_format(self):
        """European date format should pass."""
        rule = ValidationRule("date", "date")
        is_valid, error = rule.validate("15/01/2024")

        assert is_valid is True

    def test_date_invalid(self):
        """Invalid date should fail."""
        rule = ValidationRule("date", "date")
        is_valid, error = rule.validate("not-a-date")

        assert is_valid is False
        assert "valid date" in error.lower()


# ============ TestCustomRule ============


class TestCustomRule:
    """Tests for the 'custom' validation rule."""

    def test_custom_validation_passes(self):
        """Custom validation returning True should pass."""

        def custom_validator(value):
            return (
                (True, None)
                if value.startswith("PRE")
                else (False, "Must start with PRE")
            )

        rule = ValidationRule("code", "custom", value=custom_validator)
        is_valid, error = rule.validate("PRE001")

        assert is_valid is True
        assert error is None

    def test_custom_validation_fails(self):
        """Custom validation returning False should fail."""

        def custom_validator(value):
            return (
                (True, None)
                if value.startswith("PRE")
                else (False, "Must start with PRE")
            )

        rule = ValidationRule("code", "custom", value=custom_validator)
        is_valid, error = rule.validate("ABC001")

        assert is_valid is False
        assert "Must start with PRE" in error

    def test_custom_complex_validation(self):
        """Custom validation with complex logic."""

        def validate_account_code(value):
            # Account code must be 4 digits, starting with 1-9
            if not value.isdigit() or len(value) != 4:
                return (False, "Account code must be 4 digits")
            if value[0] == "0":
                return (False, "Account code cannot start with 0")
            return (True, None)

        rule = ValidationRule("account_code", "custom", value=validate_account_code)

        # Valid
        is_valid, _ = rule.validate("1000")
        assert is_valid is True

        # Invalid - starts with 0
        is_valid, error = rule.validate("0100")
        assert is_valid is False

        # Invalid - not 4 digits
        is_valid, error = rule.validate("100")
        assert is_valid is False
