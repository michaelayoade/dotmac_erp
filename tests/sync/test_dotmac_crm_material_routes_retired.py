from app.api.sync.dotmac_crm import router


def test_legacy_crm_material_request_routes_are_not_registered() -> None:
    retired = {
        ("POST", "/sync/crm/material-requests"),
        ("GET", "/sync/crm/material-requests/{omni_id}"),
    }
    actual = {
        (method, route.path)
        for route in router.routes
        for method in (route.methods or set())
    }

    assert retired.isdisjoint(actual)

