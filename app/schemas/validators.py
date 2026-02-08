"""Common validation types and helpers for Pydantic schemas.

This module provides reusable validators for common fields like email
and phone numbers, ensuring consistent validation across the application.
"""

import re
from typing import Annotated, Any

from pydantic import AfterValidator, EmailStr, Field

# Phone number validation regex
# Supports:
# - E.164 format: +1234567890
# - Standard formats: 123-456-7890, (123) 456-7890
# - International: +44 20 7123 4567
PHONE_REGEX = re.compile(
    r"^(\+?\d{1,4}[\s.-]?)?"  # Optional country code
    r"(\(?\d{1,4}\)?[\s.-]?)?"  # Optional area code in parens
    r"\d{1,4}[\s.-]?"  # First part
    r"\d{1,4}[\s.-]?"  # Second part
    r"\d{1,9}$"  # Last part
)


def validate_phone_number(value: str | None) -> str | None:
    """Validate a phone number format.

    Accepts various formats and normalizes them.
    Returns None if input is None (for optional fields).

    Raises:
        ValueError: If phone number format is invalid
    """
    if value is None:
        return None

    # Strip whitespace
    value = value.strip()
    if not value:
        return None

    # Remove common formatting characters for validation
    cleaned = re.sub(r"[\s\-\.\(\)]", "", value)

    # Check minimum length (at least 7 digits)
    if len(cleaned.replace("+", "")) < 7:
        raise ValueError("Phone number must have at least 7 digits")

    # Check maximum length (E.164 allows up to 15 digits)
    if len(cleaned.replace("+", "")) > 15:
        raise ValueError("Phone number cannot exceed 15 digits")

    # Check for valid characters
    if not re.match(r"^\+?\d+$", cleaned):
        raise ValueError("Phone number can only contain digits and optional leading +")

    return value


def validate_non_empty_string(value: str | None) -> str | None:
    """Validate that a string is not just whitespace.

    Returns None if input is None or empty after stripping.
    """
    if value is None:
        return None
    value = value.strip()
    return value if value else None


def normalize_email(value: str | None) -> str | None:
    """Normalize email to lowercase.

    Email addresses are case-insensitive per RFC 5321, so we
    normalize to lowercase for consistency.
    """
    if value is None:
        return None
    return value.strip().lower()


# Annotated types for use in Pydantic models
# These can be used directly in schema definitions

PhoneNumber = Annotated[
    str | None,
    AfterValidator(validate_phone_number),
    Field(
        description="Phone number (supports various formats)",
        json_schema_extra={"examples": ["+1-555-123-4567", "(555) 123-4567"]},
    ),
]

NormalizedEmail = Annotated[
    EmailStr | None,
    AfterValidator(normalize_email),
    Field(
        description="Email address (normalized to lowercase)",
        json_schema_extra={"examples": ["user@example.com"]},
    ),
]

NonEmptyString = Annotated[
    str | None,
    AfterValidator(validate_non_empty_string),
    Field(description="Non-empty string (whitespace-only values become None)"),
]


# Validation helpers for use in custom validators


def validate_required_email(value: Any) -> str:
    """Validate that email is provided and valid.

    Use this for required email fields.
    """
    if not value:
        raise ValueError("Email is required")
    if isinstance(value, str):
        # Basic email validation
        if "@" not in value or "." not in value.split("@")[-1]:
            raise ValueError("Invalid email format")
        return value.strip().lower()
    raise ValueError("Email must be a string")


def validate_required_phone(value: Any) -> str:
    """Validate that phone is provided and valid.

    Use this for required phone fields.
    """
    if not value:
        raise ValueError("Phone number is required")
    if isinstance(value, str):
        result = validate_phone_number(value)
        if not result:
            raise ValueError("Phone number is required")
        return result
    raise ValueError("Phone number must be a string")
