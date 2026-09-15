"""Saved-view form boundary regressions; no database or template rendering."""

import asyncio
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from starlette.datastructures import FormData, UploadFile

from app.services.people.perf.web.kpi_dashboard_web import KPIDashboardWebService


def _save_form(name):
    service = MagicMock()
    service.save_view.return_value = str(uuid4())
    request = MagicMock()
    request.form = AsyncMock(return_value=FormData({"name": name}))
    auth = MagicMock(
        organization_id=uuid4(),
        person_id=uuid4(),
        roles=["hr_manager"],
        leave_write_restricted=False,
    )
    return service, request, auth


def test_save_view_validates_and_normalizes_name_before_service_call():
    service, request, auth = _save_form("  Engineering monthly  ")
    with patch(
        "app.services.people.perf.web.kpi_dashboard_web.KPIDashboardService",
        return_value=service,
    ):
        response = asyncio.run(
            KPIDashboardWebService().save_response(request, auth, MagicMock())
        )
    assert response.status_code == 303
    assert response.headers["Cache-Control"] == "private, no-store"
    assert service.save_view.call_args.args[2] == "Engineering monthly"
    assert service.save_view.call_args.kwargs["view_id"] is None


@pytest.mark.parametrize("name", ["", "   ", "a" * 81])
def test_save_view_rejects_invalid_name_before_service_mutation(name):
    service, request, auth = _save_form(name)
    with (
        patch(
            "app.services.people.perf.web.kpi_dashboard_web.KPIDashboardService",
            return_value=service,
        ),
        pytest.raises(HTTPException) as raised,
    ):
        asyncio.run(KPIDashboardWebService().save_response(request, auth, MagicMock()))
    assert raised.value.status_code == 400
    service.save_view.assert_not_called()


def test_save_view_rejects_uploaded_file_instead_of_coercing_it_to_name():
    with BytesIO(b"not a view name") as contents:
        upload = UploadFile(file=contents, filename="name.txt")
        service, request, auth = _save_form(upload)
        with (
            patch(
                "app.services.people.perf.web.kpi_dashboard_web.KPIDashboardService",
                return_value=service,
            ),
            pytest.raises(HTTPException) as raised,
        ):
            asyncio.run(
                KPIDashboardWebService().save_response(request, auth, MagicMock())
            )
    assert raised.value.status_code == 400
    service.save_view.assert_not_called()
