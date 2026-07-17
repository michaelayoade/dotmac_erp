"""Tests for `CustomFieldDefinition.validate_value` gap-closures.

`validate_value` declared `min_value`/`max_value` columns and a full set of
`CustomFieldType` members but only actually checked TEXT/NUMBER/DECIMAL/
EMAIL/SELECT format — `min_value`/`max_value` were never compared against
NUMBER/DECIMAL values, and BOOLEAN/DATE/DATETIME had no real type check at
all (a BOOLEAN field accepted the string `"true"`, a DATE field accepted
`"not a date"`). These are pure-Python model tests — no DB required, since
`validate_value` never touches the session.
"""

import uuid
from datetime import date, datetime

from app.models.finance.automation.custom_field import (
    CustomFieldDefinition,
    CustomFieldEntityType,
    CustomFieldType,
)


def _field(**overrides) -> CustomFieldDefinition:
    defaults = dict(
        organization_id=uuid.uuid4(),
        entity_type=CustomFieldEntityType.CUSTOMER,
        field_code="test_field",
        field_name="Test Field",
        field_type=CustomFieldType.TEXT,
        created_by=uuid.uuid4(),
    )
    defaults.update(overrides)
    return CustomFieldDefinition(**defaults)


# --- (a) min_value/max_value range enforcement -----------------------------


def test_number_within_range_is_valid():
    field = _field(field_type=CustomFieldType.NUMBER, min_value="1", max_value="10")
    assert field.validate_value(5) == (True, None)


def test_number_below_min_value_is_invalid():
    field = _field(field_type=CustomFieldType.NUMBER, min_value="1", max_value="10")
    is_valid, error = field.validate_value(0)
    assert is_valid is False
    assert "at least 1" in error


def test_number_above_max_value_is_invalid():
    field = _field(field_type=CustomFieldType.NUMBER, min_value="1", max_value="10")
    is_valid, error = field.validate_value(11)
    assert is_valid is False
    assert "at most 10" in error


def test_decimal_within_range_is_valid():
    field = _field(field_type=CustomFieldType.DECIMAL, min_value="1.5", max_value="9.5")
    assert field.validate_value("5.25") == (True, None)


def test_decimal_below_min_value_is_invalid():
    field = _field(field_type=CustomFieldType.DECIMAL, min_value="1.5", max_value="9.5")
    is_valid, error = field.validate_value("1.4")
    assert is_valid is False
    assert "at least 1.5" in error


def test_decimal_above_max_value_is_invalid():
    field = _field(field_type=CustomFieldType.DECIMAL, min_value="1.5", max_value="9.5")
    is_valid, error = field.validate_value("9.6")
    assert is_valid is False
    assert "at most 9.5" in error


def test_non_numeric_stored_bound_is_skipped_silently():
    """A malformed `min_value` on the definition doesn't crash validation —
    that bound is just skipped rather than enforced."""
    field = _field(field_type=CustomFieldType.NUMBER, min_value="not-a-number")
    assert field.validate_value(5) == (True, None)


# --- (c) BOOLEAN / DATE / DATETIME real type checks -------------------------


def test_boolean_accepts_real_bool():
    field = _field(field_type=CustomFieldType.BOOLEAN)
    assert field.validate_value(True) == (True, None)
    assert field.validate_value(False) == (True, None)


def test_boolean_rejects_truthy_string():
    field = _field(field_type=CustomFieldType.BOOLEAN)
    is_valid, error = field.validate_value("true")
    assert is_valid is False
    assert "true or false" in error


def test_boolean_rejects_int():
    field = _field(field_type=CustomFieldType.BOOLEAN)
    is_valid, _ = field.validate_value(1)
    assert is_valid is False


def test_date_accepts_date_object():
    field = _field(field_type=CustomFieldType.DATE)
    assert field.validate_value(date(2026, 1, 1)) == (True, None)


def test_date_accepts_iso_string():
    field = _field(field_type=CustomFieldType.DATE)
    assert field.validate_value("2026-01-01") == (True, None)


def test_date_rejects_unparseable_string():
    field = _field(field_type=CustomFieldType.DATE)
    is_valid, error = field.validate_value("not a date")
    assert is_valid is False
    assert "valid date" in error


def test_datetime_accepts_datetime_object():
    field = _field(field_type=CustomFieldType.DATETIME)
    assert field.validate_value(datetime(2026, 1, 1, 10, 30)) == (True, None)


def test_datetime_accepts_iso_string():
    field = _field(field_type=CustomFieldType.DATETIME)
    assert field.validate_value("2026-01-01T10:30:00") == (True, None)


def test_datetime_rejects_unparseable_string():
    field = _field(field_type=CustomFieldType.DATETIME)
    is_valid, error = field.validate_value("nope")
    assert is_valid is False
    assert "valid datetime" in error


# --- (d) URL/PHONE/CURRENCY are documented passthrough ----------------------


def test_url_phone_currency_have_no_default_format_check():
    """These types are intentionally NOT format-validated by default — use
    `validation_regex` to enforce a project-specific format."""
    for field_type in (
        CustomFieldType.URL,
        CustomFieldType.PHONE,
        CustomFieldType.CURRENCY,
    ):
        field = _field(field_type=field_type)
        assert field.validate_value("anything goes") == (True, None)


def test_url_passthrough_can_still_be_constrained_via_validation_regex():
    field = _field(
        field_type=CustomFieldType.URL,
        validation_regex=r"^https://",
    )
    assert field.validate_value("https://example.com") == (True, None)
    is_valid, _ = field.validate_value("ftp://example.com")
    assert is_valid is False
