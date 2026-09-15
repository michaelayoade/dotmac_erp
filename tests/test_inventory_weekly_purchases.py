"""Regression coverage for the read-only weekly purchases projection.

The SQLite projection copies the real ORM column types while omitting unrelated
ERP tables and write constraints. It exercises the report SQL rather than
mocking totals. PostgreSQL/RLS acceptance remains a separate rollout gate.
"""

from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Column, MetaData, Table, create_engine, insert
from sqlalchemy.orm import Session

from app.models.finance.ap.goods_receipt import GoodsReceipt
from app.models.finance.ap.goods_receipt_line import GoodsReceiptLine
from app.models.finance.ap.supplier import Supplier
from app.models.finance.ap.supplier_invoice import (
    PostingStatus,
    SupplierInvoice,
    SupplierInvoiceStatus,
    SupplierInvoiceType,
)
from app.models.finance.ap.supplier_invoice_line import SupplierInvoiceLine
from app.models.inventory.item import Item, ItemType
from app.models.inventory.item_category import ItemCategory
from app.models.inventory.warehouse import Warehouse
from app.services.inventory import weekly_purchases as report_module
from app.services.inventory.weekly_purchases import (
    PurchaseFilters,
    WeeklyPurchasesService,
    csv_text,
    format_report_number,
    resolve_purchase_week,
)

TODAY = date(2026, 9, 15)
ORG = UUID("10000000-0000-0000-0000-000000000001")
OTHER_ORG = UUID("20000000-0000-0000-0000-000000000002")
ITEM_ID, SUPPLIER_ID, CATEGORY_ID, WAREHOUSE_ID = (uuid4() for _ in range(4))


@pytest.mark.parametrize(
    ("selected", "start", "end", "through"),
    [
        (None, "2026-09-14", "2026-09-20", "2026-09-15"),
        ("2026-09-13", "2026-09-07", "2026-09-13", "2026-09-13"),
        ("2026-09-14", "2026-09-14", "2026-09-20", "2026-09-15"),
        ("2026-01-01", "2025-12-29", "2026-01-04", "2026-01-04"),
        ("2024-02-29", "2024-02-26", "2024-03-03", "2024-03-03"),
    ],
)
def test_week_boundaries(selected, start, end, through):
    week = resolve_purchase_week(selected, today=TODAY)
    assert week.start.isoformat() == start
    assert week.end.isoformat() == end
    assert week.through.isoformat() == through


def test_lagos_monday_while_utc_is_still_sunday():
    utc_now = datetime(2026, 9, 13, 23, 30, tzinfo=timezone.utc)
    with patch.object(report_module, "datetime") as clock:
        clock.now.return_value = utc_now.astimezone(report_module.REPORT_TIMEZONE)
        week = resolve_purchase_week()
    clock.now.assert_called_once_with(report_module.REPORT_TIMEZONE)
    assert week.start == date(2026, 9, 14)
    assert week.end_exclusive == date(2026, 9, 15)


@pytest.mark.parametrize(
    "value",
    ["not-a-date", "2026-02-30", "20260914", "2026-W38-1", "1899-12-31", "2026-09-21"],
)
def test_invalid_or_future_week(value):
    with pytest.raises(ValueError):
        resolve_purchase_week(value, today=TODAY)


@pytest.mark.parametrize("name", ["supplier", "warehouse", "category"])
def test_invalid_filter_identifiers(name):
    with pytest.raises(ValueError, match="valid"):
        PurchaseFilters.parse(**{name: "invalid"}, today=TODAY)


def test_normalized_query_parameters_and_search_limit():
    selected = PurchaseFilters.parse(
        week_start="2026-09-15",
        supplier=str(SUPPLIER_ID),
        search="  cable & wire  ",
        today=TODAY,
    )
    assert selected.query_params() == {
        "week_start": "2026-09-14",
        "supplier": str(SUPPLIER_ID),
        "search": "cable & wire",
    }
    with pytest.raises(ValueError, match="100"):
        PurchaseFilters.parse(search="x" * 101, today=TODAY)


