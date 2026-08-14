from app.api.sync.dotmac_sub import router
from app.schemas.sync.dotmac_crm import BulkSyncResponse


def test_sub_sync_router_covers_every_phase5_transport() -> None:
    expected = {
        ("POST", "/sync/sub/bulk"),
        ("POST", "/sync/sub/expense-claims"),
        ("GET", "/sync/sub/expense-claims/{omni_id}"),
        ("GET", "/sync/sub/expense-categories"),
        ("POST", "/sync/sub/material-requests"),
        ("GET", "/sync/sub/material-requests/{omni_id}"),
        ("POST", "/sync/sub/purchase-orders"),
        ("POST", "/sync/sub/purchase-orders/variations"),
        ("POST", "/sync/sub/purchase-invoices"),
        ("GET", "/sync/sub/purchase-invoices/{source_invoice_id}"),
        (
            "POST",
            "/sync/sub/purchase-invoices/{purchase_invoice_id}/attachments",
        ),
        ("GET", "/sync/sub/inventory"),
        ("GET", "/sync/sub/inventory/{item_id}"),
        ("GET", "/sync/sub/inventory/meta/categories"),
        ("GET", "/sync/sub/inventory/meta/warehouses"),
        ("GET", "/sync/sub/inventory/serials/available"),
    }
    actual = {
        (method, route.path)
        for route in router.routes
        for method in (route.methods or set())
    }

    assert expected <= actual


def test_material_support_routes_use_the_neutral_sub_adapter() -> None:
    material_routes = {
        (method, route.path): route
        for route in router.routes
        for method in (route.methods or set())
        if "material-requests" in route.path
    }

    assert (
        material_routes[("POST", "/sync/sub/material-requests")].endpoint.__module__
        == "app.api.sync.dotmac_sub"
    )
    assert (
        material_routes[
            ("GET", "/sync/sub/material-requests/{omni_id}")
        ].endpoint.__module__
        == "app.api.sync.dotmac_sub"
    )


def test_operational_bulk_route_uses_the_real_version_two_contract() -> None:
    bulk_route = next(
        route for route in router.routes if route.path == "/sync/sub/bulk"
    )

    assert bulk_route.endpoint.__module__ == "app.api.sync.dotmac_sub"
    assert bulk_route.response_model is BulkSyncResponse
    assert BulkSyncResponse().contract_version == 2
