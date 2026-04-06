from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.web.deps import WebAuthContext
from app.web.fleet import (
    fleet_reports_expense_vehicle_detail,
    fleet_reports_invoice_vehicle_detail,
)


TEST_ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
TEST_PERSON_ID = UUID("00000000-0000-0000-0000-000000000002")


def _make_request(path: str) -> Request:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [],
            "query_string": b"",
        }
    )
    request.state.csrf_form = ""
    return request


def _make_auth() -> WebAuthContext:
    return WebAuthContext(
        is_authenticated=True,
        person_id=TEST_PERSON_ID,
        organization_id=TEST_ORG_ID,
        employee_id=TEST_PERSON_ID,
        user_name="Fleet Tester",
        user_initials="FT",
        roles=["admin"],
        scopes=["fleet:access"],
    )


def _install_template_stub(monkeypatch):
    def _template_response(request, template_name, context):
        return SimpleNamespace(
            status_code=200,
            template_name=template_name,
            context=context,
        )

    monkeypatch.setattr("app.web.fleet.base_context", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        "app.web.fleet.templates.TemplateResponse",
        _template_response,
    )


def test_expense_detail_route_accepts_blank_filters_and_parses_month(monkeypatch):
    captured: dict[str, object] = {}

    class FakeFleetWebService:
        def __init__(self, db):
            self.db = db

        def reports_expense_vehicle_context(self, organization_id, vehicle_id, **kwargs):
            captured["organization_id"] = organization_id
            captured["vehicle_id"] = vehicle_id
            captured.update(kwargs)
            return {"vehicle": SimpleNamespace(vehicle_id=vehicle_id)}

    _install_template_stub(monkeypatch)
    monkeypatch.setattr("app.web.fleet.FleetWebService", FakeFleetWebService)

    vehicle_id = uuid4()
    response = fleet_reports_expense_vehicle_detail(
        request=_make_request(f"/fleet/reports/expenses/{vehicle_id}"),
        vehicle_id=vehicle_id,
        start_date="",
        end_date="",
        year="",
        month="4",
        auth=_make_auth(),
        db=object(),
    )

    assert response.status_code == 200
    assert captured["organization_id"] == TEST_ORG_ID
    assert captured["vehicle_id"] == vehicle_id
    assert captured["start_date"] is None
    assert captured["end_date"] is None
    assert captured["year"] is None
    assert captured["month"] == 4


def test_expense_detail_route_parses_start_date_only(monkeypatch):
    captured: dict[str, object] = {}

    class FakeFleetWebService:
        def __init__(self, db):
            self.db = db

        def reports_expense_vehicle_context(self, organization_id, vehicle_id, **kwargs):
            captured.update(kwargs)
            return {"vehicle": SimpleNamespace(vehicle_id=vehicle_id)}

    _install_template_stub(monkeypatch)
    monkeypatch.setattr("app.web.fleet.FleetWebService", FakeFleetWebService)

    response = fleet_reports_expense_vehicle_detail(
        request=_make_request("/fleet/reports/expenses/test"),
        vehicle_id=uuid4(),
        start_date="2026-04-01",
        end_date="",
        year="",
        month="",
        auth=_make_auth(),
        db=object(),
    )

    assert response.status_code == 200
    assert captured["start_date"] == date(2026, 4, 1)
    assert captured["end_date"] is None
    assert captured["year"] is None
    assert captured["month"] is None


def test_invoice_detail_route_accepts_blank_filters_and_parses_year(monkeypatch):
    captured: dict[str, object] = {}

    class FakeFleetWebService:
        def __init__(self, db):
            self.db = db

        def reports_invoice_vehicle_context(self, organization_id, vehicle_id, **kwargs):
            captured["organization_id"] = organization_id
            captured["vehicle_id"] = vehicle_id
            captured.update(kwargs)
            return {"vehicle": SimpleNamespace(vehicle_id=vehicle_id)}

    _install_template_stub(monkeypatch)
    monkeypatch.setattr("app.web.fleet.FleetWebService", FakeFleetWebService)

    vehicle_id = uuid4()
    response = fleet_reports_invoice_vehicle_detail(
        request=_make_request(f"/fleet/reports/invoices/{vehicle_id}"),
        vehicle_id=vehicle_id,
        start_date="",
        end_date="",
        year="2026",
        month="",
        auth=_make_auth(),
        db=object(),
    )

    assert response.status_code == 200
    assert captured["organization_id"] == TEST_ORG_ID
    assert captured["vehicle_id"] == vehicle_id
    assert captured["start_date"] is None
    assert captured["end_date"] is None
    assert captured["year"] == 2026
    assert captured["month"] is None


def test_invoice_detail_route_parses_end_date_only(monkeypatch):
    captured: dict[str, object] = {}

    class FakeFleetWebService:
        def __init__(self, db):
            self.db = db

        def reports_invoice_vehicle_context(self, organization_id, vehicle_id, **kwargs):
            captured.update(kwargs)
            return {"vehicle": SimpleNamespace(vehicle_id=vehicle_id)}

    _install_template_stub(monkeypatch)
    monkeypatch.setattr("app.web.fleet.FleetWebService", FakeFleetWebService)

    response = fleet_reports_invoice_vehicle_detail(
        request=_make_request("/fleet/reports/invoices/test"),
        vehicle_id=uuid4(),
        start_date="",
        end_date="2026-04-30",
        year="",
        month="",
        auth=_make_auth(),
        db=object(),
    )

    assert response.status_code == 200
    assert captured["start_date"] is None
    assert captured["end_date"] == date(2026, 4, 30)
    assert captured["year"] is None
    assert captured["month"] is None


@pytest.mark.parametrize(
    ("route_name", "kwargs"),
    [
        (
            "expense",
            {
                "start_date": "",
                "end_date": "",
                "year": "abc",
                "month": "",
            },
        ),
        (
            "invoice",
            {
                "start_date": "",
                "end_date": "",
                "year": "",
                "month": "13",
            },
        ),
    ],
)
def test_detail_routes_reject_invalid_filter_values(route_name, kwargs, monkeypatch):
    _install_template_stub(monkeypatch)

    class FakeFleetWebService:
        def __init__(self, db):
            self.db = db

    monkeypatch.setattr("app.web.fleet.FleetWebService", FakeFleetWebService)

    route = (
        fleet_reports_expense_vehicle_detail
        if route_name == "expense"
        else fleet_reports_invoice_vehicle_detail
    )

    with pytest.raises(HTTPException) as exc_info:
        route(
            request=_make_request("/fleet/reports/test"),
            vehicle_id=uuid4(),
            auth=_make_auth(),
            db=object(),
            **kwargs,
        )

    assert exc_info.value.status_code == 400
