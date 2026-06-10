"""
Tests for the material-requests JSON API (mobile warehouse flows).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

ORG_ID = uuid4()
PERSON_ID = uuid4()

SVC = "app.services.inventory.material_request_web.MaterialRequestWebService"


def _auth() -> dict:
    return {
        "organization_id": str(ORG_ID),
        "person_id": str(PERSON_ID),
        "roles": ["admin"],
        "scopes": [],
    }


@pytest.fixture()
def inv_client():
    from app.api.deps import (
        get_db_with_org,
        require_organization_id,
        require_tenant_auth,
    )
    from app.api.inventory import router as inventory_router

    app = FastAPI()
    app.include_router(inventory_router, prefix="/api/v1")
    app.dependency_overrides[require_tenant_auth] = _auth
    app.dependency_overrides[require_organization_id] = lambda: ORG_ID
    app.dependency_overrides[get_db_with_org] = lambda: MagicMock()
    for dep in inventory_router.dependencies:
        app.dependency_overrides[dep.dependency] = _auth
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        stack = list(route.dependant.dependencies)
        while stack:
            dependant = stack.pop()
            stack.extend(dependant.dependencies)
            qualname = getattr(dependant.call, "__qualname__", "")
            if (
                "_require_tenant_permission" in qualname
                or "require_feature" in qualname
            ):
                app.dependency_overrides[dependant.call] = _auth
    return TestClient(app)


def _fake_request(status: str = "DRAFT") -> SimpleNamespace:
    return SimpleNamespace(
        request_id=uuid4(),
        organization_id=ORG_ID,
        request_number="MR-0042",
        request_type="PURCHASE",
        status=status,
        schedule_date=date(2026, 6, 15),
        default_warehouse_id=uuid4(),
        transfer_to_warehouse_id=None,
        remarks="Restock shelf A",
        items=[
            SimpleNamespace(
                item_id=uuid4(),
                inventory_item_id=uuid4(),
                warehouse_id=None,
                requested_qty=Decimal("5"),
                ordered_qty=Decimal("0"),
                uom="EA",
                schedule_date=None,
            )
        ],
    )


class TestListMaterialRequests:
    @patch(f"{SVC}.list_requests")
    def test_list_with_mine_filter(self, mock_list, inv_client) -> None:
        mock_list.return_value = [_fake_request()]

        resp = inv_client.get(
            "/api/v1/inventory/material-requests",
            params={"mine": "true", "status": "DRAFT"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["items"][0]["request_number"] == "MR-0042"
        kwargs = mock_list.call_args.kwargs
        assert kwargs["requested_by_id"] == PERSON_ID
        assert kwargs["status"] is not None

    @patch(f"{SVC}.list_requests")
    def test_list_without_mine_passes_none(self, mock_list, inv_client) -> None:
        mock_list.return_value = []

        resp = inv_client.get("/api/v1/inventory/material-requests")

        assert resp.status_code == 200
        assert mock_list.call_args.kwargs["requested_by_id"] is None

    @patch(f"{SVC}.list_requests")
    def test_invalid_status_is_400(self, mock_list, inv_client) -> None:
        resp = inv_client.get(
            "/api/v1/inventory/material-requests", params={"status": "BOGUS"}
        )
        assert resp.status_code == 400
        mock_list.assert_not_called()


class TestGetMaterialRequest:
    @patch(f"{SVC}.get_request")
    def test_get_found(self, mock_get, inv_client) -> None:
        req = _fake_request()
        mock_get.return_value = req

        resp = inv_client.get(f"/api/v1/inventory/material-requests/{req.request_id}")

        assert resp.status_code == 200
        assert resp.json()["status"] == "DRAFT"

    @patch(f"{SVC}.get_request")
    def test_get_not_found_maps_404(self, mock_get, inv_client) -> None:
        mock_get.side_effect = ValueError("Material request not found")

        resp = inv_client.get(f"/api/v1/inventory/material-requests/{uuid4()}")

        assert resp.status_code == 404


class TestCreateMaterialRequest:
    @patch(f"{SVC}.create_from_form")
    def test_create_maps_payload_and_requester(self, mock_create, inv_client) -> None:
        mock_create.return_value = _fake_request()
        inv_item = uuid4()

        resp = inv_client.post(
            "/api/v1/inventory/material-requests",
            json={
                "request_type": "PURCHASE",
                "remarks": "Restock",
                "items": [{"inventory_item_id": str(inv_item), "requested_qty": "5"}],
            },
        )

        assert resp.status_code == 201
        kwargs = mock_create.call_args.kwargs
        assert kwargs["request_type"] == "PURCHASE"
        assert kwargs["requested_by_id"] == str(PERSON_ID)
        assert kwargs["items"][0]["inventory_item_id"] == str(inv_item)
        assert kwargs["items"][0]["requested_qty"] == "5"

    @patch(f"{SVC}.create_from_form")
    def test_create_requires_items(self, mock_create, inv_client) -> None:
        resp = inv_client.post(
            "/api/v1/inventory/material-requests",
            json={"request_type": "PURCHASE", "items": []},
        )
        assert resp.status_code == 422
        mock_create.assert_not_called()

    @patch(f"{SVC}.create_from_form")
    def test_transfer_validation_error_maps_400(self, mock_create, inv_client) -> None:
        mock_create.side_effect = ValueError(
            "Destination warehouse is required for transfers"
        )

        resp = inv_client.post(
            "/api/v1/inventory/material-requests",
            json={
                "request_type": "TRANSFER",
                "items": [{"inventory_item_id": str(uuid4()), "requested_qty": "1"}],
            },
        )

        assert resp.status_code == 400


class TestSubmitMaterialRequest:
    @patch(f"{SVC}.submit_request")
    def test_submit_passes_actor(self, mock_submit, inv_client) -> None:
        req = _fake_request("SUBMITTED")
        mock_submit.return_value = req

        resp = inv_client.post(
            f"/api/v1/inventory/material-requests/{req.request_id}/submit"
        )

        assert resp.status_code == 200
        assert resp.json()["status"] == "SUBMITTED"
        args = mock_submit.call_args.args
        assert args[1] == ORG_ID
        assert args[2] == PERSON_ID

    @patch(f"{SVC}.submit_request")
    def test_submit_non_draft_maps_400(self, mock_submit, inv_client) -> None:
        mock_submit.side_effect = ValueError("Only draft requests can be submitted")

        resp = inv_client.post(f"/api/v1/inventory/material-requests/{uuid4()}/submit")

        assert resp.status_code == 400

    @patch(f"{SVC}.submit_request")
    def test_submit_missing_maps_404(self, mock_submit, inv_client) -> None:
        mock_submit.side_effect = ValueError("Material request not found")

        resp = inv_client.post(f"/api/v1/inventory/material-requests/{uuid4()}/submit")

        assert resp.status_code == 404