@pytest.mark.parametrize(
    "value", ["=1+1", " @SUM(1)", "+1", "-1", "\ttext", "\ntext", "\ufeff=1"]
)
def test_csv_untrusted_text_is_neutralized(value):
    assert csv_text(value) == "'" + value


def test_csv_and_decimal_formatting_preserve_values():
    assert csv_text("Dotmac, Abuja") == "Dotmac, Abuja"
    assert csv_text(None) == ""
    assert format_report_number(Decimal("-1234.125"), 3) == "(1,234.125)"
    assert format_report_number(Decimal("12345678901234.123456"), 6) == (
        "12,345,678,901,234.123456"
    )


@pytest.fixture
def projection():
    """Build just the report columns from real model types, not ORM fixtures."""
    model_columns = {
        SupplierInvoice: "invoice_id organization_id supplier_id invoice_number invoice_date invoice_type status posting_status currency_code is_prepayment",
        SupplierInvoiceLine: "line_id invoice_id item_id goods_receipt_line_id receipt_warehouse_id quantity unit_price line_amount tax_amount",
        Supplier: "supplier_id organization_id legal_name is_active",
        Item: "item_id organization_id item_code item_name item_type category_id track_inventory is_active",
        ItemCategory: "category_id organization_id category_name is_active",
        Warehouse: "warehouse_id organization_id warehouse_name is_active",
        GoodsReceipt: "receipt_id organization_id warehouse_id",
        GoodsReceiptLine: "line_id receipt_id",
    }
    metadata = MetaData()
    tables = {}
    for model, names in model_columns.items():
        original = model.__table__
        tables[model] = Table(
            original.name,
            metadata,
            *(
                Column(
                    name,
                    original.c[name].type,
                    primary_key=original.c[name].primary_key,
                )
                for name in names.split()
            ),
            schema=original.schema,
        )
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"ap": None, "inv": None}},
    )
    metadata.create_all(engine)
    with engine.connect() as connection:

        def put(model, **values):
            connection.execute(insert(tables[model]).values(**values))

        put(
            Supplier,
            supplier_id=SUPPLIER_ID,
            organization_id=ORG,
            legal_name="Supplier A",
            is_active=False,
        )
        put(
            ItemCategory,
            category_id=CATEGORY_ID,
            organization_id=ORG,
            category_name="Fibre",
            is_active=False,
        )
        put(
            Warehouse,
            warehouse_id=WAREHOUSE_ID,
            organization_id=ORG,
            warehouse_name="Abuja",
            is_active=False,
        )
        put(
            Item,
            item_id=ITEM_ID,
            organization_id=ORG,
            item_code="CABLE_10%",
            item_name="Fibre Cable",
            item_type=ItemType.INVENTORY,
            category_id=CATEGORY_ID,
            track_inventory=True,
            is_active=False,
        )
        connection.commit()

        def purchase(
            *,
            amount="100",
            tax="7.5",
            currency="NGN",
            status=SupplierInvoiceStatus.POSTED,
            kind=SupplierInvoiceType.STANDARD,
            invoice_date=TODAY,
            org=ORG,
            supplier=SUPPLIER_ID,
            item=ITEM_ID,
            warehouse=WAREHOUSE_ID,
            receipt_line=None,
            prepayment=False,
            posting_status=PostingStatus.POSTED,
            invoice_id=None,
        ):
            new_invoice = invoice_id is None
            invoice_id = invoice_id or uuid4()
            if new_invoice:
                put(
                    SupplierInvoice,
                    invoice_id=invoice_id,
                    organization_id=org,
                    supplier_id=supplier,
                    invoice_number=str(invoice_id)[:12],
                    invoice_date=invoice_date,
                    invoice_type=kind,
                    status=status,
                    posting_status=posting_status,
                    currency_code=currency,
                    is_prepayment=prepayment,
                )
            line_id = uuid4()
            put(
                SupplierInvoiceLine,
                line_id=line_id,
                invoice_id=invoice_id,
                item_id=item,
                receipt_warehouse_id=warehouse,
                goods_receipt_line_id=receipt_line,
                quantity=Decimal("2"),
                unit_price=Decimal("50"),
                line_amount=Decimal(amount),
                tax_amount=Decimal(tax),
            )
            connection.commit()
            return invoice_id, line_id

        with Session(bind=connection) as db:
            yield WeeklyPurchasesService(db), purchase, put
    engine.dispose()


