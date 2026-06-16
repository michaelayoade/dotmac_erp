"""
Tests for the inventory counts JSON API (mobile warehouse flows).

Routes wrap InventoryCountService; tests mock the service and verify
wiring, org scoping, payload mapping, and response shapes.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ORG_ID = uuid4()
PERSON_ID = uuid4()


def _auth() -> dict:
    return {
        "organization_id": str(ORG_ID),
        "person_id": str(PERSON_ID),
        "roles": ["admin"],
        "scopes": ["inventory:access"],
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
    # Router-level AND parameter-level deps (tenant auth, permission
    # closures, feature flag) are closure instances — override them by
    # reference from the resolved dependant tree, not by re-calling their
    # factories (which would produce different objects).
    for dep in inventory_router.dependencies:
        app.dependency_overrides[dep.dependency] = _auth
    from fastapi.routing import APIRoute

    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        stack = list(route.dependant.dependencies)
        while stack:
            dependant = stack.pop()
            stack.extend(dependant.dependencies)
            call = dependant.call
            qualname = getattr(call, "__qualname__", "")
            if (
                "_require_tenant_permission" in qualname
                or "require_feature" in qualname
            ):
                app.dependency_overrides[call] = _auth
    return TestClient(app)


def _fake_count(status: str = "IN_PROGRESS") -> SimpleNamespace:
    return SimpleNamespace(
        count_id=uuid4(),
        organization_id=ORG_ID,
        count_number="CNT-0007",
        count_description="Monthly cycle count",
        count_date=date(2026, 6, 10),
        warehouse_id=uuid4(),
        location_id=None,
        is_full_count=False,
        is_cycle_count=True,
        status=status,
        total_items=120,
        items_counted=45,
        items_with_variance=3,
    )


def _fake_line(counted: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        line_id=uuid4(),
        count_id=uuid4(),
        item_id=uuid4(),
        warehouse_id=uuid4(),
        location_id=None,
        lot_id=None,
        system_quantity=Decimal("10"),
        uom="EA",
        counted_quantity=Decimal("9") if counted else None,
        variance_quantity=Decimal("-1") if counted else None,
    )


class TestListCounts:
    @patch("app.services.inventory.inventory_count_service")
    def test_list_filters_by_status(self, svc, inv_client) -> None:
        svc.list.return_value = [_fake_count(), _fake_count()]

        resp = inv_client.get(
            "/api/v1/inventory/counts", params={"status": "IN_PROGRESS"}
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 2
        assert body["items"][0]["count_number"] == "CNT-0007"
        kwargs = svc.list.call_args.kwargs
        assert kwargs["organization_id"] == str(ORG_ID)
        assert kwargs["status"] is not None

    @patch("app.services.inventory.inventory_count_service")
    def test_list_invalid_status_is_400(self, svc, inv_client) -> None:
        resp = inv_client.get(
            "/api/v1/inventory/counts", params={"status": "NOT_A_STATUS"}
        )
        assert resp.status_code == 400
        svc.list.assert_not_called()


class TestGetCount:
    @patch("app.services.inventory.inventory_count_service")
    def test_get_returns_header(self, svc, inv_client) -> None:
        count = _fake_count()
        svc.get.return_value = count

        resp = inv_client.get(f"/api/v1/inventory/counts/{count.count_id}")

        assert resp.status_code == 200
        assert resp.json()["items_counted"] == 45

    @patch("app.services.inventory.inventory_count_service")
    def test_get_passes_org_for_service_side_scoping(self, svc, inv_client) -> None:
        count = _fake_count()
        svc.get.return_value = count

        resp = inv_client.get(f"/api/v1/inventory/counts/{count.count_id}")

        assert resp.status_code == 200
        # Org scoping lives in the service now — the route must pass the org.
        assert svc.get.call_args.args[2] == ORG_ID

    def test_service_get_rejects_foreign_org_count(self) -> None:
        from fastapi import HTTPException

        from app.services.inventory.count import InventoryCountService

        foreign = _fake_count()
        foreign.organization_id = uuid4()
        db = MagicMock()
        db.get.return_value = foreign

        with pytest.raises(HTTPException) as exc_info:
            InventoryCountService.get(db, str(foreign.count_id), ORG_ID)

        assert exc_info.value.status_code == 404


class TestListCountLines:
    @patch("app.services.inventory.inventory_count_service")
    def test_lines_pass_is_counted_filter(self, svc, inv_client) -> None:
        count = _fake_count()
        svc.get.return_value = count
        svc.list_lines.return_value = [_fake_line(), _fake_line(counted=True)]

        resp = inv_client.get(
            f"/api/v1/inventory/counts/{count.count_id}/lines",
            params={"is_counted": "false"},
        )

        assert resp.status_code == 200
        assert resp.json()["count"] == 2
        assert svc.list_lines.call_args.kwargs["is_counted"] is False


class TestRecordCountLine:
    @patch("app.services.inventory.inventory_count_service")
    def test_record_maps_payload_to_count_line_input(self, svc, inv_client) -> None:
        line = _fake_line(counted=True)
        svc.record_count.return_value = line
        item_id, wh_id = uuid4(), uuid4()

        resp = inv_client.post(
            f"/api/v1/inventory/counts/{uuid4()}/lines/record",
            json={
                "item_id": str(item_id),
                "warehouse_id": str(wh_id),
                "counted_quantity": "9",
                "notes": "shelf A3",
            },
        )

        assert resp.status_code == 200
        assert resp.json()["counted_quantity"] == "9"
        kwargs = svc.record_count.call_args.kwargs
        assert kwargs["counted_by_user_id"] == PERSON_ID
        line_input = kwargs["input"]
        assert line_input.item_id == item_id
        assert line_input.warehouse_id == wh_id
        assert line_input.counted_quantity == Decimal("9")
        assert line_input.notes == "shelf A3"


class TestBulkRecord:
    @patch("app.services.inventory.inventory_count_service")
    def test_bulk_record_maps_lines(self, svc, inv_client) -> None:
        svc.record_count_bulk.return_value = [
            _fake_line(counted=True),
            _fake_line(counted=True),
        ]
        l1, l2 = uuid4(), uuid4()

        resp = inv_client.post(
            f"/api/v1/inventory/counts/{uuid4()}/bulk-record",
            json={
                "lines": [
                    {"line_id": str(l1), "counted_quantity": "5"},
                    {"line_id": str(l2), "counted_quantity": "7.5"},
                ]
            },
        )

        assert resp.status_code == 200
        assert resp.json()["count"] == 2
        inputs = svc.record_count_bulk.call_args.kwargs["inputs"]
        assert [i.line_id for i in inputs] == [l1, l2]
        assert inputs[1].counted_quantity == Decimal("7.5")


class TestQuantityValidation:
    @patch("app.services.inventory.inventory_count_service")
    def test_negative_counted_quantity_rejected(self, svc, inv_client) -> None:
        resp = inv_client.post(
            f"/api/v1/inventory/counts/{uuid4()}/lines/record",
            json={
                "item_id": str(uuid4()),
                "warehouse_id": str(uuid4()),
                "counted_quantity": "-3",
            },
        )
        assert resp.status_code == 422
        svc.record_count.assert_not_called()
