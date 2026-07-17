"""Tests for `CustomFieldsService.validate_custom_fields`'s unknown-field-code
handling.

Previously a `field_code` with no matching `CustomFieldDefinition` hit a
bare `continue` — a caller-side typo passed validation silently. Now it's
collected as a validation error alongside any other violations.
"""

import uuid

from app.models.finance.automation import CustomFieldEntityType, CustomFieldType
from app.services.finance.automation.custom_fields import (
    CustomFieldInput,
    CustomFieldsService,
)


def test_unknown_field_code_is_a_validation_error(db_session):
    org_id = uuid.uuid4()
    service = CustomFieldsService()
    service.create_field(
        db_session,
        org_id,
        CustomFieldInput(
            entity_type=CustomFieldEntityType.CUSTOMER,
            field_code="known_field",
            field_name="Known Field",
            field_type=CustomFieldType.TEXT,
        ),
        uuid.uuid4(),
    )

    is_valid, errors = service.validate_custom_fields(
        db_session,
        org_id,
        CustomFieldEntityType.CUSTOMER,
        {"known_field": "ok", "typo_field": "ignored before this fix"},
    )

    assert is_valid is False
    assert any("typo_field" in error for error in errors)


def test_known_field_codes_alone_still_validate_cleanly(db_session):
    org_id = uuid.uuid4()
    service = CustomFieldsService()
    service.create_field(
        db_session,
        org_id,
        CustomFieldInput(
            entity_type=CustomFieldEntityType.CUSTOMER,
            field_code="known_field",
            field_name="Known Field",
            field_type=CustomFieldType.TEXT,
        ),
        uuid.uuid4(),
    )

    is_valid, errors = service.validate_custom_fields(
        db_session,
        org_id,
        CustomFieldEntityType.CUSTOMER,
        {"known_field": "ok"},
    )

    assert is_valid is True
    assert errors == []
