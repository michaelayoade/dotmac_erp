from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from sqlalchemy import JSON, select, text
from starlette.responses import RedirectResponse
from starlette.requests import Request

from app.models.inventory.inventory_serial import (
    InventorySerial,
    InventorySerialMovement,
)
from app.models.inventory.inventory_transaction import (
    InventoryTransaction,
    TransactionType,
)
from app.models.inventory.item import CostingMethod, Item, ItemType
from app.models.inventory.warehouse import Warehouse
from app.services.operations.inv_web import OperationsInventoryWebService
from app.services.operations.inv_web import _serials_url

UTC = timezone.utc
from app.web.deps import WebAuthContext
from tests.conftest import (
    DEFAULT_TEST_ORG_ID,
    SQLiteUUID,
    _strip_sqlite_server_defaults,
)


TEST_PERSON_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")

INVENTORY_TABLES = [
    Warehouse.__table__,
    Item.__table__,
    InventoryTransaction.__table__,
    InventorySerial.__table__,
    InventorySerialMovement.__table__,
]
INVENTORY_TABLE_SCHEMAS = {table: table.schema for table in INVENTORY_TABLES}
INVENTORY_TABLE_TYPES = {
    column: column.type for table in INVENTORY_TABLES for column in table.columns
}


@pytest.fixture(autouse=True)
def inventory_tables(engine, db_session, monkeypatch):
    import app.services.audit_listener as audit_listener

    monkeypatch.setattr(audit_listener, "_collect_changes", lambda session: [])
    db_session.expire_on_commit = False
    _strip_sqlite_server_defaults(INVENTORY_TABLES)
    try:
        db_session.execute(text("ATTACH DATABASE ':memory:' AS inv"))
    except Exception:
        db_session.rollback()
    for table in INVENTORY_TABLES:
        table.schema = INVENTORY_TABLE_SCHEMAS[table]
        for column in table.columns:
            if hasattr(column.type, "as_uuid"):
                column.type = SQLiteUUID()
            elif column is Warehouse.__table__.c.address:
                column.type = JSON()
    for table in INVENTORY_TABLES:
        table.create(engine, checkfirst=True)

    for table in reversed(INVENTORY_TABLES):
        db_session.execute(table.delete())
    db_session.commit()
    yield
    for table in reversed(INVENTORY_TABLES):
        db_session.execute(table.delete())
    db_session.commit()
    for table, schema in INVENTORY_TABLE_SCHEMAS.items():
        table.schema = schema
    for column, column_type in INVENTORY_TABLE_TYPES.items():
        column.type = column_type


@pytest.fixture(autouse=True)
def scoped_base_context(monkeypatch):
    import app.services.operations.inv_web as inv_web

    def _base_context(request, auth, page_title, active_nav):
        return {
            "request": request,
            "user": auth.user,
            "page_title": page_title,
            "active_nav": active_nav,
        }

    monkeypatch.setattr(inv_web, "base_context", _base_context)


@pytest.fixture
def auth_context() -> WebAuthContext:
    return WebAuthContext(
        is_authenticated=True,
        person_id=TEST_PERSON_ID,
        organization_id=DEFAULT_TEST_ORG_ID,
        user_name="Inventory Admin",
        user_initials="IA",
        roles=["admin"],
    )


@pytest.fixture
def service() -> OperationsInventoryWebService:
    return OperationsInventoryWebService()


def _request(path: str = "/inventory/serials?status=missing_serials") -> Request:
    clean_path, _, query = path.partition("?")
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": clean_path,
            "query_string": query.encode(),
            "headers": [],
            "client": ("testclient", 123),
            "server": ("testserver", 80),
            "scheme": "http",
        }
    )
    request.state.csrf_form = ""
    request.state.csrf_token = "test-csrf"
    return request


