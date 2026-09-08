from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.services.people.hr.info_change_service import InfoChangeService
from app.services.people.self_service_web import SelfServiceWebService


@pytest.mark.parametrize("section", ["qualifications", "certifications"])
@pytest.mark.parametrize("record_id", [None, uuid4()])
def test_single_extended_profile_submission_requires_supporting_file(
    monkeypatch,
    section,
    record_id,
):
    organization_id = uuid4()
    employee_id = uuid4()
    auth = SimpleNamespace(organization_id=organization_id, person_id=uuid4())
    db = MagicMock()
    service = SelfServiceWebService()
    rerendered = object()

    monkeypatch.setattr(service, "_get_employee_id", lambda *_args: employee_id)
    monkeypatch.setattr(service, "_rollback_and_reprime", lambda *_args: None)

    def render(*_args, **kwargs):
        assert kwargs["section"] == section
        assert kwargs["error"] == "Select a supporting file"
        assert kwargs["edit_id"] == record_id
        return rerendered

    monkeypatch.setattr(service, "extended_profile_response", render)

    response = service.submit_extended_profile_response(
        MagicMock(),
        auth,
        db,
        section=section,
        payload={},
        upload=None,
        record_id=record_id,
    )

    assert response is rerendered
    db.commit.assert_not_called()


@pytest.mark.parametrize("section", ["qualifications", "certifications"])
def test_batch_extended_profile_row_requires_supporting_file(monkeypatch, section):
    organization_id = uuid4()
    employee_id = uuid4()
    auth = SimpleNamespace(organization_id=organization_id, person_id=uuid4())
    db = MagicMock()
    service = SelfServiceWebService()
    rerendered = object()
    rows = [{"example": "value", "_upload": None}]

    monkeypatch.setattr(service, "_get_employee_id", lambda *_args: employee_id)
    monkeypatch.setattr(service, "_rollback_and_reprime", lambda *_args: None)
    monkeypatch.setattr(
        InfoChangeService,
        "_validate_extended_payload",
        lambda *_args, **_kwargs: {"example": "value"},
    )

    def render(*_args, **kwargs):
        assert kwargs["section"] == section
        assert kwargs["error"] == "Correct the highlighted rows and try again"
        assert kwargs["form_rows"] is rows
        return rerendered

    monkeypatch.setattr(service, "extended_profile_response", render)

    response = service.submit_extended_profile_batch_response(
        MagicMock(),
        auth,
        db,
        section=section,
        rows=rows,
    )

    assert response is rerendered
    assert rows[0]["_errors"]["supporting_file"] == "Select a supporting file"
    db.commit.assert_not_called()
