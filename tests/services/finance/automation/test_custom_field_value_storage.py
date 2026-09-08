"""Custom field value normalization and destructive-change guards."""

from decimal import Decimal
import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.models.finance.automation import CustomFieldEntityType, CustomFieldType
from app.services.finance.automation.custom_fields import CustomFieldsService


def test_value_normalization_preserves_typed_json_contract() -> None:
    service = CustomFieldsService()

    assert service._json_value(CustomFieldType.NUMBER, "7") == 7
    assert service._json_value(CustomFieldType.DECIMAL, Decimal("7.20")) == "7.20"
    assert service._json_value(CustomFieldType.BOOLEAN, True) is True
    assert service._json_value(CustomFieldType.MULTISELECT, ("a", "b")) == ["a", "b"]


def test_definition_lookup_is_tenant_scoped() -> None:
    db = MagicMock()
    db.scalar.return_value = None

    assert CustomFieldsService().get(db, uuid.uuid4(), uuid.uuid4()) is None

    statement = db.scalar.call_args.args[0]
    sql = str(statement)
    assert "custom_field_definition.field_id" in sql
    assert "custom_field_definition.organization_id" in sql


def test_hard_delete_refuses_definition_with_values() -> None:
    db = MagicMock()
    field = MagicMock()
    db.scalar.side_effect = [field, 2]
    org_id = uuid.uuid4()

    with pytest.raises(HTTPException) as exc:
        CustomFieldsService().hard_delete(db, uuid.uuid4(), org_id)

    assert exc.value.status_code == 409
    db.delete.assert_not_called()


@patch("app.services.finance.automation.event_dispatcher.fire_workflow_event")
@patch("app.services.finance.automation.entity_registry.resolve_entity")
def test_save_values_retains_inactive_historical_values(
    resolve_entity, fire_workflow_event
) -> None:
    service = CustomFieldsService()
    db = MagicMock()
    org_id = uuid.uuid4()
    entity_id = uuid.uuid4()
    field_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    entity = MagicMock(organization_id=org_id)
    resolve_entity.return_value = entity

    active_definition = MagicMock(
        field_id=field_id,
        field_code="service_tier",
        field_type=CustomFieldType.TEXT,
        is_required=False,
        default_value=None,
    )
    active_definition.validate_value.return_value = (True, None)
    service.list_for_entity = MagicMock(return_value=[active_definition])
    service.get_values = MagicMock(
        return_value={"legacy_reference": "kept", "service_tier": "bronze"}
    )
    existing_row = MagicMock(field_id=field_id, value="bronze")
    db.scalars.return_value.all.return_value = [existing_row]

    result = service.save_values(
        db,
        org_id,
        CustomFieldEntityType.CUSTOMER,
        entity_id,
        {"service_tier": "gold"},
        actor_id,
        replace=True,
    )

    assert result == {"legacy_reference": "kept", "service_tier": "gold"}
    assert existing_row.value == "gold"
    db.delete.assert_not_called()
    fire_workflow_event.assert_called_once()


def test_form_schema_shows_saved_inactive_value_as_read_only() -> None:
    service = CustomFieldsService()
    db = MagicMock()
    inactive = MagicMock(
        field_id=uuid.uuid4(),
        field_code="legacy_reference",
        field_name="Legacy reference",
        field_type=CustomFieldType.TEXT,
        is_required=False,
        default_value=None,
        placeholder=None,
        help_text=None,
        css_class=None,
        display_order=1,
        is_active=False,
        show_in_form=False,
        section_name=None,
        field_options=None,
        max_length=None,
        min_value=None,
        max_value=None,
        validation_regex=None,
    )
    service.list_for_entity = MagicMock(return_value=[inactive])

    sections = service.get_form_schema(
        db,
        uuid.uuid4(),
        CustomFieldEntityType.CUSTOMER,
        include_inactive_codes={"legacy_reference"},
    )

    assert sections[0]["fields"][0]["read_only"] is True
    assert sections[0]["fields"][0]["is_active"] is False