def _seed_serial_tracked_stock(
    db_session,
    *,
    quantity_on_hand: Decimal | int,
    existing_serials: list[str] | None = None,
    organization_id: uuid.UUID = DEFAULT_TEST_ORG_ID,
    item_code: str | None = None,
    item_name: str = "Serialized Item",
    warehouse_code: str | None = None,
    warehouse_name: str = "Main Warehouse",
) -> tuple[Item, Warehouse]:
    item = Item(
        organization_id=organization_id,
        item_code=item_code or f"ITEM-{uuid.uuid4().hex[:8]}",
        item_name=item_name,
        item_type=ItemType.INVENTORY,
        category_id=uuid.uuid4(),
        base_uom="EA",
        costing_method=CostingMethod.WEIGHTED_AVERAGE,
        currency_code="USD",
        track_inventory=True,
        track_lots=False,
        track_serial_numbers=True,
        is_active=True,
        is_purchaseable=True,
        is_saleable=True,
    )
    warehouse = Warehouse(
        organization_id=organization_id,
        warehouse_code=warehouse_code or f"WH-{uuid.uuid4().hex[:8]}",
        warehouse_name=warehouse_name,
        is_active=True,
    )
    db_session.add_all([item, warehouse])
    db_session.flush()

    db_session.add(
        InventoryTransaction(
            organization_id=organization_id,
            transaction_type=TransactionType.RECEIPT,
            transaction_date=datetime.now(UTC),
            fiscal_period_id=uuid.uuid4(),
            item_id=item.item_id,
            warehouse_id=warehouse.warehouse_id,
            quantity=Decimal(str(quantity_on_hand)),
            uom="EA",
            unit_cost=Decimal("10"),
            total_cost=Decimal("10") * Decimal(str(quantity_on_hand)),
            currency_code="USD",
            quantity_before=Decimal("0"),
            quantity_after=Decimal(str(quantity_on_hand)),
            created_by_user_id=TEST_PERSON_ID,
        )
    )

    for serial_number in existing_serials or []:
        db_session.add(
            InventorySerial(
                organization_id=organization_id,
                item_id=item.item_id,
                warehouse_id=warehouse.warehouse_id,
                serial_number=serial_number,
                status="AVAILABLE",
                is_active=True,
            )
        )

    db_session.commit()
    return item, warehouse


def _missing_rows(service, db_session, auth_context):
    response = service.list_serials_response(
        request=_request(),
        auth=auth_context,
        db=db_session,
        status="missing_serials",
    )
    return response.context["serial_rows"], response.context["missing_serials_count"]


def _missing_context(
    service,
    db_session,
    auth_context,
    *,
    search: str | None = None,
    warehouse: str | None = None,
    item: str | None = None,
):
    return service.list_serials_response(
        request=_request(),
        auth=auth_context,
        db=db_session,
        status="missing_serials",
        search=search,
        warehouse=warehouse,
        item=item,
    ).context


def _missing_row_for(service, db_session, auth_context, item_id: uuid.UUID):
    rows, _ = _missing_rows(service, db_session, auth_context)
    return next((row for row in rows if row["item"]["item_id"] == item_id), None)


def _serial_numbers_for(db_session, item: Item, warehouse: Warehouse) -> list[str]:
    return list(
        db_session.scalars(
            select(InventorySerial.serial_number)
            .where(
                InventorySerial.organization_id == DEFAULT_TEST_ORG_ID,
                InventorySerial.item_id == item.item_id,
                InventorySerial.warehouse_id == warehouse.warehouse_id,
                InventorySerial.is_active.is_(True),
            )
            .order_by(InventorySerial.serial_number)
        )
    )


def _query_params(location: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(location).query)


@pytest.mark.asyncio
async def test_add_missing_serials_route_reads_form_when_csrf_state_is_template_html(
    monkeypatch, db_session, auth_context
):
    import app.web.inventory as inventory_web

    captured: dict[str, str] = {}

    def fake_add_missing_serials_response(
        request, auth, db, *, item_id, warehouse_id, serial_numbers
    ):
        captured.update(
            {
                "item_id": item_id,
                "warehouse_id": warehouse_id,
                "serial_numbers": serial_numbers,
            }
        )
        return RedirectResponse("/inventory/serials", status_code=303)

    monkeypatch.setattr(
        inventory_web.operations_inv_web_service,
        "add_missing_serials_response",
        fake_add_missing_serials_response,
    )
    body = urlencode(
        {
            "item_id": str(uuid.uuid4()),
            "warehouse_id": str(uuid.uuid4()),
            "serial_numbers": "SN-001",
            "csrf_token": "token",
        }
    ).encode()

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/inventory/serials/missing/add",
            "query_string": b"",
            "headers": [
                (b"content-type", b"application/x-www-form-urlencoded"),
                (b"content-length", str(len(body)).encode()),
            ],
            "client": ("testclient", 123),
            "server": ("testserver", 80),
            "scheme": "http",
        },
        receive,
    )
    request.state.csrf_form = '<input type="hidden" name="csrf_token" value="token">'

    response = await inventory_web.add_missing_serials(
        request=request,
        auth=auth_context,
        db=db_session,
    )

    assert response.status_code == 303
    assert captured["serial_numbers"] == "SN-001"