def filters(**kwargs):
    return PurchaseFilters.parse(today=TODAY, **kwargs)


def test_amounts_currencies_credits_and_no_receipt_double_count(projection):
    service, purchase, put = projection
    receipt, receipt_line = uuid4(), uuid4()
    put(
        GoodsReceipt,
        receipt_id=receipt,
        organization_id=ORG,
        warehouse_id=WAREHOUSE_ID,
    )
    put(GoodsReceiptLine, line_id=receipt_line, receipt_id=receipt)
    invoice_id, _ = purchase(receipt_line=receipt_line, warehouse=None)
    purchase(invoice_id=invoice_id, amount="20", tax="1.5")
    purchase(kind=SupplierInvoiceType.CREDIT_NOTE, amount="10", tax="0.75")
    purchase(kind=SupplierInvoiceType.CREDIT_NOTE, amount="-5", tax="-0.375")
    purchase(kind=SupplierInvoiceType.DEBIT_NOTE, amount="30", tax="2.25")
    purchase(currency="USD", amount="9", tax="0")
    report = service.report(ORG, filters())
    assert report["summary"] == {
        "line_count": 6,
        "document_count": 5,
        "item_count": 1,
        "supplier_count": 1,
    }
    totals = {row["currency_code"]: row for row in report["currency_totals"]}
    assert totals["NGN"]["purchases"] == Decimal("150")
    assert totals["NGN"]["credits"] == Decimal("15")
    assert totals["NGN"]["net"] == Decimal("135")
    assert totals["NGN"]["tax"] == Decimal("10.125")
    assert totals["USD"]["net"] == Decimal("9")
    assert all(row["warehouse_id"] == WAREHOUSE_ID for row in report["purchase_rows"])


def test_only_posted_non_prepayment_in_period_stock_lines(projection):
    service, purchase, put = projection
    for status in SupplierInvoiceStatus:
        purchase(status=status, posting_status=PostingStatus.NOT_POSTED)
    purchase(status=SupplierInvoiceStatus.ON_HOLD)
    purchase(status=SupplierInvoiceStatus.DISPUTED)
    purchase(prepayment=True)
    purchase(invoice_date=date(2026, 9, 13))
    purchase(invoice_date=date(2026, 9, 16))
    for item_type, track in [
        (ItemType.SERVICE, True),
        (ItemType.NON_INVENTORY, False),
        (ItemType.INVENTORY, False),
    ]:
        item = uuid4()
        put(
            Item,
            item_id=item,
            organization_id=ORG,
            item_code="OTHER",
            item_name="Other",
            item_type=item_type,
            category_id=CATEGORY_ID,
            track_inventory=track,
            is_active=True,
        )
        purchase(item=item)
    assert service.report(ORG, filters())["total_count"] == 5
    assert service.report(ORG, filters(week_start="2026-09-13"))["total_count"] == 1


