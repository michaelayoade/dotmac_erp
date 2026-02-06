import uuid

import pytest
from fastapi import HTTPException

from app.models.domain_settings import SettingDomain, SettingValueType
from app.schemas.settings import DomainSettingCreate, DomainSettingUpdate
from app.services import domain_settings as domain_settings_service
from app.services import settings_api as settings_api_service


def test_domain_setting_domain_mismatch(db_session):
    settings = domain_settings_service.DomainSettings(domain=SettingDomain.auth)
    created = settings.create(
        db_session,
        DomainSettingCreate(
            domain=SettingDomain.auth,
            key=f"test_setting_{uuid.uuid4().hex[:8]}",
            value_type=SettingValueType.boolean,
            value_text="true",
            value_json=True,
        ),
    )
    with pytest.raises(HTTPException) as exc:
        settings.update(
            db_session,
            str(created.id),
            DomainSettingUpdate(domain=SettingDomain.audit),
        )
    assert exc.value.status_code == 400


def test_settings_api_auth_upsert_and_validation(db_session):
    updated = settings_api_service.upsert_auth_setting(
        db_session,
        "jwt_access_ttl_minutes",
        DomainSettingUpdate(value_text="30"),
    )
    assert updated.value_type == SettingValueType.integer
    assert updated.value_text == "30"
    fetched = settings_api_service.get_auth_setting(
        db_session, "jwt_access_ttl_minutes"
    )
    assert fetched.id == updated.id


def test_settings_api_invalid_key(db_session):
    with pytest.raises(HTTPException) as exc:
        settings_api_service.get_auth_setting(db_session, "bad_key")
    assert exc.value.status_code == 400