def _add_missing_serials(
    service,
    db_session,
    auth_context,
    item: Item | uuid.UUID | str,
    warehouse: Warehouse | uuid.UUID | str,
    serial_numbers: str,
):
    item_id = getattr(item, "item_id", item)
    warehouse_id = getattr(warehouse, "warehouse_id", warehouse)
    return service.add_missing_serials_response(
        request=_request(),
        auth=auth_context,
        db=db_session,
        item_id=str(item_id),
        warehouse_id=str(warehouse_id),
        serial_numbers=serial_numbers,
    )


def test_serials_url_encodes_special_query_characters():
    url = _serials_url(
        status="missing_serials",
        item=uuid.UUID("00000000-0000-0000-0000-000000000123"),
        error="Serial A&B?=100% missing",
    )

    params = _query_params(url)
    assert params["status"] == ["missing_serials"]
    assert params["item"] == ["00000000-0000-0000-0000-000000000123"]
    assert params["error"] == ["Serial A&B?=100% missing"]
    assert "Serial A&B?=100% missing" not in url


def test_missing_serials_template_uses_modal_instead_of_inline_table_form():
    template = Path("templates/inventory/serials.html").read_text()

    assert 'x-data="' in template
    assert "serialModalOpen" in template
    assert 'role="dialog"' in template
    assert 'aria-modal="true"' in template
    assert '@keydown.escape.window="closeSerialModal()"' in template
    assert "@click='openSerialModal({" in template
    assert '@click="openSerialModal({' not in template
    assert 'class="fixed inset-0 bg-black/60 backdrop-blur-sm"' in template
    assert 'x-trap="serialModalOpen"' in template
    assert '<details class="group">' not in template
    assert 'class="mt-3 w-80 space-y-3' not in template


def test_missing_serials_card_count_sums_missing_quantities(
    db_session, service, auth_context
):
    router_a, _warehouse_a = _seed_serial_tracked_stock(db_session, quantity_on_hand=5)
    router_b, _warehouse_b = _seed_serial_tracked_stock(db_session, quantity_on_hand=3)

    context = _missing_context(service, db_session, auth_context)

    assert {row["item"]["item_id"] for row in context["serial_rows"]} == {
        router_a.item_id,
        router_b.item_id,
    }
    assert context["filtered_total"] == 2
    assert context["missing_serials_count"] == 8


def test_missing_serials_list_is_scoped_to_authenticated_organization(
    db_session, service, auth_context
):
    visible_item, _visible_warehouse = _seed_serial_tracked_stock(
        db_session,
        quantity_on_hand=2,
        item_code="VISIBLE-ROUTER",
        item_name="Visible Router",
    )
    other_org_id = uuid.uuid4()
    hidden_item, _hidden_warehouse = _seed_serial_tracked_stock(
        db_session,
        quantity_on_hand=7,
        organization_id=other_org_id,
        item_code="HIDDEN-ROUTER",
        item_name="Hidden Router",
    )

    context = _missing_context(service, db_session, auth_context)

    assert {row["item"]["item_id"] for row in context["serial_rows"]} == {
        visible_item.item_id
    }
    assert hidden_item.item_id not in {
        row["item"]["item_id"] for row in context["serial_rows"]
    }
    assert context["missing_serials_count"] == 2


def test_missing_serials_supports_warehouse_filtering(
    db_session, service, auth_context
):
    main_item, main_warehouse = _seed_serial_tracked_stock(
        db_session,
        quantity_on_hand=2,
        item_code="MAIN-ROUTER",
        warehouse_code="WH-MAIN",
        warehouse_name="Main Warehouse",
    )
    _remote_item, _remote_warehouse = _seed_serial_tracked_stock(
        db_session,
        quantity_on_hand=4,
        item_code="REMOTE-ROUTER",
        warehouse_code="WH-REMOTE",
        warehouse_name="Remote Warehouse",
    )

    context = _missing_context(
        service,
        db_session,
        auth_context,
        warehouse=str(main_warehouse.warehouse_id),
    )

    assert [row["item"]["item_id"] for row in context["serial_rows"]] == [
        main_item.item_id
    ]
    assert context["filtered_total"] == 1
    assert context["missing_serials_count"] == 2


