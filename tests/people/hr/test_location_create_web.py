from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.api.people.hr import create_location
from app.models.finance.core_org.location import Location, LocationType
from app.models.finance.core_org.organization import Organization
from app.schemas.people.hr import LocationCreate
from app.services.people.hr.web.location_web import LocationWebService
from app.web.deps import WebAuthContext


@pytest.fixture
def location_db(tmp_path):
    engine = create_engine(
        URL.create("sqlite+pysqlite", database=str(tmp_path / "locations.db")),
        execution_options={"schema_translate_map": {"core_org": None}},
    )
    Organization.__table__.create(engine)
    Location.__table__.create(engine)
    organization_id = uuid4()
    with Session(engine) as db:
        db.add(
            Organization(
                organization_id=organization_id,
                organization_code=f"ORG-{organization_id.hex[:8]}",
                legal_name="Location Test Org",
                functional_currency_code="NGN",
                presentation_currency_code="NGN",
                fiscal_year_end_month=12,
                fiscal_year_end_day=31,
                timezone="UTC",
            )
        )
        db.commit()
        yield engine, db, organization_id
    engine.dispose()


@pytest.mark.asyncio
async def test_create_location_form_persists_location_and_redirects(location_db):
    engine, db, organization_id = location_db
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

    response = await LocationWebService.create_location_response(request, auth, db)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/people/hr/locations?success=")
    with Session(engine) as verify_db:
        location = verify_db.scalar(
            select(Location).where(
                Location.organization_id == organization_id,
                Location.location_code == "LAG",
            )
        )
        assert location is not None
        assert location.location_name == "Lagos Branch"
        assert location.location_type == LocationType.BRANCH


def test_create_location_api_preserves_geofence_polygon(location_db):
    engine, db, organization_id = location_db
    polygon = {
        "type": "Polygon",
        "coordinates": [[[3, 6], [4, 6], [4, 7], [3, 6]]],
    }
    payload = LocationCreate(
        location_code="ABJ",
        location_name="Abuja Branch",
        location_type="BRANCH",
        geofence_polygon=polygon,
    )

    response = create_location(payload, organization_id, db)
    db.commit()

    assert response.geofence_polygon == polygon
    with Session(engine) as verify_db:
        location = verify_db.scalar(
            select(Location).where(
                Location.organization_id == organization_id,
                Location.location_code == "ABJ",
            )
        )
        assert location is not None
        assert location.geofence_polygon == polygon
