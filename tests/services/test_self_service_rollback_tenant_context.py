"""Regression tests for self-service form re-renders after rollback."""

from unittest.mock import MagicMock
from uuid import uuid4

from app.services.people import self_service_web
from app.services.people.hr.info_change_service import InfoChangeService
from app.services.people.self_service_web import SelfServiceWebService
from app.web.deps import WebAuthContext


def test_rollback_and_reprime_restores_tenant_context(monkeypatch) -> None:
    db = MagicMock()
    organization_id = uuid4()
    calls: list[tuple[str, object]] = []
    db.rollback.side_effect = lambda: calls.append(("rollback", db))
    monkeypatch.setattr(
        self_service_web,
        "prime_tenant_context",
        lambda session, org_id: calls.append(("prime", (session, org_id))),
    )

    SelfServiceWebService._rollback_and_reprime(db, organization_id)

    assert calls == [
        ("rollback", db),
        ("prime", (db, organization_id)),
    ]


def test_qualification_error_reprime_happens_before_form_rerender(
    monkeypatch,
) -> None:
    organization_id = uuid4()
    person_id = uuid4()
    employee_id = uuid4()
    db = MagicMock()
    auth = WebAuthContext(
        is_authenticated=True,
        organization_id=organization_id,
        person_id=person_id,
        employee_id=employee_id,
    )
    service = SelfServiceWebService()
    rerendered = object()
    events: list[str] = []

    monkeypatch.setattr(service, "_get_employee_id", lambda *_args: employee_id)
    monkeypatch.setattr(
        InfoChangeService,
        "submit_extended_change_request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValueError("Qualification validation failed")
        ),
    )
    db.rollback.side_effect = lambda: events.append("rollback")
    monkeypatch.setattr(
        self_service_web,
        "prime_tenant_context",
        lambda _db, _org_id: events.append("prime"),
    )

    def render(*_args, **kwargs):
        events.append("render")
        assert kwargs["section"] == "qualifications"
        assert kwargs["error"] == "Qualification validation failed"
        return rerendered

    monkeypatch.setattr(service, "extended_profile_response", render)

    response = service.submit_extended_profile_response(
        MagicMock(),
        auth,
        db,
        section="qualifications",
        payload={
            "qualification_type": "BACHELORS",
            "qualification_name": "BSc Computer Science",
            "institution_name": "University of Abuja",
            "start_date": "2018-09-01",
            "end_date": "2022-07-01",
        },
    )

    assert response is rerendered
    assert events == ["rollback", "prime", "render"]
    assert db.commit.call_count == 0


def test_all_employee_profile_rerenders_restore_context() -> None:
    source = (
        SelfServiceWebService.submit_extended_profile_response.__code__.co_names,
        SelfServiceWebService.submit_extended_profile_batch_response.__code__.co_names,
        SelfServiceWebService.submit_document_upload_response.__code__.co_names,
        SelfServiceWebService.submit_document_upload_batch_response.__code__.co_names,
    )

    assert all("_rollback_and_reprime" in names for names in source)