def test_missing_serials_supports_item_filtering(db_session, service, auth_context):
    selected_item, _selected_warehouse = _seed_serial_tracked_stock(
        db_session,
        quantity_on_hand=3,
        item_code="SELECTED-ROUTER",
    )
    _other_item, _other_warehouse = _seed_serial_tracked_stock(
        db_session,
        quantity_on_hand=6,
        item_code="OTHER-ROUTER",
    )

    context = _missing_context(
        service,
        db_session,
        auth_context,
        item=str(selected_item.item_id),
    )

    assert [row["item"]["item_id"] for row in context["serial_rows"]] == [
        selected_item.item_id
    ]
    assert context["filtered_total"] == 1
    assert context["missing_serials_count"] == 3


def test_missing_serials_supports_search_filtering(db_session, service, auth_context):
    matching_item, _matching_warehouse = _seed_serial_tracked_stock(
        db_session,
        quantity_on_hand=5,
        item_code="RTR-SEARCH",
        item_name="Searchable Router",
    )
    _other_item, _other_warehouse = _seed_serial_tracked_stock(
        db_session,
        quantity_on_hand=4,
        item_code="SWITCH-001",
        item_name="Network Switch",
    )

    context = _missing_context(
        service,
        db_session,
        auth_context,
        search="searchable",
    )

    assert [row["item"]["item_id"] for row in context["serial_rows"]] == [
        matching_item.item_id
    ]
    assert context["filtered_total"] == 1
    assert context["missing_serials_count"] == 5


def test_adding_one_serial_to_item_missing_multiple_updates_missing_quantity(
    db_session, service, auth_context
):
    item, warehouse = _seed_serial_tracked_stock(db_session, quantity_on_hand=3)

    response = _add_missing_serials(
        service, db_session, auth_context, item, warehouse, "SN-001"
    )

    assert response.status_code == 303
    assert _query_params(response.headers["location"])["status"] == ["missing_serials"]
    assert _serial_numbers_for(db_session, item, warehouse) == ["SN-001"]

    row = _missing_row_for(service, db_session, auth_context, item.item_id)
    assert row is not None
    assert row["quantity_on_hand"] == Decimal("3.000000")
    assert row["serial_count"] == 1
    assert row["missing_count"] == Decimal("2.000000")
    assert (
        _missing_context(service, db_session, auth_context)["missing_serials_count"]
        == 2
    )


def test_adding_all_remaining_serials_removes_item_from_missing_serials(
    db_session, service, auth_context
):
    item, warehouse = _seed_serial_tracked_stock(
        db_session, quantity_on_hand=3, existing_serials=["SN-001"]
    )

    response = _add_missing_serials(
        service,
        db_session,
        auth_context,
        item,
        warehouse,
        "SN-002\nSN-003",
    )

    assert response.status_code == 303
    assert "status" not in _query_params(response.headers["location"])
    assert _serial_numbers_for(db_session, item, warehouse) == [
        "SN-001",
        "SN-002",
        "SN-003",
    ]
    assert _missing_row_for(service, db_session, auth_context, item.item_id) is None

    rows, missing_count = _missing_rows(service, db_session, auth_context)
    assert rows == []
    assert missing_count == 0


def test_duplicate_serial_numbers_are_rejected(db_session, service, auth_context):
    item, warehouse = _seed_serial_tracked_stock(
        db_session, quantity_on_hand=2, existing_serials=["SN-001"]
    )

    response = _add_missing_serials(
        service, db_session, auth_context, item, warehouse, "SN-001"
    )

    assert response.status_code == 303
    location = response.headers["location"]
    params = _query_params(location)
    assert params["status"] == ["missing_serials"]
    assert params["error"] == ["Serial number already exists"]
    assert _serial_numbers_for(db_session, item, warehouse) == ["SN-001"]

    row = _missing_row_for(service, db_session, auth_context, item.item_id)
    assert row is not None
    assert row["missing_count"] == Decimal("1.000000")


