from app.api.sync.dotmac_sub import router


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
