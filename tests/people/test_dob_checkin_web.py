from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from fastapi import HTTPException
from starlette.datastructures import FormData

from app.services.people import REQUIRE_DOB_FOR_CHECKIN_SETTING
from app.services.people.attendance import CheckInRequirementError
from app.services.people.self_service_web import (
    ERP_PERSONAL_RECORDS_URL,
    build_check_in_requirement_error_context,
    self_service_web_service,
)
from app.services.people.settings_web import people_settings_web_service
from app.web.deps import WebAuthContext
from app.web.people.settings import update_hr_settings


@pytest.mark.asyncio
async def test_non_admin_cannot_submit_dob_check_in_setting() -> None:
    request = SimpleNamespace(
        form=AsyncMock(
            return_value=FormData([(REQUIRE_DOB_FOR_CHECKIN_SETTING, "true")])
        )
    )
    auth = WebAuthContext(
        is_authenticated=True,
        person_id=UUID("00000000-0000-0000-0000-000000000001"),
        organization_id=UUID("00000000-0000-0000-0000-000000000002"),
        roles=["hr_manager"],
        scopes=["hr:access"],
    )

    with pytest.raises(HTTPException) as exc_info:
        await update_hr_settings(
            request=request,
            auth=auth,
            db=MagicMock(),
            sync_db=MagicMock(),
        )

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_admin_can_enable_dob_check_in_setting(monkeypatch) -> None:
    request = SimpleNamespace(
        form=AsyncMock(
            return_value=FormData([(REQUIRE_DOB_FOR_CHECKIN_SETTING, "true")])
        )
    )
    auth = WebAuthContext(
        is_authenticated=True,
        person_id=UUID("00000000-0000-0000-0000-000000000001"),
        organization_id=UUID("00000000-0000-0000-0000-000000000002"),
        roles=["admin"],
        scopes=["hr:access"],
    )
    update_requirement = MagicMock()
    monkeypatch.setattr(
        people_settings_web_service,
        "update_hr_settings",
        AsyncMock(return_value=(True, None)),
    )
    monkeypatch.setattr(
        people_settings_web_service,
        "update_check_in_requirements",
        update_requirement,
    )
    monkeypatch.setattr(
        people_settings_web_service,
        "update_employee_invite_email_template",
        MagicMock(return_value=(True, None)),
    )
    monkeypatch.setattr(
        people_settings_web_service,
        "update_default_invite_attachment",
        AsyncMock(return_value=(True, None)),
    )
    sync_db = MagicMock()

    response = await update_hr_settings(
        request=request,
        auth=auth,
        db=MagicMock(),
        sync_db=sync_db,
    )

    assert response.status_code == 303
    update_requirement.assert_called_once_with(
        sync_db,
        auth.organization_id,
        require_dob_for_erp_checkin=True,
        changed_by_id=auth.person_id,
    )


def test_web_check_in_redirects_with_machine_readable_dob_error(
    monkeypatch,
) -> None:
    auth = WebAuthContext(
        is_authenticated=True,
        person_id=UUID("00000000-0000-0000-0000-000000000001"),
        organization_id=UUID("00000000-0000-0000-0000-000000000002"),
        roles=["employee"],
        scopes=["self:access"],
    )
    monkeypatch.setattr(
        self_service_web_service,
        "_get_employee_id",
        lambda *_args: UUID("00000000-0000-0000-0000-000000000003"),
    )

    class RejectingAttendanceService:
        def __init__(self, _db) -> None:
            pass

        def check_in(self, *_args, **_kwargs) -> None:
            raise CheckInRequirementError()

    monkeypatch.setattr(
        "app.services.people.self_service_web.AttendanceService",
        RejectingAttendanceService,
    )

    response = self_service_web_service.check_in_response(auth, MagicMock())

    assert response.status_code == 303
    assert "checkin_error=ERP_DOB_REQUIRED_FOR_CHECKIN" in response.headers["location"]


def test_hr_settings_template_contains_admin_dob_toggle() -> None:
    template = Path("templates/people/settings/hr.html").read_text(encoding="utf-8")

    assert "{% if can_manage_check_in_requirements %}" in template
    assert "Require Date of Birth for ERP Check-In" in template
    assert 'name="require_dob_for_erp_checkin"' in template


def test_attendance_template_has_targeted_dob_error_action() -> None:
    template = Path("templates/people/self/attendance.html").read_text(encoding="utf-8")

    assert "{% if check_in_requirement_error %}" in template
    assert "Date of Birth Required" in template
    assert "Update ERP Records" not in template
    assert "check_in_requirement_error.action_label" in template
    assert "check_in_requirement_error.action_url" in template

    error = build_check_in_requirement_error_context("ERP_DOB_REQUIRED_FOR_CHECKIN")
    assert error is not None
    assert error["action_label"] == "Update ERP Records"
    assert error["action_url"] == ERP_PERSONAL_RECORDS_URL


def test_other_check_in_errors_do_not_show_erp_records_action() -> None:
    assert build_check_in_requirement_error_context("outside_geofence") is None
    assert build_check_in_requirement_error_context(None) is None


def test_personal_records_target_opens_personal_details() -> None:
    template = Path("templates/people/self/tax_info.html").read_text(encoding="utf-8")

    assert "request.query_params.get('focus') == 'personal-details'" in template
    assert ERP_PERSONAL_RECORDS_URL == ("/people/self/tax-info?focus=personal-details")
