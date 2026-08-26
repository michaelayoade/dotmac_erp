from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError as PydanticValidationError

from app.api.service_principal import require_explicit_service_scope
from app.api.sync.sub_attendance import _enforce_punch_rate_limit
from app.models.people.hr.employee import EmployeeStatus
from app.schemas.sync.sub_attendance import SelfcareAttendanceLocation
from app.services.people.attendance.selfcare_integration import (
    SelfcareAttendanceError,
    SelfcareAttendanceIntegrationService,
)
from app.services.common import ValidationError


ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
SUBJECT = uuid.UUID("00000000-0000-0000-0000-000000000002")


def _service_with_matches(*matches):
    db = MagicMock()
    db.scalars.return_value.all.return_value = list(matches)
    return SelfcareAttendanceIntegrationService(db), db


def _employee(**overrides):
    values = {
        "employee_id": uuid.uuid4(),
        "organization_id": ORG_ID,
        "status": EmployeeStatus.ACTIVE,
        "dotmac_sub_access_enabled": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_subject_resolves_exactly_one_active_enabled_employee() -> None:
    employee = _employee()
    service, _db = _service_with_matches(employee)

    assert service.resolve_employee(ORG_ID, SUBJECT) is employee


def test_missing_subject_mapping_fails_closed() -> None:
    service, _db = _service_with_matches()

    with pytest.raises(SelfcareAttendanceError, match="not available") as exc:
        service.resolve_employee(ORG_ID, SUBJECT)
    assert exc.value.code == "employee_not_linked"


def test_duplicate_subject_mapping_fails_closed() -> None:
    service, _db = _service_with_matches(_employee(), _employee())

    with pytest.raises(SelfcareAttendanceError) as exc:
        service.resolve_employee(ORG_ID, SUBJECT)
    assert exc.value.code == "employee_mapping_ambiguous"


@pytest.mark.parametrize(
    ("employee", "code"),
    [
        (_employee(status=EmployeeStatus.TERMINATED), "employee_inactive"),
        (_employee(dotmac_sub_access_enabled=False), "attendance_disabled"),
    ],
)
def test_ineligible_employee_mapping_fails(employee, code) -> None:
    service, _db = _service_with_matches(employee)

    with pytest.raises(SelfcareAttendanceError) as exc:
        service.resolve_employee(ORG_ID, SUBJECT)
    assert exc.value.code == code


def test_location_contract_rejects_out_of_range_and_identity_fields() -> None:
    with pytest.raises(PydanticValidationError):
        SelfcareAttendanceLocation(latitude=91, longitude=7)
    with pytest.raises(PydanticValidationError):
        SelfcareAttendanceLocation(latitude=9, longitude=-181)
    with pytest.raises(PydanticValidationError):
        SelfcareAttendanceLocation(
            latitude=9,
            longitude=7,
            employee_id=str(uuid.uuid4()),
        )


def test_attendance_scope_requires_explicit_grant() -> None:
    dependency = require_explicit_service_scope("sub:attendance:write")

    with pytest.raises(HTTPException) as exc:
        dependency(auth={"scopes": []})
    assert exc.value.status_code == 403
    assert exc.value.detail == {
        "code": "authorization_failed",
        "message": "Service credential lacks the required scope.",
    }
    assert dependency(auth={"scopes": ["sub:attendance:write"]})["scopes"]


def test_missing_geofenced_location_has_stable_error_code() -> None:
    error = SelfcareAttendanceIntegrationService._map_domain_error(
        ValidationError("Location is required to check in.")
    )

    assert error.code == "location_required"
    assert error.status_code == 422


def test_punch_rate_limit_is_per_service_subject_and_idempotency_key(
    monkeypatch,
) -> None:
    redis_client = MagicMock()
    redis_client.set.side_effect = [True, False]
    redis_client.incr.return_value = 1
    monkeypatch.setattr(
        "app.api.sync.sub_attendance.get_redis_client", lambda: redis_client
    )
    auth = {"api_key_id": uuid.uuid4()}

    _enforce_punch_rate_limit(auth, SUBJECT, "same-request")
    _enforce_punch_rate_limit(auth, SUBJECT, "same-request")

    redis_client.incr.assert_called_once()
    assert str(auth["api_key_id"]) in redis_client.incr.call_args.args[0]
    assert str(SUBJECT) in redis_client.incr.call_args.args[0]


@pytest.mark.parametrize("action", ["check_in", "check_out"])
def test_selfcare_punch_delegates_to_canonical_attendance_owner(action) -> None:
    employee = _employee()
    service, _db = _service_with_matches(employee)
    service.resolve_employee = MagicMock(return_value=employee)  # type: ignore[method-assign]
    service._reject_overnight = MagicMock()  # type: ignore[method-assign]
    service._audit = MagicMock()  # type: ignore[method-assign]
    expected = SimpleNamespace(state="authoritative")
    service._state = MagicMock(return_value=expected)  # type: ignore[method-assign]
    record = SimpleNamespace(attendance_id=uuid.uuid4())
    service.attendance = MagicMock()
    getattr(service.attendance, action).return_value = record
    location = SelfcareAttendanceLocation(
        latitude=9.0765,
        longitude=7.3986,
        accuracy_m=12.5,
    )

    result = getattr(service, action)(
        ORG_ID,
        SUBJECT,
        location,
        request_id="correlation-1",
        service_person_id=None,
    )

    kwargs = {
        "latitude": location.latitude,
        "longitude": location.longitude,
    }
    if action == "check_in":
        kwargs["marked_by"] = "SELFCARE"
    getattr(service.attendance, action).assert_called_once_with(
        ORG_ID,
        employee.employee_id,
        **kwargs,
    )
    assert result is expected
