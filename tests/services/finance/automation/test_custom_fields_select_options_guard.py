"""Tests for `CustomFieldsService.create_field`/`update_field`'s
SELECT/MULTISELECT options guard.

`CustomFieldDefinition.validate_value`'s SELECT branch only checks
membership `if self.field_options:` (see
`app/models/finance/automation/custom_field.py`) — an options-less
SELECT/MULTISELECT definition silently skips membership validation
forever after, so any value passes. Rather than change that read-time
behavior, this closes the gap at WRITE time: `create_field`/`update_field`
reject a SELECT/MULTISELECT definition unless `field_options["options"]`
is a non-empty list, the same "definition self-consistency, checked up
front" pattern as the existing per-entity limit / duplicate-code /
identifier-format guards in `create_field`.
"""

import uuid

import pytest
from fastapi import HTTPException

from app.models.finance.automation import CustomFieldEntityType, CustomFieldType
from app.services.finance.automation.custom_fields import (
    CustomFieldInput,
    CustomFieldsService,
)


def _input(
    field_type: CustomFieldType, field_options=None, **overrides
) -> CustomFieldInput:
    defaults = dict(
        entity_type=CustomFieldEntityType.CUSTOMER,
        field_code="status_field",
        field_name="Status Field",
        field_type=field_type,
        field_options=field_options,
    )
    defaults.update(overrides)
    return CustomFieldInput(**defaults)


# --- create_field -----------------------------------------------------------


def test_create_select_field_without_options_is_rejected(db_session):
    org_id = uuid.uuid4()
    service = CustomFieldsService()

    with pytest.raises(HTTPException) as exc:
        service.create_field(
            db_session, org_id, _input(CustomFieldType.SELECT), uuid.uuid4()
        )
    assert exc.value.status_code == 400
    assert "option" in exc.value.detail.lower()


def test_create_multiselect_field_without_options_is_rejected(db_session):
    org_id = uuid.uuid4()
    service = CustomFieldsService()

    with pytest.raises(HTTPException) as exc:
        service.create_field(
            db_session, org_id, _input(CustomFieldType.MULTISELECT), uuid.uuid4()
        )
    assert exc.value.status_code == 400
    assert "option" in exc.value.detail.lower()


def test_create_select_field_with_empty_options_list_is_rejected(db_session):
    org_id = uuid.uuid4()
    service = CustomFieldsService()

    with pytest.raises(HTTPException):
        service.create_field(
            db_session,
            org_id,
            _input(CustomFieldType.SELECT, field_options={"options": []}),
            uuid.uuid4(),
        )


def test_create_select_field_with_options_succeeds(db_session):
    org_id = uuid.uuid4()
    service = CustomFieldsService()

    field = service.create_field(
        db_session,
        org_id,
        _input(
            CustomFieldType.SELECT,
            field_options={"options": [{"value": "a", "label": "A"}]},
        ),
        uuid.uuid4(),
    )
    assert field.field_code == "status_field"


def test_create_text_field_without_field_options_still_succeeds(db_session):
    """The guard only applies to SELECT/MULTISELECT — other field types
    never carry `field_options` and must be unaffected."""
    org_id = uuid.uuid4()
    service = CustomFieldsService()

    field = service.create_field(
        db_session,
        org_id,
        _input(CustomFieldType.TEXT, field_code="plain_text"),
        uuid.uuid4(),
    )
    assert field.field_code == "plain_text"


# --- update_field -------------------------------------------------------


def test_update_field_to_select_without_options_is_rejected(db_session):
    org_id = uuid.uuid4()
    service = CustomFieldsService()
    field = service.create_field(
        db_session, org_id, _input(CustomFieldType.TEXT), uuid.uuid4()
    )

    with pytest.raises(HTTPException) as exc:
        service.update_field(
            db_session,
            field.field_id,
            {"field_type": CustomFieldType.SELECT},
            uuid.uuid4(),
        )
    assert exc.value.status_code == 400
    assert "option" in exc.value.detail.lower()


def test_update_field_clearing_options_on_existing_select_is_rejected(db_session):
    org_id = uuid.uuid4()
    service = CustomFieldsService()
    field = service.create_field(
        db_session,
        org_id,
        _input(
            CustomFieldType.SELECT,
            field_options={"options": [{"value": "a", "label": "A"}]},
        ),
        uuid.uuid4(),
    )

    with pytest.raises(HTTPException):
        service.update_field(
            db_session, field.field_id, {"field_options": None}, uuid.uuid4()
        )


def test_update_field_adding_options_to_existing_select_succeeds(db_session):
    org_id = uuid.uuid4()
    service = CustomFieldsService()
    field = service.create_field(
        db_session,
        org_id,
        _input(
            CustomFieldType.SELECT,
            field_options={"options": [{"value": "a", "label": "A"}]},
        ),
        uuid.uuid4(),
    )

    updated = service.update_field(
        db_session,
        field.field_id,
        {"field_options": {"options": [{"value": "b", "label": "B"}]}},
        uuid.uuid4(),
    )
    assert updated.field_options == {"options": [{"value": "b", "label": "B"}]}