def test_cross_tenant_invoice_and_joined_masters_are_not_exposed(projection):
    service, purchase, put = projection
    foreign_item, foreign_supplier, foreign_warehouse, foreign_category = (
        uuid4() for _ in range(4)
    )
    put(
        Supplier,
        supplier_id=foreign_supplier,
        organization_id=OTHER_ORG,
        legal_name="PRIVATE",
    )
    put(
        Item,
        item_id=foreign_item,
        organization_id=OTHER_ORG,
        item_code="PRIVATE",
        item_name="PRIVATE",
        item_type=ItemType.INVENTORY,
        category_id=foreign_category,
        track_inventory=True,
    )
    put(
        Warehouse,
        warehouse_id=foreign_warehouse,
        organization_id=OTHER_ORG,
        warehouse_name="PRIVATE",
    )
    put(
        ItemCategory,
        category_id=foreign_category,
        organization_id=OTHER_ORG,
        category_name="PRIVATE",
    )
    purchase(org=OTHER_ORG)
    purchase(supplier=foreign_supplier)
    purchase(item=foreign_item)
    purchase(warehouse=foreign_warehouse)
    assert service.report(ORG, filters())["total_count"] == 1
    assert service.report(ORG, filters())["purchase_rows"][0]["warehouse_name"] is None
    assert not service.export_rows(ORG, filters(warehouse=str(foreign_warehouse)))
    assert not service.export_rows(ORG, filters(supplier=str(foreign_supplier)))
    assert not service.export_rows(ORG, filters(category=str(foreign_category)))
    assert "PRIVATE" not in str(service.options(ORG, filters()))
    with pytest.raises(ValueError, match="organization"):
        service.statement(None, filters())


def test_foreign_receipt_cannot_supply_warehouse(projection):
    service, purchase, put = projection
    receipt, line = uuid4(), uuid4()
    put(
        GoodsReceipt,
        receipt_id=receipt,
        organization_id=OTHER_ORG,
        warehouse_id=WAREHOUSE_ID,
    )
    put(GoodsReceiptLine, line_id=line, receipt_id=receipt)
    purchase(warehouse=None, receipt_line=line)
    assert service.report(ORG, filters())["purchase_rows"][0]["warehouse_name"] is None


def test_pagination_does_not_truncate_totals_export_or_inactive_options(projection):
    service, purchase, _ = projection
    invoice_id, _ = purchase()
    for _ in range(54):
        purchase(invoice_id=invoice_id)
    purchase(warehouse=None)
    selected = filters(
        supplier=str(SUPPLIER_ID), category=str(CATEGORY_ID), search="CABLE_10%"
    )
    report = service.report(ORG, selected)
    assert len(report["purchase_rows"]) == 50
    assert report["total_count"] == 56
    assert report["currency_totals"][0]["net"] == Decimal("5600")
    assert len(service.export_rows(ORG, selected)) == 56
    last_page = service.report(ORG, selected, page=99)
    assert last_page["page"] == 2
    assert len(last_page["purchase_rows"]) == 6
    assert len(service.export_rows(ORG, filters(warehouse=str(WAREHOUSE_ID)))) == 55
    assert service.options(ORG, selected)["warehouses"] == [
        {"id": str(WAREHOUSE_ID), "label": "Abuja"}
    ]
    assert service.options(ORG, selected)["suppliers"] == [
        {"id": str(SUPPLIER_ID), "label": "Supplier A"}
    ]
    with patch.object(report_module, "MAX_EXPORT_ROWS", 55):
        with pytest.raises(ValueError, match="exceeds"):
            service.export_rows(ORG, selected)


def test_search_metacharacters_are_literal_and_empty_week_is_valid(projection):
    service, purchase, put = projection
    item = uuid4()
    put(
        Item,
        item_id=item,
        organization_id=ORG,
        item_code="CABLEA10OTHER",
        item_name="Other",
        item_type=ItemType.INVENTORY,
        category_id=CATEGORY_ID,
        track_inventory=True,
    )
    purchase()
    purchase(item=item)
    assert service.report(ORG, filters(search="_10%"))["total_count"] == 1
    empty = service.report(ORG, filters(search="no match"))
    assert empty["total_count"] == 0
    assert empty["currency_totals"] == []
    assert empty["page"] == empty["total_pages"] == 1
    assert service.export_rows(ORG, filters(search="no match")) == []
