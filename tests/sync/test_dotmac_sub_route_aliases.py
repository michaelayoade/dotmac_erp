from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException, Response
from pydantic import ValidationError

from app.api.sync.dotmac_sub import (
    create_sub_expense_claim,
    create_sub_purchase_invoice,
    create_sub_purchase_order,
    create_sub_purchase_order_variation,
    router,
)
from app.schemas.sync.dotmac_sub import (
    BulkSyncRequest,
    BulkSyncResponse,
    SubExpenseClaimPayload,
    SubMaterialRequestPayload,
    SubPurchaseInvoicePayload,
    SubPurchaseOrderPayload,
)
from app.services.sync.sub.errors import SubReplayConflictError


def test_sub_sync_router_is_exactly_the_canonical_sixteen_routes() -> None:
    expected = {
        ("POST", "/sync/sub/bulk"),
        ("POST", "/sync/sub/expense-claims"),
        ("GET", "/sync/sub/expense-claims/{source_request_id}"),
        ("GET", "/sync/sub/expense-categories"),
        ("POST", "/sync/sub/material-requests"),
        ("GET", "/sync/sub/material-requests/{source_request_id}"),
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

    assert actual == expected
    assert all(
        route.endpoint.__module__ == "app.api.sync.dotmac_sub"
        for route in router.routes
    )


def test_bulk_route_uses_the_version_two_ticket_free_contract() -> None:
    bulk_route = next(
        route for route in router.routes if route.path == "/sync/sub/bulk"
    )

    assert bulk_route.response_model is BulkSyncResponse
    assert BulkSyncResponse().contract_version == 2
    assert "tickets" not in BulkSyncRequest.model_fields
    with pytest.raises(ValidationError):
        BulkSyncRequest.model_validate({"tickets": []})


@pytest.mark.parametrize(
    ("model", "legacy_field"),
    [
        (SubMaterialRequestPayload, "omni_id"),
        (SubExpenseClaimPayload, "omni_id"),
        (SubPurchaseOrderPayload, "omni_work_order_id"),
        (SubPurchaseInvoicePayload, "sub_invoice_id"),
    ],
)
def test_sub_payloads_fail_closed_on_retired_wire_names(model, legacy_field) -> None:
    assert model.model_config["extra"] == "forbid"
    with pytest.raises(ValidationError) as exc_info:
        model.model_validate({legacy_field: "retired"})
    assert "extra_forbidden" in str(exc_info.value)


def test_sub_adapter_has_no_retired_provider_runtime_names() -> None:
    source = (
        Path(__file__).resolve().parents[2] / "app/api/sync/dotmac_sub.py"
    ).read_text(encoding="utf-8")
    assert "crm" not in source.lower()
    assert "operational.py" not in source


@pytest.mark.parametrize(
    ("endpoint", "service_method", "takes_response"),
    [
        (create_sub_expense_claim, "accept_expense_claim", True),
        (create_sub_purchase_order, "create_purchase_order", False),
        (
            create_sub_purchase_order_variation,
            "create_purchase_order_variation",
            False,
        ),
        (create_sub_purchase_invoice, "create_purchase_invoice", False),
    ],
)
def test_sub_command_adapters_translate_replay_conflicts_to_409(
    endpoint, service_method: str, takes_response: bool
) -> None:
    service = MagicMock()
    getattr(service, service_method).side_effect = SubReplayConflictError(
        "source identity was reused with a different immutable payload"
    )
    auth = {"organization_id": uuid4(), "person_id": uuid4()}

    with (
        patch("app.api.sync.dotmac_sub.DotmacSubSyncService", return_value=service),
        pytest.raises(HTTPException) as exc_info,
    ):
        if takes_response:
            endpoint(
                payload=MagicMock(),
                response=Response(),
                auth=auth,
                db=MagicMock(),
            )
        else:
            endpoint(payload=MagicMock(), auth=auth, db=MagicMock())

    assert exc_info.value.status_code == 409
    assert "different immutable payload" in exc_info.value.detail
