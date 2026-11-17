from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from starlette.requests import Request

from app.models.finance.core_org.location import Location
from app.services.people.hr.web.location_web import LocationWebService
from app.web.deps import WebAuthContext


@pytest.mark.asyncio
async def test_create_location_form_adds_location_and_redirects():
    organization_id = uuid4()
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/people/hr/locations/new",
            "headers": [],
            "query_string": b"",
        }
    )
    request.state.csrf_form = {
        "location_code": "LAG",
        "location_name": "Lagos Branch",
        "location_type": "BRANCH",
    }
    auth = WebAuthContext(
        is_authenticated=True,
        organization_id=organization_id,
        roles=["hr_manager"],
        scopes=["hr:access"],
    )
    db = MagicMock()

    response = await LocationWebService.create_location_response(request, auth, db)

    location = db.add.call_args.args[0]
    assert isinstance(location, Location)
    assert location.organization_id == organization_id
    assert location.location_code == "LAG"
    assert location.location_name == "Lagos Branch"
    db.flush.assert_called_once_with()
    db.refresh.assert_called_once_with(location)
    db.commit.assert_called_once_with()
    assert response.status_code == 303
    assert response.headers["location"].startswith("/people/hr/locations?success=")
