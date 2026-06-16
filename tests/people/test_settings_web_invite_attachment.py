from __future__ import annotations

from io import BytesIO

import pytest
from sqlalchemy import select
from starlette.datastructures import UploadFile

from app.models.domain_settings import DomainSetting, SettingDomain
from app.services.people.hr.invite_attachment import (
    set_default_invite_attachment_metadata,
)
from app.web.deps import WebAuthContext
from app.web.people.settings import download_default_invite_attachment
from app.services.people.settings_web import people_settings_web_service


def test_employee_invite_email_context_matches_invite_flow(db_session, person):
    preview = people_settings_web_service.get_employee_invite_email_context(
        db_session,
        person.organization_id,
    )

    assert preview["subject"] == "Reset your password"
    assert "Hi {name}" in preview["body_html"]
    assert "Reset password" in preview["body_html"]
    assert preview["next_url"] == "/people/self/tax-info"
    assert preview["link_pattern"] == (
        "{app_url}/reset-password?token=<secure-token>&next=/people/self/tax-info"
    )
    assert "Work email first" in preview["recipients"]
    assert preview["email_module"] == "ADMIN"
    assert "welcome pack" in preview["attachment"]


def test_update_employee_invite_email_template_stores_org_setting(db_session, person):
    success, error = people_settings_web_service.update_employee_invite_email_template(
        db_session,
        person.organization_id,
        {
            "employee_invite_subject": "Welcome to Dotmac",
            "employee_invite_body_html": "<p>Hello {name}</p><p>{reset_link}</p>",
            "employee_invite_body_text": "Hello {name}: {reset_link}",
        },
    )

    assert success is True
    assert error is None

    preview = people_settings_web_service.get_employee_invite_email_context(
        db_session,
        person.organization_id,
    )
    assert preview["subject"] == "Welcome to Dotmac"
    assert preview["body_html"] == "<p>Hello {name}</p><p>{reset_link}</p>"
    assert preview["body_text"] == "Hello {name}: {reset_link}"


@pytest.mark.asyncio
async def test_update_default_invite_attachment_stores_org_setting(
    db_session, person, monkeypatch
):
    uploaded: dict[str, bytes] = {}

    class _Storage:
        def upload(self, key, data, content_type=None):
            uploaded[key] = data

        def delete(self, key):
            uploaded.pop(key, None)

    monkeypatch.setattr("app.services.storage.get_storage", lambda: _Storage())
    monkeypatch.setattr(
        "app.services.people.settings_web.get_storage", lambda: _Storage()
    )

    success, error = await people_settings_web_service.update_default_invite_attachment(
        db_session,
        person.organization_id,
        file=UploadFile(BytesIO(b"%PDF welcome pack"), filename="welcome.pdf"),
    )

    assert success is True
    assert error is None

    setting = db_session.scalar(
        select(DomainSetting).where(
            DomainSetting.domain == SettingDomain.settings,
            DomainSetting.key == "hr_default_employee_invite_attachment",
            DomainSetting.organization_id == person.organization_id,
            DomainSetting.is_active.is_(True),
        )
    )
    assert setting is not None
    assert setting.value_json["filename"] == "welcome.pdf"
    assert setting.value_json["content_type"] == "application/pdf"
    assert uploaded[setting.value_json["s3_key"]] == b"%PDF welcome pack"


@pytest.mark.asyncio
async def test_update_default_invite_attachment_can_clear_existing(
    db_session, person, monkeypatch
):
    deleted: list[str] = []

    class _Storage:
        def upload(self, key, data, content_type=None):
            pass

        def delete(self, key):
            deleted.append(key)

    monkeypatch.setattr("app.services.storage.get_storage", lambda: _Storage())
    monkeypatch.setattr(
        "app.services.people.settings_web.get_storage", lambda: _Storage()
    )

    await people_settings_web_service.update_default_invite_attachment(
        db_session,
        person.organization_id,
        file=UploadFile(BytesIO(b"%PDF welcome pack"), filename="welcome.pdf"),
    )
    deleted.clear()
    setting = db_session.scalar(
        select(DomainSetting).where(
            DomainSetting.domain == SettingDomain.settings,
            DomainSetting.key == "hr_default_employee_invite_attachment",
            DomainSetting.organization_id == person.organization_id,
        )
    )
    s3_key = setting.value_json["s3_key"]

    success, error = await people_settings_web_service.update_default_invite_attachment(
        db_session,
        person.organization_id,
        file=None,
        remove_existing=True,
    )

    assert success is True
    assert error is None
    assert setting.is_active is False
    assert deleted == [s3_key]


@pytest.mark.asyncio
async def test_download_default_invite_attachment_returns_file(
    db_session, person, monkeypatch
):
    class _Storage:
        def download(self, key):
            assert key == "hr_invites/org/welcome.pdf"
            return b"welcome pack"

    set_default_invite_attachment_metadata(
        db_session,
        person.organization_id,
        {
            "s3_key": "hr_invites/org/welcome.pdf",
            "filename": "welcome.pdf",
            "content_type": "application/pdf",
        },
    )
    db_session.commit()
    monkeypatch.setattr("app.web.people.settings.get_storage", lambda: _Storage())

    auth = WebAuthContext(
        is_authenticated=True,
        person_id=person.id,
        organization_id=person.organization_id,
        roles=["hr_manager"],
        scopes=["hr:access"],
    )

    response = await download_default_invite_attachment(auth=auth, db=db_session)

    assert response.body == b"welcome pack"
    assert response.media_type == "application/pdf"
    assert response.headers["Content-Disposition"] == (
        'attachment; filename="welcome.pdf"'
    )
