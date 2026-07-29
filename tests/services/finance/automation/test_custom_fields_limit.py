"""Tests for `CustomFieldsService.create_field`'s per-entity limit enforcement.

`custom_fields_max_per_entity` (SettingDomain.automation,
app/services/settings_spec.py) was declared but never read anywhere, so an
organization could create unlimited custom field definitions per entity
type. These tests cover: the spec's own default (20) applies when no
override is configured, an org-specific override is honored via the same
`resolve_value` resolver every other setting-backed limit reads, and
deactivated (soft-deleted) definitions don't count against the limit.
"""

import uuid

import pytest
from fastapi import HTTPException

from app.models.domain_settings import SettingDomain, SettingValueType
from app.models.finance.automation import CustomFieldEntityType, CustomFieldType
from app.schemas.settings import DomainSettingCreate
from app.services import domain_settings as domain_settings_service
from app.services.finance.automation.custom_fields import (
    CustomFieldInput,
    CustomFieldsService,
)


def _input(code: str) -> CustomFieldInput:
    return CustomFieldInput(
        entity_type=CustomFieldEntityType.CUSTOMER,
        field_code=code,
        field_name=code,
        field_type=CustomFieldType.TEXT,
    )


def _set_limit(db_session, org_id: uuid.UUID, limit: int) -> None:
    domain_settings_service.automation_settings.create(
        db_session,
        DomainSettingCreate(
            domain=SettingDomain.automation,
            key="custom_fields_max_per_entity",
            value_type=SettingValueType.integer,
            value_text=str(limit),
            organization_id=org_id,
        ),
    )


def test_create_field_uses_spec_default_limit_when_unconfigured(db_session):
    """No DomainSetting row configured -> falls back to the spec's default=20."""
    org_id = uuid.uuid4()
    db_session.info["organization_id"] = org_id
    service = CustomFieldsService()

    for i in range(20):
        service.create_field(db_session, org_id, _input(f"field_{i}"), uuid.uuid4())

    with pytest.raises(HTTPException) as exc:
        service.create_field(db_session, org_id, _input("field_20"), uuid.uuid4())
    assert exc.value.status_code == 400
    assert "20" in exc.value.detail


def test_create_field_enforces_org_configured_limit(db_session):
    """An org-specific `custom_fields_max_per_entity` override is honored."""
    org_id = uuid.uuid4()
    db_session.info["organization_id"] = org_id
    _set_limit(db_session, org_id, 2)
    service = CustomFieldsService()

    service.create_field(db_session, org_id, _input("field_one"), uuid.uuid4())
    service.create_field(db_session, org_id, _input("field_two"), uuid.uuid4())

    with pytest.raises(HTTPException) as exc:
        service.create_field(db_session, org_id, _input("field_three"), uuid.uuid4())
    assert exc.value.status_code == 400
    assert "2" in exc.value.detail
    assert CustomFieldEntityType.CUSTOMER.value in exc.value.detail


def test_deactivated_fields_do_not_count_against_limit(db_session):
    org_id = uuid.uuid4()
    db_session.info["organization_id"] = org_id
    _set_limit(db_session, org_id, 1)
    service = CustomFieldsService()

    field = service.create_field(db_session, org_id, _input("field_one"), uuid.uuid4())
    with pytest.raises(HTTPException):
        service.create_field(db_session, org_id, _input("field_two"), uuid.uuid4())

    field.is_active = False
    db_session.flush()

    created = service.create_field(
        db_session, org_id, _input("field_two"), uuid.uuid4()
    )
    assert created.field_code == "field_two"


def test_limit_is_scoped_per_entity_type(db_session):
    """The limit is per (organization_id, entity_type) — a different
    entity_type on the same org has its own independent count."""
    org_id = uuid.uuid4()
    db_session.info["organization_id"] = org_id
    _set_limit(db_session, org_id, 1)
    service = CustomFieldsService()

    service.create_field(db_session, org_id, _input("field_one"), uuid.uuid4())

    other = CustomFieldInput(
        entity_type=CustomFieldEntityType.SUPPLIER,
        field_code="field_one",
        field_name="field_one",
        field_type=CustomFieldType.TEXT,
    )
    created = service.create_field(db_session, org_id, other, uuid.uuid4())
    assert created.entity_type == CustomFieldEntityType.SUPPLIER
