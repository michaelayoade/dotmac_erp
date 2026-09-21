from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

from app.web.deps import get_db_for_org, require_private_performance_mode
from app.web.people import departmental_kpi as routes


def _client() -> tuple[TestClient, MagicMock]:
    app = FastAPI()
    app.include_router(routes.router, prefix="/people")
    db = MagicMock()
    app.dependency_overrides[require_private_performance_mode] = lambda: None
    app.dependency_overrides[routes.require_kpi_read] = lambda: object()
    app.dependency_overrides[routes.require_kpi_export] = lambda: object()
    app.dependency_overrides[get_db_for_org] = lambda: db
    return TestClient(app), db


def test_scorecards_accepts_empty_optional_employee_filter() -> None:
    client, _db = _client()

    with patch.object(
        routes.departmental_kpi_web_service,
        "dashboard_response",
        return_value=HTMLResponse("ok"),
    ) as dashboard:
        response = client.get("/people/perf/kpi-dashboard/scorecards?employee_id=")

    assert response.status_code == 200
    assert dashboard.call_args.kwargs["employee_id"] == ""


def test_export_accepts_empty_optional_employee_filter() -> None:
    client, _db = _client()

    with patch.object(
        routes.departmental_kpi_web_service,
        "export_dashboard_response",
        return_value=HTMLResponse("ok"),
    ) as export:
        response = client.get(
            "/people/perf/kpi-dashboard/export.csv"
            "?department_id=00000000-0000-0000-0000-000000000001"
            "&employee_id="
        )

    assert response.status_code == 200
    assert export.call_args.kwargs["employee_id"] == ""