def test_duplicate_serial_numbers_in_same_submission_are_rejected(
    db_session, service, auth_context
):
    item, warehouse = _seed_serial_tracked_stock(db_session, quantity_on_hand=2)

    response = _add_missing_serials(
        service, db_session, auth_context, item, warehouse, "SN-001\nSN-001"
    )

    assert response.status_code == 303
    assert _query_params(response.headers["location"])["error"] == [
        "Duplicate serial number"
    ]
    assert _serial_numbers_for(db_session, item, warehouse) == []

    row = _missing_row_for(service, db_session, auth_context, item.item_id)
    assert row is not None
    assert row["missing_count"] == Decimal("2.000000")


def test_cannot_add_more_serials_than_remaining_missing_quantity(
    db_session, service, auth_context
):
    item, warehouse = _seed_serial_tracked_stock(
        db_session, quantity_on_hand=2, existing_serials=["SN-001"]
    )

    response = _add_missing_serials(
        service,
        db_session,
        auth_context,
        item,
        warehouse,
        "SN-002\nSN-003",
    )

    assert response.status_code == 303
    assert _query_params(response.headers["location"])["error"] == [
        "Serial count cannot exceed missing quantity"
    ]
    assert _serial_numbers_for(db_session, item, warehouse) == ["SN-001"]

    row = _missing_row_for(service, db_session, auth_context, item.item_id)
    assert row is not None
    assert row["missing_count"] == Decimal("1.000000")


def test_invalid_item_id_is_rejected(db_session, service, auth_context):
    _item, warehouse = _seed_serial_tracked_stock(db_session, quantity_on_hand=1)

    malformed = _add_missing_serials(
        service, db_session, auth_context, "not-a-uuid", warehouse, "SN-001"
    )
    assert malformed.status_code == 303
    assert _query_params(malformed.headers["location"])["error"] == [
        "Invalid item or warehouse"
    ]

    nonexistent = _add_missing_serials(
        service, db_session, auth_context, uuid.uuid4(), warehouse, "SN-001"
    )
    assert nonexistent.status_code == 303
    assert _query_params(nonexistent.headers["location"])["error"] == [
        "Item or warehouse not found"
    ]


def test_invalid_warehouse_id_is_rejected(db_session, service, auth_context):
    item, _warehouse = _seed_serial_tracked_stock(db_session, quantity_on_hand=1)

    malformed = _add_missing_serials(
        service, db_session, auth_context, item, "not-a-uuid", "SN-001"
    )
    assert malformed.status_code == 303
    assert _query_params(malformed.headers["location"])["error"] == [
        "Invalid item or warehouse"
    ]

    nonexistent = _add_missing_serials(
        service, db_session, auth_context, item, uuid.uuid4(), "SN-001"
    )
    assert nonexistent.status_code == 303
    assert _query_params(nonexistent.headers["location"])["error"] == [
        "Item or warehouse not found"
    ]


def test_recomputed_remaining_quantity_rejects_stale_concurrent_over_add(
    db_session, service, auth_context
):
    item, warehouse = _seed_serial_tracked_stock(db_session, quantity_on_hand=2)

    first = _add_missing_serials(
        service, db_session, auth_context, item, warehouse, "SN-001"
    )
    assert first.status_code == 303

    second = _add_missing_serials(
        service,
        db_session,
        auth_context,
        item,
        warehouse,
        "SN-002\nSN-003",
    )

    assert second.status_code == 303
    assert _query_params(second.headers["location"])["error"] == [
        "Serial count cannot exceed missing quantity"
    ]
    assert _serial_numbers_for(db_session, item, warehouse) == ["SN-001"]

    row = _missing_row_for(service, db_session, auth_context, item.item_id)
    assert row is not None
    assert row["serial_count"] == 1
    assert row["missing_count"] == Decimal("1.000000")


def test_stale_add_after_missing_serials_completed_returns_user_facing_error(
    db_session, service, auth_context
):
    item, warehouse = _seed_serial_tracked_stock(db_session, quantity_on_hand=1)

    first = _add_missing_serials(
        service, db_session, auth_context, item, warehouse, "SN-001"
    )
    assert first.status_code == 303
    assert "status" not in _query_params(first.headers["location"])

    stale_second = _add_missing_serials(
        service, db_session, auth_context, item, warehouse, "SN-002"
    )

    assert stale_second.status_code == 303
    assert _query_params(stale_second.headers["location"])["error"] == [
        "Missing serials already completed"
    ]
    assert _serial_numbers_for(db_session, item, warehouse) == ["SN-001"]
    assert _missing_row_for(service, db_session, auth_context, item.item_id) is None
