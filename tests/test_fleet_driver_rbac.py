from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.web.deps import WebAuthContext
from app.web.fleet import (
    require_fleet_fuel_manage,
    require_fleet_fuel_read,
    require_fleet_incident_read,
    require_fleet_maintenance_read,
    require_fleet_vehicle_read,
)
from scripts.seed_rbac import DEFAULT_ROLES, ROLE_PERMISSIONS


DRIVER_PERMISSIONS = {
    "fleet:access",
    "fleet:dashboard",
    "fleet:fuel:read",
    "fleet:maintenance:read",
    "fleet:incidents:read",
}


def _driver_auth() -> WebAuthContext:
    return WebAuthContext(
        is_authenticated=True,
        person_id=uuid4(),
        organization_id=uuid4(),
        roles=["driver"],
        scopes=sorted(DRIVER_PERMISSIONS),
    )


def test_driver_role_seed_is_read_only_fleet_subset():
    role_names = {name for name, _description in DEFAULT_ROLES}

    assert "driver" in role_names
    assert set(ROLE_PERMISSIONS["driver"]) == DRIVER_PERMISSIONS
    assert not any(
        permission.endswith(":manage") for permission in ROLE_PERMISSIONS["driver"]
    )
    assert "fleet:vehicles:read" not in ROLE_PERMISSIONS["driver"]
    assert "fleet:reports:read" not in ROLE_PERMISSIONS["driver"]


def test_driver_fleet_route_permissions_allow_only_configured_read_areas():
    auth = _driver_auth()

    assert require_fleet_fuel_read(auth) is auth
    assert require_fleet_maintenance_read(auth) is auth
    assert require_fleet_incident_read(auth) is auth

    with pytest.raises(HTTPException) as vehicle_error:
        require_fleet_vehicle_read(auth)
    assert vehicle_error.value.status_code == 403

    with pytest.raises(HTTPException) as manage_error:
        require_fleet_fuel_manage(auth)
    assert manage_error.value.status_code == 403
